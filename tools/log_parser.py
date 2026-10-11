"""Turn raw log files (key=value text, JSON Lines, JSON arrays, CSV) into ParsedLogEvent objects.

Secrets are redacted before anything is stored or sent to an LLM.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from models.schemas import ParsedLogEvent
from tools.logging_config import logger

TIMESTAMP_PATTERNS = ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%d/%b/%Y:%H:%M:%S %z"]
LEADING_TIMESTAMP = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
LEVEL_PATTERN = re.compile(r"\b(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\b", re.IGNORECASE)
SERVICE_PATTERN = re.compile(r"(?:service|app|component)[=: ]+([A-Za-z0-9_.-]+)", re.IGNORECASE)
TRACE_PATTERN = re.compile(r"(?:trace[_-]?id|correlation[_-]?id)[=: ]+([A-Za-z0-9-]+)", re.IGNORECASE)
ERROR_CODE_PATTERN = re.compile(r"(?:error[_-]?code)[=: ]+([A-Za-z0-9_-]+)", re.IGNORECASE)
KV_PATTERN = re.compile(r'([A-Za-z_][\w.]*)=("(?:[^"\\]|\\.)*"|\S+)')

LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL", "CRIT": "CRITICAL", "EMERG": "CRITICAL",
                 "ALERT": "CRITICAL", "ERR": "ERROR", "TRACE": "DEBUG", "NOTICE": "INFO"}
KNOWN_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

REDACTIONS = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|client[_-]?secret)(\s*[=:]\s*)(\"[^\"]*\"|\S+)"), r"\1\2[REDACTED]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,})\b"), "[REDACTED]"),
    (re.compile(r"://([^:/@\s]+):([^@\s]+)@"), r"://\1:[REDACTED]@"),
]


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e12 else value
        return datetime.fromtimestamp(seconds, timezone.utc)
    text = str(value).strip("[](),\" ")
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace(",", "."))
    except ValueError:
        for pattern in TIMESTAMP_PATTERNS:
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _normalize_level(value: Any) -> str | None:
    if not value:
        return None
    level = str(value).strip().upper()
    level = LEVEL_ALIASES.get(level, level)
    return level if level in KNOWN_LEVELS else None


def _detect_level(line: str) -> str:
    match = LEVEL_PATTERN.search(line)
    if not match:
        return "INFO"
    return _normalize_level(match.group(1)) or "INFO"


def _event_id(line: str, line_number: int) -> str:
    # Line number is included so identical lines still get distinct, stable IDs.
    return hashlib.sha256(f"{line_number}:{line}".encode("utf-8")).hexdigest()[:12]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_line(line: str, line_number: int = 1) -> ParsedLogEvent | None:
    """Parse one plain-text line. Structured `key=value` fields win; regex heuristics are the fallback."""
    line = redact(line.rstrip("\r\n"))
    if not line.strip():
        return None
    fields = {k.lower(): _unquote(v) for k, v in KV_PATTERN.findall(line)}

    timestamp = _parse_timestamp(_first(fields, "timestamp", "time", "ts"))
    if timestamp is None and (match := LEADING_TIMESTAMP.match(line)):
        timestamp = _parse_timestamp(match.group(1))

    service = _first(fields, "service", "app", "component")
    if not service and (match := SERVICE_PATTERN.search(line)):
        service = match.group(1)
    error_code = _first(fields, "error_code", "errorcode", "error.code")
    if not error_code and (match := ERROR_CODE_PATTERN.search(line)):
        error_code = match.group(1)
    trace_id = _first(fields, "trace_id", "traceid", "correlation_id", "request_id")
    if not trace_id and (match := TRACE_PATTERN.search(line)):
        trace_id = match.group(1)

    return ParsedLogEvent(
        event_id=_event_id(line, line_number),
        line_number=line_number,
        timestamp=timestamp,
        service=service or "unknown",
        level=_normalize_level(_first(fields, "level", "severity", "lvl")) or _detect_level(line),
        message=str(_first(fields, "message", "msg") or line)[:2000],
        error_code=error_code,
        trace_id=trace_id,
        host=_first(fields, "host", "hostname", "pod"),
        raw_line=line[:4000],
    )


def _parse_record(record: dict[str, Any], raw_line: str, line_number: int) -> ParsedLogEvent:
    """Parse one structured record (JSON object or CSV row)."""
    raw_line = redact(raw_line)
    fields = {str(k).lower(): v for k, v in record.items()}
    message = _first(fields, "message", "msg", "log", "event", "text") or raw_line
    level = _normalize_level(_first(fields, "level", "severity", "levelname", "log.level", "lvl"))
    return ParsedLogEvent(
        event_id=_event_id(raw_line, line_number),
        line_number=line_number,
        timestamp=_parse_timestamp(_first(fields, "timestamp", "@timestamp", "time", "ts", "datetime")),
        service=str(_first(fields, "service", "service.name", "app", "application", "component") or "unknown"),
        level=level or _detect_level(str(message)),
        message=redact(str(message))[:2000],
        error_code=_str_or_none(_first(fields, "error_code", "errorcode", "error.code", "code")),
        trace_id=_str_or_none(_first(fields, "trace_id", "traceid", "trace.id", "correlation_id", "request_id")),
        host=_str_or_none(_first(fields, "host", "hostname", "host.name", "pod")),
        raw_line=raw_line[:4000],
    )


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_jsonl_line(line: str, line_number: int = 1) -> ParsedLogEvent | None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return parse_line(line, line_number)
    if not isinstance(payload, dict):
        return parse_line(line, line_number)
    return _parse_record(payload, line.rstrip("\r\n"), line_number)


def _parse_lines(lines: Iterable[str]) -> Iterable[ParsedLogEvent]:
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = parse_jsonl_line(line, number) if line.lstrip().startswith("{") else parse_line(line, number)
            if event:
                yield event
        except Exception:
            logger.exception("Failed to parse log line %d", number)


def _parse_csv(text: str) -> Iterable[ParsedLogEvent]:
    reader = csv.DictReader(io.StringIO(text))
    for number, row in enumerate(reader, start=2):  # line 1 is the header
        row = {k: v for k, v in row.items() if k}
        if any(v and v.strip() for v in row.values()):
            yield _parse_record(row, ",".join(str(v) for v in row.values()), number)


def parse_log_file(path: Path, max_events: int | None = None) -> list[ParsedLogEvent]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    suffix = Path(path).suffix.lower()

    events: Iterable[ParsedLogEvent]
    if suffix == ".csv":
        events = _parse_csv(text)
    elif suffix == ".json" and text.lstrip().startswith("["):
        try:
            records = json.loads(text)
            events = (
                _parse_record(r, json.dumps(r, default=str), i)
                for i, r in enumerate(records, start=1) if isinstance(r, dict)
            )
        except json.JSONDecodeError:
            events = _parse_lines(text.splitlines())
    else:
        events = _parse_lines(text.splitlines())

    parsed: list[ParsedLogEvent] = []
    for event in events:
        if max_events is not None and len(parsed) >= max_events:
            logger.warning("Log file %s has more than %d events; the rest were skipped", path, max_events)
            break
        parsed.append(event)
    return parsed
