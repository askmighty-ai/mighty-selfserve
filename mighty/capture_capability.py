"""Capture capability inventory: needed vs present capture evidence per provider."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from mighty.pipeline_stages import PipelineStageId, StageStatus

# ── Capability catalog ────────────────────────────────────────────────────────

CAPABILITY_ORDER: tuple[str, ...] = (
    "visible_text",
    "network_json",
    "embedded_json",
    "page_metadata",
    "dom_html",
    "navigation_urls",
    "extension_measured",
    "timing",
    "storage_signals",
)

CAPABILITY_CATALOG: dict[str, dict[str, str]] = {
    "visible_text": {
        "label": "Visible text",
        "why_needed": "Baseline fallback for extraction when structured sources are unavailable.",
    },
    "network_json": {
        "label": "Network JSON",
        "why_needed": "Primary structured extraction source from fetch and XHR responses.",
    },
    "embedded_json": {
        "label": "Embedded JSON",
        "why_needed": "Richer state for modern SPAs (Next.js, Apollo, Redux hydration).",
    },
    "page_metadata": {
        "label": "Page metadata",
        "why_needed": "Title, canonical URL, and safe meta tags for page identity and context.",
    },
    "dom_html": {
        "label": "DOM / HTML",
        "why_needed": "Deterministic selectors and layout context for replay and connectors.",
    },
    "navigation_urls": {
        "label": "Navigation URLs",
        "why_needed": "Verify we reached the correct account pages during capture.",
    },
    "extension_measured": {
        "label": "Extension-measured",
        "why_needed": "Distinguish observed evidence from server inference.",
    },
    "timing": {
        "label": "Timing",
        "why_needed": "Diagnose race conditions and slow SPA hydration during capture.",
    },
    "storage_signals": {
        "label": "Storage signals",
        "why_needed": "Session and client-side state not visible in DOM or network payloads.",
    },
}

IMPROVEMENT_BY_CAPABILITY: dict[str, str] = {
    "visible_text": "Capture visible text from account pages",
    "network_json": "Capture network JSON",
    "embedded_json": "Capture embedded framework state",
    "page_metadata": "Capture page metadata blocks",
    "dom_html": "Store HTML snapshot",
    "navigation_urls": "Capture navigation URLs during sync",
    "extension_measured": "Enable extension stage reporting",
    "timing": "Record per-stage timing from extension",
    "storage_signals": "Capture storage signals (localStorage, cookies)",
}

_API_BLOCK_RE = re.compile(r"=== API RESPONSE:", re.IGNORECASE)
_EMBEDDED_BLOCK_RE = re.compile(r"=== EMBEDDED STATE:", re.IGNORECASE)
_PAGE_META_BLOCK_RE = re.compile(r"=== PAGE META:", re.IGNORECASE)
_JSON_LD_BLOCK_RE = re.compile(r"=== JSON-LD:", re.IGNORECASE)
_HTML_SNAPSHOT_BLOCK_RE = re.compile(r"=== HTML SNAPSHOT:", re.IGNORECASE)
_URL_SECTION_RE = re.compile(
    r"(?:^|\n\n)--- https?://[^\n]+ ---\n|=== https?://[^\n]+ ===\n",
    re.MULTILINE,
)
_URL_MARKER_RE = re.compile(r"=== URL[^\n]*===", re.IGNORECASE)
_VISIBLE_SECTION_RE = re.compile(
    r"(?:(?:^|\n\n)--- https?://[^\n]+ ---\n|=== URL[^\n]*===\n|=== https?://[^\n]+ ===\n)"
    r"(.*?)(?=\n\n--- |\n\n=== |\Z)",
    re.DOTALL | re.MULTILINE,
)


def _visible_text_char_count(text: str) -> int:
    """Count characters in visible-text sections only, excluding evidence blocks."""
    return sum(len(match.group(1).strip()) for match in _VISIBLE_SECTION_RE.finditer(text or ""))


def needed_capabilities_for_provider(_source: str) -> list[str]:
    """Baseline needed set for all providers (PR1 — no per-provider overrides)."""
    return list(CAPABILITY_ORDER)


def capability_label(capability_id: str) -> str:
    return CAPABILITY_CATALOG.get(capability_id, {}).get("label", capability_id)


def capability_why_needed(capability_id: str) -> str:
    return CAPABILITY_CATALOG.get(capability_id, {}).get("why_needed", "")


def parse_raw_text_evidence_markers(raw_text: str) -> dict[str, Any]:
    """Parse capture marker counts from raw_text or sync payload."""
    text = raw_text or ""
    url_sections = len(_URL_SECTION_RE.findall(text)) + len(_URL_MARKER_RE.findall(text))
    return {
        "visible_text_chars": _visible_text_char_count(text),
        "url_section_count": url_sections,
        "api_response_blocks": len(_API_BLOCK_RE.findall(text)),
        "embedded_state_blocks": len(_EMBEDDED_BLOCK_RE.findall(text)),
        "page_metadata_blocks": len(_PAGE_META_BLOCK_RE.findall(text)),
        "json_ld_blocks": len(_JSON_LD_BLOCK_RE.findall(text)),
        "html_snapshot_blocks": len(_HTML_SNAPSHOT_BLOCK_RE.findall(text)),
        "measurement": "server_inferred",
    }


def present_from_markers(markers: dict[str, Any] | None, *, initiator: str = "") -> set[str]:
    """Return capability IDs confirmed present from marker dict and run initiator."""
    present: set[str] = set()
    if not markers:
        markers = {}

    visible_chars = int(markers.get("visible_text_chars") or 0)
    if visible_chars > 0 or int(markers.get("url_section_count") or 0) > 0:
        present.add("visible_text")

    api_blocks = int(markers.get("api_response_blocks") or 0)
    json_chars = int(markers.get("json_payload_chars") or 0)
    if api_blocks > 0 or json_chars > 0 or initiator == "intercept":
        present.add("network_json")

    if int(markers.get("embedded_state_blocks") or 0) > 0:
        present.add("embedded_json")

    if int(markers.get("json_ld_blocks") or 0) > 0:
        present.add("embedded_json")

    if int(markers.get("page_metadata_blocks") or 0) > 0:
        present.add("page_metadata")

    if int(markers.get("html_snapshot_blocks") or 0) > 0:
        present.add("dom_html")

    if int(markers.get("url_section_count") or 0) > 0:
        present.add("navigation_urls")

    if markers.get("measurement") == "extension_reported":
        present.add("extension_measured")

    return present


@dataclass
class SourceCaptureSignals:
    present: set[str] = field(default_factory=set)
    last_seen: str | None = None
    latest_successful_capture_run_id: str | None = None
    initiator_counts: dict[str, int] = field(default_factory=dict)
    present_detail: dict[str, str] = field(default_factory=dict)


@dataclass
class CapabilityRow:
    capability_id: str
    label: str
    why_needed: str
    needed: bool
    present: bool
    gap: bool
    confidence: str
    source_detail: str


@dataclass
class ProviderCapability:
    source: str
    display_name: str
    needed: list[str]
    present: list[str]
    missing: list[str]
    needed_count: int
    present_count: int
    missing_count: int
    rows: list[CapabilityRow]
    next_best_improvement: str
    latest_successful_capture_run_id: str | None
    last_seen: str | None
    initiator_counts: dict[str, int]


def _merge_markers(
    artifacts: dict[str, Any],
    *,
    initiator: str,
) -> dict[str, Any]:
    markers = dict(artifacts.get("evidence_markers") or {})
    if not markers:
        markers = {
            "json_payload_chars": artifacts.get("json_payload_chars", 0),
            "measurement": "server_inferred",
        }
    if artifacts.get("json_payload_chars") and not markers.get("json_payload_chars"):
        markers["json_payload_chars"] = artifacts["json_payload_chars"]
    markers.setdefault("measurement", "server_inferred")
    return markers


def _apply_navigation_signals(signals: SourceCaptureSignals, artifacts: dict[str, Any]) -> None:
    urls = artifacts.get("urls") or []
    if urls:
        signals.present.add("navigation_urls")
        signals.present_detail.setdefault(
            "navigation_urls",
            f"{len(urls)} URL(s) in navigation stage",
        )
    if artifacts.get("inferred") is False:
        signals.present.add("extension_measured")
        signals.present_detail.setdefault(
            "extension_measured",
            "Extension-reported navigation stage",
        )


def _apply_timing_signals(
    signals: SourceCaptureSignals,
    *,
    duration_ms: float | None,
    finished_at: str | None,
) -> None:
    if duration_ms is not None or finished_at:
        signals.present.add("timing")
        if duration_ms is not None:
            signals.present_detail.setdefault("timing", f"Stage duration {duration_ms:.0f} ms")


def collect_signals_from_pipeline(
    db: Any,
    *,
    run_created_before: str | None = None,
    run_created_after: str | None = None,
) -> dict[str, SourceCaptureSignals]:
    """Aggregate best-ever capture signals per provider from pipeline history."""
    clauses = ["ps.stage IN (?, ?)"]
    params: list[Any] = [PipelineStageId.CAPTURE.value, PipelineStageId.NAVIGATION.value]
    if run_created_before:
        clauses.append("pr.created_at < ?")
        params.append(run_created_before)
    if run_created_after:
        clauses.append("pr.created_at >= ?")
        params.append(run_created_after)

    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT pr.source, pr.run_id, pr.initiator, pr.created_at, pr.finished_at,
               ps.stage, ps.status, ps.artifacts_json, ps.duration_ms
        FROM pipeline_runs pr
        JOIN pipeline_stages ps ON ps.run_id = pr.run_id
        WHERE {where}
        ORDER BY pr.created_at DESC
        """,
        params,
    ).fetchall()

    by_source: dict[str, SourceCaptureSignals] = {}
    for row in rows:
        source = row["source"] if isinstance(row, dict) else row[0]
        run_id = row["run_id"] if isinstance(row, dict) else row[1]
        initiator = row["initiator"] if isinstance(row, dict) else row[2]
        created_at = row["created_at"] if isinstance(row, dict) else row[3]
        finished_at = row["finished_at"] if isinstance(row, dict) else row[4]
        stage = row["stage"] if isinstance(row, dict) else row[5]
        status = row["status"] if isinstance(row, dict) else row[6]
        artifacts_json = row["artifacts_json"] if isinstance(row, dict) else row[7]
        duration_ms = row["duration_ms"] if isinstance(row, dict) else row[8]

        signals = by_source.setdefault(source, SourceCaptureSignals())
        signals.initiator_counts[initiator] = signals.initiator_counts.get(initiator, 0) + 1

        if status != StageStatus.SUCCESS.value:
            continue

        if not signals.last_seen or (created_at and created_at > signals.last_seen):
            signals.last_seen = created_at

        _apply_timing_signals(signals, duration_ms=duration_ms, finished_at=finished_at)

        try:
            artifacts = json.loads(artifacts_json) if artifacts_json else {}
        except (json.JSONDecodeError, TypeError):
            artifacts = {}

        if stage == PipelineStageId.NAVIGATION.value:
            _apply_navigation_signals(signals, artifacts)
            if artifacts.get("inferred") is False:
                signals.present.add("extension_measured")
            continue

        if stage != PipelineStageId.CAPTURE.value:
            continue

        if not signals.latest_successful_capture_run_id:
            signals.latest_successful_capture_run_id = run_id

        markers = _merge_markers(artifacts, initiator=initiator)
        found = present_from_markers(markers, initiator=initiator)
        signals.present.update(found)

        if artifacts.get("inferred") is False:
            signals.present.add("extension_measured")
            signals.present_detail.setdefault(
                "extension_measured",
                "Extension-reported capture stage",
            )

        if found:
            detail_parts = []
            if markers.get("visible_text_chars"):
                detail_parts.append(f"{markers['visible_text_chars']} visible chars")
            if markers.get("api_response_blocks"):
                detail_parts.append(f"{markers['api_response_blocks']} API block(s)")
            if markers.get("embedded_state_blocks"):
                detail_parts.append(f"{markers['embedded_state_blocks']} embedded block(s)")
            if markers.get("json_ld_blocks"):
                detail_parts.append(f"{markers['json_ld_blocks']} JSON-LD block(s)")
            if markers.get("page_metadata_blocks"):
                detail_parts.append(f"{markers['page_metadata_blocks']} page meta block(s)")
            if markers.get("url_section_count"):
                detail_parts.append(f"{markers['url_section_count']} URL section(s)")
            if detail_parts:
                signals.present_detail.setdefault(
                    next(iter(found)),
                    ", ".join(detail_parts) + f" (run {run_id[:8]}…)",
                )

    return by_source


