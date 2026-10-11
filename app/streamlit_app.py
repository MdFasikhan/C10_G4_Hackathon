"""Streamlit UI: upload a log → watch the agents work → explore incidents → approve Slack/JIRA."""
from __future__ import annotations

import html
import os
import re
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import altair as alt
import httpx
import streamlit as st
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_HEADERS = {"X-API-Key": os.environ["API_KEY"]} if os.getenv("API_KEY") else {}
SAMPLE_DIR = PROJECT_ROOT / os.getenv("SAMPLE_LOG_DIR", "data/sample_logs")

# Workflow status → what the agents are doing while that status is current.
STEPS = {
    "PENDING": "Queued",
    "RUNNING": "Reading logs",
    "LOGS_PARSED": "Classifying incidents",
    "INCIDENTS_CLASSIFIED": "Searching runbooks and planning fixes",
    "REMEDIATION_GENERATED": "Writing the cookbook and drafting Slack and JIRA",
    "COOKBOOK_GENERATED": "Running guardrail checks",
}
SEVERITIES = ["CRITICAL", "ERROR", "WARNING", "INFO"]
SEV_COLOR = {"CRITICAL": "#e5484d", "ERROR": "#f76b15", "WARNING": "#e2a336", "INFO": "#3e63dd"}
SEV_DOT = {"CRITICAL": "🔴", "ERROR": "🟠", "WARNING": "🟡", "INFO": "🔵"}
VIEWS = ["Overview", "Cookbook", "Slack & JIRA", "Trace & audit"]
AGENTS = [
    ("Log Reader", "Parses logs and extracts fields"),
    ("Classifier", "Groups events into incidents"),
    ("Remediation", "Maps fixes from runbooks"),
    ("Cookbook", "Builds action checklists"),
    ("Slack + JIRA", "Drafts alerts and tickets"),
]

st.set_page_config(page_title="DevOps Incident Analysis", page_icon="🛠️", layout="wide")
st.markdown(
    """
<style>
.block-container {padding-top: 2.2rem;}
.tiles {display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin:4px 0 18px;}
@media (max-width: 900px) {.tiles {grid-template-columns:repeat(2,minmax(0,1fr));}}
.tile {border:1px solid rgba(128,128,128,.22); border-radius:14px; padding:14px 16px;}
.tile-label {font-size:.72rem; letter-spacing:.06em; text-transform:uppercase; opacity:.6;}
.tile-value {font-size:1.7rem; font-weight:650; line-height:1.25; margin-top:2px;}
.tile-sub {font-size:.8rem; opacity:.6; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.badge {display:inline-block; padding:2px 9px; border-radius:999px; font-size:.72rem; font-weight:600; letter-spacing:.04em;}
.thinking {display:flex; align-items:center; gap:12px; padding:28px 4px 6px;}
.pulse {width:10px; height:10px; border-radius:50%; background:#6e56cf; animation:pulse 1.2s ease-in-out infinite;}
@keyframes pulse {0%,100% {transform:scale(.7); opacity:.45;} 50% {transform:scale(1.15); opacity:1;}}
.shimmer {font-size:1.15rem; font-weight:550; background:linear-gradient(90deg,#8b8d98 0%,#8b8d98 35%,#6e56cf 50%,#8b8d98 65%,#8b8d98 100%);
          background-size:250% 100%; -webkit-background-clip:text; background-clip:text; color:transparent; animation:shimmer 1.8s linear infinite;}
@keyframes shimmer {from {background-position:120% 0;} to {background-position:-120% 0;}}
.thinking-sub {font-size:.8rem; opacity:.55; padding-left:26px;}
.done-steps {font-size:.8rem; opacity:.45; padding:10px 0 0 26px; line-height:1.7;}
.agent {border:1px dashed rgba(128,128,128,.3); border-radius:14px; padding:14px 16px;}
.agent b {display:block; margin-bottom:2px;}
.agent span {font-size:.8rem; opacity:.6;}
</style>
""",
    unsafe_allow_html=True,
)


