"""HTTP POST with retries for outbound integrations (Slack, JIRA)."""
from __future__ import annotations

import time

import httpx

from tools.logging_config import logger

RETRY_STATUSES = {429, 500, 502, 503, 504}


def post_with_retry(url: str, attempts: int = 3, backoff_s: float = 1.0, **kwargs) -> httpx.Response:
    """Retry on network errors, rate limits (honouring Retry-After) and 5xx. Other 4xx are returned at once."""
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.post(url, timeout=kwargs.pop("timeout", 15), **kwargs)
            if response.status_code not in RETRY_STATUSES or attempt == attempts:
                return response
            wait = float(response.headers.get("Retry-After", backoff_s * 2 ** (attempt - 1)))
            logger.warning("Outbound POST got HTTP %s; retry %d/%d in %.1fs", response.status_code, attempt, attempts - 1, wait)
        except httpx.TransportError as exc:
            if attempt == attempts:
                raise
            wait = backoff_s * 2 ** (attempt - 1)
            logger.warning("Outbound POST failed (%s); retry %d/%d in %.1fs", type(exc).__name__, attempt, attempts - 1, wait)
        time.sleep(min(wait, 10))
    raise RuntimeError("unreachable")
