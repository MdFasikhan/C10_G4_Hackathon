"""Validate the configured OpenRouter models.

    python scripts/check_models.py          # catalog checks only (free)
    python scripts/check_models.py --live   # also sends one tiny JSON request per model (costs < $0.001)

Checks: the API key works, each model exists on OpenRouter, it supports JSON
`response_format`, its context window, price, and (with --live) that it returns
JSON that validates against a Pydantic schema.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from tools.config import settings  # noqa: E402


class Ping(BaseModel):
    status: str
    severity: str


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="send one small request to each model")
    args = parser.parse_args()

    key = settings.openrouter_api_key.get_secret_value()
    if not key:
        print("FAIL  OPENROUTER_API_KEY is not set in .env")
        return 1
    base = settings.openrouter_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {key}"}

    key_info = httpx.get(f"{base}/key", headers=headers, timeout=20)
    if key_info.status_code != 200:
        print(f"FAIL  API key rejected (HTTP {key_info.status_code})")
        return 1
    data = key_info.json().get("data", {})
    remaining = data.get("limit_remaining")
    print(f"OK    API key valid; credit remaining: {'unlimited' if remaining is None else f'${remaining:.2f}'}")

    catalog = {m["id"]: m for m in httpx.get(f"{base}/models", timeout=30).json()["data"]}
    ok = True
    for role, model_id in (("CLASSIFIER_MODEL", settings.classifier_model), ("REASONING_MODEL", settings.reasoning_model)):
        model = catalog.get(model_id)
        if not model:
            print(f"FAIL  {role}={model_id} not found on OpenRouter")
            ok = False
            continue
        params = set(model.get("supported_parameters") or [])
        json_ok = "response_format" in params or "structured_outputs" in params
        price_in = float(model["pricing"]["prompt"]) * 1e6
        price_out = float(model["pricing"]["completion"]) * 1e6
        print(
            f"{'OK  ' if json_ok else 'WARN'}  {role}={model_id}: context {model.get('context_length'):,} tokens, "
            f"${price_in:.2f}/${price_out:.2f} per 1M in/out, JSON mode {'supported' if json_ok else 'NOT supported'}"
        )
        ok &= json_ok

        if args.live:
            from tools.llm_client import get_llm_client

            try:
                result = get_llm_client().structured_complete(
                    prompt='Return JSON {"status": "ok", "severity": "INFO"}',
                    system_prompt="Return valid JSON only.",
                    model=model_id,
                    schema=Ping,
                )
                print(f"OK    {model_id} live call returned valid JSON: {result.model_dump()}")
            except Exception as exc:
                print(f"FAIL  {model_id} live call: {exc}")
                ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
