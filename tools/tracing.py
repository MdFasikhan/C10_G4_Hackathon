"""Per-agent tracing: latency, LLM calls, tokens, cost and fallbacks.

`@traced("Classifier")` wraps a LangGraph node. While the node runs, every LLM call
and every fallback is recorded into a context variable; the node's update then gets
an AgentRun appended. `run_parallel` keeps the trace when work fans out to threads.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Iterable, TypeVar

from models.schemas import AgentRun, RunMetrics

T = TypeVar("T")
R = TypeVar("R")


@dataclass
class _Trace:
    calls: list[dict] = field(default_factory=list)
    fallbacks: int = 0


_current: ContextVar[_Trace | None] = ContextVar("agent_trace", default=None)


def record_llm_call(model: str, prompt_tokens: int, completion_tokens: int, cost_usd: float):
    if trace := _current.get():
        trace.calls.append({"model": model, "prompt": prompt_tokens, "completion": completion_tokens, "cost": cost_usd})


def record_fallback():
    if trace := _current.get():
        trace.fallbacks += 1


def traced(agent: str):
    def decorator(node: Callable[..., dict]) -> Callable[..., dict]:
        @wraps(node)
        def wrapper(state):
            trace = _Trace()
            token = _current.set(trace)
            started = time.perf_counter()
            started_at = time.time()
            try:
                update = node(state) or {}
            finally:
                _current.reset(token)
            run = AgentRun(
                agent=agent,
                started_at=started_at,
                latency_ms=round((time.perf_counter() - started) * 1000),
                llm_calls=len(trace.calls),
                prompt_tokens=sum(c["prompt"] for c in trace.calls),
                completion_tokens=sum(c["completion"] for c in trace.calls),
                cost_usd=round(sum(c["cost"] for c in trace.calls), 6),
                models=sorted({c["model"] for c in trace.calls}),
                fallback=trace.fallbacks > 0,
            )
            return {**update, "agent_runs": [run]}
        return wrapper
    return decorator


def run_parallel(fn: Callable[[T], R], items: Iterable[T], max_workers: int = 4) -> list[R]:
    """ThreadPoolExecutor.map that keeps the caller's trace context in every worker."""
    items = list(items)
    if not items:
        return []
    contexts = [copy_context() for _ in items]
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(lambda pair: pair[0].run(fn, pair[1]), zip(contexts, items)))


def summarize(runs: list[AgentRun], wall_time_ms: int) -> RunMetrics:
    return RunMetrics(
        wall_time_ms=wall_time_ms,
        llm_calls=sum(r.llm_calls for r in runs),
        prompt_tokens=sum(r.prompt_tokens for r in runs),
        completion_tokens=sum(r.completion_tokens for r in runs),
        cost_usd=round(sum(r.cost_usd for r in runs), 6),
        fallbacks=sum(r.fallback for r in runs),
    )
