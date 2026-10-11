"""API protection: optional API-key auth and a per-client rate limit for uploads."""
from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from tools.config import settings


def require_api_key(x_api_key: str | None = Header(default=None)):
    """Enforced only when API_KEY is set. Uses a constant-time comparison."""
    expected = settings.api_key.get_secret_value()
    if expected and not hmac.compare_digest(x_api_key or "", expected):
        raise HTTPException(status_code=401, detail="Missing or invalid API key.")


class RateLimiter:
    """Sliding one-minute window per client IP. In-memory: fine for one instance; use Redis when scaling out."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                retry_after = int(60 - (now - hits[0])) + 1
                raise HTTPException(status_code=429, detail="Too many uploads. Try again shortly.",
                                    headers={"Retry-After": str(retry_after)})
            hits.append(now)


upload_rate_limit = RateLimiter(settings.rate_limit_uploads_per_minute)
