from __future__ import annotations

from pathlib import Path

from models.schemas import AnalysisState, AnalysisStatus
from tools.config import settings
from tools.guardrails import finding, neutralize_injection
from tools.log_parser import parse_log_file
from tools.logging_config import logger


def parse_logs(state: AnalysisState) -> dict:
    """Log reader agent: parse the uploaded file into structured events, then screen them (input guardrail)."""
    upload_path = Path(state.upload_path)
    logger.info("Parsing log file: %s", upload_path.name)
    try:
        events = parse_log_file(upload_path, max_events=settings.max_parsed_events)
    except Exception as exc:
        logger.exception("Log parsing failed")
        return {"status": AnalysisStatus.FAILED, "error": f"Log parsing failed: {exc}"}

    # Log text reaches LLM prompts, so instructions hidden in it are neutralized before any agent sees it.
    findings = []
    for event in events:
        cleaned, hit = neutralize_injection(event.message)
        if hit:
            event.message = cleaned
            findings.append(finding("input", "prompt_injection", f"Line {event.line_number}: instruction-like text removed before analysis.", "ERROR"))
    logger.info("Parsed %d events (%d flagged by input guardrail)", len(events), len(findings))
    return {"parsed_events": events, "guardrail_findings": findings, "status": AnalysisStatus.LOGS_PARSED}
