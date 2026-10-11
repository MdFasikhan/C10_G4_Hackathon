# Multi-Agent DevOps Incident Analysis Suite (C10_G4 Hackathon)

Upload operational logs. A team of AI agents, orchestrated with LangGraph, then:
1. parses the logs,
2. classifies incidents,
3. retrieves the matching runbooks,
4. recommends remediation,
5. writes an incident-response cookbook,
6. drafts a Slack alert and JIRA tickets.

Nothing is sent to Slack or JIRA until a human approves the drafts.

## Architecture

```
Streamlit UI ──HTTP──► FastAPI ──► LangGraph analysis graph (background task)
 (app/)                 (api/)
   parse_logs (+ input guardrail) → classify_incidents → generate_remediation (RAG over ChromaDB runbooks)
        ├─► synthesize_cookbook ─────────┐
        ├─► prepare_slack_notification ──┼─► apply_guardrails ─► AWAITING_APPROVAL
        └─► prepare_jira_tickets ────────┘   (the three drafters run in parallel)

   human approves ──► LangGraph notification graph
        send_slack_notification → create_jira_tickets → COMPLETED
```

| Agent | File | Model | Fallback when the LLM is unavailable |
|---|---|---|---|
| Log reader | `agents/log_parser_agent.py` | none (deterministic) | n/a |
| Incident classifier | `agents/incident_classifier_agent.py` | `CLASSIFIER_MODEL` | one incident per service with errors, keyword-based category |
| Remediation (RAG) | `agents/remediation_agent.py` | `REASONING_MODEL` | steps from the best-matching runbook |
| Cookbook synthesizer | `agents/cookbook_agent.py` | `REASONING_MODEL` | merged, de-duplicated plan actions |
| Slack notification | `agents/slack_notification_agent.py` | `CLASSIFIER_MODEL` | template message |
| JIRA ticket | `agents/jira_ticket_agent.py` | `REASONING_MODEL` | template ticket per ERROR/CRITICAL incident |
| Guardrails | `agents/guardrail_agent.py` | none (deterministic) | n/a |

**Stack:** Streamlit, FastAPI, LangGraph, OpenRouter (OpenAI-compatible), ChromaDB with local
`all-MiniLM-L6-v2` embeddings, SQLite, and Slack/JIRA in simulation or live mode.

## Configuration: `.env` vs `.env.example`

Both files are needed, and they have different jobs:

| File | Contains | In git? |
|---|---|---|
| `.env` | **Your real secrets** (OpenRouter key, Slack webhook, JIRA token). Read by the app. | **No** (ignored) |
| `.env.example` | Every setting with safe defaults and **empty secrets**. Documents what can be configured. | Yes |

Never put real keys in `.env.example`. A new teammate runs `make env` (copies the example to `.env`)
and fills in their own keys.

## Setup

macOS / Linux:
```bash
make install        # or: ./run_local.sh
make env            # creates .env from .env.example (skip if .env exists)
```
Windows: `run_local.bat`.

Put your OpenRouter key in `.env`, then validate the models:
```bash
make check-models        # key valid, models exist, JSON mode supported, context, price (free)
make check-models-live   # plus one tiny live request per model
```

## Run

```bash
make api   # Terminal 1: http://localhost:8000/docs
make ui    # Terminal 2: http://localhost:8501
```
Or start both with Docker: `docker compose up --build`.

Pick a bundled sample from `data/sample_logs/` in the UI, or upload your own file
(`.log .txt .json .jsonl .csv`, max 20 MB). Supported formats: `key=value` text, JSON Lines,
JSON arrays and CSV.

## Dashboard UI (`frontend/`)

The dashboard is served by the API: start `make api` and open **http://localhost:8000/ui/**.

1. **New Analysis**: upload a log file and click **Start Analysis** (right under the upload box). While the agents
   work, a single status line shows what is happening ("Reading logs and classifying incidents…",
   "Searching runbooks for fixes…"), with finished steps listed underneath. When it's done you land on Detected Issues.
2. **Detected Issues**: every incident with its evidence, remediation and checklist.
3. **Approve & Notify**: each critical issue, grouped by log file, waits for one decision:
   - **Send to Slack & Jira** creates the Jira ticket and then posts the Slack alert, with a link to the ticket.
   - **Don't send** closes the issue without notifying anyone. You can still send it later ("Send anyway").

   Nothing reaches Slack or Jira without that click. The same decision is available on any issue
   in the details drawer, in the **Slack & Jira** tab.

Secrets stay on the server. Live or simulated delivery follows `SLACK_MODE` / `JIRA_MODE` in `.env`.
If the API is offline, the UI shows sample data and sends nothing (`make mock-ui` serves the UI on its own on port 8600).

Per-incident endpoints (each call is audited and passes the outbound guardrails):

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/analyses/{id}/incidents/{incident_id}/send` | Approve: create the Jira ticket (reused on retry), then post to Slack |
| POST | `/api/analyses/{id}/incidents/{incident_id}/dismiss` | Don't send: record the decision |
| POST | `/api/analyses/{id}/incidents/{incident_id}/slack` · `/jira` | Send only one of the two |
| GET | `/api/integrations` | Slack/Jira mode, channel and project (no secrets) |

The analysis moves to `COMPLETED` once every critical incident has been sent or dismissed.

## Slack and JIRA

`SLACK_MODE` / `JIRA_MODE` are `simulation` (nothing leaves the app, the default) or `live`. They set the mode the API
starts in; you can switch either one between **Simulation** and **Live** at runtime on the UI's **Settings** page
(`PUT /api/integrations`). That switch lasts until the API restarts. Live mode needs:
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=KAN
```
The app refuses to start in live mode if any of these are missing. Tickets go through JIRA REST
API v3, with descriptions in Atlassian Document Format. Up to `MAX_JIRA_TICKETS` (default 5)
tickets are created per analysis, most severe first.

