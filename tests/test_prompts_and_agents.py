from agents.jira_ticket_agent import _clean_labels, to_adf
from agents.slack_notification_agent import prepare_slack_notification
from models.schemas import AnalysisState, Incident
from prompts import prompt_templates as p


def test_every_prompt_template_formats_without_errors():
    """Regression: literal JSON braces must be escaped, or str.format() raises and the LLM is never called."""
    p.CLASSIFIER_USER_PROMPT.format(events="x")
    p.REMEDIATION_USER_PROMPT.format(incident="x", events="x", context="x")
    p.COOKBOOK_USER_PROMPT.format(incidents="x", plans="x")
    p.SLACK_USER_PROMPT.format(incidents="x", actions="x")
    filled = p.JIRA_USER_PROMPT.format(incident="x", remediation="x")
    assert '"summary"' in filled and "{{" not in filled


def test_slack_leads_with_most_severe_incident(failing_llm):
    state = AnalysisState(analysis_id="a", status="RUNNING", incidents=[
        Incident(title="Minor warning", summary="w", severity="WARNING"),
        Incident(title="Total outage", summary="c", severity="CRITICAL"),
        Incident(title="Some errors", summary="e", severity="ERROR"),
    ])
    message = prepare_slack_notification(state)["slack_notification"].message
    assert message.startswith(":rotating_light: *CRITICAL: Total outage*")


def test_jira_description_is_atlassian_document_format():
    adf = to_adf("Summary\nline two\n\nActions\n- Fix")
    assert adf["type"] == "doc" and adf["version"] == 1
    assert len(adf["content"]) == 2
    assert adf["content"][0]["content"][1] == {"type": "hardBreak"}


def test_jira_labels_have_no_spaces_or_duplicates():
    assert _clean_labels(["DB Timeout", "incident", "Incident ", ""]) == ["db-timeout", "incident"]
