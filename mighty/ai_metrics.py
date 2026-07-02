"""Metrics collection for AI provider calls."""

from __future__ import annotations

from dataclasses import dataclass

# Rough input cost per 1M tokens for observability (not billing).
_COST_PER_MILLION_INPUT: dict[str, dict[str, float]] = {
    "openai": {
        "gpt-5.4-mini": 0.15,
        "default": 0.15,
    },
    "gemini": {
        "gemini-2.5-flash": 0.075,
        "gemini-2.5-pro": 1.25,
        "default": 0.075,
    },
}


@dataclass(frozen=True)
class AIMetrics:
    provider: str
    model: str
    latency_ms: float
    cache_hit: bool
    estimated_cost_usd: float | None
    prompt_version: str
    prompt_id: str


class AIMetricsCollector:
    def __init__(self) -> None:
        self._entries: list[AIMetrics] = []

    def record(self, metrics: AIMetrics) -> None:
        self._entries.append(metrics)

    def recent(self, n: int = 10) -> list[AIMetrics]:
        return self._entries[-n:]

    def clear(self) -> None:
        self._entries.clear()


_collector = AIMetricsCollector()


def get_metrics_collector() -> AIMetricsCollector:
    return _collector


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    input_chars: int = 0,
    output_chars: int = 0,
) -> float | None:
    """Rough cost estimate from character counts (~4 chars/token)."""
    provider_rates = _COST_PER_MILLION_INPUT.get(provider, {})
    rate = provider_rates.get(model) or provider_rates.get("default")
    if rate is None:
        return None
    input_tokens = max(input_chars, 0) / 4.0
    output_tokens = max(output_chars, 0) / 4.0
    if output_chars == 0:
        output_tokens = input_tokens * 0.1
    total_tokens = input_tokens + output_tokens
    return (total_tokens / 1_000_000) * rate


def build_metrics(
    *,
    provider: str,
    model: str,
    latency_ms: float,
    cache_hit: bool,
    prompt_id: str,
    prompt_version: str,
    input_chars: int = 0,
    output_chars: int = 0,
) -> AIMetrics:
    return AIMetrics(
        provider=provider,
        model=model,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        estimated_cost_usd=estimate_cost_usd(
            provider=provider,
            model=model,
            input_chars=input_chars,
            output_chars=output_chars,
        ),
        prompt_version=prompt_version,
        prompt_id=prompt_id,
    )


def record_metrics(metrics: AIMetrics) -> None:
    get_metrics_collector().record(metrics)


def cache_hit_metrics(
    *,
    provider: str,
    model: str,
    prompt_id: str,
    prompt_version: str,
) -> AIMetrics:
    metrics = AIMetrics(
        provider=provider,
        model=model,
        latency_ms=0.0,
        cache_hit=True,
        estimated_cost_usd=0.0,
        prompt_version=prompt_version,
        prompt_id=prompt_id,
    )
    record_metrics(metrics)
    return metrics
