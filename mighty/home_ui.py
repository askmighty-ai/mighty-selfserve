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
    PresentationTimelineEvent,
    PresentationTimelineSection,
    TRUTH_PROVIDER_DISPLAY,
    build_capability_view,
)
from mighty.customer_account_access import CustomerAccountAccessView
from mighty.customer_local_time import format_customer_elapsed, format_customer_local_time
from mighty.home_state import HomeStateResult
from mighty.truth_validation import (
    TruthEvidence,
    TruthPipelineStage,
    TruthValidation,
)


def _ts_html(value: str | None) -> str:
    """Browser-local <time> element; canonical UTC stays in datetime/title."""
    return format_customer_local_time(value)


def _elapsed_html(started_at: str | None) -> str:
    """Live elapsed label that updates client-side without rewriting started_at."""
    if not started_at:
        return ""
    return format_customer_elapsed(started_at)


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
    summary = capability.historical_summary
    if capability.previous_confirmed_at and summary.lower().startswith("last confirmed"):
        # Temporally direct: "Last confirmed signed out at <local time>."
        parts = [
            f'<p class="dash-truth-historical-summary">'
            f"{escape(summary)} at {_ts_html(capability.previous_confirmed_at)}.</p>"
        ]
    else:
        parts = [
            f'<p class="dash-truth-historical-summary">'
            f"{escape(summary)}</p>"
        ]
        if capability.previous_confirmed_at:
            verb = (capability.historical_timestamp_label or "Last confirmed").strip()
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
        started = capability.verification_started_at or capability.current_check_started_at
        requested = capability.current_check_requested_at
        if started:
            label = capability.timestamp_label or "Check started"
            parts.append(f"{escape(label)}: {_ts_html(started)}")
            elapsed = _elapsed_html(started)
            if elapsed:
                parts.append(elapsed)
        elif requested:
            label = capability.timestamp_label or "Requested at"
            parts.append(f"{escape(label)} {_ts_html(requested)}")
            elapsed = _elapsed_html(requested)
            if elapsed:
                parts.append(elapsed)
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
    if not capability.action_required or not capability.action_label or not capability.action_url:
        return ""
    external = capability.action_url.startswith("http")
    target = ' target="_blank" rel="noopener noreferrer"' if external else ""
    return (
        f'<a href="{escape(capability.action_url)}" '
        f'class="dash-brief-featured-cta dash-truth-cta"{target}>'
        f'{escape(capability.action_label)}</a>'
    )


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
    vid = escape(item.verification_id) if item.verification_id else ""
    cid = escape(item.access_cycle_id) if item.access_cycle_id else ""
    id_attrs = ""
    if vid:
        id_attrs += f' data-verification-id="{vid}"'
    if cid:
        id_attrs += f' data-access-cycle-id="{cid}"'
    result = escape(item.result or item.outcome)
    return (
        f'<div class="dash-truth-timeline-row" data-outcome="{escape(item.outcome)}"'
        f"{id_attrs}>"
        f'<span class="dash-truth-timeline-ts">{ts_html}</span>'
        f'<span class="dash-truth-timeline-desc">{escape(item.description)}</span>'
        f'<span class="dash-truth-timeline-outcome">{result}</span>'
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
        section_attrs = ""
        if section.verification_id:
            section_attrs += (
                f' data-verification-id="{escape(section.verification_id)}"'
            )
        if section.access_cycle_id:
            section_attrs += (
                f' data-access-cycle-id="{escape(section.access_cycle_id)}"'
            )
        bodies.append(
            f'<div class="dash-truth-timeline-section"{section_attrs}>'
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
    if truth is None or not truth.timeline:
        return ""
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
    vid_attr = ""
    if capability.current_verification_id:
        vid_attr = (
            f' data-verification-id="{escape(capability.current_verification_id)}"'
        )
    cid_attr = ""
    if capability.current_access_cycle_id:
        cid_attr = (
            f' data-access-cycle-id="{escape(capability.current_access_cycle_id)}"'
        )
    completed_attr = ""
    if capability.verification_completed_at:
        completed_attr = (
            f' data-verification-completed-at="'
            f'{escape(capability.verification_completed_at)}"'
        )
    headline = capability.primary_headline or capability.headline
    # During determining, never surface a prior terminal state as data-capability.
    capability_attr = (
        "determining"
        if phase == "determining"
        else capability.state.value
    )
    return (
        f'<article class="dash-truth-panel" data-provider="{escape(capability.provider)}" '
        f'data-capability="{escape(capability_attr)}"'
        f"{phase_attr}{historical_attr}{refreshing_attr}{vid_attr}{cid_attr}{completed_attr}>"
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


def render_home_page(
    result: HomeStateResult,
    *,
    first_name: str,
    today_label: str,
    last_checked: str = "",
    escape: Callable[[Any], str],
    extension_info: dict[str, Any] | None = None,
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
        f"{render_capability_panel(capability, escape=escape, extension_info=extension_info)}"
        f"</section>"
        f"{footer}"
        f"</div>"
        f"</div>"
        f"</div>"
    )