def enrich_signals_from_raw_text(signals: SourceCaptureSignals, raw_text: str) -> None:
    """Merge marker parse from admin account sample into signals."""
    markers = parse_raw_text_evidence_markers(raw_text)
    found = present_from_markers(markers)
    signals.present.update(found)
    if markers.get("api_response_blocks"):
        signals.present_detail.setdefault(
            "network_json",
            f"{markers['api_response_blocks']} API block(s) in account raw_text",
        )
    if markers.get("embedded_state_blocks"):
        signals.present_detail.setdefault(
            "embedded_json",
            f"{markers['embedded_state_blocks']} embedded block(s) in account raw_text",
        )
    if markers.get("json_ld_blocks"):
        signals.present_detail.setdefault(
            "embedded_json",
            f"{markers['json_ld_blocks']} JSON-LD block(s) in account raw_text",
        )
    if markers.get("page_metadata_blocks"):
        signals.present_detail.setdefault(
            "page_metadata",
            f"{markers['page_metadata_blocks']} page metadata block(s) in account raw_text",
        )


def next_best_improvement(missing: list[str], *, has_runs: bool) -> str:
    """Deterministic highest-value improvement from gap set."""
    if not has_runs:
        return IMPROVEMENT_BY_CAPABILITY["visible_text"]
    for cap_id in CAPABILITY_ORDER:
        if cap_id in missing:
            return IMPROVEMENT_BY_CAPABILITY[cap_id]
    return "Full capture capability — no gaps in checklist"


