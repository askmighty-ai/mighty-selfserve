"""
mighty.home_ui
──────────────
Render Mighty Home V2 (Living Calm) — pure projection over Attention +
enrollment context, composed with the production design system.

Truth / Capability panels remain available for debug only (show_access_debug).

See docs/HOME_V1.md, docs/LIVING_CALM_V1.md, docs/QUIET_FIELD_V2.md.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence

from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    PresentationTimelineEvent,
    PresentationTimelineSection,
    TRUTH_PROVIDER_DISPLAY,
    build_capability_view,
)
from mighty.attention_view import AttentionView
from mighty.customer_account_access import CustomerAccountAccessView
from mighty.customer_local_time import format_customer_local_time
from mighty.design_system.components import (
    render_button,
    render_hero,
    render_section,
    render_status_badge,
)
from mighty.home_projection import (
    HomeCard,
    HomeEvidenceItem,
    HomeProjection,
    HomeWin,
    attention_to_card,
    project_home,
)
from mighty.home_state import HomeStateResult
from mighty.truth_validation import (
    TruthEvidence,
    TruthPipelineStage,
    TruthValidation,
)
from mighty import user_copy


def _ts_html(value: str | None) -> str:
    """Browser-local <time> element; canonical UTC stays in datetime/title."""
    return format_customer_local_time(value)


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
    if capability.presentation_phase == "determining":
        return ""
    # Stale / historical cards must not imply current extracted values.
    if capability.status_is_historical:
        return ""
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
    meta_bits: list[str] = []
    ts_label = capability.timestamp_label or "Latest check completed"
    if capability.last_verified:
        meta_bits.append(f"{escape(ts_label)}: {_ts_html(capability.last_verified)}")
    if capability.confidence:
        meta_bits.append(f"Confidence: {escape(capability.confidence)}")
    meta_html = ""
    if meta_bits:
        meta_html = f'<p class="dash-truth-meta">{" · ".join(meta_bits)}</p>'
    return (
        f'<section class="dash-truth-extracted" aria-label="Extracted data">'
        f'<p class="dash-truth-section-label">Extracted data</p>'
        f'<dl class="dash-truth-fields">{rows}</dl>'
        f"{meta_html}"
        f"</section>"
    )


def _render_historical(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    """Explicitly labeled prior result — never present-tense current truth."""
    if not capability.status_is_historical or not capability.historical_summary:
        return ""
    parts = [
        f'<p class="dash-truth-historical-summary">'
        f"{escape(capability.historical_summary)}</p>"
    ]
    if capability.previous_confirmed_at:
        verb = (capability.historical_timestamp_label or "Confirmed").strip()
        parts.append(
            f'<p class="dash-truth-historical-meta">'
            f"{escape(verb)} {_ts_html(capability.previous_confirmed_at)}</p>"
        )
    return (
        f'<section class="dash-truth-historical" aria-label="Previous confirmed result">'
        f'{"".join(parts)}'
        f"</section>"
    )


def _render_meta_only(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    """Timestamp / confidence when not showing the extracted-data block."""
    if (
        capability.presentation_phase == "terminal"
        and capability.state == CapabilityState.EXTRACTION_SUCCESS
    ):
        return ""
    parts: list[str] = []
    if capability.presentation_phase == "determining":
        if capability.current_check_started_at:
            label = capability.timestamp_label or "Checking started"
            parts.append(
                f"{escape(label)}: {_ts_html(capability.current_check_started_at)}"
            )
    elif capability.last_verified:
        label = capability.timestamp_label or "Latest check completed"
        parts.append(f"{escape(label)}: {_ts_html(capability.last_verified)}")
    if capability.confidence and capability.presentation_phase == "terminal":
        parts.append(f"Confidence: {escape(capability.confidence)}")
    if not parts:
        return ""
    return f'<p class="dash-truth-meta">{" · ".join(parts)}</p>'


def _render_action(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    parts: list[str] = []
    # Explicit command — never fired by reload / GET polls.
    checking = bool(capability.is_refreshing) or capability.presentation_phase == "determining"
    disabled = " disabled" if checking else ""
    label = "Checking…" if checking else "Check now"
    parts.append(
        f'<button type="button" class="dash-truth-check-now" '
        f'id="amex-check-now-btn" data-provider="amex"{disabled}>'
        f"{escape(label)}</button>"
    )
    if capability.action_required and capability.action_label and capability.action_url:
        external = capability.action_url.startswith("http")
        target = ' target="_blank" rel="noopener noreferrer"' if external else ""
        parts.append(
            f'<a href="{escape(capability.action_url)}" '
            f'class="dash-brief-featured-cta dash-truth-cta"{target}>'
            f"{escape(capability.action_label)}</a>"
        )
    return "".join(parts)


def _pipeline_row(stage: TruthPipelineStage, escape: Callable[[Any], str]) -> str:
    bits: list[str] = []
    if stage.timestamp:
        bits.append(_ts_html(stage.timestamp))
    if stage.duration_ms is not None:
        bits.append(escape(f"{stage.duration_ms}ms"))
    if stage.detail:
        bits.append(escape(stage.detail))
    if stage.evidence_ids:
        bits.append(escape("evidence:" + ",".join(stage.evidence_ids)))
    verdict_label = (
        "NOT RUN" if stage.verdict.value == "NOT_RUN" else stage.verdict.value
    )
    detail = " · ".join(bits) if bits else "—"
    return (
        f'<div class="dash-truth-pipeline-stage" data-verdict="{escape(stage.verdict.value)}">'
        f'<span class="dash-truth-pipeline-name">{escape(stage.name)}</span>'
        f'<span class="dash-truth-pipeline-verdict">{escape(verdict_label)}</span>'
        f'<span class="dash-truth-pipeline-detail">{detail}</span>'
        f"</div>"
    )


def _timeline_row(item: TruthEvidence, escape: Callable[[Any], str]) -> str:
    ts_html = _ts_html(item.timestamp) if item.timestamp else "—"
    return (
        f'<div class="dash-truth-timeline-row" data-outcome="{escape(item.outcome.value)}">'
        f'<span class="dash-truth-timeline-ts">{ts_html}</span>'
        f'<span class="dash-truth-timeline-desc">{escape(item.description)}</span>'
        f'<span class="dash-truth-timeline-outcome">{escape(item.outcome.value)}</span>'
        f"</div>"
    )


def _presentation_timeline_row(
    item: PresentationTimelineEvent,
    escape: Callable[[Any], str],
) -> str:
    ts_html = _ts_html(item.timestamp) if item.timestamp else "—"
    return (
        f'<div class="dash-truth-timeline-row" data-outcome="{escape(item.outcome)}">'
        f'<span class="dash-truth-timeline-ts">{ts_html}</span>'
        f'<span class="dash-truth-timeline-desc">{escape(item.description)}</span>'
        f'<span class="dash-truth-timeline-outcome">{escape(item.outcome)}</span>'
        f"</div>"
    )


def _render_timeline_sections(
    sections: tuple[PresentationTimelineSection, ...],
    escape: Callable[[Any], str],
) -> str:
    if not sections:
        return ""
    bodies: list[str] = []
    for section in sections:
        rows = "".join(
            _presentation_timeline_row(event, escape) for event in section.events
        )
        bodies.append(
            f'<div class="dash-truth-timeline-section">'
            f'<p class="dash-truth-section-label">{escape(section.label)}</p>'
            f'<div class="dash-truth-timeline-body">{rows}</div>'
            f"</div>"
        )
    return (
        f'<details class="dash-truth-timeline">'
        f"<summary>Truth Timeline</summary>"
        f'{"".join(bodies)}'
        f"</details>"
    )


def _render_truth_timeline(
    capability: CapabilityView,
    escape: Callable[[Any], str],
) -> str:
    if capability.timeline_sections:
        return _render_timeline_sections(capability.timeline_sections, escape)
    truth = capability.truth_validation
    if truth is None:
        return ""
    if not truth.timeline:
        return (
            f'<details class="dash-truth-timeline">'
            f"<summary>Truth Timeline</summary>"
            f'<div class="dash-truth-timeline-body">'
            f'<p class="dash-truth-timeline-empty">'
            f"No correlated timeline events were recorded for this check."
            f"</p>"
            f"</div>"
            f"</details>"
        )
    rows = "".join(_timeline_row(e, escape) for e in truth.timeline)
    return (
        f'<details class="dash-truth-timeline">'
        f"<summary>Truth Timeline</summary>"
        f'<div class="dash-truth-timeline-body">{rows}</div>'
        f"</details>"
    )


def _render_extension_diagnostics(
    extension_info: dict[str, Any] | None,
    escape: Callable[[Any], str],
) -> str:
    """Technical Details block for reported vs expected extension version.

    Displays the most recently seen extension instance for this user.
    """
    if not extension_info:
        return ""
    reported = extension_info.get("extension_version")
    expected = extension_info.get("extension_expected_version")
    last_seen = extension_info.get("extension_last_seen_at")
    update_required = bool(extension_info.get("extension_update_required"))

    version_label = escape(reported) if reported else "Unknown"
    lines = [
        f'<p class="dash-truth-tech-value">Extension version: {version_label}</p>',
    ]
    if last_seen:
        lines.append(
            f'<p class="dash-truth-tech-explain">Last seen: {_ts_html(str(last_seen))}</p>'
        )
    else:
        lines.append(
            '<p class="dash-truth-tech-explain">Last seen: Unknown</p>'
        )
    if expected:
        lines.append(
            f'<p class="dash-truth-tech-explain">Current build: {escape(expected)}</p>'
        )
    if update_required and reported and expected:
        lines.append(
            f'<p class="dash-truth-tech-explain dash-truth-ext-update">'
            f'Extension update required — running {escape(reported)}; '
            f'current build is {escape(expected)}</p>'
        )
    elif update_required:
        lines.append(
            '<p class="dash-truth-tech-explain dash-truth-ext-update">'
            "Extension update required</p>"
        )

    return (
        f'<div class="dash-truth-tech-block" data-extension-diagnostics="1">'
        f'<p class="dash-truth-section-label">Extension</p>'
        f'{"".join(lines)}'
        f"</div>"
    )


def _render_technical_details(
    capability: CapabilityView,
    escape: Callable[[Any], str],
    *,
    extension_info: dict[str, Any] | None = None,
) -> str:
    truth = capability.truth_validation
    if truth is None and not extension_info:
        return ""

    # Capability
    capability_html = ""
    confidence_html = ""
    pipeline_html = ""
    evidence_html = ""
    timeline_html = ""
    ids_html = ""
    transition_html = ""
    if truth is not None:
        capability_html = (
            f'<div class="dash-truth-tech-block">'
            f'<p class="dash-truth-section-label">Capability</p>'
            f'<p class="dash-truth-tech-value">{escape(truth.capability_state)}</p>'
            f'<p class="dash-truth-tech-explain">{escape(truth.explanation)}</p>'
            f"</div>"
        )

        confidence_html = (
            f'<div class="dash-truth-tech-block">'
            f'<p class="dash-truth-section-label">Confidence</p>'
            f'<p class="dash-truth-tech-value">{escape(truth.confidence)}'
            f' ({escape(str(truth.confidence_score))})</p>'
            f"</div>"
        )

        body_parts: list[str] = []
        for i, stage in enumerate(truth.pipeline):
            body_parts.append(_pipeline_row(stage, escape))
            if i < len(truth.pipeline) - 1:
                body_parts.append(
                    '<div class="dash-truth-pipeline-arrow" aria-hidden="true">↓</div>'
                )
        pipeline_html = (
            f'<div class="dash-truth-tech-block dash-truth-pipeline">'
            f'<p class="dash-truth-section-label">Pipeline</p>'
            f'{"".join(body_parts)}'
            f"</div>"
        )

        ev_rows = "".join(
            f'<li class="dash-truth-tech-evidence-item" data-outcome="{escape(e.outcome.value)}">'
            f'<span class="dash-truth-tech-evidence-cat">{escape(e.category.value)}</span>'
            f'<span class="dash-truth-tech-evidence-desc">{escape(e.description)}</span>'
            f'<span class="dash-truth-tech-evidence-out">{escape(e.outcome.value)}</span>'
            f"</li>"
            for e in truth.evidence
        )
        evidence_html = (
            f'<div class="dash-truth-tech-block">'
            f'<p class="dash-truth-section-label">Evidence</p>'
            f'<ul class="dash-truth-tech-evidence-list">{ev_rows}</ul>'
            f"</div>"
        )

        tl_rows = "".join(_timeline_row(e, escape) for e in truth.timeline)
        timeline_html = (
            f'<div class="dash-truth-tech-block">'
            f'<p class="dash-truth-section-label">Timeline</p>'
            f'<div class="dash-truth-timeline-body">{tl_rows}</div>'
            f"</div>"
        )

        id_rows = "".join(
            f'<div class="dash-truth-dev-id">'
            f'<dt>{escape(key)}</dt>'
            f'<dd>{escape(value or "—")}</dd>'
            f"</div>"
            for key, value in truth.developer_ids.items()
        )
        ids_html = (
            f'<div class="dash-truth-tech-block">'
            f'<p class="dash-truth-section-label">Developer ids</p>'
            f'<dl class="dash-truth-dev-ids">{id_rows}</dl>'
            f"</div>"
        )

        if truth.transition and truth.transition.previous_state:
            transition_html = (
                f'<div class="dash-truth-tech-block">'
                f'<p class="dash-truth-section-label">Transition</p>'
                f'<p class="dash-truth-tech-value">'
                f'{escape(truth.transition.previous_state)} → '
                f'{escape(truth.transition.current_state)}</p>'
                f'<p class="dash-truth-tech-explain">{escape(truth.transition.reason)}</p>'
                f"</div>"
            )

    extension_html = _render_extension_diagnostics(extension_info, escape)

    return (
        f'<details class="dash-truth-tech">'
        f"<summary>Technical Details</summary>"
        f"{capability_html}"
        f"{confidence_html}"
        f"{transition_html}"
        f"{pipeline_html}"
        f"{evidence_html}"
        f"{timeline_html}"
        f"{extension_html}"
        f"{ids_html}"
        f"</details>"
    )


def render_capability_panel(
    capability: CapabilityView,
    *,
    escape: Callable[[Any], str],
    extension_info: dict[str, Any] | None = None,
) -> str:
    """Render one provider Truth panel from CapabilityView only."""
    timeline = _render_truth_timeline(capability, escape)
    phase = capability.presentation_phase or "terminal"
    refreshing_attr = ' data-refreshing="1"' if capability.is_refreshing else ""
    phase_attr = f' data-presentation-phase="{escape(phase)}"'
    historical_attr = (
        ' data-status-historical="1"' if capability.status_is_historical else ""
    )
    headline = capability.primary_headline or capability.headline
    return (
        f'<article class="dash-truth-panel" data-provider="{escape(capability.provider)}" '
        f'data-capability="{escape(capability.state.value)}"'
        f"{phase_attr}{historical_attr}{refreshing_attr}>"
        f'<h2 class="dash-truth-provider">{escape(capability.display_name)}</h2>'
        f'<p class="dash-truth-headline">{escape(headline)}</p>'
        f"{_render_explanations(capability.explanations, escape)}"
        f"{_render_historical(capability, escape)}"
        f"{_render_evidence(capability.evidence, escape)}"
        f"{_render_extracted(capability, escape)}"
        f"{_render_meta_only(capability, escape)}"
        f"{_render_action(capability, escape)}"
        f"{timeline}"
        f"{_render_technical_details(capability, escape, extension_info=extension_info)}"
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


def render_home_card(
    card: HomeCard,
    *,
    escape: Callable[[Any], str],
    featured: bool = False,
) -> str:
    """Reusable Home card — one visual language for interrupt, opportunity, calm."""
    if featured:
        return _render_featured_card(card, escape=escape)
    return _render_secondary_card(card, escape=escape)


def _body_paragraphs(body: str, escape: Callable[[Any], str]) -> str:
    lines = [line.strip() for line in (body or "").split("\n") if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return f'<p class="home-card-body">{escape(lines[0])}</p>'
    items = "".join(f'<li class="home-card-body-line">{escape(line)}</li>' for line in lines)
    return f'<ul class="home-card-body-lines">{items}</ul>'


def _cta_html(card: HomeCard, *, escape: Callable[[Any], str], primary: bool) -> str:
    if card.disabled_cta_label:
        cls = "home-card-cta home-card-cta--disabled"
        return (
            f'<span class="{cls}" aria-disabled="true">'
            f"{escape(card.disabled_cta_label)}</span>"
        )
    if not card.cta_label:
        return ""
    if not card.cta_url:
        return (
            f'<span class="home-card-cta home-card-cta--disabled">'
            f"{escape(card.cta_label)}</span>"
        )
    external = card.cta_url.startswith("http")
    target = ' target="_blank" rel="noopener noreferrer"' if external else ""
    cls = (
        "home-card-cta home-card-cta--primary"
        if primary
        else "home-card-cta home-card-cta--text home-card-secondary-link"
    )
    return (
        f'<a href="{escape(card.cta_url)}" class="{cls}"{target}>'
        f"{escape(card.cta_label)}</a>"
    )


def _render_featured_card(card: HomeCard, *, escape: Callable[[Any], str]) -> str:
    eyebrow = ""
    if card.eyebrow:
        eyebrow = f'<p class="home-card-eyebrow">{escape(card.eyebrow)}</p>'
    secondary = ""
    if card.secondary_label and card.secondary_url:
        secondary = (
            f'<a href="{escape(card.secondary_url)}" class="home-card-secondary-link">'
            f"{escape(card.secondary_label)}</a>"
        )
    attrs = [
        f'data-tone="{escape(card.tone)}"',
        f'data-kind="{escape(card.kind)}"',
    ]
    if card.attention_id:
        attrs.append(f'data-attention-id="{escape(card.attention_id)}"')
    if card.attention_class:
        attrs.append(f'data-attention-class="{escape(card.attention_class)}"')
    title = ""
    if card.title:
        # Story detail sits under the dominant status line — not a second hero.
        title = f'<p class="home-card-title">{escape(card.title)}</p>'
    # Primary filled CTA when the user must act. Calm all-clear stays CTA-free.
    # Enrollment / first-data handoff CTAs are also primary (one clear next step).
    use_primary = card.tone in ("interrupt", "opportunity") or (
        card.kind == "enrollment" and bool(card.cta_label and card.cta_url)
    )
    cta = _cta_html(card, escape=escape, primary=use_primary)
    actions = ""
    if cta or secondary:
        actions = f'<div class="home-card-actions">{cta}{secondary}</div>'
    return (
        f'<article class="home-card home-card--featured" {" ".join(attrs)}>'
        f"{eyebrow}"
        f"{title}"
        f"{_body_paragraphs(card.body, escape)}"
        f"{actions}"
        f"</article>"
    )


def _render_secondary_card(card: HomeCard, *, escape: Callable[[Any], str]) -> str:
    href = card.cta_url or "#"
    external = href.startswith("http")
    target = ' target="_blank" rel="noopener noreferrer"' if external else ""
    attrs = [
        f'data-tone="{escape(card.tone)}"',
        f'data-kind="{escape(card.kind)}"',
    ]
    if card.attention_id:
        attrs.append(f'data-attention-id="{escape(card.attention_id)}"')
    return (
        f'<a href="{escape(href)}" class="home-card home-card--row" '
        f'{" ".join(attrs)}{target}>'
        f'<span class="home-card-row-body">'
        f'<span class="home-card-row-title">{escape(card.title)}</span>'
        f"</span>"
        f'<span class="home-card-row-arrow" aria-hidden="true">'
        f'<svg viewBox="0 0 16 16" fill="none"><path d="M6 3.5l4.5 4.5L6 12.5" '
        f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
        f'stroke-linejoin="round"/></svg></span>'
        f"</a>"
    )


def render_attention_panel(
    attention: AttentionView,
    *,
    escape: Callable[[Any], str],
) -> str:
    """Compatibility renderer for AttentionView primary via HomeCard."""
    primary = attention.primary
    silence = attention.silence.value if attention.silence is not None else ""
    if primary is None:
        if attention.silence is None:
            return ""
        label = {
            "all_clear": "Nothing needs you right now.",
            "suppressed": "Attention is snoozed for now.",
            "awaiting_data": "Mighty is waiting on account data.",
        }.get(silence, "Nothing needs you right now.")
        return (
            f'<section class="dash-attention home-attention" aria-label="Attention" '
            f'data-silence="{escape(silence)}">'
            f'<p class="dash-attention-silence">{escape(label)}</p>'
            f"</section>"
        )

    card = attention_to_card(primary, kind="featured")
    return (
        f'<section class="dash-attention home-attention" aria-label="Attention" '
        f'data-attention-id="{escape(primary.attention_id)}" '
        f'data-attention-class="{escape(primary.attention_class.value)}" '
        f'data-interrupt="{"1" if attention.render_hints.interrupt else "0"}">'
        f"{render_home_card(card, escape=escape, featured=True)}"
        f"</section>"
    )


def _render_truth_debug(
    result: HomeStateResult,
    *,
    escape: Callable[[Any], str],
    extension_info: dict[str, Any] | None,
) -> str:
    if not result.show_access_debug:
        return ""
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
    return (
        f'<details class="home-v2__debug">'
        f"<summary>Capability debug</summary>"
        f'<section class="dash-truth" aria-label="Capability debug">'
        f"{render_capability_panel(capability, escape=escape, extension_info=extension_info)}"
        f"</section>"
        f"</details>"
    )


_HOME_V2_STYLES = """
<style>
.home-v2{
  --home-v2-max:40rem;
  display:flex;
  flex-direction:column;
  gap:var(--mds-space-6);
  max-width:var(--home-v2-max);
  margin:0 auto;
  padding:var(--mds-space-5) 0 var(--mds-space-7);
}
.home-v2__field{
  position:relative;
  border-radius:var(--mds-radius-lg);
  overflow:hidden;
  min-height:11.5rem;
}
.home-v2__field .mds-quiet-field{
  min-height:11.5rem;
  border-radius:var(--mds-radius-lg);
  box-shadow:none;
}
.home-v2__field .mds-quiet-field__status{display:none}
.home-v2[data-state="healthy"] .mds-quiet-field{animation:none}
.home-v2[data-state="attention"] .mds-field-point.is-signal{
  background:#e8c27a;
}
.home-v2[data-state="opportunity"] .mds-field-point.is-signal{
  background:#d4b56a;
  box-shadow:0 0 0 7px rgba(212,181,106,0.18);
}
.home-v2__greeting{
  margin:0;
  font-size:var(--mds-text-meta);
  font-weight:var(--mds-weight-medium);
  color:var(--mds-muted);
}
.home-v2__date{
  margin:0.15rem 0 0;
  font-size:var(--mds-text-meta);
  color:var(--mds-muted);
}
.home-v2__message .mds-hero{gap:var(--mds-space-3)}
.home-v2__message .mds-hero__title{max-width:16ch}
.home-v2__message .mds-hero__lede{max-width:36ch}
.home-v2__message .mds-hero__actions{margin-top:var(--mds-space-3)}
.home-v2__message .mds-hero__meta{
  border-top:none;
  padding-top:0;
  margin-top:var(--mds-space-2);
}
.home-v2__evidence-list,
.home-v2__activity-list{
  list-style:none;
  margin:0;
  padding:0;
  display:flex;
  flex-direction:column;
  gap:0.55rem;
}
.home-v2__evidence-item,
.home-v2__activity-item{
  display:flex;
  align-items:baseline;
  gap:0.55rem;
  font-size:var(--mds-text-body-sm);
  color:var(--mds-ink-soft);
  line-height:1.45;
}
.home-v2__evidence-mark{
  flex-shrink:0;
  width:0.45rem;
  height:0.45rem;
  margin-top:0.4rem;
  border-radius:999px;
  background:var(--mds-line-strong);
}
.home-v2__evidence-item[data-ok="1"] .home-v2__evidence-mark{
  background:var(--mds-success);
}
.home-v2__evidence-item[data-ok="0"] .home-v2__evidence-mark{
  background:var(--mds-waiting);
}
.home-v2__activity-item a{
  color:inherit;
  text-decoration:none;
}
.home-v2__activity-item a:hover,
.home-v2__activity-item a:focus-visible{
  color:var(--mds-pine);
  text-decoration:underline;
}
.home-v2__activity-empty{
  margin:0;
  font-size:var(--mds-text-body-sm);
  color:var(--mds-muted);
}
.home-v2__region .mds-section__header{margin-bottom:var(--mds-space-3)}
.home-v2__region .mds-heading{
  font-size:var(--mds-text-meta);
  font-weight:var(--mds-weight-semibold);
  letter-spacing:0.04em;
  text-transform:uppercase;
  color:var(--mds-muted);
}
.home-v2__debug{
  margin-top:var(--mds-space-4);
  padding-top:var(--mds-space-4);
  border-top:1px solid var(--mds-line);
}
.home-v2__debug summary{
  cursor:pointer;
  font-size:var(--mds-text-meta);
  font-weight:var(--mds-weight-semibold);
  color:var(--mds-muted);
}
@media (max-width:720px){
  .home-v2{padding:var(--mds-space-4) 0 var(--mds-space-6);gap:var(--mds-space-5)}
  .home-v2__field,.home-v2__field .mds-quiet-field{min-height:9.5rem}
  .home-v2__message .mds-hero__title{font-size:clamp(1.85rem,7vw,2.4rem)}
}
@media (prefers-reduced-motion:reduce){
  .home-v2 .mds-field-breathe,
  .home-v2 .mds-field-point.is-signal{animation:none!important}
}
</style>
"""


def _field_point_count(watched_count: int) -> int:
    if watched_count <= 0:
        return 3
    return max(3, min(8, watched_count))


def _render_quiet_field_region(
    projection: HomeProjection,
    *,
    escape: Callable[[Any], str],
) -> str:
    """Quiet Field atmosphere — decorative; answer lives in Primary Message."""
    state = projection.visual_state
    signal = state in ("attention", "opportunity")
    ambient = state in ("healthy", "handoff", "empty")
    breathe = " mds-field-breathe" if ambient else ""
    n = _field_point_count(projection.watched_count)
    points: list[str] = []
    signal_index = min(3, n - 1)
    for i in range(n):
        is_signal = signal and i == signal_index
        points.append(
            f'<span class="mds-field-point{" is-signal" if is_signal else ""}"></span>'
        )
    meta = {
        "healthy": user_copy.HOME_V2_WORKING_QUIETLY,
        "attention": user_copy.HOME_V2_NEEDS_YOU,
        "opportunity": user_copy.HOME_V2_VALUE_WAITING,
        "empty": user_copy.HOME_V2_GETTING_READY,
        "handoff": user_copy.HOME_V2_GETTING_READY,
    }.get(state, user_copy.HOME_V2_WORKING_QUIETLY)
    return (
        f'<section class="home-v2__field" aria-hidden="true" '
        f'data-field-state="{escape(state)}">'
        f'<div class="mds-quiet-field{breathe}">'
        f'<div class="mds-quiet-field__horizon"></div>'
        f'<div class="mds-quiet-field__points">{"".join(points)}</div>'
        f'<p class="mds-meta home-v2__field-meta" '
        f'style="position:relative;z-index:1;margin:0;color:rgba(244,239,230,0.72)">'
        f"{escape(meta)}</p>"
        f"</div>"
        f"</section>"
    )


def _primary_eyebrow(projection: HomeProjection) -> str:
    state = projection.visual_state
    if state == "healthy":
        return user_copy.HOME_V2_WORKING_QUIETLY
    if state == "attention":
        return user_copy.HOME_V2_NEEDS_YOU
    if state == "opportunity":
        return user_copy.HOME_V2_VALUE_WAITING
    if state in ("empty", "handoff"):
        return user_copy.HOME_V2_GETTING_READY
    return ""


def _primary_title(projection: HomeProjection) -> str:
    if projection.answer:
        return projection.answer
    if projection.featured and projection.featured.title:
        return projection.featured.title
    return user_copy.HOME_BRIEFING_ANSWER_GOOD


def _primary_lede(projection: HomeProjection) -> str:
    card = projection.featured
    if card is None:
        return ""
    body = (card.body or "").strip()
    if not body:
        return ""
    # Prefer a single paragraph in the hero lede.
    return " ".join(line.strip() for line in body.split("\n") if line.strip())


def _render_primary_actions(
    projection: HomeProjection,
    *,
    escape: Callable[[Any], str],
) -> str:
    del escape
    card = projection.featured
    if card is None:
        return ""
    parts: list[str] = []
    if card.disabled_cta_label:
        parts.append(
            render_button(
                card.disabled_cta_label,
                variant="secondary",
                disabled=True,
            )
        )
    elif card.cta_label and card.cta_url:
        parts.append(
            render_button(
                card.cta_label,
                variant="primary",
                href=card.cta_url,
            )
        )
    if card.secondary_label and card.secondary_url:
        parts.append(
            render_button(
                card.secondary_label,
                variant="ghost",
                href=card.secondary_url,
            )
        )
    return "".join(parts)


def _render_primary_message(
    projection: HomeProjection,
    *,
    escape: Callable[[Any], str],
) -> str:
    safe_name = escape(projection.first_name)
    hero_state = (
        "attention"
        if projection.visual_state in ("attention", "opportunity")
        else "default"
    )
    badge = ""
    if projection.visual_state == "healthy":
        badge = render_status_badge("All clear", variant="quiet")
    elif projection.visual_state == "attention":
        badge = render_status_badge("Attention", variant="attention")
    elif projection.visual_state == "opportunity":
        badge = render_status_badge("Opportunity", variant="review")

    meta_bits = [
        f'<p class="home-v2__greeting" id="hero-greeting">Hello, {safe_name}</p>',
        f'<p class="home-v2__date"><time>{escape(projection.today_label)}</time></p>',
    ]
    if badge:
        meta_bits.append(badge)
    if projection.last_checked:
        raw = str(projection.last_checked)
        if "T" in raw:
            fresh = f"{escape(user_copy.HOME_FRESHNESS_PREFIX)}{_ts_html(raw)}"
        else:
            fresh = escape(user_copy.home_freshness_label(raw))
        meta_bits.append(
            f'<p class="mds-meta" id="dash-last-checked" '
            f'data-last-checked="{escape(projection.last_checked)}">{fresh}</p>'
        )

    hero = render_hero(
        title=_primary_title(projection),
        lede=_primary_lede(projection),
        variant="home",
        eyebrow=_primary_eyebrow(projection),
        actions_html=_render_primary_actions(projection, escape=escape),
        meta_html="".join(meta_bits),
        state=hero_state,
        heading_level=1,
        class_name="home-v2__hero",
    )
    return (
        f'<section class="home-v2__message" aria-label="Primary message">'
        f"{hero}"
        f"</section>"
    )


def _render_evidence_region(
    evidence: Sequence[HomeEvidenceItem],
    *,
    escape: Callable[[Any], str],
) -> str:
    if not evidence:
        return ""
    rows: list[str] = []
    for item in evidence:
        ok_attr = ""
        if item.ok is True:
            ok_attr = ' data-ok="1"'
        elif item.ok is False:
            ok_attr = ' data-ok="0"'
        rows.append(
            f'<li class="home-v2__evidence-item"{ok_attr}>'
            f'<span class="home-v2__evidence-mark" aria-hidden="true"></span>'
            f"<span>{escape(item.label)}</span>"
            f"</li>"
        )
    content = f'<ul class="home-v2__evidence-list">{"".join(rows)}</ul>'
    return render_section(
        title=user_copy.HOME_V2_EVIDENCE_LABEL,
        content=content,
        variant="panel",
        heading_level=2,
        class_name="home-v2__region home-v2__evidence",
    )


def _render_activity_preview(
    wins: Sequence[HomeWin],
    *,
    escape: Callable[[Any], str],
    visual_state: str,
) -> str:
    if wins:
        items: list[str] = []
        for win in wins:
            body = escape(win.message)
            if win.href:
                items.append(
                    f'<li class="home-v2__activity-item">'
                    f'<a href="{escape(win.href)}">{body}</a></li>'
                )
            else:
                items.append(f'<li class="home-v2__activity-item">{body}</li>')
        content = f'<ul class="home-v2__activity-list">{"".join(items)}</ul>'
    elif visual_state == "healthy":
        content = (
            f'<p class="home-v2__activity-empty">'
            f"{escape(user_copy.HOME_V2_ACTIVITY_EMPTY)}</p>"
        )
    else:
        return ""
    return render_section(
        title=user_copy.HOME_V2_ACTIVITY_LABEL,
        content=content,
        variant="panel",
        heading_level=2,
        class_name="home-v2__region home-v2__activity",
    )


def render_home_projection(
    projection: HomeProjection,
    *,
    escape: Callable[[Any], str],
    result: HomeStateResult | None = None,
    extension_info: dict[str, Any] | None = None,
) -> str:
    """Render Home V2 Living Calm — Quiet Field, message, evidence, activity."""
    truth = ""
    if result is not None and projection.show_truth_debug:
        truth = _render_truth_debug(result, escape=escape, extension_info=extension_info)

    silence_attr = (
        f' data-silence="{escape(projection.attention_silence)}"'
        if projection.attention_silence
        else ""
    )
    safe_name = escape(projection.first_name)
    return (
        f'{_HOME_V2_STYLES}'
        f'<div class="mds mds-atmosphere dash-hero home-v2" '
        f'data-state="{escape(projection.visual_state)}" '
        f'data-enrollment="{escape(projection.enrollment_state.value)}" '
        f'data-story="{escape(projection.story_kind)}" '
        f'data-interrupt="{"1" if projection.attention_interrupt else "0"}"'
        f"{silence_attr}>"
        f"{_render_quiet_field_region(projection, escape=escape)}"
        f"{_render_primary_message(projection, escape=escape)}"
        f"{_render_evidence_region(projection.evidence, escape=escape)}"
        f"{_render_activity_preview(projection.activity_preview, escape=escape, visual_state=projection.visual_state)}"
        f"{truth}"
        f"<script>"
        f"(function(){{"
        f'  var h=new Date().getHours();'
        f'  var g=h<12?"Good morning":h<17?"Good afternoon":"Good evening";'
        f'  var el=document.getElementById("hero-greeting");'
        f'  if(el) el.textContent=g+", {safe_name}";'
        f"}})();"
        f"</script>"
        f"</div>"
    )


def render_home_page(
    result: HomeStateResult,
    *,
    first_name: str,
    today_label: str,
    last_checked: str = "",
    escape: Callable[[Any], str],
    extension_info: dict[str, Any] | None = None,
    attention: AttentionView | None = None,
    use_attention: bool = False,
    recent_wins: Sequence[Any] | None = None,
    gmail_connected: bool | None = None,
    chrome_active: bool | None = None,
) -> str:
    """Render Home V2 from existing platform projections."""
    checked = last_checked
    if not checked and result.capability is not None and result.capability.last_verified:
        checked = result.capability.last_verified
    # Infer Chrome from extension heartbeat when caller omits the flag.
    if chrome_active is None and extension_info is not None:
        chrome_active = bool(
            extension_info.get("extension_version")
            or extension_info.get("extension_last_seen_at")
        )
    projection = project_home(
        result,
        first_name=first_name,
        today_label=today_label,
        last_checked=checked,
        attention=attention,
        use_attention=use_attention,
        recent_wins=recent_wins,
        gmail_connected=gmail_connected,
        chrome_active=chrome_active,
    )
    return render_home_projection(
        projection,
        escape=escape,
        result=result,
        extension_info=extension_info,
    )