## Guardrails

| Stage | Guardrail | Where |
|---|---|---|
| Upload | Allowed extensions, 20 MB cap, empty-file and **binary-file rejection** | `api/routes/analysis.py` |
| Upload | **Rate limit** per client (`RATE_LIMIT_UPLOADS_PER_MINUTE`) and optional **API key** (`API_KEY`) | `api/security.py` |
| Input | **Secret redaction** (passwords, tokens, Bearer headers, API keys, URL credentials) before storage or LLM | `tools/log_parser.py` |
| Input | **Prompt-injection detection**: instruction-like text in log lines is removed before any agent sees it; the original line is kept as evidence | `tools/guardrails.py` |
| LLM output | Pydantic **schema validation** with one repair retry; unknown categories/severities normalized | `tools/llm_client.py`, `models/` |
| LLM output | **Grounding**: incidents citing event IDs that are not in the log are dropped; plans cite only retrieved runbooks | agents |
| LLM output | **Destructive-action flags** (`DROP TABLE`, `rm -rf`, `kubectl delete namespace`, `FLUSHALL`, `--force`, …) shown as "⚠️ Review first" | `agents/guardrail_agent.py` |
| Outbound | Secrets re-redacted, **@channel / @here blocked**, length capped, checked again right before sending | `tools/guardrails.py` |
| Outbound | **Human approval** required; atomic status change prevents double-sending | `api/routes/analysis.py` |

Every guardrail hit is stored on the analysis (`guardrail_findings`) and shown in the UI.

## Evals

```bash
make eval         # ~$0.15 of LLM calls, ~1.5 min
make eval-judge   # plus LLM-as-judge scoring of every remediation plan
```
`evals/cases.json` is a labelled dataset: the three sample logs, a healthy log (must produce no
serious incidents) and a prompt-injection attack log. The runner executes the analysis graph only
(nothing is sent) and scores:

| Metric | Meaning | Threshold |
|---|---|---|
| category_recall | expected incident categories found | ≥ 0.7 |
| runbook_recall | expected runbooks actually used by the remediation agent | ≥ 0.8 |
| service_recall | expected affected services identified | ≥ 0.9 |
| severity_accuracy | highest severity within the expected bounds | 1.0 |
| grounding | incidents whose evidence all exists in the log | 1.0 |
| fallback_rate | runs where any agent fell back to rules | 0 |
| guardrail_pass_rate | attack case: injection detected, no mass mentions, no injected text in output | 1.0 |
| p95 latency / avg cost | performance budget | ≤ 40 s / ≤ $0.05 |
| judge_score | 1–5 for relevance, actionability, safety (optional) | ≥ 3.5 |

Reports are written to `reports/eval-<timestamp>.md` and `.json`. The script exits with code 1 if
any threshold fails, so it can gate a CI pipeline. Add a case to `evals/cases.json` whenever a real
incident is misclassified.

## Observability and performance

- **Agent trace:** every agent records its start time, latency, LLM calls, tokens, exact cost
  (from OpenRouter) and whether it fell back. See the **Trace & audit** view in the UI.
- **Run metrics:** wall time, LLM calls and cost are stored per analysis and shown in the header.
- **`GET /api/metrics`:** average and p95 latency, average and total cost, fallback and failure
  rates over recent runs.
- **Logs:** JSON lines in `data/logs/app.jsonl` (rotated), with a request ID on every API call
  (`X-Request-ID` header).
- **Probes:** `GET /health` (liveness) and `GET /ready` (database and runbook index ready).

Measured on `order_processing_incident.csv` (12 lines, 6–7 incidents): **~16 s and ~$0.06 per
analysis**, down from 34.5 s before the drafters were parallelized.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/analyses/upload` | Upload a log and start the analysis (202) |
| GET | `/api/analyses` | Recent analyses |
| GET | `/api/analyses/{id}` | Result, progress status, trace and guardrail findings |
| GET | `/api/analyses/{id}/audit` | Audit trail |
| POST | `/api/analyses/{id}/approve` | Send the Slack/JIRA drafts. Body: `{"approver": "...", "comment": "..."}` |
| POST | `/api/analyses/{id}/reject` | Discard the drafts |
| GET | `/api/metrics` | Performance and cost summary |
| GET | `/health`, `/ready` | Liveness and readiness probes |

## Tests

```bash
make test
```
42 tests cover the parser, schemas, prompts, guardrails, tracing, the full LangGraph workflow
(with a fake LLM and with a failing LLM), and the API (upload, approve, double-approve, reject,
validation, binary files, API key, rate limit, metrics). They need no API key or network and never
call Slack or JIRA.

## Known limitations

- **Single instance:** SQLite, in-process background tasks and an in-memory rate limiter. To
  scale out, use Postgres, a task queue (Celery, RQ or Arq) and Redis for rate limits.
- **Authentication** is a single shared API key. For a team deployment, put the app behind SSO and
  record the real user as the approver.
- **Runbook coverage** limits remediation quality: there are no runbooks yet for deadlocks,
  message brokers, third-party APIs or authentication. Add markdown files to `data/runbooks/`
  (indexed automatically at startup).
- **Data retention:** uploads and analyses are kept indefinitely; add a cleanup job for production.
