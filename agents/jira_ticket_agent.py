from __future__ import annotations

import re
from typing import Any

import httpx

from models.response_schemas import JiraResponse
from models.schemas import AnalysisState, DeliveryStatus, Incident, JiraTicket, RemediationPlan, Severity
from prompts.prompt_templates import JIRA_SYSTEM_PROMPT, JIRA_USER_PROMPT
from tools.config import settings
from tools.guardrails import sanitize_outbound
from tools.http_retry import post_with_retry
from tools.llm_client import get_llm_client
from tools.logging_config import logger
from tools.tracing import record_fallback, run_parallel

PRIORITY_BY_SEVERITY = {Severity.CRITICAL: "Highest", Severity.ERROR: "High", Severity.WARNING: "Medium", Severity.INFO: "Low"}
VALID_PRIORITIES = {"Highest", "High", "Medium", "Low", "Lowest"}
MAX_DESCRIPTION_CHARS = 30_000


def prepare_jira_tickets(state: AnalysisState) -> dict:
    """JIRA agent, step 1: draft one ticket per ERROR/CRITICAL incident. Nothing is created until approval."""
    critical = sorted(
        (i for i in state.incidents if i.severity.rank >= Severity.ERROR.rank),
        key=lambda i: i.severity.rank, reverse=True,
    )[: settings.max_jira_tickets]
    plans = {p.incident_id: p for p in state.remediation_plans}
    return {"jira_tickets": run_parallel(lambda incident: _draft(incident, plans.get(incident.incident_id)), critical)}


def _draft(incident: Incident, remediation: RemediationPlan | None) -> JiraTicket:
    default_priority = PRIORITY_BY_SEVERITY[incident.severity]
    try:
        response = get_llm_client().structured_complete(
            prompt=JIRA_USER_PROMPT.format(
                incident=incident.model_dump_json(indent=2, exclude={"evidence_event_ids"}),
                remediation=remediation.model_dump_json(indent=2) if remediation else "No remediation plan.",
            ),
            system_prompt=JIRA_SYSTEM_PROMPT,
            model=settings.reasoning_model,
            schema=JiraResponse,
        )
        summary, description = response.summary, response.description
        priority = response.priority.title() if response.priority.title() in VALID_PRIORITIES else default_priority
        labels = response.labels
    except Exception:
        logger.exception("JIRA drafting failed for %s; using fallback", incident.incident_id)
        record_fallback()
        actions = remediation.immediate_actions if remediation else []
        summary = f"[{incident.severity.value}] {incident.title}"
        description = "\n\n".join([
            f"Summary\n{incident.summary}",
            f"Affected services\n{', '.join(incident.affected_services) or 'unknown'}",
            "Recommended actions\n" + ("\n".join(f"- {a}" for a in actions) or "- Investigate evidence events."),
        ])
        priority, labels = default_priority, []

    return JiraTicket(
        incident_id=incident.incident_id,
        project_key=settings.jira_project_key,
        summary=summary.replace("\n", " ")[:255],
        description=description,
        priority=priority,
        labels=_clean_labels([*labels, "incident", "ai-generated", incident.category.value.lower()]),
    )


def _clean_labels(labels: list[str]) -> list[str]:
    # Jira labels cannot contain spaces.
    cleaned = (re.sub(r"\s+", "-", label.strip().lower())[:50] for label in labels if label and label.strip())
    return list(dict.fromkeys(cleaned))


def to_adf(text: str) -> dict[str, Any]:
    """Jira REST API v3 requires descriptions in Atlassian Document Format, not plain strings."""
    paragraphs = []
    for block in (b for b in text.split("\n\n") if b.strip()):
        content: list[dict[str, Any]] = []
        for n, line in enumerate(l for l in block.split("\n") if l.strip()):
            if n:
                content.append({"type": "hardBreak"})
            content.append({"type": "text", "text": line})
        paragraphs.append({"type": "paragraph", "content": content})
    return {"type": "doc", "version": 1, "content": paragraphs}


