"""Test setup: isolated temp storage, no API key, a fake LLM and a fake vector store.

Environment variables are set before any project module is imported, and they
override values in .env, so tests never touch real data or call OpenRouter.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="incident_suite_tests_"))
os.environ.update({
    "OPENROUTER_API_KEY": "",
    "SLACK_MODE": "simulation",
    "JIRA_MODE": "simulation",
    "JIRA_PROJECT_KEY": "OPS",
    "API_KEY": "",
    "RATE_LIMIT_UPLOADS_PER_MINUTE": "1000",
    "DATABASE_PATH": str(_TMP / "test.db"),
    "CHROMA_PERSIST_DIR": str(_TMP / "chroma"),
    "UPLOAD_DIR": str(_TMP / "uploads"),
    "APP_LOG_FILE": str(_TMP / "logs" / "app.jsonl"),
})

import pytest  # noqa: E402

from models.response_schemas import ClassifierResponse, JiraResponse, RemediationResponse, SlackResponse  # noqa: E402
from models.schemas import Cookbook  # noqa: E402
from tools import llm_client, vector_store  # noqa: E402
from tools.llm_client import LLMError  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_LOGS = PROJECT_ROOT / "data" / "sample_logs"


class FakeLLM:
    """Returns schema-valid canned answers; the classifier cites real event IDs from the prompt."""

    def __init__(self):
        self.calls: list[str] = []

    def structured_complete(self, prompt, system_prompt, model, schema, temperature=None):
        self.calls.append(schema.__name__)
        if schema is ClassifierResponse:
            error_ids = re.findall(r"^\[(\w+)\][^\n]*\| (?:ERROR|CRITICAL) \|", prompt, re.MULTILINE)
            warn_ids = re.findall(r"^\[(\w+)\][^\n]*\| WARNING \|", prompt, re.MULTILINE)
            incidents = []
            if warn_ids:
                incidents.append({"title": "Degraded dependency", "category": "network", "severity": "warn",
                                  "affected_services": ["svc"], "summary": "Warnings seen", "evidence_event_ids": warn_ids, "confidence": 0.7})
            if error_ids:
                incidents.append({"title": "Service outage", "category": "DATABASE", "severity": "CRITICAL",
                                  "affected_services": ["svc"], "summary": "Errors seen",
                                  "evidence_event_ids": [f"[{error_ids[0]}]", "made-up-id", *error_ids[1:]], "confidence": 0.9})
            return ClassifierResponse.model_validate({"incidents": incidents})
        if schema is RemediationResponse:
            return RemediationResponse(root_cause_hypothesis="Pool exhausted", immediate_actions=["Kill blocking queries"],
                                       sources=["database-timeout.md", "invented.md"], risk_level="HIGH")
        if schema is Cookbook:
            return Cookbook(title="Cookbook", executive_summary="Summary", immediate_actions=["Do X"])
        if schema is SlackResponse:
            return SlackResponse(message="*CRITICAL* Service outage")
        if schema is JiraResponse:
            return JiraResponse(summary="Service outage", description="Summary\nErrors\n\nActions\n- Fix", priority="highest", labels=["DB Timeout"])
        raise AssertionError(f"unexpected schema {schema}")


class FailingLLM:
    def structured_complete(self, *args, **kwargs):
        raise LLMError("LLM unavailable")


class FakeVectorStore:
    def search(self, query, top_k=None):
        return [{"text": "# Database Timeout Runbook\n## Immediate Mitigation\n1. Terminate blocking queries.\n2. Enable circuit breakers.",
                 "source": "database-timeout.md", "runbook": "database-timeout", "chunk": 0, "distance": 0.2}]

    def sync_runbooks(self, runbook_dir):
        return 0


@pytest.fixture(autouse=True)
def fake_vector_store(monkeypatch):
    monkeypatch.setattr(vector_store, "_store", FakeVectorStore())


@pytest.fixture
def fake_llm(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(llm_client, "_client", llm)
    return llm


@pytest.fixture
def failing_llm(monkeypatch):
    monkeypatch.setattr(llm_client, "_client", FailingLLM())