def _confidence_for(capability_id: str, present: bool, has_runs: bool) -> str:
    if present:
        return "confirmed"
    if not has_runs:
        return "never observed"
    return "missing"


def compute_provider_capability(
    source: str,
    *,
    signals: SourceCaptureSignals | None,
    display_name: str | None = None,
) -> ProviderCapability:
    needed = needed_capabilities_for_provider(source)
    sig = signals or SourceCaptureSignals()
    has_runs = bool(sig.last_seen or sig.initiator_counts)
    present_set = set(sig.present)
    missing = [cap for cap in needed if cap not in present_set]

    rows: list[CapabilityRow] = []
    for cap_id in needed:
        meta = CAPABILITY_CATALOG[cap_id]
        is_present = cap_id in present_set
        rows.append(
            CapabilityRow(
                capability_id=cap_id,
                label=meta["label"],
                why_needed=meta["why_needed"],
                needed=True,
                present=is_present,
                gap=not is_present,
                confidence=_confidence_for(cap_id, is_present, has_runs),
                source_detail=sig.present_detail.get(cap_id, "—"),
            )
        )

    return ProviderCapability(
        source=source,
        display_name=display_name or source.replace("_", " ").title(),
        needed=needed,
        present=sorted(present_set & set(needed)),
        missing=missing,
        needed_count=len(needed),
        present_count=len(present_set & set(needed)),
        missing_count=len(missing),
        rows=rows,
        next_best_improvement=next_best_improvement(missing, has_runs=has_runs),
        latest_successful_capture_run_id=sig.latest_successful_capture_run_id,
        last_seen=sig.last_seen,
        initiator_counts=dict(sig.initiator_counts),
    )


def compute_all_provider_capabilities(
    db: Any,
    providers: list[str],
    *,
    display_names: dict[str, str] | None = None,
) -> list[ProviderCapability]:
    signals_by_source = collect_signals_from_pipeline(db)
    names = display_names or {}
    results = [
        compute_provider_capability(
            source,
            signals=signals_by_source.get(source),
            display_name=names.get(source),
        )
        for source in providers
    ]
    results.sort(key=lambda r: (-r.missing_count, r.present_count, r.display_name.lower()))
    return results
