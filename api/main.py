from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routes.actions import router as actions_router
from api.routes.analysis import router as analysis_router
from api.security import require_api_key
from tools.config import PROJECT_ROOT, settings
from tools.logging_config import configure_logging, logger
from tools.storage import get_store
from tools.vector_store import get_vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    for path in (settings.upload_path, settings.runbook_path):
        path.mkdir(parents=True, exist_ok=True)
    if not settings.openrouter_api_key.get_secret_value():
        logger.warning("OPENROUTER_API_KEY is not set: agents will use rule-based fallbacks.")
    if not settings.api_key.get_secret_value():
        logger.warning("API_KEY is not set: the API is unauthenticated (fine on localhost, not in production).")
    interrupted = get_store().fail_interrupted_analyses()
    if interrupted:
        logger.warning("Marked %d interrupted analyses as FAILED", interrupted)
    try:
        await asyncio.to_thread(get_vector_store().sync_runbooks, settings.runbook_path)
    except Exception:
        logger.exception("Runbook indexing failed; remediation will run without runbook context")
    yield


app = FastAPI(title="Multi-Agent DevOps Incident Analysis Suite", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"], allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Give every request an ID (echoed in X-Request-ID) and log method, path, status and latency."""
    request_id = request.headers.get("X-Request-ID") or uuid4().hex[:16]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error request_id=%s %s %s", request_id, request.method, request.url.path)
        response = JSONResponse({"detail": "Internal server error.", "request_id": request_id}, status_code=500)
    response.headers["X-Request-ID"] = request_id
    if request.url.path not in ("/health", "/ready"):
        logger.info("request_id=%s %s %s -> %d in %.0fms", request_id, request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000)
    return response


app.include_router(analysis_router, prefix="/api")
app.include_router(actions_router, prefix="/api")
# The dashboard UI is served from the same origin as the API, so secrets never leave the server.
app.mount("/ui", StaticFiles(directory=PROJECT_ROOT / "frontend", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")


@app.get("/api/metrics", dependencies=[Depends(require_api_key)], tags=["operations"])
def metrics(limit: int = 100):
    """Performance and cost over recent analyses: latency (avg/p95), cost, LLM calls, fallback and failure rates."""
    return get_store().performance_summary(limit=min(max(limit, 1), 1000))


@app.get("/health", tags=["operations"])
def health():
    """Liveness: the process is up. Also shows non-secret configuration for the UI."""
    return {
        "status": "healthy",
        "environment": settings.app_env,
        "llm_configured": bool(settings.openrouter_api_key.get_secret_value()),
        "models": {"classifier": settings.classifier_model, "reasoning": settings.reasoning_model},
        "slack_mode": settings.slack_mode,
        "jira_mode": settings.jira_mode,
        "auth_enabled": bool(settings.api_key.get_secret_value()),
    }


@app.get("/ready", tags=["operations"])
def ready():
    """Readiness: dependencies the app needs to serve traffic (database, runbook index)."""
    checks = {}
    try:
        get_store().list_analyses(limit=1)
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        checks["runbook_chunks"] = get_vector_store().collection.count()
    except Exception as exc:
        checks["runbook_chunks"] = f"error: {type(exc).__name__}"
    ok = checks["database"] == "ok" and isinstance(checks["runbook_chunks"], int) and checks["runbook_chunks"] > 0
    return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)
