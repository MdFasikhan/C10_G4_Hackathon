# Database Timeout Runbook

## Symptoms
- Database query timeout errors
- Rising API 5xx rates
- Connection pool saturation

## Diagnosis
1. Check database CPU, memory, IOPS, and active connections.
2. Review slow query logs and long-running transactions.
3. Correlate timeouts with deployments, batch jobs, and traffic spikes.

## Immediate Mitigation
1. Identify and terminate long-running or blocking queries if safe.
2. Temporarily increase connection-pool limits only if database capacity allows.
3. Enable circuit breakers to prevent cascading failures.

## Prevention
- Add query-timeout and pool-utilization alerts.
- Review slow queries regularly.
- Load-test before major releases.
