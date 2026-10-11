/* Realistic SAMPLE data for the UI prototype. Nothing here comes from, or is sent to, a real system. */
window.SAMPLE = (function () {
  const ENVS = ["Production", "Staging", "Development"];

  /* Issue templates: each one is a fully described incident used for detail views. */
  const TEMPLATES = [
    {
      key: "dbpool", sev: "Critical", cat: "Database", title: "Database connection pool exhausted",
      service: "payments-api", source: "Kubernetes", host: "payments-api-7d9f8b6c-x2lpq", code: "DB_TIMEOUT", confidence: 0.94, events: 412,
      summary: "Long-running transactions saturated the HikariCP pool (50/50 connections). New requests waited 30 s and failed with HTTP 500, which opened the circuit breaker on payment-processing.",
      logs: [
        "2026-10-10T09:12:01.120Z INFO  payments-api  Payment request received trace_id=trc-1001",
        "2026-10-10T09:12:02.450Z ERROR payments-api  error_code=DB_TIMEOUT Database query timed out after 30000 ms trace_id=trc-1001",
        "2026-10-10T09:12:03.010Z WARN  payment-db    Connection pool usage at 98%; active connections 49/50",
        "2026-10-10T09:12:04.780Z ERROR payments-api  HTTP 500 returned to client trace_id=trc-1002",
        "2026-10-10T09:12:06.320Z CRITICAL payments-api Circuit breaker opened for payment-processing dependency",
      ],
      fix: "Terminate the blocking transactions on payment-db, then temporarily raise the pool limit from 50 to 80 while the slow query on orders(user_id) is indexed.",
      rationale: "Pool saturation (49/50) preceded every timeout by under a second, and the timeouts cascade into HTTP 500s and the open circuit breaker. Freeing connections restores throughput immediately; the index removes the root cause.",
      risk: "Medium", effort: "30–45 min", perms: "DBA role on payment-db, kubectl edit on payments namespace",
      rollback: "Restore pool size 50 via the previous ConfigMap revision and redeploy payments-api (kubectl rollout undo).",
      steps: [
        ["Identify blocking queries with pg_stat_activity (state = 'active', duration > 30 s)", "List of 3–5 long transactions with their PIDs"],
        ["Terminate the blocking sessions with pg_terminate_backend after confirming with the service owner", "Active connections drop below 30"],
        ["Raise HikariCP maximumPoolSize to 80 in the payments-api ConfigMap", "Pool usage stays under 70% at peak"],
        ["Create index CONCURRENTLY on orders(user_id)", "Query p95 below 50 ms"],
        ["Close the circuit breaker and watch the 5xx rate for 15 minutes", "HTTP 5xx under 0.5%"],
      ],
    },
    {
      key: "oom", sev: "Critical", cat: "Infrastructure", title: "payments-worker pods OOMKilled in a restart loop",
      service: "payments-worker", source: "Kubernetes", host: "node-pool-b-3", code: "OOMKilled", confidence: 0.91, events: 57,
      summary: "Memory grew steadily after the 09:00 deployment until the 512 Mi limit was hit; the pod restarted 7 times in 20 minutes and readiness probes failed.",
      logs: [
        "2026-10-10T09:05:11.002Z WARN  payments-worker  Memory usage reached 92% of container limit (471Mi/512Mi)",
        "2026-10-10T09:12:07.900Z ERROR kubernetes       Pod payments-worker-5c7d8f9-pod4 restarted: OOMKilled",
        "2026-10-10T09:12:09.010Z INFO  payments-worker  Health probe failed: readiness=false",
        "2026-10-10T09:14:20.400Z WARNING kubernetes     Pod payments-worker-5c7d8f9-pod4 restart count increased to 7",
      ],
      fix: "Roll back payments-worker to the previous image (v2.14.3), then raise the memory limit to 768 Mi until the cache leak introduced in v2.15.0 is fixed.",
      rationale: "Restarts began right after the v2.15.0 rollout, and memory climbs linearly without plateauing, which points to the new in-process cache rather than load.",
      risk: "Low", effort: "15 min", perms: "kubectl rollout on payments namespace",
      rollback: "kubectl rollout undo deployment/payments-worker to re-apply v2.15.0 if the rollback causes regressions.",
      steps: [
        ["Run kubectl describe pod and kubectl logs --previous on a restarted pod", "OOMKilled reason and last log lines confirmed"],
        ["Roll back the deployment to v2.14.3", "Restart count stops increasing"],
        ["Set resources.limits.memory to 768Mi as a temporary buffer", "Memory headroom above 30%"],
        ["Open a bug for the v2.15.0 cache growth with a heap profile attached", "Owner assigned and ETA agreed"],
      ],
    },
    {
      key: "broker", sev: "Critical", cat: "Network", title: "order-api cannot reach RabbitMQ broker",
      service: "order-api", source: "Kubernetes", host: "order-api-3", code: "ECONNREFUSED", confidence: 0.89, events: 233,
      summary: "Connections to rabbitmq:5672 are refused, so order events queue in memory; queue depth passed 10,000 messages and new orders are failing.",
      logs: [
        "2026-10-10T12:01:05.600Z CRITICAL order-api  Order processing queue depth exceeded 10000 messages trace_id=trc-3002",
        "2026-10-10T12:01:08.950Z ERROR order-api     error_code=ECONNREFUSED Unable to connect to message broker at rabbitmq:5672 trace_id=trc-3003",
        "2026-10-10T12:01:09.200Z WARN  order-api     Retrying broker connection in 5 seconds (attempt 4/10)",
        "2026-10-10T12:01:14.380Z ERROR order-api     error_code=ECONNREFUSED Unable to connect to message broker at rabbitmq:5672",
      ],
      fix: "Restore the rabbitmq-0 pod (it was evicted from node-3 during disk pressure), then drain the in-memory backlog with rate-limited consumers.",
      rationale: "Every refused connection targets the same broker endpoint, and the backlog started growing at the same minute the broker pod was evicted.",
      risk: "Medium", effort: "20–30 min", perms: "kubectl on messaging namespace, RabbitMQ admin",
      rollback: "Scale order-api consumers back to 2 replicas if the drain overloads downstream services.",
      steps: [
        ["Check rabbitmq-0 pod status and node events", "Eviction reason identified"],
        ["Reschedule rabbitmq-0 on a node without disk pressure", "Broker accepts connections on 5672"],
        ["Scale order consumers to 6 replicas with prefetch 50", "Queue depth decreasing steadily"],
        ["Verify no orders were lost by reconciling with the orders table", "Counts match"],
      ],
    },
    {
      key: "redis", sev: "High", cat: "Performance", title: "Redis eviction storm on cache-02",
      service: "checkout-service", source: "Application Logs", host: "cache-02", code: "ECONNREFUSED", confidence: 0.86, events: 148,
      summary: "cache-02 hit maxmemory and evicted hot keys, so checkout-service fell back to the database; the extra load caused health-check failures and pod restarts.",
      logs: [
        "2026-10-10T10:01:05.220Z ERROR checkout-service error_code=ECONNREFUSED Unable to connect to Redis at redis-master:6379",
        "2026-10-10T10:01:06.100Z WARN  checkout-service Retrying Redis connection in 5 seconds",
        "2026-10-10T10:01:12.300Z ERROR checkout-service Health check failed: redis unavailable",
        "2026-10-10T10:01:20.400Z WARNING kubernetes    Pod checkout-service-5c7d8f9-pod4 restart count increased to 3",
      ],
      fix: "Raise cache-02 maxmemory from 2 GB to 4 GB and switch the eviction policy to allkeys-lfu; add a 2-second circuit breaker around cache reads.",
      rationale: "Evictions spiked to 18k/s when used_memory reached maxmemory, matching the start of the cache-miss surge.",
      risk: "Low", effort: "20 min", perms: "Redis CONFIG on cache-02",
      rollback: "CONFIG SET maxmemory 2gb and maxmemory-policy volatile-lru.",
      steps: [
        ["Confirm evicted_keys and used_memory with INFO stats/memory", "Eviction rate and memory usage captured"],
        ["CONFIG SET maxmemory 4gb and maxmemory-policy allkeys-lfu", "Evictions under 100/s"],
        ["Restart failed checkout-service pods", "All pods Ready"],
      ],
    },
    {
      key: "tls", sev: "High", cat: "Security", title: "TLS certificate for api.payment.example.com expires in 2 days",
      service: "api-gateway", source: "AWS CloudWatch", host: "alb-payments-prod", code: "CERT_EXPIRY", confidence: 0.98, events: 12,
      summary: "Automatic renewal failed because the DNS-01 challenge record could not be written; the certificate expires on 12 Oct 2026 at 23:59 UTC.",
      logs: [
        "2026-10-10T09:12:08.450Z ERROR api-gateway  Certificate for api.payment.example.com expires in 2 days",
        "2026-10-10T03:00:02.117Z ERROR cert-manager Renewal failed: DNS-01 challenge record not created (AccessDenied)",
        "2026-10-10T03:00:02.118Z WARN  cert-manager Next renewal attempt in 24h",
      ],
      fix: "Grant the cert-manager IAM role route53:ChangeResourceRecordSets on the payments hosted zone and trigger a manual renewal.",
      rationale: "The renewal job fails with AccessDenied on the DNS challenge, not on the certificate authority, so fixing permissions unblocks automation.",
      risk: "Low", effort: "15 min", perms: "IAM policy edit, cert-manager namespace access",
      rollback: "Remove the added IAM statement; the current certificate stays valid until expiry.",
      steps: [
        ["Add route53:ChangeResourceRecordSets for the hosted zone to the cert-manager role", "Policy simulator returns allowed"],
        ["Delete the failed CertificateRequest to trigger renewal", "New certificate issued"],
        ["Verify the new expiry with openssl s_client", "Expiry 90 days out"],
      ],
    },
    {
      key: "deadlock", sev: "High", cat: "Database", title: "Deadlocks on orders table during inserts",
      service: "order-db", source: "Application Logs", host: "order-db-1", code: "DB_DEADLOCK", confidence: 0.83, events: 64,
      summary: "Two code paths lock orders and order_items in opposite order; under load the database aborts one transaction and the API returns HTTP 500.",
      logs: [
        "2026-10-10T12:01:02.150Z ERROR order-api  error_code=DB_DEADLOCK Database insert failed due to deadlock trace_id=trc-3001",
        "2026-10-10T12:01:03.300Z WARN  order-db   Long-running transaction detected; duration 45 seconds",
        "2026-10-10T12:01:04.420Z ERROR order-api  HTTP 500 returned to client trace_id=trc-3002",
      ],
      fix: "Make both write paths lock orders before order_items and add a retry with jitter for serialization failures.",
      rationale: "Deadlock graphs show the same two statements every time, acquiring row locks in opposite order.",
      risk: "Medium", effort: "2–4 h (code change)", perms: "Repository write access, staging deploy",
      rollback: "Revert the change; deadlocks return but no data is affected.",
      steps: [
        ["Capture deadlock graphs from the database log", "Both conflicting statements identified"],
        ["Change the bulk-import path to lock orders first", "Lock order consistent in both paths"],
        ["Add retry with jitter (3 attempts) for deadlock errors", "No 500s from deadlocks in load test"],
      ],
    },
    {
      key: "dns", sev: "High", cat: "Network", title: "CoreDNS resolution timeouts (504 Gateway Timeout)",
      service: "auth-api", source: "Kubernetes", host: "kube-dns.kube-system", code: "HTTP_504", confidence: 0.92, events: 189,
      summary: "i/o timeouts against kube-dns caused auth-api upstream calls to exceed the gateway timeout; CoreDNS pods were CPU-throttled.",
      logs: [
        "2026-10-10T08:41:12.004Z ERROR auth-api  dial tcp: lookup users-svc.auth.svc.cluster.local on 10.96.0.10:53: i/o timeout",
        "2026-10-10T08:41:12.090Z ERROR gateway   upstream auth-api returned 504 Gateway Timeout",
        "2026-10-10T08:41:15.300Z WARN  coredns   CPU throttling 78% for coredns-6d4b75cb6d-q8x2m",
      ],
      fix: "Scale CoreDNS to 4 replicas, raise its CPU limit to 500m and enable NodeLocal DNSCache.",
      rationale: "Timeouts correlate with CoreDNS CPU throttling above 70%; only two replicas serve the whole cluster.",
      risk: "Low", effort: "30 min", perms: "kubectl on kube-system",
      rollback: "Scale CoreDNS back to 2 replicas and remove the NodeLocal DNSCache DaemonSet.",
      steps: [
        ["Scale the coredns deployment to 4 replicas", "Throttling below 20%"],
        ["Raise the CPU limit to 500m", "No i/o timeouts for 15 minutes"],
        ["Plan NodeLocal DNSCache rollout for the next maintenance window", "Change request approved"],
      ],
    },
    {
      key: "shipping", sev: "Medium", cat: "Application", title: "Third-party shipping API returning 504",
      service: "shipping-service", source: "AWS CloudWatch", host: "shipping-1", code: "HTTP_504", confidence: 0.78, events: 96,
      summary: "The carrier's rate API times out for roughly 12% of requests; retries without backoff are amplifying load.",
      logs: [
        "2026-10-10T12:01:10.250Z ERROR shipping-service error_code=HTTP_504 Third-party shipping API returned 504 Gateway Timeout trace_id=trc-3004",
        "2026-10-10T12:01:10.900Z WARN  shipping-service Retrying carrier request immediately (attempt 3)",
      ],
      fix: "Add exponential backoff with jitter and serve cached rates for up to 10 minutes when the carrier is unavailable.",
      rationale: "Failures are external, but immediate retries triple the request volume during each degradation.",
      risk: "Low", effort: "1–2 h", perms: "Repository write access",
      rollback: "Disable the rate-cache feature flag.",
      steps: [
        ["Enable the carrier-rate cache feature flag", "Checkout succeeds during carrier degradation"],
        ["Add exponential backoff (base 200 ms, max 5 s) with jitter", "Retry volume reduced by 60%"],
      ],
    },
    {
      key: "auth", sev: "Medium", cat: "Security", title: "Authentication failures for internal service account",
      service: "order-api", source: "Azure Monitor", host: "order-api-1", code: "AUTH_401", confidence: 0.81, events: 38,
      summary: "The svc-orders client secret was rotated in Key Vault but order-api still uses the cached old secret.",
      logs: [
        "2026-10-10T12:01:12.500Z ERROR order-api error_code=AUTH_401 Authentication failed for internal service account trace_id=trc-3005",
        "2026-10-10T11:58:00.000Z INFO  keyvault  Secret svc-orders-client-secret new version created",
      ],
      fix: "Restart order-api pods so they load the new secret version, and enable the Key Vault CSI rotation poll (2 min).",
      rationale: "401s began two minutes after the secret rotation event, and only order-api is affected.",
      risk: "Low", effort: "10 min", perms: "kubectl rollout on orders namespace",
      rollback: "Restore the previous secret version in Key Vault.",
      steps: [
        ["Rollout-restart order-api", "Pods pick up new secret version"],
        ["Enable CSI secret rotation with a 2-minute poll", "Future rotations applied automatically"],
      ],
    },
    {
      key: "sap", sev: "Medium", cat: "Application", title: "SAP IDoc inbound backlog (status 64)",
      service: "SAP S/4HANA", source: "SAP System Logs", host: "s4p-app-01", code: "IDOC_64", confidence: 0.76, events: 1204,
      summary: "1,204 ORDERS05 IDocs are stuck in status 64 because the background job RBDAPP01 was cancelled after the kernel patch.",
      logs: [
        "2026-10-10T07:30:00.000Z WARN  s4p-app-01 Job RBDAPP01_ORDERS cancelled (user DDIC, reason: kernel restart)",
        "2026-10-10T08:15:22.410Z ERROR s4p-app-01 1204 IDocs in status 64 (ready to be transferred) for message type ORDERS",
      ],
      fix: "Reschedule RBDAPP01 for ORDERS05 with packet size 50 and process the backlog in parallel mode.",
      rationale: "IDocs stopped processing exactly when the job was cancelled; inbound partner profiles are healthy.",
      risk: "Low", effort: "20 min", perms: "SM36/SM37, BD87 authorization",
      rollback: "Cancel the rescheduled job in SM37.",
      steps: [
        ["Reschedule RBDAPP01 in SM36 with variant ORDERS_PARALLEL", "Job running every 5 minutes"],
        ["Monitor BD87 until status 64 count reaches zero", "Backlog cleared"],
      ],
    },
    {
      key: "flag", sev: "Low", cat: "Configuration", title: "Feature flag misconfiguration returns 404 on /v2/cart",
      service: "web-frontend", source: "Application Logs", host: "web-frontend-2", code: "HTTP_404", confidence: 0.72, events: 41,
      summary: "The new-cart flag is enabled for 5% of users in Staging config copied to Production, but the /v2/cart route is not deployed there.",
      logs: [
        "2026-10-10T14:22:10.010Z WARN web-frontend GET /v2/cart 404 (flag new-cart=true, user cohort 5%)",
      ],
      fix: "Disable the new-cart flag in Production until the v2 route ships.",
      rationale: "All 404s carry new-cart=true and the route exists only in Staging.",
      risk: "Low", effort: "5 min", perms: "Feature flag admin",
      rollback: "Re-enable the flag at 5%.",
      steps: [["Turn off new-cart in the Production flag environment", "404s stop within one minute"]],
    },
    {
      key: "gc", sev: "Low", cat: "Performance", title: "Long JVM GC pauses on inventory-worker",
      service: "inventory-worker", source: "Application Logs", host: "inventory-worker-1", code: "GC_PAUSE", confidence: 0.69, events: 23,
      summary: "G1 full GC pauses of 1.8–2.4 s during the nightly stock sync; no errors yet but batch latency doubled.",
      logs: [
        "2026-10-10T02:10:44.871Z WARN inventory-worker GC pause (G1 Evacuation Pause) 2412ms",
        "2026-10-10T02:10:47.002Z WARN inventory-worker Batch stock-sync took 184s (baseline 90s)",
      ],
      fix: "Increase heap to 3 GB and set MaxGCPauseMillis=200 for the inventory-worker JVM.",
      rationale: "Pauses only occur during the sync batch when old-gen occupancy passes 85%.",
      risk: "Low", effort: "15 min", perms: "Deployment config",
      rollback: "Revert JVM options to the previous values.",
      steps: [["Update JAVA_OPTS with -Xmx3g -XX:MaxGCPauseMillis=200", "GC pauses under 300 ms"]],
    },
  ];

  /* ---------- analyses ---------- */
  const files = [
    ["prod-payments-api.log", "payments-api", "Kubernetes"], ["checkout-service.log", "checkout-service", "Kubernetes"],
    ["auth-gateway.log", "auth-api", "Kubernetes"], ["inventory-worker.log", "inventory-worker", "Application Logs"],
    ["notification-svc.log", "notification-svc", "Application Logs"], ["api-edge-nginx.log", "api-edge", "AWS CloudWatch"],
    ["order_processing_incident.csv", "order-api", "Application Logs"], ["sap-s4p-idoc.txt", "SAP S/4HANA", "SAP System Logs"],
    ["aks-orders-cluster.json", "order-api", "Azure Monitor"], ["cloudwatch-shipping.json", "shipping-service", "AWS CloudWatch"],
    ["payments-worker.log", "payments-worker", "Kubernetes"], ["web-frontend.log", "web-frontend", "Application Logs"],
  ];
  const statuses = ["Processing", "Completed", "Awaiting Approval", "Failed", "Completed", "Completed", "Awaiting Approval", "Completed", "Completed", "Failed", "Completed", "Completed"];
  const analyses = [];
  let t = new Date("2026-10-10T20:14:52");
  for (let i = 0; i < 26; i++) {
    const [file, service, source] = files[i % files.length];
    const env = i % 7 === 2 ? "Staging" : i % 9 === 4 ? "Development" : "Production";
    const lines = [214906, 88410, 31002, 56774, 12390, 140208, 12, 4820, 66310, 23950, 47120, 9874][i % 12] + (i > 11 ? 1311 * i : 0);
    const status = statuses[i % statuses.length];
    const crit = status === "Processing" ? 2 : [2, 1, 0, 1, 0, 2, 3, 0, 1, 1, 2, 0][i % 12];
    const warn = [3, 4, 2, 3, 1, 5, 2, 1, 4, 2, 3, 1][i % 12];
    const start = new Date(t);
    const dur = 18 + ((i * 37) % 40);
    analyses.push({
      id: "ANL-" + (2048 - i), file, service, source, env, lines, critical: crit, warnings: warn, status,
      uploaded: fmt(start), started: fmt(new Date(+start + 2000)),
      completed: status === "Processing" ? null : fmt(new Date(+start + dur * 1000)),
      duration: status === "Processing" ? null : dur,
      failedAt: status === "Failed" ? (i % 2 ? "Remediation Agent" : "Jira Ticket Agent") : null,
    });
    t = new Date(+t - (19 + (i * 13) % 41) * 60000);
  }

  /* ---------- issues ---------- */
  const issueStatus = ["Open", "Remediation Suggested", "Ticket Created", "Remediation Suggested", "Open", "Resolved", "Ticket Created", "Remediation Suggested"];
  const sevMap = { Critical: 0, High: 1, Medium: 2, Low: 3 };
  const issues = [];
  const altServices = { "payments-api": "billing-api", "payments-worker": "ledger-worker", "order-api": "fulfillment-api", "checkout-service": "cart-service", "api-gateway": "public-gateway", "order-db": "billing-db", "auth-api": "identity-api" };
  let n = 0;
  // Issues belong to the most recent finished analyses; their counts are derived from these issues below.
  const pool = analyses.filter((x) => x.status === "Completed" || x.status === "Awaiting Approval").slice(0, 11);
  const addIssue = (tpl, variant) => {
    const a = pool[n % pool.length];
    const svc = variant ? (altServices[tpl.service] || tpl.service) : tpl.service;
    const detected = new Date(Date.parse(a.uploaded.replace(" ", "T")) + 40000 + n * 7000);
    issues.push({
      ...tpl, id: "ISS-" + (3140 - n), service: svc, env: variant && n % 3 === 0 ? "Staging" : a.env,
      analysis: a.id, detected: fmt(detected), firstSeen: fmt(new Date(+detected - 9 * 60000)), lastSeen: fmt(detected),
      status: issueStatus[n % issueStatus.length], confidence: Math.max(0.55, tpl.confidence - (variant ? 0.07 : 0)),
      events: variant ? Math.round(tpl.events * 0.6) : tpl.events,
      jiraKey: null, slackStatus: "Not sent", checklistDone: [], owners: {},
    });
    n++;
  };
  TEMPLATES.forEach((tpl) => addIssue(tpl, false));
  TEMPLATES.slice(0, 10).forEach((tpl) => addIssue(tpl, true));
  issues.sort((x, y) => sevMap[x.sev] - sevMap[y.sev] || y.detected.localeCompare(x.detected));
  analyses.forEach((a) => {
    if (a.status === "Processing") return;
    const mine = issues.filter((i) => i.analysis === a.id);
    a.critical = mine.filter((i) => i.sev === "Critical").length;
    a.warnings = mine.length - a.critical;
  });

  /* ---------- Jira tickets ---------- */
  const people = ["Alex Chen", "Priya Raman", "Marco Silva", "Sara Okafor", "Unassigned", "Jonas Weber"];
  const jstat = ["To Do", "In Progress", "In Review", "Done", "In Progress", "To Do"];
  const prio = { Critical: "Highest", High: "High", Medium: "Medium", Low: "Low" };
  const tickets = issues.filter((i) => i.status !== "Open").slice(0, 16).map((i, k) => {
    const key = (k % 4 === 3 ? "SAP-" : "OPS-") + (1431 - k);
    i.jiraKey = key;
    return {
      key, project: key.startsWith("SAP") ? "SAP Basis (SAP)" : "Operations (OPS)", title: i.title, issueId: i.id, severity: i.sev,
      priority: prio[i.sev], assignee: people[k % people.length], status: i.status === "Resolved" ? "Done" : jstat[k % jstat.length],
      created: i.detected, labels: ["incident", "ai-generated", i.cat.toLowerCase()],
    };
  });

  /* ---------- Slack notifications ---------- */
  const channels = ["#sre-incidents", "#payments-oncall", "#platform-alerts", "#sap-basis"];
  const sstat = ["Delivered", "Delivered", "Delivered", "Failed", "Delivered", "Scheduled", "Delivered"];
  const slack = issues.slice(0, 15).map((i, k) => ({
    time: i.detected, channel: channels[k % channels.length], issueId: i.id, title: i.title, severity: i.sev, status: sstat[k % sstat.length],
  }));

  /* ---------- playbooks ---------- */
  const playbooks = [
    { name: "Database connection pool saturation", cat: "Database", runs: 31, success: 94, updated: "2026-09-28", steps: TEMPLATES[0].steps.map((s) => s[0]) },
    { name: "Kubernetes OOMKilled / CrashLoopBackOff", cat: "Infrastructure", runs: 47, success: 91, updated: "2026-10-02", steps: TEMPLATES[1].steps.map((s) => s[0]) },
    { name: "Message broker unreachable", cat: "Network", runs: 12, success: 88, updated: "2026-09-14", steps: TEMPLATES[2].steps.map((s) => s[0]) },
    { name: "Redis memory pressure and evictions", cat: "Performance", runs: 19, success: 95, updated: "2026-09-30", steps: TEMPLATES[3].steps.map((s) => s[0]) },
    { name: "TLS certificate renewal failure", cat: "Security", runs: 8, success: 100, updated: "2026-08-21", steps: TEMPLATES[4].steps.map((s) => s[0]) },
    { name: "DNS resolution timeouts in cluster", cat: "Network", runs: 15, success: 87, updated: "2026-09-09", steps: TEMPLATES[6].steps.map((s) => s[0]) },
    { name: "SAP IDoc processing backlog", cat: "Application", runs: 6, success: 100, updated: "2026-07-30", steps: TEMPLATES[9].steps.map((s) => s[0]) },
    { name: "Secret rotation not picked up", cat: "Security", runs: 10, success: 90, updated: "2026-09-18", steps: TEMPLATES[8].steps.map((s) => s[0]) },
  ];

  /* ---------- reports ---------- */
  const days = [];
  const base = new Date("2026-09-27");
  const trend = [[1, 3, 6, 4], [0, 2, 5, 6], [2, 4, 7, 3], [1, 3, 4, 5], [0, 1, 3, 4], [0, 2, 2, 3], [3, 5, 6, 4],
                 [1, 4, 5, 6], [2, 3, 7, 5], [1, 2, 4, 3], [0, 3, 5, 4], [2, 4, 6, 2], [1, 3, 3, 4], [6, 8, 10, 4]];
  trend.forEach((v, i) => days.push({ day: new Date(+base + i * 86400000).toISOString().slice(5, 10), critical: v[0], high: v[1], medium: v[2], low: v[3] }));
  const mttr = [4.8, 4.5, 4.6, 4.1, 3.9, 3.6, 3.8, 3.2, 2.9, 2.7, 2.6, 2.3, 2.1, 1.9];
  const services = [
    ["payments-api", 14, 4, "Database"], ["order-api", 11, 3, "Network"], ["checkout-service", 9, 1, "Performance"],
    ["auth-api", 7, 1, "Network"], ["payments-worker", 6, 2, "Infrastructure"], ["SAP S/4HANA", 4, 0, "Application"], ["shipping-service", 4, 0, "Application"],
  ];

  const notifications = [
    { tone: "#ef4444", text: "Critical: Database connection pool exhausted", sub: "payments-api · ISS-3140 · 4 min ago", href: "#/issues?open=ISS-3140" },
    { tone: "#ef4444", text: "Critical: payments-worker OOMKilled", sub: "payments-worker · 9 min ago", href: "#/issues?severity=Critical" },
    { tone: "#8b5cf6", text: "ANL-2046 is awaiting your approval", sub: "auth-gateway.log · Staging", href: "#/agents" },
    { tone: "#ef4444", text: "Analysis ANL-2045 failed at Jira Ticket Agent", sub: "inventory-worker.log · 57 min ago", href: "#/agents" },
    { tone: "#22c55e", text: "OPS-1431 created for ISS-3138", sub: "Jira (simulated) · 1 h ago", href: "#/approvals" },
    { tone: "#f59e0b", text: "Slack delivery failed for #platform-alerts", sub: "Retry scheduled · 2 h ago", href: "#/approvals" },
  ];

  function fmt(d) {
    const p = (x) => String(x).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  }

  return { ENVS, TEMPLATES, analyses, issues, tickets, slack, playbooks, reports: { days, mttr, services }, notifications, people, channels, fmt };
})();
