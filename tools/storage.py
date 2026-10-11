"""SQLite persistence for analyses and the audit trail.

Each operation opens its own connection, so the store is safe to use from
FastAPI's worker threads and background tasks at the same time.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from models.schemas import IN_PROGRESS_STATUSES, AnalysisResult, AnalysisStatus
from tools.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or settings.database_file
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    analysis_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    upload_filename TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at);
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    analysis_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_analysis ON audit_events(analysis_id);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_analysis(self, analysis_id: str, upload_filename: str) -> AnalysisResult:
        result = AnalysisResult(analysis_id=analysis_id, status=AnalysisStatus.PENDING, upload_filename=upload_filename)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO analyses VALUES (?, ?, ?, ?, ?, ?)",
                (analysis_id, now, now, upload_filename, result.status.value, result.model_dump_json()),
            )
        return result

    def save_result(self, result: AnalysisResult):
        with self._connect() as conn:
            conn.execute(
                "UPDATE analyses SET status = ?, result_json = ?, updated_at = ? WHERE analysis_id = ?",
                (result.status.value, result.model_dump_json(), _now(), result.analysis_id),
            )

    def get_result(self, analysis_id: str) -> AnalysisResult | None:
        with self._connect() as conn:
            row = conn.execute("SELECT status, result_json FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
        if not row:
            return None
        result = AnalysisResult.model_validate_json(row["result_json"])
        result.status = AnalysisStatus(row["status"])  # the column is the source of truth (see transition_status)
        return result

    def transition_status(self, analysis_id: str, allowed_from: Iterable[AnalysisStatus], to: AnalysisStatus) -> bool:
        """Atomically move to `to` only if the current status is in `allowed_from` (prevents double approval)."""
        allowed = [s.value for s in allowed_from]
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE analyses SET status = ?, updated_at = ? WHERE analysis_id = ? AND status IN ({','.join('?' * len(allowed))})",
                (to.value, _now(), analysis_id, *allowed),
            )
        return cursor.rowcount == 1

    def fail_interrupted_analyses(self) -> int:
        """Analyses still running when the server stopped will never finish; mark them FAILED."""
        stale = [s.value for s in IN_PROGRESS_STATUSES | {AnalysisStatus.NOTIFYING}]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT analysis_id FROM analyses WHERE status IN ({','.join('?' * len(stale))})", stale
            ).fetchall()
        for row in rows:
            result = self.get_result(row["analysis_id"])
            if result:
                result.status = AnalysisStatus.FAILED
                result.error = "Interrupted by a server restart. Upload the log again."
                self.save_result(result)
        return len(rows)

    def list_analyses(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT analysis_id, created_at, updated_at, upload_filename, status FROM analyses ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def performance_summary(self, limit: int = 100) -> dict[str, Any]:
        """Aggregate run metrics over the most recent finished analyses (read straight from the stored JSON)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT json_extract(result_json, '$.metrics.wall_time_ms') AS ms,
                       json_extract(result_json, '$.metrics.cost_usd') AS cost,
                       json_extract(result_json, '$.metrics.llm_calls') AS calls,
                       json_extract(result_json, '$.metrics.fallbacks') AS fallbacks,
                       status
                FROM analyses WHERE json_extract(result_json, '$.metrics') IS NOT NULL
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        if not rows:
            return {"runs": 0}
        times = sorted(r["ms"] or 0 for r in rows)
        return {
            "runs": len(rows),
            "avg_seconds": round(sum(times) / len(times) / 1000, 2),
            "p95_seconds": round(times[min(len(times) - 1, int(0.95 * len(times)))] / 1000, 2),
            "avg_cost_usd": round(sum(r["cost"] or 0 for r in rows) / len(rows), 5),
            "total_cost_usd": round(sum(r["cost"] or 0 for r in rows), 4),
            "avg_llm_calls": round(sum(r["calls"] or 0 for r in rows) / len(rows), 1),
            "runs_with_fallback_pct": round(100 * sum(1 for r in rows if r["fallbacks"]) / len(rows), 1),
            "failed_pct": round(100 * sum(1 for r in rows if r["status"] == "FAILED") / len(rows), 1),
        }

    def record_audit(self, analysis_id: str, actor: str, action: str, details: dict[str, Any]):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_events (analysis_id, created_at, actor, action, details_json) VALUES (?, ?, ?, ?, ?)",
                (analysis_id, _now(), actor, action, json.dumps(details, default=str)),
            )

    def get_audit(self, analysis_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, actor, action, details_json FROM audit_events WHERE analysis_id = ? ORDER BY event_id",
                (analysis_id,),
            ).fetchall()
        return [{**{k: row[k] for k in ("created_at", "actor", "action")}, "details": json.loads(row["details_json"])} for row in rows]


_store: AnalysisStore | None = None


def get_store() -> AnalysisStore:
    global _store
    if _store is None:
        _store = AnalysisStore()
    return _store
