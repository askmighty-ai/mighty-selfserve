"""
mighty.home_ui
──────────────
Render Mighty Home — Truth Dashboard (single-provider capability instrument).
"""

from __future__ import annotations

from typing import Any, Callable

from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    PipelineStage,
    TRUTH_PROVIDER_DISPLAY,
    build_capability_view,
)
from mighty.customer_account_access import CustomerAccountAccessView
from mighty.home_state import HomeStateResult


def _evidence_mark(item: EvidenceItem) -> str:
    if item.ok is True:
        return "✓ "
    if item.ok is False:
        return "✗ "
    return ""


def _render_evidence(
    evidence: tuple[EvidenceItem, ...],
    escape: Callable[[Any], str],
) -> str:
    if not evidence:
        return ""
    rows = "".join(
        f'<li class="dash-truth-evidence-item">{escape(_evidence_mark(e) + e.text)}</li>'
        for e in evidence
    )
    return (
        f'<section class="dash-truth-evidence" aria-label="Why Mighty believes this">'
        f'<p class="dash-truth-section-label">Why Mighty believes this</p>'
        f'<ul class="dash-truth-evidence-list">{rows}</ul>'
        f"</section>"
    )


def _render_explanations(
    explanations: tuple[str, ...],
    escape: Callable[[Any], str],
) -> str:
    if not explanations:
        return ""
    items = "".join(
        f'<li class="dash-truth-explain-item">{escape(line)}</li>'
        for line in explanations
    )
    return f'<ul class="dash-truth-explain">{items}</ul>'


