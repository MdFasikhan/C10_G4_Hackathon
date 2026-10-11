from __future__ import annotations

from models.schemas import AnalysisState
from tools.guardrails import destructive_reason, finding, sanitize_outbound

SLACK_MAX_CHARS = 3000
JIRA_MAX_CHARS = 30_000


def apply_guardrails(state: AnalysisState) -> dict:
    """Output guardrail agent: runs after all drafts exist and before a human sees them.

    - Flags destructive remediation steps (they stay visible, marked for manual review).
    - Redacts secrets and blocks @channel/@here in the Slack and JIRA drafts.
    """
    findings = []

    plans = []
    for plan in state.remediation_plans:
        flagged = []
        for action in plan.immediate_actions + plan.investigation_steps + [plan.rollback_plan]:
            if reason := destructive_reason(action):
                flagged.append(action)
                findings.append(finding("output", "destructive_action", f"{reason}: {action}", "ERROR"))
        plans.append(plan.model_copy(update={"flagged_actions": flagged}))

    slack = state.slack_notification
    if slack:
        message, rules = sanitize_outbound(slack.message, SLACK_MAX_CHARS)
        findings += [finding("outbound", rule, "Slack draft") for rule in rules]
        slack = slack.model_copy(update={"message": message})

    tickets = []
    for ticket in state.jira_tickets:
        summary, summary_rules = sanitize_outbound(ticket.summary, 255)
        description, rules = sanitize_outbound(ticket.description, JIRA_MAX_CHARS)
        findings += [finding("outbound", rule, f"JIRA draft: {summary[:80]}") for rule in sorted(set(summary_rules + rules))]
        tickets.append(ticket.model_copy(update={"summary": summary, "description": description}))

    return {"remediation_plans": plans, "slack_notification": slack, "jira_tickets": tickets, "guardrail_findings": findings}
