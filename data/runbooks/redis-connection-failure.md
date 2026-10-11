# Redis Connection Failure Runbook

## Symptoms
- ECONNREFUSED errors
- Cache miss increase
- Application startup failures

## Diagnosis
1. Verify Redis pod or instance status.
2. Check Redis service DNS and port connectivity.
3. Review Redis memory and replication status.

## Immediate Mitigation
1. Restart Redis if safe.
2. Restore from the latest healthy snapshot if acceptable.
3. Verify application retry configuration.

## Prevention
- Monitor Redis availability, memory, and replication lag.
- Use persistent storage and backups.
