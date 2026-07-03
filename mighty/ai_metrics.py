"""Metrics collection for AI provider calls."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True)
class AIMetrics:
    provider: str
    model: str
    latency_ms: float
    cache_hit: bool
    estimated_cost_usd: float | None
    prompt_version: str
    prompt_id: str = "field_discovery"
    input_chars: int = 0
    output_chars: int = 0

    def estimated_token_count(self) -> int:
        return estimate_tokens(self.input_chars) + estimate_tokens(self.output_chars)


_COST_PER_MILLION: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.15, 0.60),
    "gpt-4o-mini": (0.15, 0.60),
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-2.5-pro": (1.25, 5.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
}


def estimate_tokens(char_count: int) -> int:
    return max(1, char_count // 4) if char_count > 0 else 0


def estimate_cost_usd(provider: str, model: str, *, input_chars: int, output_chars: int) -> float | None:
    rates = _COST_PER_MILLION.get(model)
    if rates is None:
        if provider in ("openai",) or (model and model.startswith("gpt")):
            rates = _COST_PER_MILLION["gpt-5.4-mini"]
        elif provider in ("gemini",) or (model and model.startswith("gemini")):
            rates = _COST_PER_MILLION["gemini-2.5-flash"]
        elif provider == "anthropic":
            rates = _COST_PER_MILLION["claude-haiku-4-5-20251001"]
        else:
            return None
    inp, out = estimate_tokens(input_chars), estimate_tokens(output_chars)
    ir, orr = rates
    return (inp * ir + out * orr) / 1_000_000


def build_metrics(
    *,
    provider: str,
    model: str,
    latency_ms: float,
    cache_hit: bool,
    prompt_id: str,
    prompt_version: str,
    input_text: str = "",
    output_text: str = "",
) -> AIMetrics:
    ic, oc = len(input_text or ""), len(output_text or "")
    cost = None if cache_hit else estimate_cost_usd(provider, model, input_chars=ic, output_chars=oc)
    return AIMetrics(provider, model, latency_ms, cache_hit, cost, prompt_version, prompt_id, ic, oc)


class AIMetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[AIMetrics] = []

    def record(self, metrics: AIMetrics) -> None:
        with self._lock:
            self._entries.append(metrics)

    def recent(self, limit: int = 100) -> list[AIMetrics]:
        with self._lock:
            return list(self._entries[-limit:])

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_collector = AIMetricsCollector()


def get_metrics_collector() -> AIMetricsCollector:
    return _collector


def record_metrics(metrics: AIMetrics, *, failure_reason: str | None = None, source: str | None = None) -> AIMetrics:
    _collector.record(metrics)
    from mighty.ai_observability import observe_request
    observe_request(metrics, failure_reason=failure_reason, source=source)
    return metrics


def cache_hit_metrics(*, provider: str, model: str, prompt_id: str, prompt_version: str, source: str | None = None) -> AIMetrics:
    return record_metrics(
        build_metrics(provider=provider, model=model, latency_ms=0.0, cache_hit=True,
                      prompt_id=prompt_id, prompt_version=prompt_version),
        source=source,
    )


def with_cache_hit(metrics: AIMetrics, *, cache_hit: bool) -> AIMetrics:
    return record_metrics(replace(metrics, cache_hit=cache_hit))
