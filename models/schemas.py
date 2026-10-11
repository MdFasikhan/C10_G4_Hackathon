"""Domain models shared by the API, the agents and the UI."""
from __future__ import annotations

import operator
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Optional
from uuid import uuid4

from pydantic import BaseModel, BeforeValidator, Field

_SEVERITY_ORDER = ("INFO", "WARNING", "ERROR", "CRITICAL")
_SEVERITY_ALIASES = {
    "WARN": "WARNING", "MEDIUM": "WARNING", "MODERATE": "WARNING",
    "ERR": "ERROR", "HIGH": "ERROR", "MAJOR": "ERROR",
    "FATAL": "CRITICAL", "SEVERE": "CRITICAL", "BLOCKER": "CRITICAL", "SEV1": "CRITICAL", "P1": "CRITICAL",
    "LOW": "INFO", "MINOR": "INFO", "DEBUG": "INFO", "TRACE": "INFO",
}


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        """Numeric order for comparisons (string comparison would put WARNING above CRITICAL)."""
        return _SEVERITY_ORDER.index(self.value)


class IncidentCategory(str, Enum):
    APPLICATION_ERROR = "APPLICATION_ERROR"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"
    AUTHENTICATION = "AUTHENTICATION"
    CAPACITY = "CAPACITY"
    SECURITY = "SECURITY"
    THIRD_PARTY = "THIRD_PARTY"
    UNKNOWN = "UNKNOWN"


class AnalysisStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    LOGS_PARSED = "LOGS_PARSED"
    INCIDENTS_CLASSIFIED = "INCIDENTS_CLASSIFIED"
    REMEDIATION_GENERATED = "REMEDIATION_GENERATED"
    COOKBOOK_GENERATED = "COOKBOOK_GENERATED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED_NO_INCIDENTS = "COMPLETED_NO_INCIDENTS"
    NOTIFYING = "NOTIFYING"
    COMPLETED = "COMPLETED"
    NOTIFICATION_FAILED = "NOTIFICATION_FAILED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


IN_PROGRESS_STATUSES = {
    AnalysisStatus.PENDING, AnalysisStatus.RUNNING, AnalysisStatus.LOGS_PARSED,
    AnalysisStatus.INCIDENTS_CLASSIFIED, AnalysisStatus.REMEDIATION_GENERATED, AnalysisStatus.COOKBOOK_GENERATED,
}


class DeliveryStatus(str, Enum):
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    SIMULATED = "SIMULATED"
    SENT = "SENT"
    CREATED = "CREATED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


def normalize_severity(value: Any) -> Any:
    if isinstance(value, str):
        upper = value.strip().upper()
        return _SEVERITY_ALIASES.get(upper, upper if upper in _SEVERITY_ORDER else "WARNING")
    return value


def normalize_category(value: Any) -> Any:
    if isinstance(value, str):
        upper = value.strip().upper().replace(" ", "_").replace("-", "_")
        return upper if upper in IncidentCategory.__members__ else "UNKNOWN"
    return value


SeverityField = Annotated[Severity, BeforeValidator(normalize_severity)]
CategoryField = Annotated[IncidentCategory, BeforeValidator(normalize_category)]


class ParsedLogEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    line_number: int = 0
    timestamp: Optional[datetime] = None
    service: str = "unknown"
    level: str = "INFO"
    message: str
    error_code: Optional[str] = None
    trace_id: Optional[str] = None
    host: Optional[str] = None
    raw_line: str


class Incident(BaseModel):
    incident_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    title: str
    category: CategoryField = IncidentCategory.UNKNOWN
    severity: SeverityField = Severity.INFO
    affected_services: list[str] = Field(default_factory=list)
    summary: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class RemediationPlan(BaseModel):
    incident_id: str
    root_cause_hypothesis: str
    immediate_actions: list[str] = Field(default_factory=list)
    investigation_steps: list[str] = Field(default_factory=list)
    preventive_actions: list[str] = Field(default_factory=list)
    rollback_plan: str = "No rollback required."
    risk_level: SeverityField = Severity.INFO
    confidence: float = Field(default=0.5, ge=0, le=1)
    sources: list[str] = Field(default_factory=list)
    flagged_actions: list[str] = Field(default_factory=list)  # set by guardrails: needs manual review


