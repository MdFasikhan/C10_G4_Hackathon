"""Guardrails for untrusted input and for anything the agents send outside the app.

Input:    reject binary uploads; detect and neutralize prompt-injection text inside log lines.
Output:   flag destructive remediation commands so a human reviews them before acting.
Outbound: redact secrets and block mass mentions (@channel, @here) in Slack/JIRA content.
"""
from __future__ import annotations

import re

from models.schemas import GuardrailFinding
from tools.log_parser import redact

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above|system) (instructions|prompts|rules)",
    r"forget (all |your )?(previous |prior )?instructions",
    r"you are now (a|an|the) ",
    r"(reveal|print|show|output) (your |the )?(system prompt|instructions|api key|secrets?)",
    r"new instructions?:",
    r"\bsystem prompt\b",
    r"\bjailbreak\b",
    r"(mark|classify|report) (everything|all (incidents|events|errors)) as (info|resolved|healthy)",
]
# Match from the trigger phrase to the end of its sentence, so the whole instruction is removed.
INJECTION_RE = re.compile("(?:" + "|".join(f"(?:{p})" for p in INJECTION_PATTERNS) + r")[^.!?\n]*[.!?]?", re.IGNORECASE)
INJECTION_PLACEHOLDER = "[filtered: possible prompt injection]"

DESTRUCTIVE_PATTERNS = [
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "recursive force delete (rm -rf)"),
    (r"\bdrop\s+(table|database|schema)\b", "SQL DROP"),
    (r"\btruncate\s+(table\s+)?\w+", "SQL TRUNCATE"),
    (r"\bdelete\s+from\s+\w+(?![^;]*\bwhere\b)", "SQL DELETE without WHERE"),
    (r"\bkubectl\s+delete\s+(namespace|ns|pvc|pv|deployment|statefulset|node)s?\b", "kubectl delete of a durable resource"),
    (r"--force\b|--grace-period=0", "forced operation"),
    (r"\bflushall\b|\bflushdb\b", "Redis flush"),
    (r"\bmkfs\b|\bdd\s+if=", "disk format/overwrite"),
    (r"\b(shutdown|reboot|halt)\s+(-h\s+)?now\b", "host shutdown"),
    (r"\bterraform\s+destroy\b", "terraform destroy"),
    (r"\bgit\s+push\s+.*--force|\bgit\s+push\s+-f\b", "force push"),
]
DESTRUCTIVE_RE = [(re.compile(p, re.IGNORECASE), label) for p, label in DESTRUCTIVE_PATTERNS]

MASS_MENTION_RE = re.compile(r"<!(channel|here|everyone)(\|[^>]*)?>|(?<![\w@])@(channel|here|everyone)\b", re.IGNORECASE)


def is_probably_binary(sample: bytes) -> bool:
    """Log files are text. NUL bytes or mostly undecodable bytes mean a binary file."""
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    decoded = sample.decode("utf-8", errors="replace")
    return decoded.count("�") / max(len(decoded), 1) > 0.10


def neutralize_injection(text: str) -> tuple[str, bool]:
    cleaned, count = INJECTION_RE.subn(INJECTION_PLACEHOLDER, text)
    return cleaned, count > 0


def destructive_reason(action: str) -> str | None:
    for pattern, label in DESTRUCTIVE_RE:
        if pattern.search(action):
            return label
    return None


def sanitize_outbound(text: str, max_chars: int) -> tuple[str, list[str]]:
    """Return cleaned text and the names of the rules that changed it."""
    rules = []
    redacted = redact(text)
    if redacted != text:
        rules.append("secret_redacted")
    cleaned, mentions = MASS_MENTION_RE.subn("[mention removed]", redacted)
    if mentions:
        rules.append("mass_mention_blocked")
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1] + "…"
        rules.append("truncated")
    return cleaned, rules


def finding(stage: str, rule: str, detail: str, severity: str = "WARNING") -> GuardrailFinding:
    return GuardrailFinding(stage=stage, rule=rule, severity=severity, detail=detail[:300])
