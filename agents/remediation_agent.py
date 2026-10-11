from __future__ import annotations

from models.response_schemas import RemediationResponse
from models.schemas import AnalysisState, AnalysisStatus, Incident, ParsedLogEvent, RemediationPlan, Severity
from prompts.prompt_templates import REMEDIATION_SYSTEM_PROMPT, REMEDIATION_USER_PROMPT
from tools.config import settings
from tools.llm_client import get_llm_client
from tools.logging_config import logger
from tools.tracing import record_fallback, run_parallel
from tools.vector_store import get_vector_store


def generate_remediation(state: AnalysisState) -> dict:
    """Remediation agent: retrieve runbooks (RAG) and map each incident to fixes with rationale."""
    lookup = {e.event_id: e for e in state.parsed_events}
    results = run_parallel(lambda incident: _plan_for(incident, lookup), state.incidents)
    return {
        "remediation_plans": [plan for plan, _ in results],
        "retrieved_context": [chunk for _, context in results for chunk in context],
        "status": AnalysisStatus.REMEDIATION_GENERATED,
    }


def _plan_for(incident: Incident, lookup: dict[str, ParsedLogEvent]) -> tuple[RemediationPlan, list[dict]]:
    # The raw log messages share vocabulary with runbooks ("connection pool", "OOMKilled"), so they make a
    # sharper retrieval query than the LLM's summary; evals showed fewer wrong-runbook matches this way.
    messages = " ".join(lookup[eid].message for eid in incident.evidence_event_ids if eid in lookup)
    query = f"{incident.title}. {messages}"[:1000]
    try:
        context = get_vector_store().search(query)
    except Exception:
        logger.exception("Runbook retrieval failed for %s", incident.incident_id)
        context = []
    context_sources = sorted({c.get("source", "unknown") for c in context})

    evidence = "\n".join(
        f"[{eid}] {lookup[eid].level} | {lookup[eid].service} | {lookup[eid].message}"
        for eid in incident.evidence_event_ids if eid in lookup
    ) or "No specific evidence available."
    context_text = "\n\n".join(f"Source: {c.get('source', 'unknown')}\n{c['text']}" for c in context) or "No runbook context found."

    try:
        response = get_llm_client().structured_complete(
            prompt=REMEDIATION_USER_PROMPT.format(
                incident=incident.model_dump_json(indent=2, exclude={"evidence_event_ids"}),
                events=evidence,
                context=context_text,
            ),
            system_prompt=REMEDIATION_SYSTEM_PROMPT,
            model=settings.reasoning_model,
            schema=RemediationResponse,
        )
        # Only cite runbooks that were actually retrieved.
        sources = sorted(set(response.sources) & set(context_sources)) or context_sources
        plan = RemediationPlan(incident_id=incident.incident_id, **response.model_dump(exclude={"sources"}), sources=sources)
    except Exception:
        logger.exception("Remediation failed for %s; using fallback", incident.incident_id)
        record_fallback()
        plan = _fallback(incident, context, context_sources)
    return plan, context


def _fallback(incident: Incident, context: list[dict], sources: list[str]) -> RemediationPlan:
    runbook_steps = [
        line.strip()[3:].strip()
        for c in context[:1]
        for line in c["text"].splitlines()
        if line.strip()[:3] in {f"{n}. " for n in range(1, 10)}
    ]
    return RemediationPlan(
        incident_id=incident.incident_id,
        root_cause_hypothesis="Unable to determine automatically. Manual investigation required.",
        immediate_actions=runbook_steps[:5] or [
            "Review linked evidence events.",
            "Check service dashboards, recent deployments, and dependency health.",
            "Engage the service owner if customer impact continues.",
        ],
        investigation_steps=[
            "Correlate errors with deployments and infrastructure changes.",
            "Inspect application, database, and network metrics.",
        ],
        preventive_actions=["Improve alerting for this failure pattern.", "Update the relevant runbook."],
        risk_level=incident.severity if incident.severity.rank >= Severity.ERROR.rank else Severity.WARNING,
        confidence=0.3,
        sources=sources,
    )