def deliver_ticket(ticket: JiraTicket, n: int = 1) -> JiraTicket:
    """Create one ticket in Jira (live) or assign a simulated key (simulation mode)."""
    if settings.jira_mode == "simulation":
        key = f"{ticket.project_key}-SIM-{n}"
        base = settings.jira_base_url.rstrip("/") or "https://your-jira-site.atlassian.net"
        return ticket.model_copy(update={"issue_key": key, "issue_url": f"{base}/browse/{key}", "delivery_status": DeliveryStatus.SIMULATED})
    return _create_live(ticket)


def create_jira_tickets(state: AnalysisState) -> dict:
    """JIRA agent, step 2 (after approval): create the approved tickets."""
    if not state.jira_tickets:
        return {}
    tickets = []
    for n, ticket in enumerate(state.jira_tickets, start=1):
        if ticket.delivery_status != DeliveryStatus.READY_FOR_APPROVAL:
            tickets.append(ticket)
        elif settings.jira_mode == "simulation":
            key = f"{ticket.project_key}-SIM-{n}"
            base = settings.jira_base_url.rstrip("/") or "https://your-jira-site.atlassian.net"
            tickets.append(ticket.model_copy(update={"issue_key": key, "issue_url": f"{base}/browse/{key}", "delivery_status": DeliveryStatus.SIMULATED}))
        else:
            tickets.append(ticket)
    # Live tickets are created in parallel; order is preserved.
    live = [i for i, t in enumerate(tickets) if t.delivery_status == DeliveryStatus.READY_FOR_APPROVAL and settings.jira_mode == "live"]
    for i, created in zip(live, run_parallel(_create_live, [tickets[i] for i in live])):
        tickets[i] = created
    return {"jira_tickets": tickets}


def _field_errors(response: httpx.Response) -> dict:
    try:
        return response.json().get("errors") or {}
    except ValueError:
        return {}


def _create_live(ticket: JiraTicket) -> JiraTicket:
    base = settings.jira_base_url.rstrip("/")
    description, _ = sanitize_outbound(ticket.description, MAX_DESCRIPTION_CHARS)
    fields: dict[str, Any] = {
        "project": {"key": ticket.project_key},
        "summary": sanitize_outbound(ticket.summary, 255)[0],
        "description": to_adf(description),
        "issuetype": {"name": settings.jira_issue_type},
        "priority": {"name": ticket.priority},
        "labels": ticket.labels,
    }
    auth = (settings.jira_email, settings.jira_api_token.get_secret_value())
    try:
        response = post_with_retry(f"{base}/rest/api/3/issue", auth=auth, json={"fields": fields})
        # Some projects hide priority/labels on the create screen; retry once without the rejected fields.
        rejected = set(_field_errors(response)) & {"priority", "labels"} if response.status_code == 400 else set()
        if rejected:
            logger.warning("Jira rejected fields %s; retrying without them", sorted(rejected))
            response = post_with_retry(f"{base}/rest/api/3/issue", auth=auth,
                                       json={"fields": {k: v for k, v in fields.items() if k not in rejected}})
        response.raise_for_status()
        key = response.json().get("key")
        update = {"issue_key": key, "issue_url": f"{base}/browse/{key}", "delivery_status": DeliveryStatus.CREATED, "delivery_error": None}
    except httpx.HTTPStatusError as exc:
        update = {"delivery_status": DeliveryStatus.FAILED, "delivery_error": f"Jira returned HTTP {exc.response.status_code}: {exc.response.text[:300]}"}
    except httpx.HTTPError as exc:
        update = {"delivery_status": DeliveryStatus.FAILED, "delivery_error": f"Jira request failed: {type(exc).__name__}"}
    logger.info("Jira delivery for %s: %s", ticket.incident_id, update["delivery_status"].value)
    return ticket.model_copy(update=update)
