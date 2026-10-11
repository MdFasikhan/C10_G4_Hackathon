# Kubernetes Pod Restart Runbook

## Symptoms
- Pods restarting frequently
- OOMKilled events
- CrashLoopBackOff
- Readiness probe failures

## Diagnosis
1. Run `kubectl describe pod <pod-name>`.
2. Check previous container logs with `kubectl logs --previous`.
3. Review memory and CPU limits.
4. Check recent deployment changes.

## Immediate Mitigation
1. Increase memory limits if required.
2. Roll back a recent deployment if needed.
3. Fix memory leaks or excessive caching.

## Prevention
- Set realistic resource requests and limits.
- Add memory and restart-count alerts.
