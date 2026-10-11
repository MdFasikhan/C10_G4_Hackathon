from pydantic import SecretStr
from fastapi.testclient import TestClient

from api.main import app
from tests.conftest import SAMPLE_LOGS

# Not used as a context manager, so the startup hook (which loads the embedding model) does not run.
client = TestClient(app)


def _upload(name="payment_api_incident.log", content=None):
    data = content if content is not None else (SAMPLE_LOGS / name).read_bytes()
    return client.post("/api/analyses/upload", files={"file": (name, data, "text/plain")})


def test_health():
    body = client.get("/health").json()
    assert body["status"] == "healthy" and body["slack_mode"] == "simulation"


def test_upload_analyze_approve_flow(fake_llm):
    response = _upload()
    assert response.status_code == 202
    analysis_id = response.json()["analysis_id"]

    # TestClient runs background tasks before returning, so the analysis is finished.
    result = client.get(f"/api/analyses/{analysis_id}").json()
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["upload_filename"] == "payment_api_incident.log"
    assert result["slack_notification"]["delivery_status"] == "READY_FOR_APPROVAL"

    approved = client.post(f"/api/analyses/{analysis_id}/approve", json={"approver": "alice"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "COMPLETED"
    assert approved.json()["slack_notification"]["delivery_status"] == "SIMULATED"

    # A second approval is refused, so notifications cannot be sent twice.
    assert client.post(f"/api/analyses/{analysis_id}/approve").status_code == 409

    actions = [a["action"] for a in client.get(f"/api/analyses/{analysis_id}/audit").json()]
    assert actions == ["UPLOAD", "ANALYSIS_COMPLETED", "NOTIFICATIONS_APPROVED", "NOTIFICATIONS_DELIVERED"]
    assert any(a["analysis_id"] == analysis_id for a in client.get("/api/analyses").json())


def test_reject_discards_drafts(fake_llm):
    analysis_id = _upload().json()["analysis_id"]
    rejected = client.post(f"/api/analyses/{analysis_id}/reject", json={"approver": "bob", "comment": "false alarm"}).json()
    assert rejected["status"] == "REJECTED"
    assert rejected["slack_notification"]["delivery_status"] == "REJECTED"
    assert client.post(f"/api/analyses/{analysis_id}/approve").status_code == 409


def test_upload_validation():
    assert client.post("/api/analyses/upload", files={"file": ("x.exe", b"abc", "application/octet-stream")}).status_code == 400
    assert _upload("empty.log", content=b"").status_code == 400


def test_unknown_analysis_is_404():
    assert client.get("/api/analyses/does-not-exist").status_code == 404
    assert client.post("/api/analyses/does-not-exist/approve").status_code == 404


def test_binary_upload_is_rejected():
    response = client.post("/api/analyses/upload", files={"file": ("x.log", b"\x00\x01\x02binary", "text/plain")})
    assert response.status_code == 415


def test_api_key_is_enforced_when_configured(monkeypatch):
    from pydantic import SecretStr
    from tools.config import settings
    monkeypatch.setattr(settings, "api_key", SecretStr("s3cret"))
    assert client.get("/api/analyses").status_code == 401
    assert client.get("/api/analyses", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/analyses", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # liveness stays open for orchestrators


def test_upload_rate_limit(monkeypatch):
    from api.security import upload_rate_limit
    monkeypatch.setattr(upload_rate_limit, "per_minute", 1)
    upload_rate_limit._hits.clear()
    bad = ("x.exe", b"a", "text/plain")  # rejected after the limiter, so no analysis is started
    assert client.post("/api/analyses/upload", files={"file": bad}).status_code == 400
    limited = client.post("/api/analyses/upload", files={"file": bad})
    assert limited.status_code == 429 and "Retry-After" in limited.headers
    upload_rate_limit._hits.clear()


def test_request_id_and_metrics(fake_llm):
    _upload()
    response = client.get("/api/metrics")
    assert response.headers.get("X-Request-ID")
    body = response.json()
    assert body["runs"] >= 1 and body["avg_seconds"] >= 0 and "p95_seconds" in body


def test_per_incident_slack_and_jira_actions(fake_llm):
    analysis_id = _upload().json()["analysis_id"]
    result = client.get(f"/api/analyses/{analysis_id}").json()
    incident_id = result["incidents"][0]["incident_id"]

    integrations = client.get("/api/integrations").json()
    assert integrations["slack"]["mode"] == "simulation" and integrations["jira"]["mode"] == "simulation"

    slack = client.post(f"/api/analyses/{analysis_id}/incidents/{incident_id}/slack", json={"message": "<!channel> token=abc123 outage"})
    assert slack.status_code == 200 and slack.json()["delivery_status"] == "SIMULATED"
    assert set(slack.json()["guardrails"]) == {"secret_redacted", "mass_mention_blocked"}

    jira = client.post(f"/api/analyses/{analysis_id}/incidents/{incident_id}/jira", json={"summary": "Pool exhausted", "labels": ["DB Timeout"]})
    assert jira.status_code == 200
    body = jira.json()
    assert body["issue_key"].startswith("OPS-SIM-") and "db-timeout" in body["labels"]
    # A second ticket for the same incident is refused.
    assert client.post(f"/api/analyses/{analysis_id}/incidents/{incident_id}/jira").status_code == 409

    stored = client.get(f"/api/analyses/{analysis_id}").json()
    assert stored["incident_slack"][incident_id] == "SIMULATED"
    actions = [a["action"] for a in client.get(f"/api/analyses/{analysis_id}/audit").json()]
    assert "SLACK_SENT_FOR_INCIDENT" in actions and "JIRA_CREATED_FOR_INCIDENT" in actions

    summary = client.post(f"/api/analyses/{analysis_id}/slack")
    assert summary.status_code == 200 and summary.json()["delivery_status"] == "SIMULATED"
    assert client.post(f"/api/analyses/{analysis_id}/incidents/nope/slack").status_code == 404


def test_send_or_dont_send_per_incident(fake_llm):
    analysis_id = _upload().json()["analysis_id"]
    incidents = client.get(f"/api/analyses/{analysis_id}").json()["incidents"]
    first = incidents[0]["incident_id"]

    sent = client.post(f"/api/analyses/{analysis_id}/incidents/{first}/send")
    assert sent.status_code == 200
    body = sent.json()
    assert body["jira"]["issue_key"].startswith("OPS-SIM-") and body["slack"]["delivery_status"] == "SIMULATED"
    # Clicking Send again reuses the ticket and does not post a second Slack message.
    again = client.post(f"/api/analyses/{analysis_id}/incidents/{first}/send").json()
    assert again["jira"]["issue_key"] == body["jira"]["issue_key"] and again["slack"].get("already_sent")

    for inc in incidents[1:]:
        assert client.post(f"/api/analyses/{analysis_id}/incidents/{inc['incident_id']}/dismiss").json()["dismissed"]
    stored = client.get(f"/api/analyses/{analysis_id}").json()
    assert set(stored["dismissed_incidents"]) == {i["incident_id"] for i in incidents[1:]}
    if any(i["severity"] == "CRITICAL" for i in incidents):
        assert stored["status"] == "COMPLETED"  # every critical incident has a decision
    actions = [a["action"] for a in client.get(f"/api/analyses/{analysis_id}/audit").json()]
    assert "JIRA_CREATED_FOR_INCIDENT" in actions and "SLACK_SENT_FOR_INCIDENT" in actions
    assert client.post(f"/api/analyses/{analysis_id}/incidents/nope/dismiss").status_code == 404


def test_ui_is_served_from_the_api():
    assert client.get("/ui/").status_code == 200
    assert "Incident Analysis" in client.get("/ui/").text
    assert client.get("/", follow_redirects=False).status_code in (302, 307)


def test_integration_modes_can_be_switched_at_runtime(monkeypatch):
    from tools.config import settings
    assert client.get("/api/integrations").json()["slack"]["mode"] == "simulation"
    # No credentials in the test environment: going live is refused, nothing changes.
    monkeypatch.setattr(settings, "slack_webhook_url", SecretStr(""))
    refused = client.put("/api/integrations", json={"slack_mode": "live"})
    assert refused.status_code == 400 and settings.slack_mode == "simulation"
    # With credentials it switches, and back again.
    monkeypatch.setattr(settings, "slack_webhook_url", SecretStr("https://hooks.slack.com/services/T/B/x"))
    monkeypatch.setattr(settings, "slack_mode", "simulation")
    assert client.put("/api/integrations", json={"slack_mode": "live"}).json()["slack"]["mode"] == "live"
    assert client.put("/api/integrations", json={"slack_mode": "simulation"}).json()["slack"]["mode"] == "simulation"
    assert client.put("/api/integrations", json={"jira_mode": "bogus"}).status_code == 422