# ---------------- API helpers ----------------
def api(method: str, path: str, **kwargs):
    response = httpx.request(method, f"{API_BASE_URL}{path}", headers=API_HEADERS, timeout=kwargs.pop("timeout", 30), **kwargs)
    if response.status_code >= 400:
        is_json = response.headers.get("content-type", "").startswith("application/json")
        raise RuntimeError(f"HTTP {response.status_code}: {response.json().get('detail') if is_json else response.text}")
    return response.json()


@st.cache_data(ttl=30, show_spinner=False)
def get_health():
    return api("GET", "/health", timeout=5)


@st.cache_data(ttl=10, show_spinner=False)
def get_history():
    return api("GET", "/api/analyses", params={"limit": 20})


@st.cache_data(show_spinner=False)
def get_audit(analysis_id: str, status: str):  # status is in the cache key, so the audit refreshes when it changes
    return api("GET", f"/api/analyses/{analysis_id}/audit")


# ---------------- Small render helpers ----------------
def badge(severity: str) -> str:
    color = SEV_COLOR.get(severity, "#8b8d98")
    return f'<span class="badge" style="background:{color}22;color:{color}">{severity}</span>'


def legend(items: dict[str, str]):
    dots = "".join(f'<span style="margin-right:14px"><span style="color:{c}">●</span> {html.escape(k)}</span>' for k, c in items.items())
    st.markdown(f'<div style="font-size:.78rem;opacity:.75;margin:-6px 0 4px">{dots}</div>', unsafe_allow_html=True)


def tiles(items: list[tuple[str, str, str, str | None]]):
    cells = "".join(
        f'<div class="tile"><div class="tile-label">{html.escape(label)}</div>'
        f'<div class="tile-value" style="color:{color or "inherit"}">{html.escape(value)}</div>'
        f'<div class="tile-sub">{html.escape(sub)}</div></div>'
        for label, value, sub, color in items
    )
    st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)


def thinking(area, status: str, done: list[str], elapsed: float):
    """Flash the current step, like the 'Thinking…' line in AI assistants."""
    done_html = "".join(f"✓ {html.escape(s)}<br>" for s in done)
    area.markdown(
        f'<div class="thinking"><div class="pulse"></div><div class="shimmer">{html.escape(STEPS.get(status, status))}…</div></div>'
        f'<div class="thinking-sub">{elapsed:.0f}s</div><div class="done-steps">{done_html}</div>',
        unsafe_allow_html=True,
    )


# ---------------- Actions ----------------
def request_run():
    """Button callback (runs before the script): clear the old result so it never shows during a new run."""
    st.session_state.pop("result", None)
    st.session_state["history_pick"] = None
    st.session_state["run_requested"] = True


def open_history():
    if analysis_id := st.session_state.get("history_pick"):
        st.session_state["result"] = api("GET", f"/api/analyses/{analysis_id}")
        st.session_state["view"] = "Overview"


def go_to(view: str):
    st.session_state["view"] = view


def run_analysis(name: str, content: bytes, area) -> dict:
    started = time.monotonic()
    initial = api("POST", "/api/analyses/upload", files={"file": (name, content, "text/plain")}, timeout=60)
    done: list[str] = []
    last = None
    while time.monotonic() - started < 360:
        result = api("GET", f"/api/analyses/{initial['analysis_id']}")
        status = result["status"]
        if status not in STEPS:
            return result
        if last and status != last and last != "PENDING":
            done.append(STEPS[last])
        last = status
        thinking(area, status, done, time.monotonic() - started)
        time.sleep(0.8)
    raise RuntimeError("Analysis timed out.")


