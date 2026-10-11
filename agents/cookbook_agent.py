from __future__ import annotations

from models.schemas import AnalysisState, AnalysisStatus, Cookbook
from prompts.prompt_templates import COOKBOOK_SYSTEM_PROMPT, COOKBOOK_USER_PROMPT
from tools.config import settings
from tools.llm_client import get_llm_client
from tools.logging_config import logger
from tools.tracing import record_fallback


def synthesize_cookbook(state: AnalysisState) -> dict:
    """Cookbook agent: merge incidents and plans into one prioritized, checkable runbook."""
    try:
        cookbook = get_llm_client().structured_complete(
            prompt=COOKBOOK_USER_PROMPT.format(
                incidents="\n".join(i.model_dump_json(indent=2, exclude={"evidence_event_ids"}) for i in state.incidents),
                plans="\n".join(p.model_dump_json(indent=2) for p in state.remediation_plans),
            ),
            system_prompt=COOKBOOK_SYSTEM_PROMPT,
            model=settings.reasoning_model,
            schema=Cookbook,
        )
    except Exception:
        logger.exception("Cookbook generation failed; using fallback")
        record_fallback()
        cookbook = Cookbook(
            title="Incident Response Cookbook",
            executive_summary="One or more operational incidents were detected. Review evidence and remediation plans before acting.",
            timeline=[f"{i.severity.value}: {i.title}" for i in state.incidents],
            immediate_actions=_dedupe(a for p in state.remediation_plans for a in p.immediate_actions)[:10],
            investigation_steps=_dedupe(a for p in state.remediation_plans for a in p.investigation_steps)[:10],
            preventive_actions=_dedupe(a for p in state.remediation_plans for a in p.preventive_actions)[:10],
            communication_template="We are investigating an operational incident. Analysis and recommended actions are available.",
        )
    return {"cookbook": cookbook, "status": AnalysisStatus.COOKBOOK_GENERATED}


def _dedupe(items) -> list[str]:
    return list(dict.fromkeys(items))
