"""Thin OpenRouter client (OpenAI-compatible) that returns validated Pydantic objects."""
from __future__ import annotations

import json
import re
import time
from typing import Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from tools.config import settings
from tools.logging_config import logger
from tools.tracing import record_llm_call

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self):
        api_key = settings.openrouter_api_key.get_secret_value()
        if not api_key:
            raise LLMError("OPENROUTER_API_KEY is not configured.")
        self.client = OpenAI(
            api_key=api_key,
            base_url=settings.openrouter_base_url,
            timeout=settings.llm_timeout_s,
            max_retries=settings.llm_max_retries,
            default_headers={"X-Title": "C10_G4 DevOps Incident Analysis Suite"},
        )

    def complete(self, messages: list[dict], model: str, temperature: float | None = None, json_mode: bool = False) -> str:
        started = time.perf_counter()
        response = self.client.chat.completions.create(
            model=model,
            temperature=settings.llm_temperature if temperature is None else temperature,
            max_tokens=settings.llm_max_tokens,
            messages=messages,
            extra_body={"usage": {"include": True}},  # OpenRouter returns the exact cost in usage.cost
            **({"response_format": {"type": "json_object"}} if json_mode else {}),
        )
        content = response.choices[0].message.content if response.choices else None
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        cost = float(getattr(usage, "cost", 0) or 0)
        record_llm_call(model, prompt_tokens, completion_tokens, cost)
        logger.info(
            "LLM call model=%s latency_ms=%d prompt_tokens=%d completion_tokens=%d cost_usd=%.6f",
            model, (time.perf_counter() - started) * 1000, prompt_tokens, completion_tokens, cost,
        )
        if not content:
            raise LLMError("LLM returned an empty response.")
        return content

    def structured_complete(self, prompt: str, system_prompt: str, model: str, schema: Type[T], temperature: float | None = None) -> T:
        """Ask for JSON, validate it against `schema`, and give the model one chance to repair invalid output."""
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        last_error: Exception | None = None
        for _ in range(2):
            raw = self.complete(messages, model, temperature, json_mode=True)
            try:
                return schema.model_validate(self._extract_json(raw))
            except (LLMError, ValidationError, json.JSONDecodeError) as exc:
                last_error = exc
                logger.warning("LLM output did not match %s; asking for a repair", schema.__name__)
                messages += [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"That reply was invalid: {str(exc)[:1000]}\nReturn only corrected JSON."},
                ]
        raise LLMError(f"LLM output did not match {schema.__name__}: {last_error}")

    @staticmethod
    def _extract_json(raw: str) -> dict:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise LLMError("Could not extract JSON from LLM response.")
            return json.loads(match.group(0))


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
