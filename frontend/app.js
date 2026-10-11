/* Multi-Agent Incident Analysis Suite — dashboard UI.
   Hash-routed single page app. Served by the API at /ui it shows real analyses and sends to Slack/Jira only
   after a person clicks Send on the Approvals page. With the API offline it falls back to sample data (data.js). */
(function () {
  "use strict";
  const D = window.SAMPLE;
  D.issues.forEach((i) => (i.decision ||= i.jiraKey ? "sent" : "pending"));
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const main = $("#main");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const icon = (id, style = "") => `<svg class="i"${style ? ` style="${style}"` : ""}><use href="#i-${id}"/></svg>`;
  const num = (n) => Number(n).toLocaleString("en-US");
  const short = (ts) => { if (!ts) return "—"; const d = new Date(ts.replace(" ", "T")); return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + " " + ts.slice(11, 16); };
  const clock = (d = new Date()) => d.toTimeString().slice(0, 8);
  const bytes = (b) => (b < 1024 ? b + " B" : b < 1048576 ? (b / 1024).toFixed(1) + " KB" : (b / 1048576).toFixed(1) + " MB");
  const SEV = ["Critical", "High", "Medium", "Low"];
  const SEV_COLOR = { Critical: "#ef4444", High: "#f97316", Medium: "#f59e0b", Low: "#3b82f6" };
  const CATS = ["Infrastructure", "Application", "Database", "Network", "Security", "Performance", "Configuration"];
  const ISS_STATUSES = ["Open", "Remediation Suggested", "Ticket Created", "Resolved"];
  const ANL_CLASS = { Completed: "s-ok", Processing: "s-run", Failed: "s-fail", "Awaiting Approval": "s-appr" };
  const ISS_CLASS = { Open: "s-run", "Remediation Suggested": "s-appr", "Ticket Created": "s-info", Resolved: "s-ok", "False Positive": "s-pend" };
  const ENV_CLASS = { Production: "prod", Staging: "stg", Development: "dev" };
  const sevBadge = (s) => `<span class="sev sev-${s.toLowerCase()}">${s}</span>`;
  const stBadge = (s, map) => `<span class="st ${map[s] || "s-pend"}">${esc(s)}</span>`;
  const envCell = (e) => `<span class="env ${ENV_CLASS[e]}">${e}</span>`;

  const S = { env: "Production", t: {}, f: {}, refresh: {}, run: null, drawer: null, modal: null, upload: null, expanded: null,
    cfg: { env: "Production", service: "", source: "Application Logs", threshold: "All", slack: true, jira: true, checklist: true, fail: false },
    slackDraft: null, slackEdit: false, bellRead: false, seq: { anl: 2049, iss: 3141, ops: 1432 } };

  /* ================= utilities ================= */
  function toast(title, sub = "", tone = "ok") {
    const el = document.createElement("div");
    el.className = "toast " + tone;
    el.innerHTML = `<div><b>${esc(title)}</b>${sub ? `<small>${esc(sub)}</small>` : ""}</div>`;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }
  function download(name, text, type = "text/plain") {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); }
    catch { const ta = Object.assign(document.createElement("textarea"), { value: text }); document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove(); }
  }
  const tstate = (id) => (S.t[id] ||= { page: 1 });
  const fstate = (id, defaults = {}) => (S.f[id] ||= { ...defaults });

  function pager(id, total, size) {
    const st = tstate(id);
    const pages = Math.max(1, Math.ceil(total / size));
    st.page = Math.min(Math.max(1, st.page), pages);
    const from = total ? (st.page - 1) * size + 1 : 0, to = Math.min(total, st.page * size);
    let lo = Math.max(1, st.page - 2), hi = Math.min(pages, lo + 4); lo = Math.max(1, hi - 4);
    let btns = "";
    for (let p = lo; p <= hi; p++) btns += `<button class="${p === st.page ? "on" : ""}" data-action="page" data-table="${id}" data-page="${p}" aria-label="Page ${p}">${p}</button>`;
    if (total && pages === 1) return { slice: [0, total], html: `<div class="pager"><span>${total} ${total === 1 ? "row" : "rows"} · all shown on one page</span></div>` };
    return { slice: [from - 1, to], html: total ? `<div class="pager"><span>Rows ${from}–${to} of ${total} · Page ${st.page} of ${pages}</span><div class="pg">
      <button data-action="page" data-table="${id}" data-page="${st.page - 1}" ${st.page === 1 ? "disabled" : ""} aria-label="Previous page">‹</button>${btns}
      <button data-action="page" data-table="${id}" data-page="${st.page + 1}" ${st.page === pages ? "disabled" : ""} aria-label="Next page">›</button></div></div>` : "" };
  }
  function emptyState(title, text, ic = "search", action = "") {
    return `<div class="empty"><div class="ei">${icon(ic)}</div><b>${esc(title)}</b><span>${esc(text)}</span>${action ? `<div style="margin-top:14px">${action}</div>` : ""}</div>`;
  }
  function field(id, key, kind, opts) {
    const v = fstate(id)[key] ?? "";
    if (kind === "search") return `<label class="field">${icon("search")}<input type="search" data-filter="${id}:${key}" value="${esc(v)}" placeholder="${esc(opts)}" aria-label="${esc(opts)}"></label>`;
    return `<label class="field"><span class="dim">${esc(opts.label)}</span><select data-filter="${id}:${key}" aria-label="${esc(opts.label)}">
      <option value="">All</option>${opts.values.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></label>`;
  }

  /* ================= backend integration ================= */
  // Served by FastAPI at /ui → same origin. Opened any other way (e.g. `make mock-ui`) → API on localhost:8000.
  const API_BASE = location.pathname.startsWith("/ui") ? "" : "http://localhost:8000";
  const LIVE = { ok: false, error: "", auth: false, slack: { mode: "simulation", channel: "#sre-incidents" }, jira: { mode: "simulation", project_key: "OPS", base_url: "" } };
  const apiKey = () => { try { return sessionStorage.getItem("apiKey") || ""; } catch { return ""; } };
  async function apiFetch(path, opts = {}) {
    const headers = { ...(opts.headers || {}) };
    if (apiKey()) headers["X-API-Key"] = apiKey();
    let body = opts.body;
    if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.json); }
    const res = await fetch(API_BASE + path, { method: opts.method || "GET", headers, body });
    let data = null; try { data = await res.json(); } catch { /* no body */ }
    if (!res.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `HTTP ${res.status}`);
    return data;
  }
  const SEV_FROM = { CRITICAL: "Critical", ERROR: "High", WARNING: "Medium", INFO: "Low" };
  const CAT_FROM = { INFRASTRUCTURE: "Infrastructure", APPLICATION_ERROR: "Application", DATABASE: "Database", NETWORK: "Network", AUTHENTICATION: "Security",
    SECURITY: "Security", CAPACITY: "Performance", THIRD_PARTY: "Application", UNKNOWN: "Configuration" };
  const RISK_FROM = { INFO: "Low", WARNING: "Medium", ERROR: "High", CRITICAL: "High" };
  const TERMINAL = new Set(["AWAITING_APPROVAL", "COMPLETED", "COMPLETED_NO_INCIDENTS", "NOTIFICATION_FAILED", "REJECTED", "FAILED"]);
  const statusFrom = (st) => (st === "FAILED" ? "Failed" : st === "AWAITING_APPROVAL" ? "Awaiting Approval" : TERMINAL.has(st) ? "Completed" : "Processing");
  const displayId = (id) => "ANL-" + id.slice(0, 6).toUpperCase();
  const tsLocal = (iso) => (iso ? D.fmt(new Date(iso)) : null);
  const slackLabel = (st) => ({ SENT: "Sent", SIMULATED: "Sent (simulated)", FAILED: "Failed" })[st];
  const saveMeta = (id, m) => { try { localStorage.setItem("anl-meta-" + id, JSON.stringify(m)); } catch { /* storage unavailable */ } };
  const loadMeta = (id) => { try { return JSON.parse(localStorage.getItem("anl-meta-" + id) || "{}"); } catch { return {}; } };
  const removeWhere = (arr, pred) => { for (let k = arr.length - 1; k >= 0; k--) if (pred(arr[k])) arr.splice(k, 1); };
  const ticketRow = (i, t, created) => ({ key: t.issue_key, url: t.issue_url, real: true, project: `Jira (${t.issue_key.split("-")[0]})`, title: t.summary, issueId: i.id,
    severity: i.sev, priority: t.priority, assignee: "Unassigned", status: t.delivery_status === "SIMULATED" ? "Simulated" : "To Do", created: created || D.fmt(new Date()), labels: t.labels });

  /* Convert an API AnalysisResult into the UI's analysis / issue / ticket shapes. */
  function mapReal(res, row = {}) {
    const meta = loadMeta(res.analysis_id);
    const aid = displayId(res.analysis_id);
    const created = tsLocal(row.created_at) || D.fmt(new Date());
    const ev = Object.fromEntries((res.parsed_events || []).map((e) => [e.event_id, e]));
    const plans = Object.fromEntries((res.remediation_plans || []).map((x) => [x.incident_id, x]));
    const env = meta.env || "Production";
    const issues = (res.incidents || []).map((inc) => {
      const plan = plans[inc.incident_id];
      const evid = inc.evidence_event_ids.map((x) => ev[x]).filter(Boolean);
      const stamps = evid.map((e) => e.timestamp).filter(Boolean).sort();
      const draft = (res.jira_tickets || []).find((x) => x.incident_id === inc.incident_id);
      const made = draft && ["CREATED", "SIMULATED"].includes(draft.delivery_status) ? draft : null;
      const actions = plan ? plan.immediate_actions : [];
      return {
        id: "INC-" + inc.incident_id.slice(0, 6).toUpperCase(), real: true, incidentId: inc.incident_id, realAnalysisId: res.analysis_id,
        sev: SEV_FROM[inc.severity] || "Medium", cat: CAT_FROM[inc.category] || "Application", title: inc.title,
        service: inc.affected_services.join(", ") || "unknown", source: meta.source || "Application Logs",
        host: (evid.find((e) => e.host) || {}).host || "—", code: (evid.find((e) => e.error_code) || {}).error_code || "—",
        confidence: inc.confidence, events: evid.length, summary: inc.summary, logs: evid.map((e) => e.raw_line),
        fix: actions[0] || "Investigate the evidence events.", actions, rationale: plan ? plan.root_cause_hypothesis : inc.summary,
        risk: RISK_FROM[plan?.risk_level] || "Medium", effort: "Not estimated by the agent", perms: "Not specified by the agent",
        rollback: plan ? plan.rollback_plan : "—", flagged: plan?.flagged_actions || [], sources: plan?.sources || [],
        steps: plan ? [...plan.immediate_actions, ...plan.investigation_steps].map((a) => [a, ""]) : [],
        env, analysis: aid, detected: created, firstSeen: stamps.length ? tsLocal(stamps[0]) : created, lastSeen: stamps.length ? tsLocal(stamps[stamps.length - 1]) : created,
        status: made ? "Ticket Created" : plan ? "Remediation Suggested" : "Open", jiraKey: made?.issue_key || null, jiraUrl: made?.issue_url || null,
        jiraDraft: draft && !made ? { title: draft.summary, desc: draft.description, priority: draft.priority, assignee: "Unassigned", labels: draft.labels.join(", ") } : undefined,
        slackStatus: slackLabel(res.incident_slack?.[inc.incident_id]) || "Not sent", checklistDone: [], owners: {},
        decision: res.dismissed_incidents?.[inc.incident_id] ? "dismissed" : made || ["SENT", "SIMULATED"].includes(res.incident_slack?.[inc.incident_id]) ? "sent" : "pending",
      };
    });
    const crit = issues.filter((i) => i.sev === "Critical").length;
    const analysis = { id: aid, realId: res.analysis_id, real: true, file: res.upload_filename, service: meta.service || issues[0]?.service || "—",
      source: meta.source || "Application Logs", env, lines: (res.parsed_events || []).length, critical: crit, warnings: issues.length - crit,
      status: statusFrom(res.status) === "Awaiting Approval" && !issues.some((i) => i.sev === "Critical" && i.decision === "pending") ? "Completed" : statusFrom(res.status), uploaded: created, started: created, completed: tsLocal(row.updated_at) || created,
      duration: res.metrics ? Math.round(res.metrics.wall_time_ms / 1000) : null, failedAt: res.status === "FAILED" ? "Workflow" : null,
      agentRuns: res.agent_runs || [], error: res.error };
    const tickets = issues.filter((i) => i.jiraKey).map((i) => {
      const t = res.jira_tickets.find((x) => x.incident_id === i.incidentId);
      return ticketRow(i, t, created);
    });
    return { analysis, issues, tickets };
  }
  function upsertReal({ analysis, issues, tickets }) {
    removeWhere(D.analyses, (x) => x.realId === analysis.realId);
    removeWhere(D.issues, (x) => x.realAnalysisId === analysis.realId);
    removeWhere(D.tickets, (x) => x.real && issues.some((i) => i.id === x.issueId));
    D.analyses.unshift(analysis); D.issues.unshift(...issues); D.tickets.unshift(...tickets);
  }
  async function loadReal() {
    const rows = await apiFetch("/api/analyses?limit=15");
    const results = await Promise.all(rows.map((row) => apiFetch(`/api/analyses/${row.analysis_id}`).then((res) => [res, row]).catch(() => null)));
    results.filter(Boolean).reverse().forEach(([res, row]) => upsertReal(mapReal(res, row)));
  }
  async function boot() {
    try {
      const [health, integ] = await Promise.all([apiFetch("/health"), apiFetch("/api/integrations")]);
      Object.assign(LIVE, { ok: true, error: "", auth: health.auth_enabled, slack: integ.slack, jira: integ.jira });
      await loadReal();
    } catch (e) {
      LIVE.ok = false; LIVE.error = e.message;
    }
    if (!S.run) render(); else updateChrome();
  }

  /* ================= router ================= */
  const PAGES = {};
  let currentPage = null;
  function parseHash() {
    const h = location.hash.replace(/^#\/?/, "") || "dashboard";
    let [page, qs] = h.split("?");
    if (page === "slack" || page === "jira") page = "approvals";  // old links
    return { page: PAGES[page] ? page : "dashboard", params: new URLSearchParams(qs || "") };
  }
  function render() {
    const { page, params } = parseHash();
    if (page !== currentPage) window.scrollTo(0, 0);
    if (S.drawer) closeDrawer();
    currentPage = page;
    document.body.classList.remove("nav-open");
    $("#bellMenu").hidden = true;
    $$(".side a").forEach((a) => a.classList.toggle("on", a.dataset.route === page));
    $$(".side a").forEach((a) => a.toggleAttribute("aria-current", a.dataset.route === page));
    main.innerHTML = PAGES[page].html(params);
    PAGES[page].mount?.(params);
    document.title = `${PAGES[page].title} · Incident Analysis Suite`;
    updateChrome();
  }
  function go(hash) { if (location.hash === hash) render(); else location.hash = hash; }
  window.addEventListener("hashchange", render);

  function updateChrome() {
    const note = $(".demo-note");
    if (note) note.innerHTML = LIVE.ok
      ? `<b style="color:#86efac">● Connected to API</b><br>Slack: ${LIVE.slack.mode} · Jira: ${LIVE.jira.mode} (${esc(LIVE.jira.project_key)})`
      : `<b style="color:#fca5a5">● API not reachable</b><br>${esc(LIVE.error || "")}<br>Showing sample data only. Start it with <code>make api</code>.`;
    const crit = D.issues.filter((i) => i.sev === "Critical" && !["Resolved", "False Positive"].includes(i.status)).length;
    $("#navCritical").textContent = crit || "";
    const waiting = pendingCritical().length;
    $("#navApprovals").textContent = waiting || "";
    $("#navApprovals").title = waiting ? `${waiting} critical issue(s) waiting for your decision` : "";
    const chip = $("#runchip");
    if (S.run && S.run.state === "running" && currentPage !== "analysis") {
      chip.hidden = false;
      chip.innerHTML = `<i></i>${esc(nowText(S.run))}…`;
    } else chip.hidden = true;
    $("#bellCount").textContent = S.bellRead ? "" : D.notifications.length;
    $("#bellCount").hidden = S.bellRead;
  }

  /* ================= DASHBOARD ================= */
  const KPI = {
    Production: [12480, 6, 18, 24, ["↓ 12%", "up", "↑ 2", "upr", "↑ 4", "upa", "↑ 3", "up"]],
    Staging: [3120, 1, 7, 9, ["↑ 4%", "up", "0", "", "↓ 1", "up", "↑ 2", "up"]],
    Development: [860, 0, 3, 4, ["↓ 8%", "up", "0", "", "↑ 1", "upa", "0", ""]],
  };
  PAGES.dashboard = {
    title: "Dashboard",
    html() {
      const [logs, crit, warn, auto, d] = KPI[S.env];
      const attention = D.issues.filter((i) => i.env === S.env && ["Critical", "High"].includes(i.sev) && !["Resolved", "False Positive"].includes(i.status)).slice(0, 5);
      return `
      <div class="titlebar"><div><h1>Dashboard</h1><p>${S.env} · last 24 hours · updated live</p></div></div>
      <section class="kpis">
        <a class="kpi" href="#/agents"><div class="ic" style="background:#2563eb">${icon("file")}</div><div><label>Logs Analyzed</label><strong>${num(logs)}</strong><span><b class="${d[1]}">${d[0]}</b> from last hour</span></div></a>
        <a class="kpi ${crit ? "crit" : ""}" href="#/issues?severity=Critical"><div class="ic" style="background:#dc2626">${icon("alert")}</div><div><label>Critical Issues</label><strong>${crit}</strong><span><b class="${d[3]}">${d[2]}</b> from last hour</span></div></a>
        <a class="kpi" href="#/issues?severity=High"><div class="ic" style="background:#d97706">${icon("alert")}</div><div><label>Warnings</label><strong>${warn}</strong><span><b class="${d[5]}">${d[4]}</b> from last hour</span></div></a>
        <a class="kpi" href="#/playbooks"><div class="ic" style="background:#16a34a">${icon("bolt")}</div><div><label>Auto-Remediation Suggestions</label><strong>${auto}</strong><span><b class="${d[7]}">${d[6]}</b> from last hour</span></div></a>
      </section>
      <section class="runlayout">
        <div class="card">
          <div class="h"><div><h2>Recent analyses</h2><div class="sub">${S.env} environment</div></div>
            <div class="toolbar" style="margin:0">${field("dash", "q", "search", "Search analyses…")}${field("dash", "status", "select", { label: "Status", values: ["Completed", "Processing", "Failed", "Awaiting Approval"] })}</div></div>
          <div id="t-dash"></div>
        </div>
        <div class="card">
          <div class="h"><div><h2>Needs attention</h2><div class="sub">Open critical and high issues</div></div><a class="link" href="#/issues">All issues ${icon("ext")}</a></div>
          ${attention.length ? `<div class="feed" style="max-height:none">${attention.map((i) => `
            <div style="grid-template-columns:1fr;cursor:pointer" data-action="open-issue" data-id="${i.id}" tabindex="0" role="button">
              <span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">${sevBadge(i.sev)}<b style="font-weight:500">${esc(i.title)}</b></span>
              <span class="muted">${i.id} · ${esc(i.service)} · ${short(i.detected)}</span></div>`).join("")}</div>`
            : emptyState("No critical issues detected in the selected time range.", `Nothing needs attention in ${S.env}.`, "shield")}
        </div>
      </section>`;
    },
    mount() {
      S.refresh.dash = () => {
        const f = fstate("dash"); const q = (f.q || "").toLowerCase();
        const rows = D.analyses.filter((a) => a.env === S.env && (!f.status || a.status === f.status) && (!q || `${a.id} ${a.file} ${a.service}`.toLowerCase().includes(q)));
        const p = pager("dash", rows.length, 6);
        $("#t-dash").innerHTML = rows.length ? `<div class="tablewrap"><table>
          <thead><tr><th>Analysis ID</th><th>Log file</th><th>Environment</th><th>Uploaded at</th><th>Logs processed</th><th>Critical</th><th>Warnings</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>${rows.slice(...p.slice).map((a) => `<tr>
            <td class="id">${a.id}${LIVE.ok && !a.real ? ` <span class="tag">sample</span>` : ""}</td><td class="trunc" title="${esc(a.file)}">${esc(a.file)}</td><td>${envCell(a.env)}</td><td class="nowrap num" title="${a.uploaded}">${short(a.uploaded)}</td>
            <td class="num">${num(a.lines)}</td><td class="num" style="color:${a.critical ? "#f87171" : ""};font-weight:${a.critical ? 700 : 400}">${a.critical}</td>
            <td class="num" style="color:${a.warnings ? "#fbbf24" : ""}">${a.warnings}</td><td>${stBadge(a.status, ANL_CLASS)}</td>
            <td><div class="acts"><button class="act" data-action="view-report" data-id="${a.id}" title="View report" aria-label="View report for ${a.id}" ${a.status === "Processing" ? "disabled" : ""}>${icon("eye")}Report</button>
              <button class="act" data-action="view-agents" data-id="${a.id}" title="View agents" aria-label="View agents for ${a.id}" ${a.status === "Processing" ? "disabled" : ""}>${icon("agent")}Agents</button>
              ${a.status === "Awaiting Approval" ? `<a class="act primary" href="#/approvals" aria-label="Approve notifications for ${a.id}">${icon("send")}Approve</a>` : ""}</div></td></tr>`).join("")}</tbody></table></div>${p.html}`
          : emptyState("No analyses found", `No analyses in ${S.env} match these filters.`, "file");
      };
      S.refresh.dash();
    },
  };

  /* ================= LOG ANALYSIS (new analysis + live pipeline) ================= */
  const STEP_DEFS = [
    { key: "upload", name: "Uploaded Logs", short: "Ingest", ic: "upload", role: (c) => `Ingest · ${c.source}`, ms: 1600 },
    { key: "orch", name: "Orchestrator", short: "Orchestrator", ic: "orch", role: () => "LangGraph workflow controller", ms: 1300 },
    { key: "classify", name: "Log Reader / Classifier Agent", short: "Classifier", ic: "read", role: () => "Parse · extract fields · categorize", ms: 3600 },
    { key: "remediate", name: "Remediation Agent", short: "Remediation", ic: "shield", role: (c, n) => `RAG over runbooks × ${n} (parallel)`, ms: 4400 },
    { key: "cookbook", name: "Cookbook Synthesizer Agent", short: "Cookbook", ic: "list", role: () => "Actionable checklist", ms: 2200 },
    { key: "notify", name: "Notification Agent", short: "Notification", ic: "bell", role: () => "Slack · #sre-incidents", ms: 1700 },
    { key: "jira", name: "Jira Ticket Agent", short: "Jira", ic: "ticket", role: () => "Tickets for critical issues", ms: 1800 },
  ];
  const THRESH = { All: 3, Warning: 2, Error: 1, Critical: 0 };
  const LOG_CLASS = { upload: "c-ing", orch: "c-or", classify: "c-cls", remediate: "c-rem", cookbook: "c-cb", notify: "c-ntf", jira: "c-jira" };

  PAGES.analysis = {
    title: "Log Analysis",
    html() {
      if (S.run) return runViewHtml();
      const c = S.cfg;
      return `
      <div class="titlebar"><div><h1>New Analysis</h1><p>Upload a log file and click Start Analysis. The incidents in the log are identified and fixes are suggested. Nothing is sent to Slack or Jira until you approve it.</p></div></div>
      <div class="newgrid">
        <section class="card">
          <div class="h"><div><h2>Upload logs</h2><div class="sub">One file · .log .txt .json .csv · up to 20 MB</div></div></div>
          <div id="uploadArea"></div>
          <div class="startrow">
            <button class="btn primary lg" id="startBtn" data-action="start-analysis" disabled>${icon("bolt")}Start Analysis</button>
            <span class="muted" id="startHint">Choose a file to enable this button.</span>
          </div>
        </section>
        <section class="card">
          <div class="h"><div><h2>Options</h2><div class="sub">Optional · the defaults work for most logs</div></div></div>
          <div class="form">
            <div class="fg full"><label for="cfg-env">Environment</label><select class="inp" id="cfg-env" data-cfg="env">${D.ENVS.map((e) => `<option ${e === c.env ? "selected" : ""}>${e}</option>`).join("")}</select><small>Used to label and filter the results.</small></div>
            <div class="fg full"><span class="fl" id="thr-l">Show issues of at least this severity</span><div class="seg" role="radiogroup" aria-labelledby="thr-l">${Object.keys(THRESH).map((t) => `<button type="button" role="radio" aria-checked="${t === c.threshold}" class="${t === c.threshold ? "on" : ""}" data-action="threshold" data-v="${t}">${t}</button>`).join("")}</div><small>“All” shows everything the agents find.</small></div>
            ${LIVE.ok ? "" : `<details class="demo fg full"><summary>Demo options (API offline)</summary>${toggle("fail", "Simulate an agent failure", "Shows the error and retry flow")}</details>`}
          </div>
        </section>
      </div>`;
    },
    mount() {
      if (S.run) { mountRunView(); return; }
      renderUpload();
    },
  };
  function toggle(key, title, sub) {
    return `<label class="tgl"><span class="tt"><b>${title}</b><small>${sub}</small></span><span class="sw"><input type="checkbox" data-cfg="${key}" ${S.cfg[key] ? "checked" : ""}><span></span></span></label>`;
  }
  function renderUpload() {
    const area = $("#uploadArea"); if (!area) return;
    const u = S.upload;
    area.innerHTML = `
      <div class="drop" id="drop" data-action="browse" tabindex="0" role="button" aria-label="Upload a log file">
        <div class="di">${icon("cloud")}</div><b>Drag and drop a log file here</b><p>or <span class="link">browse your computer</span></p>
        <div class="types"><span>.log</span><span>.txt</span><span>.json</span><span>.csv</span></div>
      </div>
      <input type="file" id="fileInput" accept=".log,.txt,.json,.csv" hidden>
      ${u ? `<div class="fileitem"><div class="fi">${icon("file")}</div><div class="fm"><b>${esc(u.name)}</b>
          <small>${bytes(u.size)}${u.lines != null ? ` · ${num(u.lines)} lines` : ""} · ${u.progress < 100 ? `Uploading ${Math.round(u.progress)}%` : "Ready"}</small>
          <div class="pbar"><i style="width:${u.progress}%"></i></div></div>
          <button class="iconbtn" data-action="remove-file" aria-label="Remove file">${icon("x")}</button></div>`
        : ""}
      ${S.uploadError ? `<div class="err">${esc(S.uploadError)}</div>` : ""}`;
    const drop = $("#drop");
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => e.dataTransfer.files[0] && takeFile(e.dataTransfer.files[0]));
    $("#fileInput").addEventListener("change", (e) => e.target.files[0] && takeFile(e.target.files[0]));
    const ready = !!(u && u.progress >= 100), start = $("#startBtn"), hint = $("#startHint");
    if (start) start.disabled = !ready;
    if (hint) hint.textContent = ready ? `Ready to analyze ${u.name}` : u ? "Reading the file…" : "Choose a file to enable this button.";
  }
  function takeFile(file) {
    const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    S.uploadError = "";
    if (![".log", ".txt", ".json", ".csv"].includes(ext)) { S.uploadError = `“${file.name}” is not supported. Use .log, .txt, .json or .csv.`; S.upload = null; renderUpload(); return; }
    if (file.size > 20 * 1048576) { S.uploadError = `“${file.name}” is ${bytes(file.size)}; the limit is 20 MB.`; S.upload = null; renderUpload(); return; }
    if (file.size === 0) { S.uploadError = `“${file.name}” is empty.`; S.upload = null; renderUpload(); return; }
    const u = (S.upload = { name: file.name, size: file.size, progress: 0, lines: null, file });
    const reader = new FileReader();
    reader.onload = () => { if (S.upload === u) { u.lines = String(reader.result).split(/\r?\n/).filter((l) => l.trim()).length; renderUpload(); } };
    reader.readAsText(file);
    const iv = setInterval(() => {
      if (S.upload !== u) return clearInterval(iv);
      u.progress = Math.min(100, u.progress + 9 + Math.random() * 14);
      renderUpload();
      if (u.progress >= 100) clearInterval(iv);
    }, 110);
  }

  /* ---- simulated workflow ---- */
  function pickTemplates(cfg) {
    const bySource = { "SAP System Logs": [9, 8, 0, 4, 11], "AWS CloudWatch": [4, 7, 0, 3, 10], "Azure Monitor": [8, 2, 5, 3, 11] };
    const idx = bySource[cfg.source] || [0, 1, 3, 4, 7];
    return idx.map((i) => D.TEMPLATES[i]).filter((t) => SEV.indexOf(t.sev) <= THRESH[cfg.threshold]);
  }
  /* ---- real workflow: upload to the API and follow its status ---- */
  const REAL_STAGE = { PENDING: 1, RUNNING: 2, LOGS_PARSED: 2, INCIDENTS_CLASSIFIED: 3, REMEDIATION_GENERATED: 4, COOKBOOK_GENERATED: 4 };
  const AGENT_STEP = { "Log Reader": "classify", Classifier: "classify", Remediation: "remediate", Cookbook: "cookbook", "Slack draft": "notify", "JIRA draft": "jira" };
  function setStep(run, i, status, msg) {
    const st = run.steps[i];
    if (status === "run" && st.status !== "run") { st.startedAt = Date.now(); st.ts = clock(); st.progress = Math.max(st.progress, 5); logLine(st.key, `${st.name} started`); }
    if ((status === "done" || status === "skip") && st.status !== status) { st.progress = 100; st.ts = clock(); if (st.dur == null) st.dur = st.startedAt ? (Date.now() - st.startedAt) / 1000 : 0; logLine(st.key, msg); }
    st.status = status; if (msg) st.msg = msg;
  }
  async function startRealRun() {
    const cfg = { ...S.cfg };
    const run = (S.run = { id: "ANL-…", real: true, file: S.upload.name, size: S.upload.size, lines: S.upload.lines ?? 0, cfg, tpls: [], crit: 0, state: "running",
      started: Date.now(), log: [], tokens: 0,
      steps: STEP_DEFS.map((d) => ({ ...d, role: d.key === "remediate" ? "RAG over runbooks (parallel)" : d.role(cfg, 0), status: "pend", progress: 0, msg: "Waiting", ts: null, dur: null, startedAt: null })) });
    setStep(run, 0, "run", "Uploading to the analysis API…");
    run.timer = setInterval(tick, 200);
    render();
    try {
      const fd = new FormData(); fd.append("file", S.upload.file, S.upload.name);
      const init = await apiFetch("/api/analyses/upload", { method: "POST", body: fd });
      if (S.run !== run) return;
      run.realId = init.analysis_id; run.id = displayId(init.analysis_id);
      saveMeta(run.realId, { env: cfg.env });
      setStep(run, 0, "done", `${run.file} · ${bytes(run.size)}`);
      logLine("orch", `Run ${run.id} accepted by the API`);
      render();
      const poll = async () => {
        if (S.run !== run || run.state !== "running") return;
        try { applyReal(run, await apiFetch(`/api/analyses/${run.realId}`)); } catch (e) { logLine("err", `Status check failed: ${e.message}`); }
        if (S.run === run && run.state === "running") run.poll = setTimeout(poll, 1000);
      };
      poll();
    } catch (e) {
      if (S.run === run) failRun(run.steps[0], `Upload failed: ${e.message}`);
    }
  }
  function applyReal(run, res) {
    run.result = res;
    const n = res.incidents.length, crit = res.incidents.filter((i) => i.severity === "CRITICAL").length;
    run.tpls = res.incidents.map((i) => ({ sev: SEV_FROM[i.severity], title: i.title })); run.crit = crit;
    run.lines = res.parsed_events.length || run.lines;
    run.tokens = (res.agent_runs || []).reduce((t, a) => t + a.prompt_tokens + a.completion_tokens, 0);
    if (res.status === "FAILED") return failRun(run.steps.find((x) => x.status === "run") || run.steps[2], res.error || "The workflow failed");
    const sources = new Set(res.remediation_plans.flatMap((x) => x.sources));
    const cb = res.cookbook;
    const msgs = {
      orch: ["Queued · planning the workflow", "multi-agent workflow (LangGraph)"],
      classify: ["Parsing logs and classifying incidents…", `${num(res.parsed_events.length)} events · ${n} incident${n === 1 ? "" : "s"} · ${crit} critical`],
      remediate: [`Retrieving runbooks and planning fixes for ${n} incident${n === 1 ? "" : "s"}…`, `${res.remediation_plans.length} plan(s) · ${sources.size} runbook(s) cited`],
      cookbook: ["Building the checklist…", `${cb ? cb.immediate_actions.length + cb.investigation_steps.length + cb.preventive_actions.length : 0} checklist actions`],
      notify: ["Drafting the Slack alert…", "Slack alert drafted · waiting for your approval"],
      jira: ["Drafting Jira tickets…", `${res.jira_tickets.length} Jira ticket draft(s) · waiting for your approval`],
    };
    const terminal = TERMINAL.has(res.status), none = res.status === "COMPLETED_NO_INCIDENTS";
    const stage = terminal ? 7 : (REAL_STAGE[res.status] ?? 1);
    run.steps.forEach((st, idx) => {
      if (idx === 0) return;
      const m = msgs[st.key];
      const skip = idx >= 3 ? skipReason(st.key, run.cfg) || (none ? "Skipped · no incidents detected" : null) : null;
      const active = stage === 4 ? idx >= 4 : idx === stage;
      if (idx < stage || terminal) { if (skip) setStep(run, idx, "skip", skip); else { if (st.status === "pend") setStep(run, idx, "run", m[0]); setStep(run, idx, "done", m[1]); } }
      else if (active) { if (skip) setStep(run, idx, "skip", skip); else setStep(run, idx, "run", m[0]); }
    });
    const durs = {};
    (res.agent_runs || []).forEach((a) => { const k = AGENT_STEP[a.agent]; if (k) durs[k] = (durs[k] || 0) + a.latency_ms / 1000; });
    run.steps.forEach((st) => { if (durs[st.key] != null && st.status === "done") st.dur = durs[st.key]; });
    if (terminal) {
      const g = res.guardrail_findings || [];
      if (g.length) logLine("orch", `Guardrails: ${g.map((f) => f.rule.replace(/_/g, " ")).join(", ")}`);
      return finishRun();
    }
    updateRunView(); updateChrome();
  }
  function commitReal(run) {
    const mapped = mapReal(run.result, { created_at: new Date(run.started).toISOString(), updated_at: new Date().toISOString() });
    mapped.issues = mapped.issues.filter((i) => SEV.indexOf(i.sev) <= THRESH[run.cfg.threshold]);
    upsertReal(mapped);
    return mapped.issues.length;
  }

  function startRun() {
    if (LIVE.ok && S.upload?.file) return startRealRun();
    const cfg = { ...S.cfg };
    const tpls = pickTemplates(cfg);
    const crit = tpls.filter((t) => t.sev === "Critical").length;
    const lines = S.upload.lines ?? Math.max(1, Math.round(S.upload.size / 110));
    S.run = {
      id: "ANL-" + S.seq.anl++, file: S.upload.name, size: S.upload.size, lines, cfg, tpls, crit, state: "running",
      started: Date.now(), log: [], failDone: false, tokens: 0,
      steps: STEP_DEFS.map((d) => ({ ...d, role: d.role(cfg, tpls.length), status: "pend", progress: 0, msg: "Waiting", ts: null, dur: null, startedAt: null })),
    };
    logLine("orch", `Run ${S.run.id} started · manual upload`);
    beginStep(0);
    S.run.timer = setInterval(tick, 200);
    render();
  }
  function stepMessages(step, run) {
    const n = run.tpls.length, c = run.crit, p = step.progress;
    switch (step.key) {
      case "upload": return p < 100 ? `Receiving ${run.file} · ${bytes(run.size)}` : `${run.file} · ${num(run.lines)} lines received`;
      case "orch": return p < 100 ? "Planning execution graph…" : `7-step plan · fan-out to ${n} remediation task${n === 1 ? "" : "s"}`;
      case "classify":
        if (p < 35) return `Parsing ${num(run.lines)} lines…`;
        if (p < 70) return "Extracting fields: timestamp, service, level, error_code";
        if (p < 100) return "Categorizing events into incidents…";
        return `${num(run.lines)} lines · ${n} incident${n === 1 ? "" : "s"} grouped · ${c} critical`;
      case "remediate": {
        if (p >= 100) return `${n} remediation plan${n === 1 ? "" : "s"} · ${new Set(run.tpls.map((t) => t.cat)).size} runbooks matched`;
        const k = Math.min(n - 1, Math.floor((p / 100) * n));
        return n ? `Analyzing INC-${k + 1} · ${run.tpls[k].title}…` : "No incidents to remediate";
      }
      case "cookbook": return p < 100 ? "Merging plans into one checklist…" : `${run.tpls.reduce((s, t) => s + t.steps.length, 0)} checklist steps across ${n} incidents`;
      case "notify": return p < 100 ? "Drafting the Slack alert…" : "Slack alert drafted · waiting for your approval";
      case "jira": return p < 100 ? `Drafting tickets for ${c} critical issue${c === 1 ? "" : "s"}…` : `${c} Jira ticket draft${c === 1 ? "" : "s"} · waiting for your approval`;
    }
    return "";
  }
  function skipReason(key, cfg) {
    if (key === "notify" && !cfg.slack) return "Skipped · Slack notifications disabled";
    if (key === "jira" && !cfg.jira) return "Skipped · Jira tickets disabled";
    if (key === "cookbook" && !cfg.checklist) return "Skipped · checklist disabled";
    return null;
  }
  function beginStep(i) {
    const run = S.run, step = run.steps[i];
    if (!step) return finishRun();
    const skip = skipReason(step.key, run.cfg);
    step.startedAt = Date.now(); step.ts = clock();
    if (skip) {
      Object.assign(step, { status: "skip", progress: 100, msg: skip, dur: 0 });
      logLine(step.key, skip);
      return beginStep(i + 1);
    }
    Object.assign(step, { status: "run", progress: 0 });
    step.msg = stepMessages(step, run);
    logLine(step.key, `${step.name} started`);
  }
  function tick() {
    const run = S.run; if (!run || run.state !== "running") return;
    if (run.real) {  // real run: status comes from the API; just animate the running cards
      run.steps.forEach((st) => { if (st.status === "run") st.progress = Math.min(92, st.progress + (92 - st.progress) * 0.04); });
      updateRunView(); updateChrome(); return;
    }
    const i = run.steps.findIndex((s) => s.status === "run");
    if (i < 0) return;
    const step = run.steps[i];
    step.progress = Math.min(100, step.progress + (200 / step.ms) * 100 * (0.75 + Math.random() * 0.5));
    if (["classify", "remediate", "cookbook", "notify", "jira"].includes(step.key)) run.tokens += 40 + Math.round(Math.random() * 60);
    const prevMsg = step.msg;
    if (run.cfg.fail && !run.failDone && step.key === "remediate" && step.progress >= 58) return failRun(step);
    step.msg = stepMessages(step, run);
    if (step.key === "remediate" && step.msg !== prevMsg && step.progress < 100) logLine("remediate", step.msg.replace(/…$/, ""));
    if (step.progress >= 100) {
      step.status = "done"; step.dur = (Date.now() - step.startedAt) / 1000; step.ts = clock();
      logLine(step.key, step.msg);
      beginStep(i + 1);
    }
    updateRunView();
    updateChrome();
  }
  function failRun(step, msg = "LLM request timed out after 30 s while mapping INC-3 (OpenRouter 504)") {
    const run = S.run;
    clearInterval(run.timer); clearTimeout(run.poll);
    Object.assign(step, { status: "fail", msg, ts: clock(), dur: step.startedAt ? (Date.now() - step.startedAt) / 1000 : 0 });
    run.state = "failed"; run.ended = Date.now();
    logLine("err", `${step.name} failed: ${msg}`);
    if (currentPage === "analysis") render(); else toast(`${run.file}: analysis stopped`, "Open New Analysis to retry", "bad");
    updateChrome();
  }
  function retryRun() {
    const run = S.run; if (!run) return;
    if (run.real) { clearInterval(run.timer); clearTimeout(run.poll); S.run = null; return startRealRun(); }
    run.failDone = true; run.state = "running";
    const i = run.steps.findIndex((s) => s.status === "fail");
    run.steps.slice(i).forEach((s) => Object.assign(s, { status: "pend", progress: 0, msg: "Waiting", ts: null, dur: null }));
    logLine("orch", `Retry requested · resuming from ${run.steps[i].name}`);
    beginStep(i);
    run.timer = setInterval(tick, 200);
    render();
  }
  function finishRun() {
    const run = S.run;
    clearInterval(run.timer);
    run.state = "done"; run.ended = Date.now();
    logLine("orch", `Analysis completed in ${((run.ended - run.started) / 1000).toFixed(1)}s`);
    clearTimeout(run.poll);
    const created = run.real ? commitReal(run) : commitResults(run);
    if (currentPage === "analysis") {
      render();
      setTimeout(() => {
        if (S.run !== run) return; // the user already moved on ("View results now")
        const pipe = $("#pipe");
        if (pipe) { pipe.style.maxHeight = pipe.scrollHeight + "px"; requestAnimationFrame(() => pipe.classList.add("collapsing")); }
        setTimeout(() => { if (S.run !== run) return; S.run = null; S.upload = null; go(`#/issues?analysis=${run.id}&new=1`); }, 650);
      }, 3000);
    } else {
      S.run = null; S.upload = null;
      toast(`${run.id} completed`, `${created} issues detected · open Detected Issues to review`);
    }
    updateChrome();
  }
  function commitResults(run) {
    const now = new Date();
    const stamp = D.fmt(now);
    const svc = run.cfg.service || run.file.replace(/\.[^.]+$/, "");
    const newIssues = run.tpls.map((t, k) => {
      const id = "ISS-" + S.seq.iss++;
      const issue = { ...t, id, service: k === 0 ? svc : t.service, env: run.cfg.env, source: run.cfg.source, analysis: run.id,
        detected: stamp, firstSeen: D.fmt(new Date(+now - 8 * 60000)), lastSeen: stamp, status: run.cfg.checklist ? "Remediation Suggested" : "Open",
        jiraKey: null, slackStatus: "Not sent", checklistDone: [], owners: {}, decision: "pending" };
      return issue;
    });
    D.issues.unshift(...newIssues);
    D.analyses.unshift({ id: run.id, file: run.file, service: svc, source: run.cfg.source, env: run.cfg.env, lines: run.lines, critical: run.crit,
      warnings: run.tpls.length - run.crit, status: run.crit ? "Awaiting Approval" : "Completed", uploaded: D.fmt(new Date(run.started)),
      started: D.fmt(new Date(run.started)), completed: stamp, duration: Math.round((run.ended - run.started) / 1000), failedAt: null });
    return newIssues.length;
  }
  function logLine(key, text) {
    const names = { upload: "Ingest", orch: "Orchestrator", classify: "Classifier", remediate: "Remediation", cookbook: "Cookbook", notify: "Notification", jira: "Jira", err: "Error" };
    S.run.log.push({ time: clock(), cls: key === "err" ? "c-err" : LOG_CLASS[key], who: names[key], text });
  }

  /* LLM-style status: one shimmering line for what is happening now, a short list of what is done. */
  const NOW_TEXT = { upload: "Uploading the log file", orch: "Planning the workflow", classify: "Reading logs and classifying incidents",
    remediate: "Searching runbooks for fixes", cookbook: "Synthesizing the remediation checklist", notify: "Drafting the Slack alert", jira: "Drafting Jira tickets" };
  const DONE_TEXT = { upload: "Uploaded", orch: "Started", classify: "Classified", remediate: "Remediation", cookbook: "Checklist", notify: "Slack", jira: "Jira" };
  function nowText(run) {
    const running = run.steps.filter((s) => s.status === "run");
    if (!running.length) return "Thinking";
    if (running.length > 1) return "Synthesizing the checklist and drafting Slack & Jira messages";
    return NOW_TEXT[running[0].key];
  }
  const secsSince = (t0, t1 = Date.now()) => ((t1 - t0) / 1000).toFixed(0) + "s";
  function runViewHtml() {
    const run = S.run;
    const failed = run.steps.find((s) => s.status === "fail");
    const banner = run.state === "failed"
      ? `<div class="banner bad" role="alert"><div class="bi">${icon("alert")}</div><div class="grow"><b>The analysis stopped at “${esc(NOW_TEXT[failed.key])}”</b>
          <p>${esc(failed.msg)}</p></div>
          <button class="btn ghost" data-action="back-to-upload">${icon("back")}Back to Upload</button><button class="btn primary" data-action="retry">${icon("refresh")}Retry</button></div>`
      : run.state === "done"
        ? `<div class="banner ok" role="status"><div class="bi">${icon("check")}</div><div class="grow"><b>Done · ${run.tpls.length} issue${run.tpls.length === 1 ? "" : "s"} found (${run.crit} critical)</b>
            <p>Finished in ${((run.ended - run.started) / 1000).toFixed(1)}s. Opening the results…</p></div>
            <button class="btn primary" data-action="view-new-results">View results now</button></div>`
        : "";
    return `
      <div class="titlebar"><div><h1>Analyzing ${esc(run.file)}</h1><p>${run.cfg.env} · ${bytes(run.size)}${run.real ? "" : " · demo simulation (API offline)"}</p></div></div>
      ${banner}
      <section class="card pipe think" id="pipe" aria-live="polite">
        <div class="now" id="thinkNow"></div>
        <ul class="tsteps" id="thinkSteps"></ul>
      </section>`;
  }
  function mountRunView() { S.run.stepsSig = ""; updateRunView(); }
  function updateRunView() {
    const run = S.run; if (!run || currentPage !== "analysis" || !$("#pipe")) return;
    const now = $("#thinkNow");
    if (run.state === "running") {
      const sub = run.steps.filter((s) => s.status === "run").map((s) => s.msg).filter(Boolean).join(" · ");
      now.innerHTML = `<span class="spin" aria-hidden="true"></span><div><div class="shimmer">${esc(nowText(run))}…</div><small>${esc(sub)}</small></div><span class="el" id="elapsed">${secsSince(run.started)}</span>`;
    } else if (run.state === "done") now.innerHTML = `<span class="okdot">${icon("check")}</span><div><div>Analysis complete</div><small>${run.tpls.length} issues · ${run.crit} critical</small></div><span class="el">${((run.ended - run.started) / 1000).toFixed(1)}s</span>`;
    else now.innerHTML = `<span class="baddot">${icon("x")}</span><div><div>Stopped</div><small>Use Retry, or go back and choose another file</small></div><span class="el">${secsSince(run.started, run.ended)}</span>`;
    const sig = run.steps.map((s) => s.status + s.msg).join("|");
    if (sig === run.stepsSig) return;
    run.stepsSig = sig;
    $("#thinkSteps").innerHTML = run.steps.filter((s) => ["done", "skip", "fail"].includes(s.status)).map((s) => `
      <li class="${s.status}"><span class="ti">${icon(s.status === "fail" ? "x" : s.status === "skip" ? "clock" : "check")}</span>
        <span><b>${DONE_TEXT[s.key]}</b> ${esc(s.msg)}</span><span class="el">${s.dur >= 0.1 && s.status === "done" ? s.dur.toFixed(1) + "s" : ""}</span></li>`).join("");
  }
  setInterval(() => { if (S.run && S.run.state === "running" && currentPage === "analysis" && $("#elapsed")) $("#elapsed").textContent = secsSince(S.run.started); }, 1000);

  /* ================= DETECTED ISSUES ================= */
  PAGES.issues = {
    title: "Detected Issues",
    html(params) {
      const f = fstate("issues");
      ["severity", "category", "service", "env", "status", "q", "analysis"].forEach((k) => { if (params.has(k)) f[k] = params.get(k); });
      if (!params.has("analysis") && params.toString()) f.analysis = f.analysis && params.has("new") ? f.analysis : "";
      tstate("issues").page = 1;
      const services = [...new Set(D.issues.map((i) => i.service))].sort();
      const fresh = params.get("new") && f.analysis ? D.issues.filter((i) => i.analysis === f.analysis) : null;
      return `
      <div class="titlebar"><div><h1>Detected Issues</h1><p>Incidents found in your log files, with the log evidence, suggested fixes and notification status</p></div></div>
      ${fresh ? `<div class="banner ok" role="status"><div class="bi">${icon("check")}</div><div class="grow"><b>${f.analysis} completed</b><p>${fresh.length} issues detected, ${fresh.filter((i) => i.sev === "Critical").length} critical. Click an issue to see the evidence and the suggested fix.</p></div>
        ${fresh.some((i) => i.sev === "Critical") ? `<a class="btn primary" href="#/approvals">${icon("send")}Review &amp; send to Slack / Jira</a>` : ""}</div>` : ""}
      <div class="kpis" id="sevChips"></div>
      <section class="card">
        <div class="toolbar">
          ${field("issues", "q", "search", "Search ID, title, service…")}
          ${field("issues", "severity", "select", { label: "Severity", values: SEV })}
          ${field("issues", "category", "select", { label: "Category", values: CATS })}
          ${field("issues", "service", "select", { label: "Service", values: services })}
          ${field("issues", "env", "select", { label: "Environment", values: D.ENVS })}
          ${field("issues", "status", "select", { label: "Status", values: ISS_STATUSES })}
          <span class="grow"></span>
          <span id="anlFilter" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"></span>
        </div>
        <div id="t-issues"></div>
      </section>`;
    },
    mount(params) {
      S.refresh.issues = () => {
        const f = fstate("issues"); const q = (f.q || "").toLowerCase();
        const base = D.issues.filter((i) => !f.analysis || i.analysis === f.analysis);
        const rows = base.filter((i) => (!f.severity || i.sev === f.severity) && (!f.category || i.cat === f.category) && (!f.service || i.service === f.service)
          && (!f.env || i.env === f.env) && (!f.status || i.status === f.status) && (!q || `${i.id} ${i.title} ${i.service} ${i.summary} ${i.analysis}`.toLowerCase().includes(q)));
        const counts = Object.fromEntries(SEV.map((s) => [s, base.filter((i) => i.sev === s).length]));
        $("#sevChips").innerHTML = SEV.map((s) => `<button class="kpi ${s === "Critical" && counts[s] ? "crit" : ""}" style="cursor:pointer;text-align:left;${f.severity === s ? "border-color:" + SEV_COLOR[s] : ""}" data-action="sev-filter" data-v="${s}" aria-pressed="${f.severity === s}">
          <div class="ic" style="background:${SEV_COLOR[s]}">${icon(s === "Low" ? "flag" : "alert")}</div><div><label>${s}</label><strong>${counts[s]}</strong><span>${s === "Critical" ? "needs immediate action" : "issues in view"}</span></div></button>`).join("");
        $("#anlFilter").innerHTML = f.analysis ? `<span class="chip">Only analysis <b>${esc(f.analysis)}</b></span><button class="btn ghost sm" data-action="clear-analysis">${icon("x")}Show issues from all analyses</button>` : "";
        const p = pager("issues", rows.length, 8);
        const filtered = Object.entries(f).some(([k, v]) => v && k !== "analysis");
        $("#t-issues").innerHTML = rows.length ? `<div class="tablewrap"><table>
          <thead><tr><th>Issue</th><th>Severity</th><th>Service / log source</th><th>Category / detected</th><th>Confidence</th><th>Status</th><th>Actions</th></tr></thead>
          <tbody>${rows.slice(...p.slice).map((i) => `<tr class="${i.sev === "Critical" && !["Resolved", "False Positive"].includes(i.status) ? "crit-row" : ""}">
            <td class="issue-col"><div class="t1"><span class="id" style="color:#7fb2ff">${i.id}</span>${LIVE.ok && !i.real ? ` <span class="tag">sample</span>` : ""} · ${esc(i.title)}</div><div class="t2 clamp" title="${esc(i.summary)}">${esc(i.summary)}</div></td>
            <td>${sevBadge(i.sev)}</td><td><div class="t1">${esc(i.service)}</div><div class="t2">${esc(i.source)}</div></td>
            <td class="nowrap"><div class="t1">${i.cat}</div><div class="t2 num">${short(i.detected)}</div></td>
            <td><div class="conf"><div class="bar"><i style="width:${Math.round(i.confidence * 100)}%"></i></div><span class="num">${Math.round(i.confidence * 100)}%</span></div></td>
            <td class="wrapst">${stBadge(i.status, ISS_CLASS)}</td>
            <td><div class="acts"><button class="act primary" data-action="open-issue" data-id="${i.id}" aria-label="View details for ${i.id}">${icon("eye")}Details</button>
              <button class="act" data-action="open-issue" data-tab="cookbook" data-id="${i.id}" title="Generate checklist" aria-label="Generate checklist for ${i.id}">${icon("list")}</button>
              <button class="act" data-action="open-issue" data-tab="send" data-id="${i.id}" title="Send to Slack & Jira" aria-label="Send ${i.id} to Slack and Jira">${icon("send")}</button></div></td></tr>`).join("")}</tbody></table></div>${p.html}`
          : f.analysis && !base.length
            ? emptyState(`No issues detected in ${f.analysis}.`, "The agents found no incidents above the severity threshold for this run.", "shield", `<button class="btn ghost sm" data-action="clear-analysis">Show all issues</button>`)
          : f.severity === "Critical"
            ? emptyState("No critical issues detected in the selected time range.", "Adjust the filters or upload new logs to analyse.", "shield", `<button class="btn ghost sm" data-action="clear-issue-filters">Clear filters</button>`)
            : emptyState(filtered ? "No issues match these filters." : "No issues yet", filtered ? "Try a different severity, service or status." : "Upload a log file to begin a new analysis.", "search",
              filtered ? `<button class="btn ghost sm" data-action="clear-issue-filters">Clear filters</button>` : `<button class="btn primary sm" data-action="new-analysis">New Analysis</button>`);
      };
      S.refresh.issues();
      if (params.get("open")) openIssue(params.get("open"), params.get("tab") || "summary");
    },
  };

  /* ---- issue drawer ---- */
  const TABS = [["summary", "Summary"], ["evidence", "Log Evidence"], ["remediation", "Remediation"], ["cookbook", "Checklist"], ["send", "Slack & Jira"]];
  function openIssue(id, tab = "summary") { S.drawer = { type: "issue", id, tab }; renderDrawer(); }
  function closeDrawer() { S.drawer = null; $("#drawerRoot").innerHTML = ""; document.body.style.overflow = ""; }
  function renderDrawer() {
    const root = $("#drawerRoot");
    if (!S.drawer) return closeDrawer();
    document.body.style.overflow = "hidden";
    let inner = "";
    if (S.drawer.type === "issue") inner = issueDrawer(D.issues.find((i) => i.id === S.drawer.id));
    root.innerHTML = `<div class="overlay" data-action="close-drawer"></div><aside class="drawer" role="dialog" aria-modal="true" aria-label="Details">${inner}</aside>`;
    $(".drawer [data-autofocus]", root)?.focus();
  }
  const highlight = (line) => esc(line)
    .replace(/\b(ERROR|CRITICAL|FATAL|OOMKilled|ECONNREFUSED|DB_TIMEOUT|DB_DEADLOCK|deadlock|timed out|timeout|i\/o timeout|refused|failed|Failed|AccessDenied|HTTP 500|504 Gateway Timeout|404|AUTH_401|cancelled)\b/g, "<mark>$1</mark>")
    .replace(/\b(WARN|WARNING)\b/g, '<mark class="w">$1</mark>');
  function parseEvidence(line, issue) {
    const m = line.match(/^(\S+)\s+(\S+)\s+(\S+)\s+(.*)$/) || [];
    const code = (line.match(/error_code=(\S+)/) || [])[1] || (/(ERROR|CRITICAL)/.test(line) ? issue.code : "—");
    return { ts: m[1] || "—", sev: m[2] || "—", service: m[3] || issue.service, host: issue.host, code, message: (m[4] || line).replace(/error_code=\S+\s?/, "") };
  }
  function slackPreview(i, channel, draft) {
    const d = draft || {};
    return `<div class="slack"><div class="sh">${icon("slack", "width:14px;height:14px")}<b style="color:#d1d2d3">${esc(channel)}</b> · Incident Bot (preview)</div>
      <div class="msgrow"><div class="bot">${icon("hex")}</div><div>
        <div><span class="nm">Incident Bot</span><span class="app">APP</span><span class="tm">${clock().slice(0, 5)}</span></div>
        <div style="margin-top:2px">🚨 <b style="color:#f8f8f8">[${i.sev.toUpperCase()}] ${esc(d.headline || i.title)}</b></div>
        <div class="att" style="border-left-color:${SEV_COLOR[i.sev]}">
          <div class="f"><div><span>Severity</span>${i.sev}</div><div><span>Affected service</span>${esc(i.service)} (${i.env})</div></div>
          <div><span style="font-weight:700;color:#f8f8f8">Root cause</span><br>${esc(d.cause || i.rationale || i.summary)}</div>
          <div><span style="font-weight:700;color:#f8f8f8">Recommended remediation</span><br>${(i.actions?.length ? i.actions.slice(0, 3) : [i.fix]).map((a) => "• " + esc(a)).join("<br>")}</div>
          <div><a class="sl" href="#/issues?open=${i.id}">View full analysis (${i.analysis})</a>${i.jiraKey ? ` · <a class="sl" data-action="jira-link" data-key="${i.jiraKey}">Jira ${i.jiraKey}</a>` : " · Jira ticket not created yet"}</div>
        </div></div></div></div>`;
  }
  function issueDrawer(i) {
    if (!i) return `<div class="dbody">${emptyState("Issue not found", "It may have been removed.")}</div>`;
    const tab = S.drawer.tab;
    const done = new Set(i.checklistDone);
    let body = "";
    if (tab === "summary") body = `
      <div class="kv">
        <div><span>Severity</span><b>${sevBadge(i.sev)}</b></div><div><span>Category</span><b>${i.cat}</b></div>
        <div><span>Affected service</span><b>${esc(i.service)}</b></div><div><span>Environment</span><b>${envCell(i.env)}</b></div>
        <div><span>First occurrence</span><b class="num">${i.firstSeen}</b></div><div><span>Last occurrence</span><b class="num">${i.lastSeen}</b></div>
        <div><span>Matching log events</span><b class="num">${num(i.events)}</b></div><div><span>Confidence</span><b>${Math.round(i.confidence * 100)}%</b></div>
        <div><span>Log source</span><b>${esc(i.source)}</b></div><div><span>Linked analysis</span><b>${i.analysis}</b></div>
      </div>
      <div class="box ai"><h4>${icon("bolt", "width:14px;height:14px;vertical-align:-2px")} Root-cause summary</h4><p>${esc(i.summary)}</p></div>
      <div class="box"><h4>Status</h4><p>${stBadge(i.status, ISS_CLASS)} ${i.approved ? `<span class="chip ok" style="margin-left:6px">Remediation approved by ${esc(i.approved)}</span>` : ""}${i.jiraKey ? `<span class="chip" style="margin-left:6px">Jira <b>${i.jiraKey}</b></span>` : ""}</p></div>`;
    if (tab === "evidence") {
      const parsed = i.logs.map((l) => parseEvidence(l, i));
      body = `<div><div class="fl" style="margin-bottom:8px">Raw log lines · ${i.logs.length} shown of ${num(i.events)} matching events</div>
        <div class="code" role="region" aria-label="Log evidence">${i.logs.map((l, k) => `<div><span class="ln">${k + 1}</span><span>${highlight(l)}</span></div>`).join("")}</div></div>
        <div><div class="fl" style="margin-bottom:8px">Extracted fields</div><div class="fields">${parsed.map((p, k) => `<div class="fcard">
          <div class="fgrid"><div><span>Line</span>${k + 1}</div><div><span>Timestamp</span>${esc(p.ts.replace("T", " ").replace("Z", ""))}</div><div><span>Severity</span>${esc(p.sev)}</div>
            <div><span>Service</span>${esc(p.service)}</div><div><span>Host</span>${esc(p.host)}</div><div><span>Error code</span>${esc(p.code)}</div></div>
          <div class="fmsg"><span>Message</span>${esc(p.message)}</div></div>`).join("")}</div></div>`;
    }
    if (tab === "remediation") body = `
      <div class="box ai"><h4>Recommended fix</h4>${i.actions?.length
        ? `<ol style="padding-left:18px;display:grid;gap:5px">${i.actions.map((a) => `<li>${(i.flagged || []).includes(a) ? "⚠️ <b>Review first:</b> " : ""}${esc(a)}</li>`).join("")}</ol>`
        : `<p>${esc(i.fix)}</p>`}${i.sources?.length ? `<p class="muted" style="margin-top:8px;font-size:12.5px">Runbooks cited: ${esc(i.sources.join(", "))}</p>` : ""}</div>
      <div class="box"><h4>Rationale</h4><p>${esc(i.rationale)}</p></div>
      <div class="kv"><div><span>Risk level</span><b>${sevBadge(i.risk === "Medium" ? "Medium" : i.risk === "High" ? "High" : "Low").replace(/>(\w+)</, `>${i.risk} risk<`)}</b></div><div><span>Estimated effort</span><b>${esc(i.effort)}</b></div>
        <div style="grid-column:1/-1"><span>Required permissions</span><b>${esc(i.perms)}</b></div></div>
      <div class="box"><h4>Rollback plan</h4><p>${esc(i.rollback)}</p></div>
      ${i.approved ? `<div class="banner ok"><div class="bi">${icon("check")}</div><div class="grow"><b>Remediation approved</b><p>By ${esc(i.approved)} · recorded in the audit trail (simulated)</p></div></div>` : ""}`;
    if (tab === "cookbook") body = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap"><div class="fl">${done.size} of ${i.steps.length} steps done</div>
        <div class="pbar" style="max-width:220px"><i style="width:${(done.size / i.steps.length) * 100}%;background:linear-gradient(90deg,#14b8a6,#22c55e)"></i></div></div>
      <div class="check">${i.steps.map(([step, outcome], k) => `<div class="step ${done.has(k) ? "done" : ""}">
        <input type="checkbox" id="st-${k}" data-action="check-step" data-k="${k}" ${done.has(k) ? "checked" : ""}>
        <div><label for="st-${k}" class="st-t">${k + 1}. ${esc(step)}</label>${outcome ? `<div class="st-o">Expected outcome: ${esc(outcome)}</div>` : ""}
          <input class="inp owner" list="people" placeholder="Assign owner" aria-label="Owner for step ${k + 1}" data-owner="${k}" value="${esc(i.owners[k] || "")}"></div></div>`).join("")}</div>
      <datalist id="people">${D.people.filter((p) => p !== "Unassigned").map((p) => `<option value="${p}">`).join("")}</datalist>`;
    if (tab === "send") body = approvalCard(i, true);
    const foot = {
      remediation: `<button class="btn ghost" data-action="false-positive" ${i.status === "False Positive" ? "disabled" : ""}>${icon("flag")}Mark as False Positive</button><button class="btn success" data-action="approve-remediation" ${i.approved ? "disabled" : ""}>${icon("check")}Approve Remediation</button>`,
      cookbook: `<button class="btn ghost" data-action="copy-checklist">${icon("copy")}Copy Checklist</button><button class="btn primary" data-action="export-md">${icon("dl")}Export as Markdown</button>`,
    }[tab] || "";
    return `
      <div class="dhead"><div class="row"><div style="flex:1;min-width:0"><div class="muted" style="font-size:12.5px">${i.id} · ${i.analysis}</div><h2>${esc(i.title)}</h2></div>
        <button class="iconbtn" data-action="close-drawer" aria-label="Close details" data-autofocus>${icon("x")}</button></div>
        <div class="meta">${sevBadge(i.sev)}${stBadge(i.status, ISS_CLASS)}<span>${esc(i.service)}</span><span>·</span><span>${i.cat}</span><span>·</span><span>${short(i.detected)}</span></div>
        <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button role="tab" aria-selected="${k === tab}" class="${k === tab ? "on" : ""}" data-action="tab" data-tab="${k}">${l}</button>`).join("")}</div></div>
      <div class="dbody" role="tabpanel">${body}</div>
      ${foot ? `<div class="dfoot">${foot}</div>` : ""}`;
  }
  const currentIssue = () => D.issues.find((i) => i.id === S.drawer?.id);
  function checklistMd(i) {
    return `# Remediation checklist — ${i.id}: ${i.title}\n\n- Severity: ${i.sev}\n- Service: ${i.service} (${i.env})\n- Analysis: ${i.analysis}\n\n## Steps\n\n` +
      i.steps.map(([s, o], k) => `- [${i.checklistDone.includes(k) ? "x" : " "}] ${s}${i.owners[k] ? ` _(owner: ${i.owners[k]})_` : ""}\n  - Expected outcome: ${o}`).join("\n") +
      `\n\n## Rollback\n\n${i.rollback}\n`;
  }

  /* ================= REMEDIATION PLAYBOOKS ================= */
  PAGES.playbooks = {
    title: "Remediation Playbooks",
    html() {
      return `<div class="titlebar"><div><h1>Remediation Playbooks</h1><p>Runbooks the Remediation Agent retrieves and cites when mapping fixes</p></div>
        <div class="tb-actions">${field("pb", "q", "search", "Search playbooks…")}</div></div><div class="grid3" id="t-pb"></div>`;
    },
    mount() {
      S.refresh.pb = () => {
        const q = (fstate("pb").q || "").toLowerCase();
        const rows = D.playbooks.filter((p) => !q || `${p.name} ${p.cat} ${p.steps.join(" ")}`.toLowerCase().includes(q));
        $("#t-pb").innerHTML = rows.length ? rows.map((p) => `<article class="card pb"><div class="ph"><div class="tile">${icon("book")}</div><div><h3>${esc(p.name)}</h3><div class="muted" style="font-size:12px">${p.cat}</div></div></div>
          <ol>${p.steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
          <div class="pm"><span>${p.runs} uses</span><span style="color:${p.success >= 95 ? "#4ade80" : "#fbbf24"}">${p.success}% success</span><span>Updated ${p.updated}</span></div></article>`).join("")
          : `<div class="card" style="grid-column:1/-1">${emptyState("No playbooks match your search.", "Try another keyword, such as “redis” or “certificate”.")}</div>`;
      };
      S.refresh.pb();
    },
  };

  /* ================= AGENT ACTIVITY (historical runs only) ================= */
  const TRACE_AGENTS = [
    ["Orchestrator", "LangGraph workflow controller"], ["Log Reader / Classifier Agent", "Parses logs, extracts fields, categorizes"],
    ["Remediation Agent", "Maps issues to fixes using runbooks (RAG)"], ["Cookbook Synthesizer Agent", "Builds actionable checklist"],
    ["Notification Agent", "Drafts Slack alert"], ["Jira Ticket Agent", "Creates tickets for critical issues"],
  ];
  function buildTrace(a) {
    if (a.real) {
      const runs = a.agentRuns || [];
      if (!runs.length) return [{ name: "Workflow", role: "", inp: a.file, out: "—", reason: "", dur: 0, state: a.status === "Failed" ? "f" : "ok", error: a.error }];
      return runs.map((x) => ({ name: x.agent, role: x.models.length ? x.models.join(", ") : "deterministic step",
        inp: x.agent === "Log Reader" ? `${a.file} (${num(a.lines)} lines)` : "Workflow state from the previous step",
        out: x.fallback ? "Rule-based fallback output" : x.llm_calls ? `${x.llm_calls} validated LLM response(s)` : "Deterministic output",
        reason: `llm_calls=${x.llm_calls} · tokens=${x.prompt_tokens + x.completion_tokens} · cost=$${x.cost_usd.toFixed(4)}`,
        dur: +(x.latency_ms / 1000).toFixed(1), state: "ok", error: x.fallback ? "The LLM call failed or returned invalid JSON; a rule-based fallback was used." : null }));
    }
    const k = a.critical + a.warnings, total = a.duration || 30;
    const share = [0.06, 0.3, 0.3, 0.14, 0.08, 0.12];
    const io = [
      [`${a.file} (${num(a.lines)} lines)`, `Plan: 6 agents, fan-out to ${k} remediation tasks`, `Planned graph parse → classify → remediate ×${k} → cookbook ∥ notify ∥ jira.\nHuman approval gate inserted before delivery.`],
      [`${num(a.lines)} raw lines`, `${k} incidents · ${a.critical} critical`, `format=key-value · levels normalized (WARN→WARNING)\nsecrets redacted: 2 · prompt-injection lines: 0\nmodel=gpt-4o-mini prompt_tokens=1342 completion_tokens=486`],
      [`${k} incidents + evidence`, `${k} plans · runbooks: database-timeout.md, kubernetes-pod-restart.md`, `retrieval top_k=4, max_distance=0.6\nsources limited to retrieved runbooks · destructive actions flagged: 0\nmodel=gpt-4o (parallel ×${Math.min(k, 4)})`],
      [`${k} remediation plans`, `${k * 3} checklist steps`, `merged and de-duplicated actions; ordered by severity, then dependency`],
      [`Incidents + first actions`, `Slack draft for #sre-incidents (918 chars)`, `outbound guardrails: secrets redacted, @channel blocked\nmodel=gpt-4o-mini`],
      [`${a.critical} critical incidents`, `${a.critical} ticket${a.critical === 1 ? "" : "s"} drafted`, `priority mapped from severity (Critical→Highest) · labels sanitized`],
    ];
    const failIdx = a.failedAt === "Remediation Agent" ? 2 : a.failedAt === "Jira Ticket Agent" ? 5 : -1;
    return TRACE_AGENTS.map(([name, role], idx) => ({ name, role, inp: io[idx][0], out: failIdx >= 0 && idx >= failIdx ? "—" : io[idx][1], reason: io[idx][2],
      dur: failIdx >= 0 && idx > failIdx ? null : +(total * share[idx]).toFixed(1),
      state: failIdx === idx ? "f" : failIdx >= 0 && idx > failIdx ? "s" : "ok",
      error: failIdx === idx ? (idx === 2 ? "LLM request timed out after 30 s (OpenRouter 504) while mapping INC-3; retries exhausted." : "Jira REST API returned 400: field 'priority' is not on the create screen of project OPS.") : null }));
  }
  PAGES.agents = {
    title: "Agent Activity",
    html(params) {
      if (params.get("run")) { S.expanded = params.get("run"); fstate("agents").q = ""; }
      const runs = D.analyses.filter((a) => a.status !== "Processing");
      const failed = runs.filter((a) => a.status === "Failed").length;
      const avg = runs.reduce((s, a) => s + (a.duration || 0), 0) / runs.length;
      return `<div class="titlebar"><div><h1>Agent Activity</h1><p>Past workflow runs and what each agent did. Live progress appears on New Analysis while a run is in progress.</p></div></div>
        <section class="kpis">
          <div class="kpi"><div class="ic" style="background:#2563eb">${icon("orch")}</div><div><label>Workflow runs</label><strong>${runs.length}</strong><span>completed or failed</span></div></div>
          <div class="kpi"><div class="ic" style="background:#16a34a">${icon("check")}</div><div><label>Success rate</label><strong>${Math.round(((runs.length - failed) / runs.length) * 100)}%</strong><span>${failed} failed runs</span></div></div>
          <div class="kpi"><div class="ic" style="background:#7c3aed">${icon("clock")}</div><div><label>Avg run duration</label><strong>${avg.toFixed(1)}s</strong><span>upload to drafts</span></div></div>
          <div class="kpi"><div class="ic" style="background:#0d9488">${icon("agent")}</div><div><label>Agents per run</label><strong>6</strong><span>+ human approval gate</span></div></div>
        </section>
        <section class="card"><div class="toolbar">${field("agents", "q", "search", "Search runs…")}${field("agents", "status", "select", { label: "Status", values: ["Completed", "Awaiting Approval", "Failed"] })}${field("agents", "env", "select", { label: "Environment", values: D.ENVS })}</div><div id="t-agents"></div></section>`;
    },
    mount() {
      S.refresh.agents = () => {
        const f = fstate("agents"); const q = (f.q || "").toLowerCase();
        const rows = D.analyses.filter((a) => a.status !== "Processing" && (!f.status || a.status === f.status) && (!f.env || a.env === f.env) && (!q || `${a.id} ${a.file}`.toLowerCase().includes(q)));
        if (S.expanded) { const idx = rows.findIndex((r) => r.id === S.expanded); if (idx >= 0) tstate("agents").page = Math.floor(idx / 8) + 1; }
        const p = pager("agents", rows.length, 8);
        $("#t-agents").innerHTML = rows.length ? `<div class="tablewrap"><table>
          <thead><tr><th>Analysis ID</th><th>Log file</th><th>Environment</th><th>Started at</th><th>Completed at</th><th>Status</th><th>Agents executed</th><th>Critical</th><th>Actions</th></tr></thead>
          <tbody>${rows.slice(...p.slice).map((a) => {
            const tr = buildTrace(a); const ran = tr.filter((t) => t.state !== "s").length; const open = S.expanded === a.id;
            return `<tr id="run-${a.id}"><td class="id">${a.id}</td><td class="trunc" title="${esc(a.file)}">${esc(a.file)}</td><td>${envCell(a.env)}</td><td class="nowrap num" title="${a.started}">${short(a.started)}</td><td class="nowrap num" title="${a.completed || ""}">${short(a.completed)}</td>
              <td>${stBadge(a.status, ANL_CLASS)}</td><td class="num">${ran} / ${a.real ? tr.length : 6}</td><td class="num" style="color:${a.critical ? "#f87171" : ""}">${a.critical}</td>
              <td><div class="acts"><button class="act ${open ? "primary" : ""}" data-action="toggle-trace" data-id="${a.id}" aria-expanded="${open}">${icon("orch")}${open ? "Hide trace" : "View trace"}</button>
                <button class="act" data-action="view-report" data-id="${a.id}">${icon("eye")}View results</button></div></td></tr>
              ${open ? `<tr class="expand"><td colspan="9"><div class="trace">${tr.map((t) => `<div class="tstep ${t.state === "f" ? "f" : t.state === "s" ? "s" : ""}"><div class="td">${icon(t.state === "f" ? "x" : t.state === "s" ? "clock" : "check")}</div>
                <div class="tb"><div class="tt"><div><b>${t.name}</b> <span class="muted" style="font-size:12px">· ${t.role}</span></div><span class="muted num">${t.state === "s" ? "Skipped" : t.dur + "s"}</span></div>
                <div class="io"><div><span>Inputs received</span>${esc(t.inp)}</div><div><span>Outputs produced</span>${esc(t.out)}</div></div>
                ${t.error ? `<div class="er"><b>Error:</b> ${esc(t.error)}</div>` : ""}${t.state !== "s" ? `<div class="rs">${esc(t.reason)}</div>` : ""}</div></div>`).join("")}</div></td></tr>` : ""}`;
          }).join("")}</tbody></table></div>${p.html}`
          : emptyState("No workflow runs match these filters.", "Completed and failed analyses appear here.", "agent");
        if (S.expanded) document.getElementById("run-" + S.expanded)?.scrollIntoView({ block: "nearest" });
      };
      S.refresh.agents();
    },
  };

  /* ================= APPROVALS (Slack + Jira) ================= */
  // One decision per critical issue: "Send" creates the Jira ticket and posts the Slack alert; "Don't send" closes it.
  const priorityFor = (sev) => ({ Critical: "Highest", High: "High", Medium: "Medium", Low: "Low" })[sev];
  const ticketDraft = (i) => i.jiraDraft || { title: `[${i.sev}] ${i.title}`, desc: `Summary\n${i.summary}\n\nRecommended fix\n${i.fix}\n\nRollback\n${i.rollback}`, priority: priorityFor(i.sev) };
  const approvalScope = () => D.issues.filter((i) => (LIVE.ok ? i.real : !i.real));
  const pendingCritical = () => approvalScope().filter((i) => i.sev === "Critical" && (i.decision || "pending") === "pending" && i.status !== "Resolved");
  function approvalCard(i, inDrawer = false) {
    const d = i.decision || "pending", j = ticketDraft(i);
    const ch = LIVE.ok ? LIVE.slack.channel : "#sre-incidents", proj = LIVE.ok ? LIVE.jira.project_key : "OPS";
    const jiraLink = i.jiraKey ? (i.jiraUrl && !i.jiraKey.includes("-SIM-") ? `<a class="link" href="${esc(i.jiraUrl)}" target="_blank" rel="noopener">${i.jiraKey} ${icon("ext", "width:13px;height:13px")}</a>` : `<b>${i.jiraKey}</b>`) : "";
    const outcome = d === "sent"
      ? `<div class="outcome ok">${icon("check")}<span><b>Sent.</b> Slack: ${esc(i.slackStatus === "Not sent" ? "not posted" : i.slackStatus.toLowerCase())}${i.jiraKey ? ` · Jira: ${jiraLink}` : " · Jira: no ticket"}</span>
          ${i.jiraKey && i.slackStatus !== "Not sent" ? "" : `<button class="btn ghost sm" data-action="decide-send" data-id="${i.id}">${icon("refresh")}Retry</button>`}</div>`
      : d === "dismissed"
        ? `<div class="outcome off">${icon("x")}<span><b>Not sent.</b> You chose not to notify anyone about this issue.</span><button class="btn ghost sm" data-action="decide-send" data-id="${i.id}">${icon("send")}Send anyway</button></div>`
        : `<div class="decide"><span class="muted">Notify the team about this issue?</span>
            <button class="btn ghost" data-action="decide-dismiss" data-id="${i.id}">${icon("x")}Don’t send</button>
            <button class="btn primary" data-action="decide-send" data-id="${i.id}">${icon("send")}Send to Slack &amp; Jira</button></div>`;
    return `<article class="appr ${d}" id="appr-${i.id}">
      ${inDrawer ? "" : `<div class="ah2">${sevBadge(i.sev)}<button class="link at" data-action="open-issue" data-id="${i.id}">${esc(i.title)}</button><span class="muted id">${i.id}</span></div>
      <p class="muted">${esc(i.service)} · ${i.cat} · ${short(i.detected)}</p>`}
      <p class="why"><b>Likely cause:</b> ${esc(i.rationale || i.summary)}</p>
      ${d === "pending" ? `<div class="will"><span>${icon("slack")}Slack alert to <b>${esc(ch)}</b></span><span>${icon("ticket")}Jira ticket in <b>${esc(proj)}</b> · ${j.priority}</span></div>` : ""}
      <details class="prev"${inDrawer ? " open" : ""}><summary>Preview what will be sent</summary>
        <div class="prevgrid"><div><div class="fl">Slack message</div>${slackPreview(i, ch)}</div>
          <div><div class="fl">Jira ticket</div><div class="jprev"><b>${esc(j.title)}</b><span class="muted">Project ${esc(proj)} · Priority ${esc(j.priority)}</span><pre>${esc(j.desc)}</pre></div></div></div></details>
      ${outcome}
    </article>`;
  }
  PAGES.approvals = {
    title: "JIRA/SLACK Notification",
    html() {
      const mode = LIVE.ok && (LIVE.slack.mode !== "live" || LIVE.jira.mode !== "live")
        ? `<div class="banner info"><div class="bi">${icon("shield")}</div><div class="grow"><b>Test mode</b><p>${LIVE.slack.mode !== "live" ? "Slack" : ""}${LIVE.slack.mode !== "live" && LIVE.jira.mode !== "live" ? " and " : ""}${LIVE.jira.mode !== "live" ? "Jira" : ""} ${LIVE.slack.mode !== "live" && LIVE.jira.mode !== "live" ? "are" : "is"} in simulation mode, so nothing real is sent. Set SLACK_MODE / JIRA_MODE=live in .env and restart the API.</p></div></div>` : "";
      return `<div class="titlebar"><div><h1>JIRA/SLACK Notification</h1><p>Nothing is sent automatically. For each critical issue, choose <b>Send</b> (creates a Jira ticket${LIVE.ok ? ` in ${esc(LIVE.jira.project_key)}` : ""} and posts an alert to ${esc(LIVE.ok ? LIVE.slack.channel : "Slack")}) or <b>Don’t send</b>.</p></div></div>
        ${LIVE.ok ? "" : `<div class="banner info"><div class="bi">${icon("flag")}</div><div class="grow"><b>Demo data</b><p>The API is offline, so these are sample issues and nothing is sent. Start it with <code>make api</code>.</p></div></div>`}
        ${mode}
        <div class="seg tabsel" role="tablist" id="apprTabs"></div>
        <div id="t-appr"></div>`;
    },
    mount() {
      S.refresh.appr = () => {
        const f = fstate("appr", { show: "waiting" });
        const scope = approvalScope().filter((i) => i.sev === "Critical");
        const waiting = scope.filter((i) => (i.decision || "pending") === "pending").length;
        $("#apprTabs").innerHTML = [["waiting", `Waiting for you (${waiting})`], ["done", `Decided (${scope.length - waiting})`]].map(([k, l]) => `<button role="tab" aria-selected="${f.show === k}" class="${f.show === k ? "on" : ""}" data-action="appr-show" data-v="${k}">${l}</button>`).join("");
        const rows = approvalScope().filter((i) => i.sev === "Critical" && ((i.decision || "pending") === "pending") === (f.show === "waiting"));
        const groups = [...new Set(rows.map((i) => i.analysis))].map((id) => [D.analyses.find((a) => a.id === id) || { id, file: "" }, rows.filter((i) => i.analysis === id)]);
        $("#t-appr").innerHTML = groups.length ? groups.map(([a, list]) => `<section class="card agroup">
            <div class="h"><div><h2>${esc(a.file || a.id)}</h2><div class="sub">${a.id}${a.uploaded ? ` · analyzed ${short(a.uploaded)}` : ""} · ${list.length} critical issue${list.length === 1 ? "" : "s"}</div></div>
              <button class="link" data-action="view-report" data-id="${a.id}">All issues in this log ${icon("ext")}</button></div>
            <div class="alist">${list.map((i) => approvalCard(i)).join("")}</div></section>`).join("")
          : `<section class="card">${f.show === "waiting"
            ? emptyState("You’re all caught up", "No critical issues are waiting for a decision. Run a new analysis to check another log.", "check", `<button class="btn primary sm" data-action="new-analysis">New Analysis</button>`)
            : emptyState("No decisions yet", "Issues you send or dismiss appear here.", "send")}</section>`;
      };
      S.refresh.appr();
    },
  };
  async function decide(i, send) {
    if (!i.real) {  // sample data: record the choice locally only
      i.decision = send ? "sent" : "dismissed";
      if (send) { i.slackStatus = "Sent (simulated)"; i.jiraKey ||= "OPS-" + S.seq.ops++; i.status = "Ticket Created"; }
      toast(send ? "Demo only · nothing was sent" : "Marked as not sent", send ? "Sample issues never reach Slack or Jira" : i.id, "warn");
      return;
    }
    const path = `/api/analyses/${i.realAnalysisId}/incidents/${i.incidentId}/${send ? "send" : "dismiss"}`;
    const res = await apiFetch(path, { method: "POST", json: { actor: "ui-user" } });
    if (!send) { i.decision = "dismissed"; toast("Not sent", `${i.title}`); }
    else {
      const t = res.jira, sl = res.slack;
      const ticketOk = ["CREATED", "SIMULATED"].includes(t.delivery_status), slackOk = ["SENT", "SIMULATED"].includes(sl.delivery_status);
      if (ticketOk) { i.jiraKey = t.issue_key; i.jiraUrl = t.issue_url; i.status = "Ticket Created"; removeWhere(D.tickets, (x) => x.issueId === i.id); D.tickets.unshift(ticketRow(i, t)); }
      i.slackStatus = slackLabel(sl.delivery_status) || "Not sent";
      i.decision = ticketOk || slackOk ? "sent" : "pending";
      const sim = t.mode !== "live" || sl.mode !== "live";
      if (ticketOk && slackOk) toast(sim ? "Sent (test mode)" : `Sent · ${t.issue_key} created`, sim ? "Simulation mode in .env: nothing real was sent" : `Posted to ${sl.channel}`, sim ? "warn" : "ok");
      else toast("Only partly sent", [ticketOk ? "" : `Jira: ${t.delivery_error || "failed"}`, slackOk ? "" : `Slack: ${sl.delivery_error || "failed"}`].filter(Boolean).join(" · "), "bad");
    }
    const a = D.analyses.find((x) => x.realId === i.realAnalysisId);
    if (a && res.analysis_status === "COMPLETED") a.status = "Completed";
  }

  /* ================= REPORTS ================= */
  function stackedBars(days) {
    const W = 640, H = 230, L = 30, B = 24, T = 8, keys = ["critical", "high", "medium", "low"], cols = ["#ef4444", "#f97316", "#f59e0b", "#3b82f6"];
    const max = Math.max(...days.map((d) => keys.reduce((s, k) => s + d[k], 0)));
    const top = Math.ceil(max / 5) * 5, bw = (W - L) / days.length;
    let g = "";
    for (let v = 0; v <= top; v += 5) { const y = T + (H - B - T) * (1 - v / top); g += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="#1f2c48"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${v}</text>`; }
    days.forEach((d, i) => {
      let y = H - B;
      keys.forEach((k, j) => { const h = ((H - B - T) * d[k]) / top; y -= h; g += `<rect x="${L + i * bw + bw * 0.18}" y="${y}" width="${bw * 0.64}" height="${Math.max(0, h - 1)}" rx="2" fill="${cols[j]}"><title>${d.day} · ${k}: ${d[k]}</title></rect>`; });
      if (i % 2 === 0 || i === days.length - 1) g += `<text x="${L + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${d.day}</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Incidents per day by severity">${g}</svg>`;
  }
  function donut(counts) {
    const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1;
    let a0 = -Math.PI / 2, g = "";
    SEV.forEach((s) => {
      const frac = counts[s] / total; if (!frac) return;
      const a1 = a0 + frac * Math.PI * 2, large = frac > 0.5 ? 1 : 0, r = 70, R = 90;
      const p = (ang, rad) => `${90 + rad * Math.cos(ang)},${90 + rad * Math.sin(ang)}`;
      g += `<path d="M${p(a0, R)} A${R},${R} 0 ${large} 1 ${p(a1, R)} L${p(a1, r)} A${r},${r} 0 ${large} 0 ${p(a0, r)} Z" fill="${SEV_COLOR[s]}" stroke="#111b30" stroke-width="2"><title>${s}: ${counts[s]}</title></path>`;
      a0 = a1;
    });
    return `<div class="donutwrap"><svg viewBox="0 0 180 180" role="img" aria-label="Issues by severity">${g}<text x="90" y="90" text-anchor="middle" style="font-size:26px;fill:#e7edf8;font-weight:600">${total}</text><text x="90" y="112" text-anchor="middle">issues</text></svg>
      <div class="legend" style="flex-direction:column;gap:8px">${SEV.map((s) => `<span><i style="background:${SEV_COLOR[s]}"></i>${s} · <b style="color:var(--text)">${counts[s]}</b> (${Math.round((counts[s] / total) * 100)}%)</span>`).join("")}</div></div>`;
  }
  function hbars(entries) {
    const max = Math.max(...entries.map((e) => e[1]), 1), W = 520, rowH = 30;
    const g = entries.map(([k, v], i) => `<text x="0" y="${i * rowH + 19}">${k}</text><rect x="120" y="${i * rowH + 7}" width="${((W - 160) * v) / max}" height="16" rx="4" fill="url(#hb)"/><text x="${126 + ((W - 160) * v) / max}" y="${i * rowH + 19}" style="fill:#c9d4ea">${v}</text>`).join("");
    return `<svg class="chart" viewBox="0 0 ${W} ${entries.length * rowH + 6}" role="img" aria-label="Issues by category"><defs><linearGradient id="hb"><stop offset="0" stop-color="#14b8a6"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>${g}</svg>`;
  }
  function lineChart(vals, labels) {
    const W = 640, H = 220, L = 34, B = 24, T = 10, max = Math.ceil(Math.max(...vals)), step = (W - L - 10) / (vals.length - 1);
    const pts = vals.map((v, i) => [L + i * step, T + (H - B - T) * (1 - v / max)]);
    let g = "";
    for (let v = 0; v <= max; v++) { const y = T + (H - B - T) * (1 - v / max); g += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="#1f2c48"/><text x="${L - 6}" y="${y + 4}" text-anchor="end">${v}m</text>`; }
    const d = pts.map((p, i) => (i ? "L" : "M") + p.join(",")).join(" ");
    g += `<path d="${d} L${pts[pts.length - 1][0]},${H - B} L${L},${H - B} Z" fill="url(#lg)" opacity=".35"/><path d="${d}" fill="none" stroke="#22d3ee" stroke-width="2.5"/>`;
    pts.forEach((p, i) => { g += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="#0c1528" stroke="#22d3ee" stroke-width="2"><title>${labels[i]}: ${vals[i]} min</title></circle>`; if (i % 2 === 0 || i === pts.length - 1) g += `<text x="${p[0]}" y="${H - 6}" text-anchor="middle">${labels[i]}</text>`; });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Mean time to remediation suggestion"><defs><linearGradient id="lg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#22d3ee" stop-opacity="0"/></linearGradient></defs>${g}</svg>`;
  }
  PAGES.reports = {
    title: "Reports",
    html() {
      const R = D.reports;
      const sevCounts = Object.fromEntries(SEV.map((s) => [s, D.issues.filter((i) => i.sev === s).length]));
      const cats = CATS.map((c) => [c, D.issues.filter((i) => i.cat === c).length]).sort((a, b) => b[1] - a[1]);
      const totals = R.days.reduce((s, d) => s + d.critical + d.high + d.medium + d.low, 0);
      return `<div class="titlebar"><div><h1>Reports</h1><p>Last 14 days · all environments</p></div>
        <div class="tb-actions"><button class="btn ghost" data-action="export-csv">${icon("dl")}Export CSV</button><button class="btn primary" data-action="export-pdf">${icon("dl")}Export PDF</button></div></div>
        <section class="kpis">
          <div class="kpi"><div class="ic" style="background:#2563eb">${icon("alert")}</div><div><label>Incidents (14 days)</label><strong>${totals}</strong><span>${R.days.slice(-7).reduce((s, d) => s + d.critical, 0)} critical in the last 7 days</span></div></div>
          <div class="kpi"><div class="ic" style="background:#0d9488">${icon("clock")}</div><div><label>Time to remediation suggestion</label><strong>${R.mttr[R.mttr.length - 1]} min</strong><span><b class="up">↓ ${Math.round((1 - R.mttr[R.mttr.length - 1] / R.mttr[0]) * 100)}%</b> over 14 days</span></div></div>
          <div class="kpi"><div class="ic" style="background:#7c3aed">${icon("ticket")}</div><div><label>Tickets created</label><strong>${D.tickets.length}</strong><span>for critical and high issues</span></div></div>
          <div class="kpi"><div class="ic" style="background:#16a34a">${icon("check")}</div><div><label>Remediations accepted</label><strong>87%</strong><span>approved without edits</span></div></div>
        </section>
        <div class="grid2">
          <section class="card"><div class="h"><div><h2>Incident trend by day</h2><div class="sub">Stacked by severity</div></div></div>${stackedBars(R.days)}
            <div class="legend">${SEV.map((s) => `<span><i style="background:${SEV_COLOR[s]}"></i>${s}</span>`).join("")}</div></section>
          <section class="card"><div class="h"><div><h2>Issues by severity</h2><div class="sub">Current detected issues</div></div></div>${donut(sevCounts)}</section>
          <section class="card"><div class="h"><div><h2>Issues by category</h2><div class="sub">Current detected issues</div></div></div>${hbars(cats)}</section>
          <section class="card"><div class="h"><div><h2>Mean time to remediation suggestion</h2><div class="sub">Minutes from upload to approved-ready plan</div></div></div>${lineChart(R.mttr, R.days.map((d) => d.day))}</section>
        </div>
        <section class="card"><div class="h"><h2>Top affected services</h2></div><div class="tablewrap"><table>
          <thead><tr><th>Service</th><th>Incidents</th><th>Critical</th><th>Most common category</th><th>Share</th></tr></thead>
          <tbody>${R.services.map(([s, n, c, cat]) => `<tr><td class="t1">${s}</td><td class="num">${n}</td><td class="num" style="color:${c ? "#f87171" : ""}">${c}</td><td>${cat}</td>
            <td><div class="conf" style="max-width:220px"><div class="bar"><i style="width:${(n / R.services[0][1]) * 100}%"></i></div><span class="num">${Math.round((n / R.services.reduce((x, y) => x + y[1], 0)) * 100)}%</span></div></td></tr>`).join("")}</tbody></table></div></section>`;
    },
  };

  /* ================= SETTINGS ================= */
  PAGES.settings = {
    title: "Settings",
    html() {
      return `<div class="titlebar"><div><h1>Settings</h1><p>Slack and Jira modes are applied on the server. Other preferences stay in this browser.</p></div></div>
        ${LIVE.ok ? "" : `<div class="banner bad"><div class="bi">${icon("alert")}</div><div class="grow"><b>API not reachable</b><p>${esc(LIVE.error || "")} · The UI shows sample data until the API at ${esc(API_BASE || location.origin)} responds.</p></div></div>`}
        <div class="grid2">
          <section class="card"><div class="h"><div><h2>Slack &amp; Jira mode</h2><div class="sub">Simulation: nothing leaves the app. Live: real Slack messages and Jira tickets.</div></div></div><div class="toggles">
            ${[["slack", "Slack", `Posts to ${esc(LIVE.slack.channel)}`, "SLACK_WEBHOOK_URL"], ["jira", "Jira", `Creates tickets in project ${esc(LIVE.jira.project_key)}`, "JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN"]].map(([k, n, d, env]) => {
              const m = LIVE[k].mode, can = LIVE.ok && LIVE[k].can_go_live !== false;
              return `<div class="tgl" style="cursor:default"><span class="tt"><b>${n}</b><small>${!LIVE.ok ? "Unavailable while the API is offline" : can ? d : `Live needs ${env} in .env`}</small></span>
                <div class="seg modeseg" role="radiogroup" aria-label="${n} mode">${["simulation", "live"].map((v) => `<button type="button" role="radio" aria-checked="${m === v}" class="${m === v ? "on" + (v === "live" ? " live" : "") : ""}" data-action="set-mode" data-k="${k}" data-v="${v}" ${!LIVE.ok || (v === "live" && !can) ? "disabled" : ""}>${v === "live" ? "Live" : "Simulation"}</button>`).join("")}</div></div>`;
            }).join("")}
            <p class="muted" style="font-size:12px">The API starts in the mode set in .env (Simulation by default) and returns to it after a restart.</p>
            <div class="tgl" style="cursor:default"><span class="tt"><b>API key</b><small>${LIVE.auth ? "Required by this API (X-API-Key)" : "Only needed if API_KEY is set on the server"}</small></span>
              <input class="inp" id="apikey" type="password" style="max-width:220px" placeholder="Paste API key" value="${esc(apiKey())}" aria-label="API key"></div></div></section>
          <section class="card"><div class="h"><h2>Models</h2></div><div class="form">
            <div class="fg"><label for="m1">Classifier model</label><select class="inp" id="m1"><option>openai/gpt-4o-mini</option><option>openai/gpt-4.1-mini</option></select></div>
            <div class="fg"><label for="m2">Reasoning model</label><select class="inp" id="m2"><option>openai/gpt-4o</option><option>openai/gpt-4.1</option></select></div>
            <div class="fg"><label for="m3">Max Jira tickets per analysis</label><input class="inp" id="m3" type="number" min="1" max="20" value="5"></div>
</div></section>
          <section class="card"><div class="h"><h2>Guardrails</h2></div><div class="toggles">
            <div class="tgl" style="cursor:default"><span class="tt"><b>Human approval before sending</b><small>Always on · every Slack alert and Jira ticket needs a click on JIRA/SLACK Notification</small></span><span class="badge b-done">On</span></div>
            <label class="tgl"><span class="tt"><b>Redact secrets in logs and messages</b><small>Tokens, passwords and keys are masked</small></span><span class="sw"><input type="checkbox" checked><span></span></span></label>
            <label class="tgl"><span class="tt"><b>Flag destructive remediation steps</b><small>e.g. DROP TABLE, rm -rf, kubectl delete namespace</small></span><span class="sw"><input type="checkbox" checked><span></span></span></label></div></section>
          <section class="card"><div class="h"><h2>Notifications</h2></div><div class="toggles">
            <label class="tgl"><span class="tt"><b>Notify me on critical issues</b><small>In-app notification and email digest</small></span><span class="sw"><input type="checkbox" checked><span></span></span></label>
            <label class="tgl"><span class="tt"><b>Daily report at 09:00</b><small>Incident summary for the previous day</small></span><span class="sw"><input type="checkbox"><span></span></span></label></div></section>
        </div>
        <div class="formfoot"><button class="btn primary" data-action="save-settings">Save settings</button></div>`;
    },
  };

  const closeModal = () => ($("#modalRoot").innerHTML = "");

  /* ================= events ================= */
  function renderBell() {
    $("#bellMenu").innerHTML = `<div class="mh">Notifications<button class="link" data-action="bell-read">Mark all as read</button></div>
      ${D.notifications.map((n) => `<a class="mi ${S.bellRead ? "read" : ""}" href="${n.href}"><span class="dot" style="background:${n.tone}"></span><span>${esc(n.text)}<small>${esc(n.sub)}</small></span></a>`).join("")}`;
  }
  async function decideAction(el, send) {
    const i = D.issues.find((x) => x.id === el.dataset.id); if (!i) return;
    const box = el.closest(".appr"); box?.querySelectorAll("button").forEach((b) => (b.disabled = true));
    if (send) el.innerHTML = `<span class="spin sm"></span>Sending…`;
    try { await decide(i, send); }
    catch (e) { toast(send ? "Could not send" : "Could not save your choice", e.message, "bad"); }
    S.refresh[{ approvals: "appr", issues: "issues", dashboard: "dash" }[currentPage]]?.();
    if (S.drawer) renderDrawer();
    updateChrome();
  }
  const ACTIONS = {
    "toggle-nav": () => document.body.classList.toggle("nav-open"),
    "toggle-bell": () => { const m = $("#bellMenu"); m.hidden = !m.hidden; if (!m.hidden) renderBell(); },
    "bell-read": () => { S.bellRead = true; renderBell(); updateChrome(); },
    "new-analysis": () => { closeDrawer(); go("#/analysis"); },
    page: (el) => { tstate(el.dataset.table).page = +el.dataset.page; S.refresh[el.dataset.table]?.(); },
    "view-report": (el) => { fstate("issues").analysis = el.dataset.id; go(`#/issues?analysis=${el.dataset.id}`); },
    "view-agents": (el) => go(`#/agents?run=${el.dataset.id}`),
    "close-modal": closeModal,
    "open-issue": (el) => openIssue(el.dataset.id, el.dataset.tab || "summary"),
    "close-drawer": closeDrawer,
    tab: (el) => { S.drawer.tab = el.dataset.tab; renderDrawer(); },
    "sev-filter": (el) => { const f = fstate("issues"); f.severity = f.severity === el.dataset.v ? "" : el.dataset.v; tstate("issues").page = 1; const sel = $('[data-filter="issues:severity"]'); if (sel) sel.value = f.severity; S.refresh.issues(); },
    "clear-issue-filters": () => { S.f.issues = { analysis: fstate("issues").analysis }; $$('[data-filter^="issues:"]').forEach((x) => (x.value = "")); tstate("issues").page = 1; S.refresh.issues(); },
    "clear-analysis": () => { fstate("issues").analysis = ""; go("#/issues"); },
    "approve-remediation": () => { const i = currentIssue(); i.approved = "Alex Chen"; if (i.status === "Open") i.status = "Remediation Suggested"; renderDrawer(); S.refresh.issues?.(); toast("Remediation approved", `${i.id} · recorded in the audit trail`); },
    "false-positive": () => { const i = currentIssue(); i.status = "False Positive"; renderDrawer(); S.refresh.issues?.(); updateChrome(); toast("Marked as false positive", `${i.id} will be excluded from reports`, "warn"); },
    "check-step": (el) => { const i = currentIssue(), k = +el.dataset.k; i.checklistDone = el.checked ? [...new Set([...i.checklistDone, k])] : i.checklistDone.filter((x) => x !== k); renderDrawer(); },
    "copy-checklist": async () => { await copy(checklistMd(currentIssue())); toast("Checklist copied", "Paste it into a runbook, ticket or chat"); },
    "export-md": () => { const i = currentIssue(); download(`${i.id}-checklist.md`, checklistMd(i), "text/markdown"); toast("Markdown exported", `${i.id}-checklist.md`); },
    "decide-send": (el) => decideAction(el, true),
    "decide-dismiss": (el) => decideAction(el, false),
    "appr-show": (el) => { fstate("appr").show = el.dataset.v; S.refresh.appr(); },
    "jira-link": (el) => el.dataset.url && !el.dataset.key.includes("-SIM-") ? window.open(el.dataset.url, "_blank", "noopener") : toast(`${el.dataset.key} is not a real Jira ticket`, el.dataset.key.includes("-SIM-") ? "Created in simulation mode" : "Sample data", "warn"),
    threshold: (el) => { S.cfg.threshold = el.dataset.v; $$(".seg button").forEach((b) => { b.classList.toggle("on", b === el); b.setAttribute("aria-checked", b === el); }); },
    browse: () => $("#fileInput")?.click(),
    "remove-file": () => { S.upload = null; S.uploadError = ""; renderUpload(); },
    "cancel-analysis": () => { S.upload = null; S.uploadError = ""; go("#/dashboard"); },
    "start-analysis": () => { if (S.upload && S.upload.progress >= 100) startRun(); },
    retry: retryRun,
    "back-to-upload": () => { clearInterval(S.run?.timer); clearTimeout(S.run?.poll); S.run = null; render(); },
    "view-new-results": () => { const id = S.run.id; S.run = null; S.upload = null; go(`#/issues?analysis=${id}&new=1`); },
    "toggle-trace": (el) => { S.expanded = S.expanded === el.dataset.id ? null : el.dataset.id; S.refresh.agents(); },
    "export-csv": () => {
      const rows = [["issue_id", "severity", "title", "service", "environment", "category", "status", "confidence", "detected", "analysis"],
        ...D.issues.map((i) => [i.id, i.sev, i.title, i.service, i.env, i.cat, i.status, Math.round(i.confidence * 100) + "%", i.detected, i.analysis])];
      download("incident-report.csv", rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\n"), "text/csv");
      toast("CSV exported", "incident-report.csv");
    },
    "export-pdf": () => { toast("Opening print dialog", "Choose “Save as PDF” as the destination"); setTimeout(() => window.print(), 300); },
    "set-mode": async (el) => {
      const k = el.dataset.k, v = el.dataset.v, name = k === "slack" ? "Slack" : "Jira";
      if (LIVE[k].mode === v) return;
      if (v === "live" && !confirm(k === "slack" ? `Switch Slack to Live? Sent alerts will be posted to ${LIVE.slack.channel}.` : `Switch Jira to Live? Sent issues will create real tickets in ${LIVE.jira.project_key}.`)) return;
      try {
        const res = await apiFetch("/api/integrations", { method: "PUT", json: { [k + "_mode"]: v, actor: "ui-user" } });
        Object.assign(LIVE, { slack: res.slack, jira: res.jira });
        toast(`${name}: ${v === "live" ? "Live" : "Simulation"}`, v === "live" ? "Real messages will be sent when you click Send" : "Nothing will leave the app", v === "live" ? "warn" : "ok");
      } catch (e) { toast(`Could not change ${name} mode`, e.message, "bad"); }
      render();
    },
    "save-settings": () => {
      const k = $("#apikey")?.value.trim();
      try { if (k) sessionStorage.setItem("apiKey", k); else sessionStorage.removeItem("apiKey"); } catch { /* storage unavailable */ }
      toast("Settings saved", "Reconnecting to the API…"); boot();
    },
  };

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!e.target.closest(".dd")) $("#bellMenu").hidden = true;
    if (!el) return;
    if (el.tagName === "A" && el.getAttribute("href")?.startsWith("#") && !el.dataset.action) return;
    const fn = ACTIONS[el.dataset.action];
    if (!fn) return;
    if (el.type !== "checkbox") e.preventDefault();
    fn(el, e);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if ($("#modalRoot").innerHTML) closeModal(); else if (S.drawer) closeDrawer(); $("#bellMenu").hidden = true; document.body.classList.remove("nav-open"); }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches('[role="button"][data-action]')) { e.preventDefault(); e.target.click(); }
  });
  document.addEventListener("input", (e) => {
    const t = e.target;
    if (t.dataset.filter) {
      const [id, key] = t.dataset.filter.split(":");
      fstate(id)[key] = t.value; tstate(id).page = 1;
      clearTimeout(t._deb); t._deb = setTimeout(() => S.refresh[id]?.(), t.type === "search" ? 120 : 0);
    }
    if (t.dataset.cfg && t.type !== "checkbox") S.cfg[t.dataset.cfg] = t.value;
    if (t.dataset.owner != null) currentIssue().owners[t.dataset.owner] = t.value;
  });
  document.addEventListener("change", (e) => {
    const t = e.target;
    if (t.dataset.cfg && t.type === "checkbox") S.cfg[t.dataset.cfg] = t.checked;
  });
  $("#env").addEventListener("change", (e) => { S.env = e.target.value; S.cfg.env = S.env; tstate("dash").page = 1; if (currentPage === "dashboard") render(); toast(`Environment: ${S.env}`, "Dashboard figures updated"); });
  $("#globalSearch").addEventListener("submit", (e) => {
    e.preventDefault();
    const q = $("#q").value.trim(); if (!q) return;
    if (/^ANL-\d+$/i.test(q)) { fstate("issues").analysis = q.toUpperCase(); go(`#/issues?analysis=${q.toUpperCase()}`); return; }
    if (/^ISS-\d+$/i.test(q)) { go(`#/issues?open=${q.toUpperCase()}`); return; }
    S.f.issues = { q }; go(`#/issues?q=${encodeURIComponent(q)}`);
  });

  render();
  boot();
})();
