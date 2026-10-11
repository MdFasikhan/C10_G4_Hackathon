"""Schemas the LLM must return. Validated with Pydantic before anything is used."""
from __future__ import annotations

from pydantic import BaseModel, Field

from models.schemas import CategoryField, IncidentCategory, Severity, SeverityField


class ClassifierIncident(BaseModel):
    title: str
    category: CategoryField = IncidentCategory.UNKNOWN
    severity: SeverityField = Severity.INFO
    affected_services: list[str] = Field(default_factory=list)
    summary: str
    evidence_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)


class ClassifierResponse(BaseModel):
    incidents: list[ClassifierIncident] = Field(default_factory=list)


class RemediationResponse(BaseModel):
    root_cause_hypothesis: str
    immediate_actions: list[str] = Field(default_factory=list)
    investigation_steps: list[str] = Field(default_factory=list)
    preventive_actions: list[str] = Field(default_factory=list)
    rollback_plan: str = "No rollback required."
    risk_level: SeverityField = Severity.INFO
    confidence: float = Field(default=0.5, ge=0, le=1)
    sources: list[str] = Field(default_factory=list)


class SlackResponse(BaseModel):
    message: str


class JiraResponse(BaseModel):
    summary: str
    description: str
    priority: str = "High"
    labels: list[str] = Field(default_factory=list)
