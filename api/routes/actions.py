"""Per-incident approval actions from the UI: "Send" (Jira ticket + Slack alert) or "Don't send".

Clicking the button is the human approval. Every call is audited, outbound text passes the same
guardrails as the automated flow, and Slack/Jira run live or simulated according to SLACK_MODE / JIRA_MODE.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agents.jira_ticket_agent import PRIORITY_BY_SEVERITY, VALID_PRIORITIES, _clean_labels, deliver_ticket
from agents.slack_notification_agent import deliver_slack_message
from api.security import require_api_key
from models.schemas import AnalysisResult, AnalysisStatus, DeliveryStatus, Incident, JiraTicket, RemediationPlan, Severity
from tools.config import settings
from tools.guardrails import sanitize_outbound
from tools.logging_config import logger
from tools.storage import get_store

router = APIRouter(tags=["actions"], dependencies=[Depends(require_api_key)])


class SlackSendRequest(BaseModel):
    message: Optional[str] = Field(default=None, max_length=3000, description="Edited message; default is generated from the incident")
    actor: str = Field(default="ui-user", max_length=100)


class JiraCreateRequest(BaseModel):
    summary: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=30_000)
    priority: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    actor: str = Field(default="ui-user", max_length=100)


def _integrations() -> dict:
    return {
        "slack": {"mode": settings.slack_mode, "channel": settings.slack_channel, "can_go_live": settings.slack_configured},
        "jira": {"mode": settings.jira_mode, "project_key": settings.jira_project_key, "can_go_live": settings.jira_configured,
                 "base_url": settings.jira_base_url.rstrip("/") if settings.jira_mode == "live" else ""},
    }


@router.get("/integrations")
def integrations():
    """Non-secret integration status for the UI."""
    return _integrations()


class IntegrationModes(BaseModel):
    slack_mode: Optional[Literal["simulation", "live"]] = None
    jira_mode: Optional[Literal["simulation", "live"]] = None
    actor: str = Field(default="ui-user", max_length=100)


@router.put("/integrations")
def set_integration_modes(request: IntegrationModes):
    """Switch Slack/Jira between simulation and live at runtime (Settings page).

    The change lasts until the API restarts; SLACK_MODE / JIRA_MODE in .env set the startup default.
    Live needs the credentials from .env; they are never sent to or from the browser.
    """
    if request.slack_mode == "live" and not settings.slack_configured:
        raise HTTPException(status_code=400, detail="Slack can't go live: SLACK_WEBHOOK_URL is not set in .env.")
    if request.jira_mode == "live" and not settings.jira_configured:
        raise HTTPException(status_code=400, detail="Jira can't go live: JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN must be set in .env.")
    for name in ("slack_mode", "jira_mode"):
        value = getattr(request, name)
        if value and value != getattr(settings, name):
            logger.warning("%s changed from %s to %s by %s", name, getattr(settings, name), value, request.actor)
            setattr(settings, name, value)
    return _integrations()


def _load(analysis_id: str, incident_id: str | None = None) -> tuple[AnalysisResult, Incident | None, RemediationPlan | None]:
    result = get_store().get_result(analysis_id)
    if not result:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    if incident_id is None:
        return result, None, None
    incident = next((i for i in result.incidents if i.incident_id == incident_id), None)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found in this analysis.")
    plan = next((p for p in result.remediation_plans if p.incident_id == incident_id), None)
    return result, incident, plan


def incident_slack_text(incident: Incident, plan: RemediationPlan | None, ticket: JiraTicket | None, analysis_id: str) -> str:
    actions = (plan.immediate_actions if plan else [])[:3]
    lines = [
        f":rotating_light: *[{incident.severity.value}] {incident.title}*",
        f"*Affected service:* {', '.join(incident.affected_services) or 'unknown'}  ·  *Category:* {incident.category.value.replace('_', ' ').title()}",
        f"*Root cause:* {plan.root_cause_hypothesis if plan else incident.summary}",
    ]
    if actions:
        lines.append("*Recommended remediation:*\n" + "\n".join(f"• {a}" for a in actions))
    lines.append(f"*Analysis:* {analysis_id[:8]}" + (f"  ·  *Jira:* <{ticket.issue_url}|{ticket.issue_key}>" if ticket and ticket.issue_url else ""))
    return "\n".join(lines)


def _post_incident_to_slack(result: AnalysisResult, incident: Incident, plan: RemediationPlan | None,
                            message: str | None, actor: str) -> dict:
    ticket = next((t for t in result.jira_tickets if t.incident_id == incident.incident_id and t.issue_key), None)
    text, rules = sanitize_outbound(message or incident_slack_text(incident, plan, ticket, result.analysis_id), 3000)
    outcome = deliver_slack_message(text)
    result.incident_slack[incident.incident_id] = outcome["delivery_status"]
    get_store().record_audit(result.analysis_id, actor, "SLACK_SENT_FOR_INCIDENT", {
        "incident_id": incident.incident_id, "status": outcome["delivery_status"].value, "guardrails": rules,
    })
    return {**outcome, "mode": settings.slack_mode, "channel": settings.slack_channel, "guardrails": rules}


def _complete_if_all_decided(result: AnalysisResult) -> None:
    """Once every critical incident has been sent or dismissed, the analysis no longer awaits approval."""
    def decided(incident_id: str) -> bool:
        return (incident_id in result.dismissed_incidents
                or result.incident_slack.get(incident_id) in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED)
                or any(t.incident_id == incident_id and t.delivery_status in (DeliveryStatus.CREATED, DeliveryStatus.SIMULATED)
                       for t in result.jira_tickets))
    critical = [i.incident_id for i in result.incidents if i.severity == Severity.CRITICAL]
    if critical and all(decided(i) for i in critical) and result.status == AnalysisStatus.AWAITING_APPROVAL:
        if get_store().transition_status(result.analysis_id, [AnalysisStatus.AWAITING_APPROVAL], AnalysisStatus.COMPLETED):
            result.status = AnalysisStatus.COMPLETED


@router.post("/analyses/{analysis_id}/incidents/{incident_id}/slack")
def send_incident_to_slack(analysis_id: str, incident_id: str, request: SlackSendRequest | None = None):
    request = request or SlackSendRequest()
    result, incident, plan = _load(analysis_id, incident_id)
    outcome = _post_incident_to_slack(result, incident, plan, request.message, request.actor)
    get_store().save_result(result)
    return outcome


@router.post("/analyses/{analysis_id}/slack")
def send_analysis_summary_to_slack(analysis_id: str, request: SlackSendRequest | None = None):
    """Send the analysis-level Slack draft (all incidents) without creating Jira tickets."""
    request = request or SlackSendRequest()
    result, _, _ = _load(analysis_id)
    if not result.slack_notification and not request.message:
        raise HTTPException(status_code=409, detail="This analysis has no Slack draft (no incidents were detected).")
    text = request.message or result.slack_notification.message
    outcome = deliver_slack_message(text)
    if result.slack_notification:
        result.slack_notification = result.slack_notification.model_copy(update=outcome)
    store = get_store()
    store.save_result(result)
    store.record_audit(analysis_id, request.actor, "SLACK_SUMMARY_SENT", {"status": outcome["delivery_status"].value})
    return {**outcome, "mode": settings.slack_mode, "channel": settings.slack_channel}


def _create_incident_ticket(result: AnalysisResult, incident: Incident, plan: RemediationPlan | None,
                            request: JiraCreateRequest) -> JiraTicket:
    incident_id = incident.incident_id
    draft = next((t for t in result.jira_tickets if t.incident_id == incident_id), None) or JiraTicket(
        incident_id=incident_id, project_key=settings.jira_project_key,
        summary=f"[{incident.severity.value}] {incident.title}",
        description="\n\n".join(filter(None, [
            f"Summary\n{incident.summary}",
            f"Root cause\n{plan.root_cause_hypothesis}" if plan else "",
            "Recommended actions\n" + "\n".join(f"- {a}" for a in plan.immediate_actions) if plan and plan.immediate_actions else "",
            f"Rollback\n{plan.rollback_plan}" if plan else "",
        ])),
        priority=PRIORITY_BY_SEVERITY[incident.severity],
        labels=["incident", "ai-generated", incident.category.value.lower()],
    )
    priority = (request.priority or draft.priority).title()
    ticket = draft.model_copy(update={
        "project_key": settings.jira_project_key,
        "summary": (request.summary or draft.summary).replace("\n", " ")[:255],
        "description": request.description or draft.description,
        "priority": priority if priority in VALID_PRIORITIES else draft.priority,
        "labels": _clean_labels(request.labels or draft.labels),
        "delivery_status": DeliveryStatus.READY_FOR_APPROVAL,
    })
    created = deliver_ticket(ticket, n=len(result.jira_tickets) + 1)
    result.jira_tickets = [t for t in result.jira_tickets if t.incident_id != incident_id] + [created]
    get_store().record_audit(result.analysis_id, request.actor, "JIRA_CREATED_FOR_INCIDENT", {
        "incident_id": incident_id, "status": created.delivery_status.value, "issue_key": created.issue_key,
    })
    return created


def _existing_ticket(result: AnalysisResult, incident_id: str) -> JiraTicket | None:
    return next((t for t in result.jira_tickets if t.incident_id == incident_id
                 and t.delivery_status in (DeliveryStatus.CREATED, DeliveryStatus.SIMULATED)), None)


@router.post("/analyses/{analysis_id}/incidents/{incident_id}/jira")
def create_incident_ticket(analysis_id: str, incident_id: str, request: JiraCreateRequest | None = None):
    request = request or JiraCreateRequest()
    result, incident, plan = _load(analysis_id, incident_id)
    existing = _existing_ticket(result, incident_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Ticket {existing.issue_key} already exists for this incident.")
    created = _create_incident_ticket(result, incident, plan, request)
    get_store().save_result(result)
    if created.delivery_status == DeliveryStatus.FAILED:
        raise HTTPException(status_code=502, detail=created.delivery_error or "Jira rejected the ticket.")
    return {**created.model_dump(), "mode": settings.jira_mode}


class DecisionRequest(BaseModel):
    actor: str = Field(default="ui-user", max_length=100)


@router.post("/analyses/{analysis_id}/incidents/{incident_id}/send")
def approve_and_send(analysis_id: str, incident_id: str, request: DecisionRequest | None = None):
    """The approval in one click: create the Jira ticket (if none yet), then post to Slack with the ticket link.

    Each half is reported separately, so a Slack failure never hides a ticket that was created (and vice versa).
    Retrying is safe: an existing ticket is reused, not duplicated.
    """
    request = request or DecisionRequest()
    result, incident, plan = _load(analysis_id, incident_id)
    result.dismissed_incidents.pop(incident_id, None)
    ticket = _existing_ticket(result, incident_id) or _create_incident_ticket(result, incident, plan, JiraCreateRequest(actor=request.actor))
    previous = result.incident_slack.get(incident_id)
    if previous in (DeliveryStatus.SENT, DeliveryStatus.SIMULATED):  # retry after a Jira failure: don't post twice
        slack = {"delivery_status": previous, "delivery_error": None, "mode": settings.slack_mode,
                 "channel": settings.slack_channel, "guardrails": [], "already_sent": True}
    else:
        slack = _post_incident_to_slack(result, incident, plan, None, request.actor)
    _complete_if_all_decided(result)
    get_store().save_result(result)
    return {
        "jira": {**ticket.model_dump(), "mode": settings.jira_mode},
        "slack": slack,
        "analysis_status": result.status.value,
    }


@router.post("/analyses/{analysis_id}/incidents/{incident_id}/dismiss")
def dismiss_incident(analysis_id: str, incident_id: str, request: DecisionRequest | None = None):
    """'Don't send': record the decision so the incident leaves the approval queue. Nothing is sent."""
    request = request or DecisionRequest()
    result, _, _ = _load(analysis_id, incident_id)
    result.dismissed_incidents[incident_id] = request.actor
    get_store().record_audit(analysis_id, request.actor, "NOTIFICATION_DISMISSED_FOR_INCIDENT", {"incident_id": incident_id})
    _complete_if_all_decided(result)
    get_store().save_result(result)
    return {"incident_id": incident_id, "dismissed": True, "analysis_status": result.status.value}
