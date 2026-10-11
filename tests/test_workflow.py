from models.schemas import AnalysisState, AnalysisStatus, DeliveryStatus, Severity
from orchestration.graph import run_analysis, run_notifications
from tests.conftest import SAMPLE_LOGS


def _run(path) -> AnalysisState:
    return run_analysis(AnalysisState(analysis_id="t1", upload_path=str(path), status=AnalysisStatus.RUNNING))


def test_full_workflow_with_llm(fake_llm):
    progress = []
    state = run_analysis(
        AnalysisState(analysis_id="t1", upload_path=str(SAMPLE_LOGS / "payment_api_incident.log"), status=AnalysisStatus.RUNNING),
        on_update=lambda s: progress.append(s.status),
    )
    assert state.status == AnalysisStatus.AWAITING_APPROVAL
    assert AnalysisStatus.LOGS_PARSED in progress and AnalysisStatus.COOKBOOK_GENERATED in progress

    # Incidents are sorted most severe first; hallucinated evidence IDs are removed, bracketed ones repaired.
    assert [i.severity for i in state.incidents] == [Severity.CRITICAL, Severity.WARNING]
    valid_ids = {e.event_id for e in state.parsed_events}
    assert all(eid in valid_ids for i in state.incidents for eid in i.evidence_event_ids)
    assert len(state.incidents[0].evidence_event_ids) == 6

    plan = state.remediation_plans[0]
    assert plan.incident_id == state.incidents[0].incident_id
    assert plan.sources == ["database-timeout.md"]  # an invented source is not cited
    assert plan.risk_level == Severity.ERROR

    # Drafts exist before approval, but nothing has been delivered.
    assert state.slack_notification.delivery_status == DeliveryStatus.READY_FOR_APPROVAL
    assert len(state.jira_tickets) == 1  # only the CRITICAL incident, not the WARNING one
    assert state.jira_tickets[0].priority == "Highest"
    assert "db-timeout" in state.jira_tickets[0].labels


def test_notifications_after_approval_are_simulated(fake_llm):
    state = run_notifications(_run(SAMPLE_LOGS / "payment_api_incident.log"))
    assert state.status == AnalysisStatus.COMPLETED
    assert state.slack_notification.delivery_status == DeliveryStatus.SIMULATED
    assert state.jira_tickets[0].issue_key == "OPS-SIM-1"


def test_workflow_falls_back_when_llm_fails(failing_llm):
    state = _run(SAMPLE_LOGS / "kubernetes_startup.log")
    assert state.status == AnalysisStatus.AWAITING_APPROVAL
    assert state.incidents and state.incidents[0].affected_services == ["checkout-service"]
    assert state.incidents[0].category.value == "NETWORK"
    assert state.remediation_plans[0].immediate_actions == ["Terminate blocking queries.", "Enable circuit breakers."]
    assert state.cookbook is not None and state.slack_notification is not None


def test_clean_log_has_no_incidents(fake_llm, tmp_path):
    path = tmp_path / "ok.log"
    path.write_text('service=api level=INFO message="all good"\n')
    state = _run(path)
    assert state.status == AnalysisStatus.COMPLETED_NO_INCIDENTS
    assert state.cookbook is None and state.slack_notification is None


def test_unreadable_file_stops_after_parsing(fake_llm, tmp_path):
    state = _run(tmp_path / "missing.log")
    assert state.status == AnalysisStatus.FAILED
    assert "Log parsing failed" in state.error
    assert fake_llm.calls == []


def test_severity_is_never_below_the_evidence(monkeypatch, fake_llm):
    from models.response_schemas import ClassifierResponse
    original = fake_llm.structured_complete

    def downgrade(prompt, system_prompt, model, schema, temperature=None):
        result = original(prompt, system_prompt, model, schema, temperature)
        if schema is ClassifierResponse:
            for incident in result.incidents:
                incident.severity = Severity.INFO  # the LLM (or injected log text) tries to downplay everything
        return result
    monkeypatch.setattr(fake_llm, "structured_complete", downgrade)
    state = _run(SAMPLE_LOGS / "payment_api_incident.log")
    assert [i.severity for i in state.incidents] == [Severity.CRITICAL, Severity.WARNING]
