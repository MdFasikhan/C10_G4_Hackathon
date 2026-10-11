from __future__ import annotations

import json
import re

import httpx

from models.response_schemas import SlackResponse
from models.schemas import AnalysisState, DeliveryStatus, SlackNotification
from prompts.prompt_templates import SLACK_SYSTEM_PROMPT, SLACK_USER_PROMPT
from tools.config import settings
from tools.guardrails import sanitize_outbound
from tools.http_retry import post_with_retry
from tools.llm_client import get_llm_client
from tools.logging_config import logger
from tools.tracing import record_fallback

MAX_MESSAGE_CHARS = 3000


def prepare_slack_notification(state: AnalysisState) -> dict:
    """Notification agent, step 1: draft one Slack alert. Nothing is sent until a human approves."""
    if not state.incidents:
        return {}
    incidents = sorted(state.incidents, key=lambda i: i.severity.rank, reverse=True)
    plans = {p.incident_id: p for p in state.remediation_plans}
    first_actions = {
        i.title: plans[i.incident_id].immediate_actions[:2]
        for i in incidents if i.incident_id in plans and plans[i.incident_id].immediate_actions
    }

    try:
        response = get_llm_client().structured_complete(
            prompt=SLACK_USER_PROMPT.format(
                incidents="\n".join(i.model_dump_json(include={"title", "severity", "category", "affected_services", "summary"}) for i in incidents),
                actions=json.dumps(first_actions, indent=2),
            ),
            system_prompt=SLACK_SYSTEM_PROMPT,
            model=settings.classifier_model,
            schema=SlackResponse,
        )
        message = to_slack_mrkdwn(response.message)
    except Exception:
        logger.exception("Slack drafting failed; using fallback")
        record_fallback()
        message = _fallback_message(incidents, first_actions)

    notification = SlackNotification(
        channel=settings.slack_channel,
        message=message[:MAX_MESSAGE_CHARS],
        incident_ids=[i.incident_id for i in incidents],
    )
    return {"slack_notification": notification}


def to_slack_mrkdwn(text: str) -> str:
    """Models often write Markdown (**bold**, ## headings); Slack shows those literally."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    return re.sub(r"(?m)^#+\s*(.+)$", r"*\1*", text)


def _fallback_message(incidents, first_actions: dict[str, list[str]]) -> str:
    top = incidents[0]
    lines = [f":rotating_light: *{top.severity.value}: {top.title}*", top.summary, ""]
    for incident in incidents:
        actions = first_actions.get(incident.title) or ["Investigate evidence events."]
        lines.append(f"• *{incident.severity.value}* {incident.title} ({', '.join(incident.affected_services) or 'unknown'}) — next: {actions[0]}")
    return "\n".join(lines)


def send_slack_notification(state: AnalysisState) -> dict:
    """Notification agent, step 2 (after approval): deliver the approved draft."""
    notification = state.slack_notification
    if not notification or notification.delivery_status != DeliveryStatus.READY_FOR_APPROVAL:
        return {}
    return {"slack_notification": notification.model_copy(update=deliver_slack_message(notification.message))}


def deliver_slack_message(text: str) -> dict:
    """Post one message to the configured webhook (or simulate). Returns delivery_status / delivery_error."""
    if settings.slack_mode == "simulation":
        return {"delivery_status": DeliveryStatus.SIMULATED, "delivery_error": None}
    message, _ = sanitize_outbound(text, MAX_MESSAGE_CHARS)  # defence in depth: re-check right before sending
    try:
        response = post_with_retry(settings.slack_webhook_url.get_secret_value(), json={"text": message}, timeout=10)
        response.raise_for_status()
        update = {"delivery_status": DeliveryStatus.SENT, "delivery_error": None}
    except httpx.HTTPStatusError as exc:
        # Never echo the exception text: the webhook URL itself is a secret.
        update = {"delivery_status": DeliveryStatus.FAILED, "delivery_error": f"Slack returned HTTP {exc.response.status_code}"}
    except httpx.HTTPError as exc:
        update = {"delivery_status": DeliveryStatus.FAILED, "delivery_error": f"Slack request failed: {type(exc).__name__}"}
    logger.info("Slack delivery: %s", update["delivery_status"].value)
    return update