def _render_extracted(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    if capability.state != CapabilityState.EXTRACTION_SUCCESS:
        return ""
    if not capability.extracted_fields:
        return (
            f'<section class="dash-truth-extracted" aria-label="Extracted data">'
            f'<p class="dash-truth-section-label">Extracted data</p>'
            f'<p class="dash-truth-empty">No field values in the latest snapshot.</p>'
            f"</section>"
        )
    rows = "".join(
        f'<div class="dash-truth-field">'
        f'<dt>{escape(f.label)}</dt>'
        f'<dd>{escape(f.value)}</dd>'
        f"</div>"
        for f in capability.extracted_fields
    )
    meta_parts: list[str] = []
    if capability.last_verified:
        meta_parts.append(f"Last verified: {capability.last_verified}")
    if capability.confidence:
        meta_parts.append(f"Confidence: {capability.confidence}")
    meta_html = ""
    if meta_parts:
        meta_html = (
            f'<p class="dash-truth-meta">{escape(" · ".join(meta_parts))}</p>'
        )
    return (
        f'<section class="dash-truth-extracted" aria-label="Extracted data">'
        f'<p class="dash-truth-section-label">Extracted data</p>'
        f'<dl class="dash-truth-fields">{rows}</dl>'
        f"{meta_html}"
        f"</section>"
    )


def _render_meta_only(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    """Last verified / confidence when not showing the extracted-data block."""
    if capability.state == CapabilityState.EXTRACTION_SUCCESS:
        return ""
    parts: list[str] = []
    if capability.last_verified:
        parts.append(f"Last verified: {capability.last_verified}")
    if capability.confidence:
        parts.append(f"Confidence: {capability.confidence}")
    if not parts:
        return ""
    return f'<p class="dash-truth-meta">{escape(" · ".join(parts))}</p>'


def _render_action(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    if not capability.action_required or not capability.action_label or not capability.action_url:
        return ""
    external = capability.action_url.startswith("http")
    target = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return (
        f'<a href="{escape(capability.action_url)}" '
        f'class="dash-brief-featured-cta dash-truth-cta"{target}>'
        f'{escape(capability.action_label)}</a>'
    )


def _pipeline_row(stage: PipelineStage, escape: Callable[[Any], str]) -> str:
    bits: list[str] = [stage.verdict]
    if stage.timestamp:
        bits.append(stage.timestamp)
    if stage.detail:
        bits.append(stage.detail)
    if stage.id_label:
        bits.append(stage.id_label)
    return (
        f'<div class="dash-truth-pipeline-stage" data-verdict="{escape(stage.verdict)}">'
        f'<span class="dash-truth-pipeline-name">{escape(stage.name)}</span>'
        f'<span class="dash-truth-pipeline-verdict">{escape(stage.verdict)}</span>'
        f'<span class="dash-truth-pipeline-detail">{escape(" · ".join(bits[1:]) if len(bits) > 1 else "—")}</span>'
        f"</div>"
    )


def _render_technical_details(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    stages = capability.pipeline
    if not stages:
        return ""
    body_parts: list[str] = []
    for i, stage in enumerate(stages):
        body_parts.append(_pipeline_row(stage, escape))
        if i < len(stages) - 1:
            body_parts.append('<div class="dash-truth-pipeline-arrow" aria-hidden="true">↓</div>')
    return (
        f'<details class="dash-truth-tech">'
        f"<summary>Technical Details</summary>"
        f'<div class="dash-truth-pipeline">'
        f'<p class="dash-truth-section-label">Pipeline</p>'
        f'{"".join(body_parts)}'
        f"</div>"
        f"</details>"
    )


def render_capability_panel(
    capability: CapabilityView,
    *,
    escape: Callable[[Any], str],
) -> str:
    """Render one provider Truth panel from CapabilityView only."""
    return (
        f'<article class="dash-truth-panel" data-provider="{escape(capability.provider)}" '
        f'data-capability="{escape(capability.state.value)}">'
        f'<h2 class="dash-truth-provider">{escape(capability.display_name)}</h2>'
        f'<p class="dash-truth-headline">{escape(capability.headline)}</p>'
        f"{_render_explanations(capability.explanations, escape)}"
        f"{_render_evidence(capability.evidence, escape)}"
        f"{_render_extracted(capability, escape)}"
        f"{_render_meta_only(capability, escape)}"
        f"{_render_action(capability, escape)}"
        f"{_render_technical_details(capability, escape)}"
        f"</article>"
    )


def render_account_access_row(
    view: CustomerAccountAccessView,
    *,
    escape: Callable[[Any], str],
    show_debug: bool = False,
    extracted_items: list[dict] | None = None,
    session_confidence: str | None = None,
) -> str:
    """Compatibility wrapper — maps access view → CapabilityView → Truth panel."""
    del show_debug
    capability = build_capability_view(
        view,
        display_name=view.display_name,
        provider=view.provider,
        extracted_items=extracted_items,
        session_confidence=session_confidence,
    )
    return render_capability_panel(capability, escape=escape)


def render_account_access_table(
    views: list[CustomerAccountAccessView],
    *,
    escape: Callable[[Any], str],
    show_debug: bool = False,
    extracted_items_by_provider: dict[str, list[dict]] | None = None,
    session_confidence_by_provider: dict[str, str] | None = None,
) -> str:
    del show_debug
    items_map = extracted_items_by_provider or {}
    conf_map = session_confidence_by_provider or {}
    if not views:
        capability = build_capability_view(None)
        return (
            f'<section class="dash-truth" aria-label="{escape(TRUTH_PROVIDER_DISPLAY)}">'
            f"{render_capability_panel(capability, escape=escape)}"
            f"</section>"
        )
    panels = "".join(
        render_capability_panel(
            build_capability_view(
                view,
                display_name=view.display_name,
                provider=view.provider,
                extracted_items=items_map.get(view.provider),
                session_confidence=conf_map.get(view.provider),
                login_url=view.user_action_url,
            ),
            escape=escape,
        )
        for view in views
    )
    return (
        f'<section class="dash-truth" aria-label="{escape(TRUTH_PROVIDER_DISPLAY)}">'
        f"{panels}"
        f"</section>"
    )


def render_home_page(
    result: HomeStateResult,
    *,
    first_name: str,
    today_label: str,
    last_checked: str = "",
    escape: Callable[[Any], str],
) -> str:
    """Render the Truth Dashboard — Amex capability instrument only."""
    del first_name, today_label  # Greeting removed; diagnostic instrument only.
    capability = result.capability
    if capability is None:
        view = result.access_views[0] if result.access_views else None
        capability = build_capability_view(
            view,
            display_name=(view.display_name if view else TRUTH_PROVIDER_DISPLAY),
            provider=(view.provider if view else "amex"),
            extracted_items=result.extracted_items,
            session_confidence=result.session_confidence,
            login_url=(
                view.user_action_url
                if view and view.user_action_url
                else result.provider_open_url
            ),
        )

    footer = ""
    if last_checked:
        footer = (
            f'<footer class="dash-home-footer">'
            f'Last checked: {escape(last_checked)}'
            f"</footer>"
        )

    return (
        f'<div class="dash-hero">'
        f'<div class="dash-brief-card dash-brief-card--exec dash-truth-card">'
        f'<div class="dash-brief-exec">'
        f'<header class="dash-brief-header">'
        f'<h1 class="dash-brief-greeting">Mighty</h1>'
        f'<p class="dash-truth-subhead">Can Mighty see and extract authenticated American Express account data?</p>'
        f"</header>"
        f'<section class="dash-brief-primary" aria-label="Capability status">'
        f"{render_capability_panel(capability, escape=escape)}"
        f"</section>"
        f"{footer}"
        f"</div>"
        f"</div>"
        f"</div>"
    )
