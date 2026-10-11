from models.schemas import AnalysisResult, AnalysisState, Incident, Severity


def test_analysis_result_defaults():
    result = AnalysisResult(analysis_id="abc123", status="PENDING")
    assert result.analysis_id == "abc123"
    assert result.parsed_events == []
    assert result.jira_tickets == []


def test_severity_rank_orders_by_impact_not_alphabet():
    assert max([Severity.WARNING, Severity.CRITICAL, Severity.INFO], key=lambda s: s.rank) == Severity.CRITICAL
    assert Severity.ERROR.rank > Severity.WARNING.rank


def test_llm_style_values_are_normalized():
    incident = Incident(title="t", summary="s", severity="high", category="network")
    assert incident.severity == Severity.ERROR
    assert incident.category.value == "NETWORK"
    assert Incident(title="t", summary="s", category="made-up").category.value == "UNKNOWN"


def test_state_round_trip_drops_working_fields():
    state = AnalysisState(analysis_id="a", status="RUNNING", upload_path="/tmp/x.log", retrieved_context=[{"text": "x"}])
    result = state.to_result()
    assert not hasattr(result, "upload_path")
    assert AnalysisState.from_result(result).analysis_id == "a"