# ---------------- Sidebar ----------------
with st.sidebar:
    try:
        health = get_health()
    except Exception:
        st.error(f"API not reachable at {API_BASE_URL}.\n\nStart it with `make api`.")
        st.stop()
    st.markdown("**System**")
    st.caption(
        f"🟢 API connected  \nClassifier · `{health['models']['classifier']}`  \nReasoning · `{health['models']['reasoning']}`  \n"
        f"Slack · {health['slack_mode']} · JIRA · {health['jira_mode']}"
    )
    if not health["llm_configured"]:
        st.warning("No OpenRouter key: agents use rule-based fallbacks.")
    st.markdown("**History**")
    history = get_history()
    labels = {
        h["analysis_id"]: f"{h['upload_filename']} · {datetime.fromisoformat(h['created_at']).astimezone():%d %b %H:%M}"
        for h in history
    }
    st.selectbox("Open a previous run", list(labels), format_func=labels.get, index=None, key="history_pick",
                 placeholder="Select a run", on_change=open_history, label_visibility="collapsed")


# ---------------- Input ----------------
st.markdown("## 🛠️ Incident Analysis")
st.caption("Five AI agents, orchestrated by LangGraph, turn raw logs into prioritised incidents, fixes, and approved Slack/JIRA actions.")

samples = sorted(p for p in SAMPLE_DIR.glob("*") if p.suffix in {".log", ".txt", ".json", ".jsonl", ".csv"}) if SAMPLE_DIR.exists() else []
c1, c2, c3 = st.columns([3, 2, 1], vertical_alignment="bottom")
uploaded = c1.file_uploader("Upload an operational log", type=["log", "txt", "json", "jsonl", "csv"], key="upload")
sample = c2.selectbox("…or try a sample", [p.name for p in samples], index=None, placeholder="Choose a sample log", key="sample",
                      help="Example logs shipped in data/sample_logs/ for demos.")
c3.button("Analyze", type="primary", width="stretch", disabled=not (uploaded or sample), on_click=request_run)

area = st.empty()  # everything below the input lives here, so a new run replaces the old result at once

if st.session_state.pop("run_requested", False):
    name, content = (uploaded.name, uploaded.getvalue()) if uploaded else (sample, (SAMPLE_DIR / sample).read_bytes())
    try:
        st.session_state["result"] = run_analysis(name, content, area)
        st.session_state["view"] = "Overview"
        get_history.clear()
        st.rerun()
    except Exception as exc:
        area.error(f"Analysis failed: {exc}")
        st.stop()

result = st.session_state.get("result")
if not result:
    with area.container():
        st.write("")
        cells = "".join(f'<div class="agent"><b>{i}. {n}</b><span>{d}</span></div>' for i, (n, d) in enumerate(AGENTS, 1))
        st.markdown(f'<div class="tiles">{cells}</div>', unsafe_allow_html=True)
        st.caption("Upload a log or choose a sample, then click **Analyze**.")
    st.stop()


# ---------------- Result ----------------
aid = result["analysis_id"]
incidents = sorted(result["incidents"], key=lambda i: (SEVERITIES.index(i["severity"]), -i["confidence"]))
plans = {p["incident_id"]: p for p in result["remediation_plans"]}
events = {e["event_id"]: e for e in result["parsed_events"]}
slack, tickets, cookbook = result["slack_notification"], result["jira_tickets"], result["cookbook"]

