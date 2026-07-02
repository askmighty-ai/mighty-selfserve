"""App integration helpers for the production AI platform layer."""

from __future__ import annotations

from mighty.ai_metrics import cache_hit_metrics
from mighty.ai_provider import (
    DiscoveryContext,
    DiscoveryResult,
    ai_provider_name,
    get_field_discovery_provider,
)
from mighty.prompts import render_prompt


def build_field_discovery_context(
    *,
    site_name: str,
    source: str | None,
    snippets: str,
    today: str,
    category_hint: str,
) -> DiscoveryContext:
    rendered = render_prompt(
        "field_discovery",
        site=site_name,
        text=snippets,
        today=today,
        category_hint=category_hint,
    )
    return DiscoveryContext(
        site_name=site_name,
        source=source,
        prompt=rendered.text,
        prompt_id=rendered.prompt_id,
        prompt_version=rendered.version,
        today=today,
        category_hint=category_hint,
    )


def record_field_discovery_cache_hit(*, site_name: str) -> None:
    provider = get_field_discovery_provider()
    model = getattr(provider, "model", "") or (
        provider.models[0] if getattr(provider, "models", None) else "cached"
    )
    rendered = render_prompt(
        "field_discovery",
        site=site_name,
        text="",
        today="",
        category_hint="",
    )
    cache_hit_metrics(
        provider=ai_provider_name(),
        model=model or "cached",
        prompt_id=rendered.prompt_id,
        prompt_version=rendered.version,
    )


def render_missing_pages_prompt(*, source: str, missing_str: str) -> str:
    return render_prompt(
        "field_discovery_missing_pages",
        source=source,
        missing_str=missing_str,
    ).text


def discovery_log_suffix(result: DiscoveryResult) -> str:
    if not result.metrics:
        return ""
    m = result.metrics
    suffix = (
        f", prompt={m.prompt_id}@{m.prompt_version}, "
        f"latency={m.latency_ms:.0f}ms, cache_hit={m.cache_hit}"
    )
    if m.estimated_cost_usd is not None:
        suffix += f", est_cost=${m.estimated_cost_usd:.6f}"
    return suffix


def render_field_discovery_prompt_text(
    *,
    site_name: str,
    snippets: str,
    today: str,
    category_hint: str,
) -> str:
    return render_prompt(
        "field_discovery",
        site=site_name,
        text=snippets,
        today=today,
        category_hint=category_hint,
    ).text
