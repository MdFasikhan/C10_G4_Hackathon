from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from models.schemas import (
    AnalysisResult, AnalysisState, AnalysisStatus, ApprovalRequest, DeliveryStatus,
)
from api.security import require_api_key, upload_rate_limit
from orchestration.graph import run_analysis, run_notifications
from tools.config import settings
from tools.guardrails import is_probably_binary
from tools.logging_config import logger
from tools.storage import get_store

router = APIRouter(prefix="/analyses", tags=["analysis"], dependencies=[Depends(require_api_key)])
ALLOWED_EXTENSIONS = {".log", ".txt", ".json", ".jsonl", ".csv"}
CHUNK_BYTES = 1024 * 1024


@router.post("/upload", response_model=AnalysisResult, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(upload_rate_limit)])
async def upload_logs(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Store the uploaded log file and start the analysis workflow in the background."""
    filename = Path(file.filename or "").name  # drop any client-supplied directories
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    analysis_id = uuid4().hex
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    upload_path = settings.upload_path / f"{analysis_id}{suffix}"
    max_bytes = settings.max_upload_mb * 1024 * 1024

    size = 0
    with upload_path.open("wb") as handle:
        while chunk := await file.read(CHUNK_BYTES):
            if size == 0 and is_probably_binary(chunk[:8192]):
                handle.close()
                upload_path.unlink(missing_ok=True)
                raise HTTPException(status_code=415, detail="This looks like a binary file. Upload a text log.")
            size += len(chunk)
            if size > max_bytes:
                handle.close()
                upload_path.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB limit.")
            handle.write(chunk)
    if size == 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    store = get_store()
    result = store.create_analysis(analysis_id, filename)
    store.record_audit(analysis_id, "user", "UPLOAD", {"filename": filename, "size_bytes": size})
    background_tasks.add_task(execute_analysis, analysis_id, str(upload_path), filename)
    return result


def execute_analysis(analysis_id: str, upload_path: str, filename: str = ""):
    store = get_store()
    state = AnalysisState(analysis_id=analysis_id, upload_path=upload_path, upload_filename=filename, status=AnalysisStatus.RUNNING)
    store.save_result(state.to_result())
    try:
        state = run_analysis(state, on_update=lambda s: store.save_result(s.to_result()))
    except Exception as exc:
        logger.exception("Analysis workflow failed: %s", analysis_id)
        state.status = AnalysisStatus.FAILED
        state.error = f"Analysis workflow failed: {type(exc).__name__}"
    store.save_result(state.to_result())
    store.record_audit(analysis_id, "system", "ANALYSIS_COMPLETED", {
        "status": state.status.value,
        "events": len(state.parsed_events),
        "incidents": len(state.incidents),
        "runbook_sources": sorted({c.get("source", "unknown") for c in state.retrieved_context}),
        "guardrail_findings": len(state.guardrail_findings),
        "metrics": state.metrics.model_dump() if state.metrics else None,
    })


@router.get("")
def list_analyses(limit: int = 50):
    return get_store().list_analyses(limit=min(max(limit, 1), 200))


@router.get("/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: str):
    return _get_or_404(analysis_id)


@router.get("/{analysis_id}/audit")
def get_audit(analysis_id: str):
    _get_or_404(analysis_id)
    return get_store().get_audit(analysis_id)


@router.post("/{analysis_id}/approve", response_model=AnalysisResult)
def approve_notifications(analysis_id: str, request: ApprovalRequest | None = None):
    """Human-in-the-loop gate: only now are the reviewed Slack/JIRA drafts sent."""
    request = request or ApprovalRequest()
    store = get_store()
    result = _get_or_404(analysis_id)
    # Atomic check-and-set, so a double click cannot send notifications twice.
    if not store.transition_status(analysis_id, {AnalysisStatus.AWAITING_APPROVAL}, AnalysisStatus.NOTIFYING):
        current = store.get_result(analysis_id)
        raise HTTPException(status_code=409, detail=f"Analysis is not awaiting approval. Current status: {current.status.value}")
    store.record_audit(analysis_id, request.approver, "NOTIFICATIONS_APPROVED", {"comment": request.comment})

    state = AnalysisState.from_result(result)
    try:
        state = run_notifications(state)
    except Exception as exc:
        logger.exception("Notification workflow failed: %s", analysis_id)
        state.status = AnalysisStatus.NOTIFICATION_FAILED
        state.error = f"Notification workflow failed: {type(exc).__name__}"

    final = state.to_result()
    store.save_result(final)
    store.record_audit(analysis_id, "system", "NOTIFICATIONS_DELIVERED", {
        "status": final.status.value,
        "slack": final.slack_notification.delivery_status.value if final.slack_notification else None,
        "jira": [{"issue_key": t.issue_key, "status": t.delivery_status.value} for t in final.jira_tickets],
    })
    return final


@router.post("/{analysis_id}/reject", response_model=AnalysisResult)
def reject_notifications(analysis_id: str, request: ApprovalRequest | None = None):
    """Discard the Slack/JIRA drafts; nothing is sent."""
    request = request or ApprovalRequest()
    store = get_store()
    result = _get_or_404(analysis_id)
    if not store.transition_status(analysis_id, {AnalysisStatus.AWAITING_APPROVAL}, AnalysisStatus.REJECTED):
        raise HTTPException(status_code=409, detail=f"Analysis is not awaiting approval. Current status: {result.status.value}")
    result.status = AnalysisStatus.REJECTED
    if result.slack_notification:
        result.slack_notification.delivery_status = DeliveryStatus.REJECTED
    for ticket in result.jira_tickets:
        ticket.delivery_status = DeliveryStatus.REJECTED
    store.save_result(result)
    store.record_audit(analysis_id, request.approver, "NOTIFICATIONS_REJECTED", {"comment": request.comment})
    return result


def _get_or_404(analysis_id: str) -> AnalysisResult:
    result = get_store().get_result(analysis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return result