with area.container():
    head, nav = st.columns([3, 2], vertical_alignment="bottom")
    head.markdown(f"#### {html.escape(result['upload_filename'] or 'Analysis')}")
    m = result.get("metrics")
    perf = f" · {m['wall_time_ms'] / 1000:.1f}s · {m['llm_calls']} LLM calls · ${m['cost_usd']:.3f}" if m else ""
    head.caption(f"Run {aid[:8]} · {result['status'].replace('_', ' ').lower()}{perf}")
    st.session_state.setdefault("view", "Overview")
    view = nav.segmented_control("View", VIEWS, key="view", label_visibility="collapsed") or "Overview"

    if result.get("error"):
        st.error(result["error"])
    if result["status"] == "AWAITING_APPROVAL" and view != "Slack & JIRA":
        b1, b2 = st.columns([5, 1], vertical_alignment="center")
        b1.info("Slack alert and JIRA tickets are drafted and waiting for your approval. Nothing has been sent.")
        b2.button("Review →", on_click=go_to, args=("Slack & JIRA",), width="stretch")
    findings = result.get("guardrail_findings") or []
    if findings and view != "Trace & audit":
        g1, g2 = st.columns([5, 1], vertical_alignment="center")
        rules = Counter(f["rule"].replace("_", " ") for f in findings)
        g1.warning("🛡️ Guardrails: " + ", ".join(f"{n} × {rule}" for rule, n in rules.items()))
        g2.button("Details →", on_click=go_to, args=("Trace & audit",), width="stretch", key="guard-details")

    # ---------- Overview: tiles, charts, prioritised incidents ----------
    if view == "Overview":
        sev_counts = Counter(i["severity"] for i in incidents)
        services = sorted({s for i in incidents for s in i["affected_services"]})
        errors = sum(e["level"] in ("ERROR", "CRITICAL") for e in events.values())
        top = next((s for s in SEVERITIES if sev_counts[s]), None)
        delivery = slack["delivery_status"].replace("_", " ").title() if slack else "Nothing to send"
        tiles([
            ("Log events", f"{len(events):,}", f"{errors} errors", None),
            ("Incidents", str(len(incidents)),
             " · ".join(f"{sev_counts[s]} {s.lower()}" for s in SEVERITIES if sev_counts[s]) or "none", SEV_COLOR.get(top)),
            ("Services impacted", str(len(services)), ", ".join(services) or "—", None),
            ("Runbooks used", str(len({s for p in plans.values() for s in p["sources"]})), "matched by RAG", None),
            ("Notifications", f"{len(tickets)} JIRA", delivery, None),
        ])

        if not incidents:
            st.success("No incidents detected. The logs look healthy.")
            st.stop()

        rows = [{"category": c.replace("_", " ").title(), "key": c, "severity": s, "rank": SEVERITIES.index(s), "count": n}
                for (c, s), n in Counter((i["category"], i["severity"]) for i in incidents).items()]
        totals = Counter()
        for r in rows:
            totals[r["category"]] += r["count"]
        category_order = [c for c, _ in totals.most_common()]
        chart_height = max(150, 38 * len(category_order))
        sev_scale = alt.Scale(domain=SEVERITIES, range=[SEV_COLOR[s] for s in SEVERITIES])

        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("**Incidents by category** · click a bar to filter")
            legend({s.title(): SEV_COLOR[s] for s in SEVERITIES if sev_counts[s]})
            pick = alt.selection_point(name="cat", fields=["key"])
            chart = (
                alt.Chart(alt.Data(values=rows)).mark_bar()
                .encode(
                    y=alt.Y("category:N", title=None, sort=category_order, scale=alt.Scale(paddingInner=0.35)),
                    x=alt.X("count:Q", title=None, stack="zero", axis=alt.Axis(tickMinStep=1, format="d")),
                    color=alt.Color("severity:N", scale=sev_scale, legend=None),
                    order=alt.Order("rank:Q"),
                    opacity=alt.condition(pick, alt.value(1), alt.value(0.3)),
                    tooltip=["category:N", "severity:N", "count:Q"],
                )
                .add_params(pick)
            )
            event = st.altair_chart(chart, on_select="rerun", key=f"cat-{aid}", width="stretch", height=chart_height)
            picked = [p["key"] for p in (event.selection.get("cat") or []) if "key" in p] if event else []

        with right:
            st.markdown("**Log activity** · errors and warnings over time")
            legend({"Error": SEV_COLOR["CRITICAL"], "Warning": SEV_COLOR["WARNING"], "Other": "#8b8d98"})
            timed = [e for e in events.values() if e["timestamp"]]
            if timed:
                stamps = [datetime.fromisoformat(e["timestamp"]) for e in timed]
                span = (max(stamps) - min(stamps)).total_seconds()
                unit = "hoursminutesseconds" if span <= 300 else "monthdatehoursminutes" if span <= 6 * 3600 else "monthdatehours"
                activity = [
                    {"time": e["timestamp"],
                     "level": "ERROR" if e["level"] in ("ERROR", "CRITICAL") else "WARNING" if e["level"] == "WARNING" else "OTHER"}
                    for e in timed
                ]
                st.altair_chart(
                    alt.Chart(alt.Data(values=activity)).mark_bar(cornerRadiusEnd=2)
                    .encode(
                        x=alt.X(f"{unit}(time):T", title=None),
                        y=alt.Y("count():Q", title=None, axis=alt.Axis(tickMinStep=1, format="d")),
                        color=alt.Color("level:N", legend=None,
                                        scale=alt.Scale(domain=["ERROR", "WARNING", "OTHER"],
                                                        range=[SEV_COLOR["CRITICAL"], SEV_COLOR["WARNING"], "#8b8d9866"])),
                        tooltip=[alt.Tooltip(f"{unit}(time):T", title="time"), "level:N", "count():Q"],
                    ),
                    width="stretch", height=chart_height,
                )
            else:
                st.caption("No timestamps found in this log.")

        st.write("")
        f1, f2 = st.columns([3, 2], vertical_alignment="bottom")
        present = ["ALL"] + [s for s in SEVERITIES if sev_counts[s]]
        sev = f1.segmented_control("Severity", present, default="ALL", key=f"sev-{aid}", label_visibility="collapsed",
                                   format_func=lambda s: f"All {len(incidents)}" if s == "ALL" else f"{s.title()} {sev_counts[s]}")
        query = f2.text_input("Search", placeholder="Search title, service, summary…", key=f"q-{aid}", label_visibility="collapsed")

        shown = [
            i for i in incidents
            if (not sev or sev == "ALL" or i["severity"] == sev)
            and (not picked or i["category"] in picked)
            and (not query or query.lower() in f"{i['title']} {i['summary']} {' '.join(i['affected_services'])}".lower())
        ]
        note = f" · category: {', '.join(c.replace('_', ' ').lower() for c in picked)} (click the bar again to clear)" if picked else ""
        st.caption(f"Showing {len(shown)} of {len(incidents)} incidents, most critical first{note}")

        if not shown:
            st.info("No incidents match these filters.")
            st.stop()

        table_col, detail_col = st.columns([5, 6], gap="large")
        with table_col:
            table = st.dataframe(
                [{"Severity": f"{SEV_DOT[i['severity']]} {i['severity'].title()}", "Incident": i["title"],
                  "Confidence": round(i["confidence"] * 100)} for i in shown],
                column_config={
                    "Severity": st.column_config.TextColumn(width="small"),
                    "Incident": st.column_config.TextColumn(width="large"),
                    "Confidence": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%", width="small"),
                },
                hide_index=True, width="stretch", height=min(38 + 35 * len(shown), 420),
                on_select="rerun", selection_mode="single-row", key=f"tbl-{aid}-{sev}-{','.join(picked)}-{query}",
            )
            selected_rows = table.selection.rows if table else []
            st.caption("Select a row to see its details.")
        incident = shown[selected_rows[0]] if selected_rows else shown[0]
        plan = plans.get(incident["incident_id"])

        with detail_col, st.container(border=True):
            st.markdown(
                f"{badge(incident['severity'])} &nbsp;<span style='opacity:.6;font-size:.85rem'>"
                f"{incident['category'].replace('_', ' ').title()} · {html.escape(', '.join(incident['affected_services']) or 'unknown service')}"
                f" · {incident['confidence']:.0%} confidence</span>",
                unsafe_allow_html=True,
            )
            st.markdown(f"### {incident['title']}")
            st.write(incident["summary"])
            if plan:
                st.markdown(f"**Likely root cause**  \n{plan['root_cause_hypothesis']}")
                flagged = set(plan.get("flagged_actions") or [])
                st.markdown("**Fix now**\n" + "\n".join(
                    f"{n}. {'⚠️ **Review first:** ' if a in flagged else ''}{a}" for n, a in enumerate(plan["immediate_actions"], 1)))
            evidence = [events[e]["raw_line"] for e in incident["evidence_event_ids"] if e in events]
            with st.expander(f"Evidence · {len(evidence)} log lines"):
                st.code("\n".join(evidence) or "—", language="log", wrap_lines=True)
            if plan:
                with st.expander("Investigate · prevent · rollback"):
                    st.markdown("**Investigate**\n" + "\n".join(f"- {a}" for a in plan["investigation_steps"]))
                    st.markdown("**Prevent**\n" + "\n".join(f"- {a}" for a in plan["preventive_actions"]))
                    st.markdown(f"**Rollback:** {plan['rollback_plan']}")
                st.caption(f"Runbooks: {', '.join(plan['sources']) or 'none matched'}")

    # ---------- Cookbook ----------
    elif view == "Cookbook":
        if not cookbook:
            st.info("No cookbook: nothing to fix.")
        else:
            st.markdown(f"### {cookbook['title']}")
            st.write(cookbook["executive_summary"])
            for col, title, key in zip(st.columns(3, gap="large"), ("🚑 Immediate", "🔍 Investigate", "🛡️ Prevent"),
                                       ("immediate_actions", "investigation_steps", "preventive_actions")):
                with col, st.container(border=True):
                    st.markdown(f"**{title}**\n" + "\n".join(f"- [ ] {a}" for a in cookbook[key]))
            if cookbook["timeline"]:
                with st.expander("Timeline"):
                    st.markdown("\n".join(f"- {t}" for t in cookbook["timeline"]))
            if cookbook["communication_template"]:
                with st.expander("Stakeholder update template"):
                    st.code(cookbook["communication_template"], language=None, wrap_lines=True)

    # ---------- Slack & JIRA (human approval) ----------
    elif view == "Slack & JIRA":
        if not slack and not tickets:
            st.info("Nothing to notify.")
        else:
            left, right = st.columns(2, gap="large")
            with left, st.container(border=True):
                if slack:
                    st.markdown(f"**💬 Slack** · {slack['channel']} &nbsp; `{slack['delivery_status']}`")
                    with st.container(border=True):  # preview as Slack renders it: *bold* → bold
                        st.markdown(re.sub(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])", r"**\1**", slack["message"]).replace("\n", "  \n"))
                    if slack.get("delivery_error"):
                        st.error(slack["delivery_error"])
            with right, st.container(border=True):
                serious = sum(i["severity"] in ("CRITICAL", "ERROR") for i in incidents)
                st.markdown(f"**🎫 JIRA** · {len(tickets)} ticket(s) for {serious} error/critical incident(s)")
                if len(tickets) < serious:
                    st.caption("Ticket cap reached: the least severe incidents have no ticket (raise MAX_JIRA_TICKETS to include them).")
                for t in tickets:
                    key = f"[{t['issue_key']}]({t['issue_url']}) · " if t.get("issue_key") else ""
                    st.markdown(f"{key}**{t['priority']}** · {t['summary']} &nbsp; `{t['delivery_status']}`")
                    with st.expander("Description and labels"):
                        st.text(t["description"])
                        st.caption("Labels: " + ", ".join(t["labels"]))
                    if t.get("delivery_error"):
                        st.error(t["delivery_error"])

            if result["status"] == "AWAITING_APPROVAL":
                live = [x for x, mode in (("post to Slack " + (slack["channel"] if slack else ""), health["slack_mode"]),
                                          (f"create {len(tickets)} JIRA ticket(s)", health["jira_mode"])) if mode == "live"]
                st.caption(("Approving will **" + "** and **".join(live) + "** for real.") if live
                           else "Slack and JIRA are in simulation mode: approving records the decision but sends nothing.")
                with st.form("approval", border=False):
                    a1, a2, a3, a4 = st.columns([2, 3, 1, 1], vertical_alignment="bottom")
                    approver = a1.text_input("Approver", value="on-call engineer")
                    comment = a2.text_input("Comment (optional)")
                    approve = a3.form_submit_button("Approve & send", type="primary", width="stretch")
                    reject = a4.form_submit_button("Reject", width="stretch")
                if approve or reject:
                    action = "approve" if approve else "reject"
                    try:
                        with st.spinner("Sending to Slack and JIRA…" if approve else "Rejecting…"):
                            st.session_state["result"] = api(
                                "POST", f"/api/analyses/{aid}/{action}",
                                json={"approver": approver or "anonymous", "comment": comment or None}, timeout=120,
                            )
                        get_history.clear()
                        st.rerun()
                    except Exception as exc:
                        st.error(f"{action.title()} failed: {exc}")

    # ---------- Trace & audit ----------
    else:
        runs = result.get("agent_runs") or []
        if runs:
            st.markdown("**Agent timeline** · analysis phase; overlapping bars ran in parallel")
            trace_rows = [{"Agent": r["agent"], "Seconds": round(r["latency_ms"] / 1000, 2), "LLM calls": r["llm_calls"],
                           "Tokens": r["prompt_tokens"] + r["completion_tokens"], "Cost $": round(r["cost_usd"], 4),
                           "Model": ", ".join(m.split("/")[-1] for m in r["models"]) or "—",
                           "Status": "⚠️ fallback" if r["fallback"] else "ok"} for r in runs]
            analysis_runs = [r for r in runs if r["agent"] not in ("Slack send", "JIRA create") and r.get("started_at")]
            if analysis_runs:
                t0 = min(r["started_at"] for r in analysis_runs)
                spans = [{"Agent": r["agent"], "start": round(r["started_at"] - t0, 2),
                          "end": round(r["started_at"] - t0 + max(r["latency_ms"], 60) / 1000, 2),
                          "Seconds": round(r["latency_ms"] / 1000, 2), "Status": "fallback" if r["fallback"] else "ok"}
                         for r in analysis_runs]
                st.altair_chart(
                    alt.Chart(alt.Data(values=spans)).mark_bar(cornerRadius=3, height=16)
                    .encode(y=alt.Y("Agent:N", sort=[r["Agent"] for r in spans], title=None),
                            x=alt.X("start:Q", title="seconds since upload"), x2="end:Q",
                            color=alt.condition(alt.datum.Status == "ok", alt.value("#6e56cf"), alt.value(SEV_COLOR["WARNING"])),
                            tooltip=["Agent:N", "start:Q", "Seconds:Q", "Status:N"]),
                    width="stretch", height=30 * len(spans) + 40,
                )
            st.dataframe(trace_rows, width="stretch", hide_index=True)
        if findings:
            st.markdown("**Guardrail findings**")
            st.dataframe([{"Stage": f["stage"], "Rule": f["rule"].replace("_", " "), "Severity": f["severity"], "Detail": f["detail"]}
                          for f in findings], width="stretch", hide_index=True)
        st.markdown("**Parsed log events**")
        st.dataframe(
            [{k: e[k] for k in ("line_number", "timestamp", "service", "level", "error_code", "message")} for e in events.values()],
            width="stretch", hide_index=True, height=360,
        )
        st.markdown("**Audit trail**")
        try:
            st.dataframe(get_audit(aid, result["status"]), width="stretch", hide_index=True)
        except Exception as exc:
            st.error(str(exc))
