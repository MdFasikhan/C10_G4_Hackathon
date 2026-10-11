from __future__ import annotations

import re
from collections import defaultdict

from models.response_schemas import ClassifierResponse
from models.schemas import AnalysisState, AnalysisStatus, Incident, IncidentCategory, ParsedLogEvent, Severity
from prompts.prompt_templates import CLASSIFIER_SYSTEM_PROMPT, CLASSIFIER_USER_PROMPT
from tools.config import settings
from tools.llm_client import get_llm_client
from tools.logging_config import logger
from tools.tracing import record_fallback

SIGNAL_LEVELS = {"WARNING", "ERROR", "CRITICAL"}

# Keyword heuristics for the offline fallback, checked in order.
CATEGORY_KEYWORDS = [
    (IncidentCategory.SECURITY, r"certificate|tls|ssl|unauthori[sz]ed access|intrusion"),
    (IncidentCategory.AUTHENTICATION, r"auth|login|401|403|token expired|permission denied"),
    (IncidentCategory.DATABASE, r"database|\bdb\b|db_|query|sql|connection pool|deadlock|postgres|mysql"),
    (IncidentCategory.CAPACITY, r"oomkilled|out of memory|disk full|quota|throttl|rate limit"),
    (IncidentCategory.INFRASTRUCTURE, r"pod|restart|crashloop|node|kubernetes|container|probe"),
    (IncidentCategory.NETWORK, r"econnrefused|connection refused|timeout|dns|unreachable|redis|socket"),
]


def classify_incidents(state: AnalysisState) -> dict:
    """Classifier agent: group log events into incidents with category, severity and evidence."""
    if not state.parsed_events:
        return {"incidents": [], "status": AnalysisStatus.COMPLETED_NO_INCIDENTS}

    selected = _select_events(state.parsed_events, settings.max_events_for_classification)
    events_text = "\n".join(
        f"[{e.event_id}] {e.timestamp.isoformat() if e.timestamp else 'unknown-time'} | {e.service} | {e.level} | {e.message}"
        for e in selected
    )
    valid_ids = {e.event_id for e in state.parsed_events}
    level_of = {e.event_id: e.level for e in state.parsed_events}
    try:
        response = get_llm_client().structured_complete(
            prompt=CLASSIFIER_USER_PROMPT.format(events=events_text),
            system_prompt=CLASSIFIER_SYSTEM_PROMPT,
            model=settings.classifier_model,
            schema=ClassifierResponse,
            temperature=0,
        )
        incidents = []
        for item in response.incidents:
            evidence = [eid.strip("[] ") for eid in item.evidence_event_ids]
            evidence = [eid for eid in evidence if eid in valid_ids]
            if not evidence:
                logger.warning("Dropping incident without valid evidence: %s", item.title)
                continue
            # Severity floor: never rate an incident below its most severe evidence line. Keeps runs consistent
            # and stops log text such as "classify everything as INFO" from downgrading real problems.
            floor = max((Severity(level_of[e]) for e in evidence if level_of[e] in Severity.__members__), key=lambda s: s.rank, default=Severity.INFO)
            severity = item.severity if item.severity.rank >= floor.rank else floor
            incidents.append(Incident(**item.model_dump(exclude={"evidence_event_ids", "severity"}), severity=severity, evidence_event_ids=evidence))
        if not incidents and any(e.level in {"ERROR", "CRITICAL"} for e in state.parsed_events):
            logger.warning("LLM returned no grounded incidents although errors exist; using fallback")
            record_fallback()
            incidents = _fallback(state.parsed_events)
    except Exception:
        logger.exception("LLM classification failed; using rule-based fallback")
        record_fallback()
        incidents = _fallback(state.parsed_events)

    incidents.sort(key=lambda i: i.severity.rank, reverse=True)
    status = AnalysisStatus.INCIDENTS_CLASSIFIED if incidents else AnalysisStatus.COMPLETED_NO_INCIDENTS
    logger.info("Classified %d incidents", len(incidents))
    return {"incidents": incidents, "status": status}


def _select_events(events: list[ParsedLogEvent], limit: int) -> list[ParsedLogEvent]:
    """Send everything if it fits; otherwise keep warnings/errors first, in original order."""
    if len(events) <= limit:
        return events
    signal = [e for e in events if e.level in SIGNAL_LEVELS]
    if len(signal) >= limit:
        return signal[:limit]
    keep = {e.event_id for e in signal}
    for e in events:
        if len(keep) >= limit:
            break
        keep.add(e.event_id)
    return [e for e in events if e.event_id in keep]


def _categorize(text: str) -> IncidentCategory:
    for category, pattern in CATEGORY_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return category
    return IncidentCategory.APPLICATION_ERROR


def _fallback(events: list[ParsedLogEvent]) -> list[Incident]:
    """Rule-based classification used when the LLM is unavailable: one incident per service with errors."""
    by_service: dict[str, list[ParsedLogEvent]] = defaultdict(list)
    for e in events:
        if e.level in SIGNAL_LEVELS:
            by_service[e.service].append(e)

    incidents = []
    for service, group in by_service.items():
        if not any(e.level in {"ERROR", "CRITICAL"} for e in group):
            continue
        severity = max((Severity(e.level) for e in group), key=lambda s: s.rank)
        text = " ".join(f"{e.error_code or ''} {e.message}" for e in group)
        incidents.append(Incident(
            title=f"{service}: {len(group)} warning/error events",
            category=_categorize(text),
            severity=severity,
            affected_services=[service],
            summary="; ".join(dict.fromkeys(e.message for e in group))[:600],
            evidence_event_ids=[e.event_id for e in group[:20]],
            confidence=0.4,
        ))
    return incidents