class Cookbook(BaseModel):
    title: str
    executive_summary: str
    timeline: list[str] = Field(default_factory=list)
    immediate_actions: list[str] = Field(default_factory=list)
    investigation_steps: list[str] = Field(default_factory=list)
    preventive_actions: list[str] = Field(default_factory=list)
    communication_template: str = ""


class SlackNotification(BaseModel):
    channel: str = "#incident-response"
    message: str
    incident_ids: list[str] = Field(default_factory=list)
    delivery_status: DeliveryStatus = DeliveryStatus.READY_FOR_APPROVAL
    delivery_error: Optional[str] = None


class JiraTicket(BaseModel):
    incident_id: str
    project_key: str = "OPS"
    summary: str
    description: str
    priority: str = "High"
    labels: list[str] = Field(default_factory=list)
    issue_key: Optional[str] = None
    issue_url: Optional[str] = None
    delivery_status: DeliveryStatus = DeliveryStatus.READY_FOR_APPROVAL
    delivery_error: Optional[str] = None


class GuardrailFinding(BaseModel):
    stage: str  # input | output | outbound
    rule: str
    severity: str = "WARNING"
    detail: str


class AgentRun(BaseModel):
    """One agent step: timing, LLM usage and whether it had to fall back to rules."""
    agent: str
    started_at: float = 0.0  # unix time, lets the UI draw parallel agents on a timeline
    latency_ms: int
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    models: list[str] = Field(default_factory=list)
    fallback: bool = False


class RunMetrics(BaseModel):
    wall_time_ms: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    fallbacks: int = 0


class AnalysisResult(BaseModel):
    """What the API stores and returns."""
    analysis_id: str
    status: AnalysisStatus
    upload_filename: str = ""
    parsed_events: list[ParsedLogEvent] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    remediation_plans: list[RemediationPlan] = Field(default_factory=list)
    cookbook: Optional[Cookbook] = None
    slack_notification: Optional[SlackNotification] = None
    jira_tickets: list[JiraTicket] = Field(default_factory=list)
    incident_slack: dict[str, DeliveryStatus] = Field(default_factory=dict)  # per-incident Slack sends from the UI
    dismissed_incidents: dict[str, str] = Field(default_factory=dict)  # incident_id -> who chose "Don't send"
    guardrail_findings: list[GuardrailFinding] = Field(default_factory=list)
    agent_runs: list[AgentRun] = Field(default_factory=list)
    metrics: Optional[RunMetrics] = None
    error: Optional[str] = None


class AnalysisState(AnalysisResult):
    """LangGraph workflow state: the stored result plus working fields.

    `agent_runs` and `guardrail_findings` use an append reducer, so agents running
    in parallel can each add entries without overwriting one another.
    """
    agent_runs: Annotated[list[AgentRun], operator.add] = Field(default_factory=list)
    guardrail_findings: Annotated[list[GuardrailFinding], operator.add] = Field(default_factory=list)
    upload_path: str = ""
    retrieved_context: list[dict[str, Any]] = Field(default_factory=list)

    def to_result(self) -> AnalysisResult:
        return AnalysisResult.model_validate(self.model_dump(include=set(AnalysisResult.model_fields)))

    @classmethod
    def from_result(cls, result: AnalysisResult, upload_path: str = "") -> "AnalysisState":
        return cls.model_validate({**result.model_dump(), "upload_path": upload_path})


class ApprovalRequest(BaseModel):
    approver: str = Field(default="anonymous", min_length=1, max_length=100)
    comment: Optional[str] = Field(default=None, max_length=1000)
