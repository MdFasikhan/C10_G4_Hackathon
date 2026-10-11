"""LangGraph orchestration.

Analysis graph (runs automatically after upload):

    parse_logs ─┬─(FAILED)───────────────────────────────────────────────────────────► END
                └─► classify_incidents ─┬─(no incidents)──────────────────────────────► END
                                        └─► generate_remediation ─┬─► synthesize_cookbook ────────┐
                                                                  ├─► prepare_slack_notification ─┼─► apply_guardrails ─► await_approval ─► END
                                                                  └─► prepare_jira_tickets ───────┘
                                                                     (these three run in parallel)

Notification graph (runs only after a human approves the drafts):

    send_slack_notification ─► create_jira_tickets ─► finalize_notifications ─► END

Nodes take the typed AnalysisState and return only the fields they change. Every node is
wrapped with @traced, which appends an AgentRun (latency, LLM calls, tokens, cost, fallback).
"""
from __future__ import annotations

import time
from typing import Callable

from langgraph.graph import END, StateGraph

from agents.cookbook_agent import synthesize_cookbook
from agents.guardrail_agent import apply_guardrails
from agents.incident_classifier_agent import classify_incidents
from agents.jira_ticket_agent import create_jira_tickets, prepare_jira_tickets
from agents.log_parser_agent import parse_logs
from agents.remediation_agent import generate_remediation
from agents.slack_notification_agent import prepare_slack_notification, send_slack_notification
from models.schemas import AnalysisState, AnalysisStatus, DeliveryStatus
from tools.logging_config import logger
from tools.tracing import summarize, traced


def await_approval(state: AnalysisState) -> dict:
    return {"status": AnalysisStatus.AWAITING_APPROVAL}


def finalize_notifications(state: AnalysisState) -> dict:
    statuses = [t.delivery_status for t in state.jira_tickets]
    if state.slack_notification:
        statuses.append(state.slack_notification.delivery_status)
    failed = DeliveryStatus.FAILED in statuses
    return {"status": AnalysisStatus.NOTIFICATION_FAILED if failed else AnalysisStatus.COMPLETED}


def _after_parse(state: AnalysisState) -> str:
    return END if state.status == AnalysisStatus.FAILED else "classify_incidents"


def _after_classify(state: AnalysisState) -> str:
    return "generate_remediation" if state.incidents else END


DRAFTERS = ["synthesize_cookbook", "prepare_slack_notification", "prepare_jira_tickets"]


def build_analysis_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("parse_logs", traced("Log Reader")(parse_logs))
    graph.add_node("classify_incidents", traced("Classifier")(classify_incidents))
    graph.add_node("generate_remediation", traced("Remediation")(generate_remediation))
    graph.add_node("synthesize_cookbook", traced("Cookbook")(synthesize_cookbook))
    graph.add_node("prepare_slack_notification", traced("Slack draft")(prepare_slack_notification))
    graph.add_node("prepare_jira_tickets", traced("JIRA draft")(prepare_jira_tickets))
    graph.add_node("apply_guardrails", traced("Guardrails")(apply_guardrails))
    graph.add_node("await_approval", await_approval)
    graph.set_entry_point("parse_logs")
    graph.add_conditional_edges("parse_logs", _after_parse, ["classify_incidents", END])
    graph.add_conditional_edges("classify_incidents", _after_classify, ["generate_remediation", END])
    for drafter in DRAFTERS:  # fan out: the three drafters are independent
        graph.add_edge("generate_remediation", drafter)
    graph.add_edge(DRAFTERS, "apply_guardrails")  # fan in: waits for all three
    graph.add_edge("apply_guardrails", "await_approval")
    graph.add_edge("await_approval", END)
    return graph.compile()


def build_notification_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("send_slack_notification", traced("Slack send")(send_slack_notification))
    graph.add_node("create_jira_tickets", traced("JIRA create")(create_jira_tickets))
    graph.add_node("finalize_notifications", finalize_notifications)
    graph.set_entry_point("send_slack_notification")
    graph.add_edge("send_slack_notification", "create_jira_tickets")
    graph.add_edge("create_jira_tickets", "finalize_notifications")
    graph.add_edge("finalize_notifications", END)
    return graph.compile()


analysis_graph = build_analysis_graph()
notification_graph = build_notification_graph()


def run_analysis(state: AnalysisState, on_update: Callable[[AnalysisState], None] | None = None) -> AnalysisState:
    """Run the analysis graph; `on_update` is called after every step (used to persist progress)."""
    logger.info("Starting analysis workflow: %s", state.analysis_id)
    started = time.perf_counter()
    current = state
    for values in analysis_graph.stream(state, stream_mode="values"):
        current = AnalysisState.model_validate(values)
        if on_update:
            on_update(current)
    current.metrics = summarize(current.agent_runs, round((time.perf_counter() - started) * 1000))
    m = current.metrics
    logger.info("Analysis %s done in %dms: %d LLM calls, %d tokens, $%.4f, %d fallbacks",
                state.analysis_id, m.wall_time_ms, m.llm_calls, m.prompt_tokens + m.completion_tokens, m.cost_usd, m.fallbacks)
    return current


def run_notifications(state: AnalysisState) -> AnalysisState:
    logger.info("Starting notification workflow: %s", state.analysis_id)
    analysis_wall = state.metrics.wall_time_ms if state.metrics else 0
    result = AnalysisState.model_validate(notification_graph.invoke(state))
    # Keep the analysis wall time; add the notification agents' usage to the totals.
    result.metrics = summarize(result.agent_runs, analysis_wall)
    return result
