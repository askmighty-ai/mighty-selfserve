"""
mighty.customer_capability_presentation
───────────────────────────────────────
Customer presentation state machine for the Truth Dashboard.

Capability resolution (resolve_capability_state / build_capability_view) still
computes live pipeline truth. This module maps that truth into exactly one
customer-visible mode:

  • never_checked / checking — active verification; never present-tense
    CapabilityState conclusions. Prior results appear only under
    Truth Timeline → Previous completed check.
  • signed_out / connected / logged_in_no_account_data / extraction_failed /
    check_inconclusive — terminal answers for the selected verification.
  • stale_* — definitive terminals outside the freshness window; primary
    headline is the historical claim (no duplicate Last confirmed block).

Illegal: Checking plus “Last confirmed: …” on the primary card stack.
Illegal: flashing Signed out / Connected while a verification is still active.
Illegal: mixing verification IDs or clocks across Current vs Previous timeline.

Persistence is monotonic: an older verification/access cycle must never
overwrite a newer published presentation. Ordering uses canonical completion
time (and cycle ids for idempotency), not formatted display strings.

Does not change capability precedence, verification FSM, extraction, snapshots,
provider adapters, or truth-validation scoring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from mighty.admin_local_time import parse_admin_timestamp, to_utc_iso_z
from mighty.capability_state import (
    CapabilityState,
    CapabilityView,
    EvidenceItem,
    ExtractedField,
    PipelineStage,
    PresentationTimelineEvent,
    PresentationTimelineSection,
)
from mighty.session_verification import (
    ACTIVE_VERIFICATION_LIFECYCLES,
    READY_RESULT_GRACE_SECONDS,
    TERMINAL_VERIFICATION_LIFECYCLES,
)

# Customer-truth freshness window — aligns with ready-result grace.
CUSTOMER_TRUTH_FRESHNESS_SECONDS = READY_RESULT_GRACE_SECONDS

DETERMINING_HEADLINE = "Checking your login state…"
DETERMINING_HEADLINE_CURRENT = "Checking your login state…"
DETERMINING_BODY = (
    "Mighty is checking whether your American Express session is signed in."
)
REFRESHING_STATUS_HEADLINE = "Checking your login state…"
STALE_RECONFIRM_EXPLANATION = (
    "Mighty has not reconfirmed this result within the current freshness window."
)
EMPTY_CORRELATED_TIMELINE_MESSAGE = (
    "No correlated timeline events were recorded for this check."
)
PREVIOUS_CHECK_SECTION_LABEL = "Previous completed check"
CURRENT_CHECK_SECTION_LABEL = "Current check"

# Backward-compatible aliases (tests / callers from PR #102).
REFRESH_LABEL = DETERMINING_HEADLINE_CURRENT
REFRESH_LABEL_VERBOSE = DETERMINING_HEADLINE
FIRST_EVER_CHECKING_HEADLINE = DETERMINING_HEADLINE
FIRST_EVER_CHECKING_EVIDENCE = DETERMINING_BODY

_LIVE_CHECKING = "Checking"
_EMPTY_TIMELINE_EVENT_ID = "tl-empty-correlated"


# ── Customer presentation modes (product state machine) ───────────────────────


@dataclass(frozen=True)
class CustomerPresentationMode:
    """One row in the customer presentation truth table."""

    mode: str
    is_checking: bool
    is_stale: bool
    shows_previous_in_timeline: bool
    shows_previous_on_card: bool


def resolve_customer_presentation_mode(
    *,
    refreshing: bool,
    capability_state: CapabilityState | None,
    has_previous: bool,
    is_stale: bool,
    ever_checked: bool,
) -> CustomerPresentationMode:
    """Map lifecycle+capability into exactly one customer presentation mode."""
    if refreshing:
        return CustomerPresentationMode(
            mode="checking" if ever_checked or has_previous else "never_checked",
            is_checking=True,
            is_stale=False,
            shows_previous_in_timeline=has_previous,
            shows_previous_on_card=False,
        )
    state = capability_state or CapabilityState.LOGIN_UNKNOWN
    if state == CapabilityState.LOGIN_UNKNOWN:
        return CustomerPresentationMode(
            mode="check_inconclusive",
            is_checking=False,
            is_stale=False,
            shows_previous_in_timeline=False,
            shows_previous_on_card=False,
        )
    mode_by_state = {
        CapabilityState.SIGNED_OUT: "signed_out",
        CapabilityState.EXTRACTION_SUCCESS: "connected",
        CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA: "logged_in_no_account_data",
        CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED: "extraction_failed",
    }
    base = mode_by_state.get(state, "check_inconclusive")
    if is_stale and base != "check_inconclusive":
        return CustomerPresentationMode(
            mode=f"stale_{base}",
            is_checking=False,
            is_stale=True,
            shows_previous_in_timeline=False,
            shows_previous_on_card=False,
        )
    return CustomerPresentationMode(
        mode=base,
        is_checking=False,
        is_stale=False,
        shows_previous_in_timeline=False,
        shows_previous_on_card=False,
    )


# ── Ordering metadata ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PresentationOrderMeta:
    """First-class ordering keys for monotonic presentation persistence.

    Ordering rule (documented):
      1. Compare ``verification_completed_at`` as aware UTC datetimes.
         Incoming without a completed_at cannot replace an existing row that has one.
      2. When completed_at is strictly newer → accept; strictly older → reject.
      3. When completed_at is equal (same instant) or both missing:
         - Same ``verification_id`` (or both empty and same ``access_cycle_id``)
           → accept (idempotent / duplicate write).
         - Otherwise → reject (conservative; prevents out-of-order UUID races).
      4. First write (no existing row) always accepts.
    """

    verification_id: str | None = None
    access_cycle_id: str | None = None
    verification_completed_at: str | None = None
    lifecycle: str | None = None
    terminal_reason: str | None = None
    account_identity: str | None = None

    def to_row_dict(self) -> dict[str, str | None]:
        return {
            "verification_id": self.verification_id,
            "access_cycle_id": self.access_cycle_id,
            "verification_completed_at": self.verification_completed_at,
            "lifecycle": self.lifecycle,
            "terminal_reason": self.terminal_reason,
            "account_identity": self.account_identity,
        }


def _norm_id(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _completed_dt(meta: PresentationOrderMeta) -> datetime | None:
    return parse_admin_timestamp(meta.verification_completed_at)


def is_newer_presentation(
    incoming: PresentationOrderMeta,
    existing: PresentationOrderMeta | None,
) -> bool:
    """Return True when ``incoming`` may replace ``existing`` under the ordering rule."""
    if existing is None:
        return True

    inc_at = _completed_dt(incoming)
    ex_at = _completed_dt(existing)

    if inc_at is not None and ex_at is not None:
        if inc_at > ex_at:
            return True
        if inc_at < ex_at:
            return False
        # Equal instants → idempotency check below.
    elif inc_at is not None and ex_at is None:
        return True
    elif inc_at is None and ex_at is not None:
        return False

    inc_vid = _norm_id(incoming.verification_id)
    ex_vid = _norm_id(existing.verification_id)
    if inc_vid and ex_vid:
        return inc_vid == ex_vid

    inc_cid = _norm_id(incoming.access_cycle_id)
    ex_cid = _norm_id(existing.access_cycle_id)
    if inc_cid and ex_cid:
        return inc_cid == ex_cid

    # Both sides lack comparable cycle identity — reject to avoid silent regression.
    return False


def order_meta_from_capability(
    view: CapabilityView,
    *,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    terminal_reason: str | None = None,
    verification_completed_at: str | None = None,
    account_identity: str | None = None,
) -> PresentationOrderMeta:
    """Extract ordering metadata from a presented CapabilityView + access signals."""
    ids: dict[str, Any] = {}
    if view.truth_validation is not None:
        ids = dict(view.truth_validation.developer_ids or {})

    verification_id = _norm_id(
        ids.get("verification_id")
        or (getattr(access_view, "verification_id", None) if access_view else None)
    )
    access_cycle_id = _norm_id(
        ids.get("access_cycle_id")
        or (getattr(access_view, "access_cycle_id", None) if access_view else None)
        or (
            getattr(access_view, "last_confirmed_access_cycle_id", None)
            if access_view
            else None
        )
        or verification_id
    )
    lifecycle = _norm_id(
        verification_lifecycle
        or (
            getattr(access_view, "active_verification_lifecycle", None)
            if access_view
            else None
        )
    )
    completed = verification_completed_at
    if completed is None:
        completed = view.last_verified
    if completed:
        dt = parse_admin_timestamp(completed)
        completed = to_utc_iso_z(dt) if dt is not None else str(completed).strip() or None
    return PresentationOrderMeta(
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
        verification_completed_at=completed,
        lifecycle=lifecycle,
        terminal_reason=_norm_id(terminal_reason),
        account_identity=_norm_id(account_identity),
    )


def lookup_verification_order_fields(
    db: Any,
    user_id: str,
    *,
    verification_id: str | None = None,
    provider: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (completed_at, lifecycle, terminal_reason) from session verification."""
    if not verification_id and not provider:
        return None, None, None
    try:
        if verification_id:
            row = db.execute(
                """
                SELECT completed_at, lifecycle, terminal_reason
                FROM provider_session_verification
                WHERE verification_id = ? AND user_id = ?
                """,
                (verification_id, user_id),
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT completed_at, lifecycle, terminal_reason
                FROM provider_session_verification
                WHERE user_id = ? AND provider = ?
                ORDER BY COALESCE(completed_at, requested_at) DESC
                LIMIT 1
                """,
                (user_id, provider),
            ).fetchone()
    except Exception:  # noqa: BLE001 — table may not exist in unit tests
        return None, None, None
    if row is None:
        return None, None, None
    if hasattr(row, "keys"):
        return row["completed_at"], row["lifecycle"], row["terminal_reason"]
    return row[0], row[1], row[2]


def enrich_order_meta_from_db(
    db: Any,
    user_id: str,
    meta: PresentationOrderMeta,
    *,
    provider: str | None = None,
) -> PresentationOrderMeta:
    """Fill completed_at / lifecycle / terminal_reason from the verification row."""
    completed, lifecycle, reason = lookup_verification_order_fields(
        db,
        user_id,
        verification_id=meta.verification_id,
        provider=provider if not meta.verification_id else None,
    )
    completed_iso = meta.verification_completed_at
    if completed:
        dt = parse_admin_timestamp(completed)
        completed_iso = to_utc_iso_z(dt) if dt is not None else str(completed).strip()
    return PresentationOrderMeta(
        verification_id=meta.verification_id,
        access_cycle_id=meta.access_cycle_id or meta.verification_id,
        verification_completed_at=completed_iso or meta.verification_completed_at,
        lifecycle=_norm_id(lifecycle) or meta.lifecycle,
        terminal_reason=_norm_id(reason) or meta.terminal_reason,
        account_identity=meta.account_identity,
    )


def apply_selected_verification_timestamp(
    view: CapabilityView,
    meta: PresentationOrderMeta,
) -> CapabilityView:
    """Bind presentation clocks and Truth Timeline to the selected verification.

    ``CapabilityView.last_verified`` is otherwise created from
    ``last_confirmed_ready_at`` (extraction / prior ready). That value can lag
    the selected terminal verification's completion time. Customer UI labels
    ("Last confirmed", "Latest check completed") bind to ``last_verified``, and
    Truth Timeline events were historically stamped from the same readiness
    clock — so both must be rebuilt from ``verification_completed_at``.
    """
    completed = meta.verification_completed_at
    verification_id = _norm_id(meta.verification_id) or view.current_verification_id
    access_cycle_id = _norm_id(meta.access_cycle_id) or verification_id

    if completed and view.last_verified != completed:
        view = replace(
            view,
            last_verified=completed,
            current_verification_id=verification_id or view.current_verification_id,
            pipeline=_restamp_pipeline(view.pipeline, completed),
        )
        view = _rebuild_truth_for_selected_verification(
            view,
            verification_id=verification_id,
            access_cycle_id=access_cycle_id,
        )
    elif verification_id or access_cycle_id:
        # Completion clock already matches; still align IDs / correlation tags.
        view = replace(
            view,
            current_verification_id=verification_id or view.current_verification_id,
        )
        view = _ensure_truth_ids(
            view,
            verification_id=verification_id,
            access_cycle_id=access_cycle_id,
        )

    return correlate_presentation_timeline(
        view,
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
        empty_if_none=bool(verification_id or completed),
    )


def _restamp_pipeline(
    pipeline: tuple[PipelineStage, ...],
    completed_at: str,
) -> tuple[PipelineStage, ...]:
    """Align pipeline stage clocks with the selected verification completion."""
    return tuple(
        replace(stage, timestamp=completed_at if stage.timestamp is not None else None)
        for stage in pipeline
    )


def _rebuild_truth_for_selected_verification(
    view: CapabilityView,
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
) -> CapabilityView:
    from mighty.truth_validation import attach_truth_validation

    previous_state = None
    if view.truth_validation is not None and view.truth_validation.transition:
        previous_state = view.truth_validation.transition.previous_state
    return attach_truth_validation(
        view,
        previous_state=previous_state,
        session_confidence=view.confidence,
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
    )


def _ensure_truth_ids(
    view: CapabilityView,
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
) -> CapabilityView:
    truth = view.truth_validation
    if truth is None:
        return _rebuild_truth_for_selected_verification(
            view,
            verification_id=verification_id,
            access_cycle_id=access_cycle_id,
        )
    ids = dict(truth.developer_ids or {})
    if verification_id:
        ids["verification_id"] = verification_id
    if access_cycle_id:
        ids["access_cycle_id"] = access_cycle_id
    if ids == dict(truth.developer_ids or {}):
        return view
    # IDs changed without a clock rewrite — rebuild so event metadata matches.
    return _rebuild_truth_for_selected_verification(
        view,
        verification_id=verification_id or ids.get("verification_id"),
        access_cycle_id=access_cycle_id or ids.get("access_cycle_id"),
    )


def _event_verification_id(event: Any) -> str | None:
    meta = getattr(event, "metadata", None) or {}
    if isinstance(meta, dict):
        return _norm_id(meta.get("verification_id"))
    return None


def _event_access_cycle_id(event: Any) -> str | None:
    meta = getattr(event, "metadata", None) or {}
    if isinstance(meta, dict):
        return _norm_id(meta.get("access_cycle_id"))
    return None


def event_matches_presentation(
    event: Any,
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
) -> bool:
    """True when an event is correlated to the presented verification cycle.

    Legacy events with no correlation IDs are not treated as belonging to a
    newer verification that does have an identity.
    """
    event_vid = _event_verification_id(event)
    event_cycle = _event_access_cycle_id(event)
    if verification_id:
        if not event_vid:
            return False
        if event_vid != verification_id:
            return False
    if access_cycle_id:
        if event_cycle and event_cycle != access_cycle_id:
            return False
        # When the presentation has a cycle id but the event only carries
        # verification_id, allow match if verification_id already matched.
        if not event_cycle and not verification_id:
            return False
    return True


def filter_correlated_timeline_events(
    events: tuple[Any, ...] | list[Any],
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
) -> tuple[tuple[Any, ...], int]:
    """Return (matching events sorted, omitted_count)."""
    from mighty.truth_validation import sort_timeline_events

    matched = [
        event
        for event in events
        if event_matches_presentation(
            event,
            verification_id=verification_id,
            access_cycle_id=access_cycle_id,
        )
    ]
    omitted = len(list(events)) - len(matched)
    if matched and hasattr(matched[0], "category"):
        return sort_timeline_events(matched), omitted
    # PresentationTimelineEvent has no category — sort by timestamp/description.
    matched_sorted = tuple(
        sorted(
            matched,
            key=lambda e: (
                getattr(e, "timestamp", None) or "",
                getattr(e, "description", None) or "",
            ),
        )
    )
    return matched_sorted, omitted


def _empty_correlated_timeline_event(
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
    timestamp: str | None,
) -> Any:
    from mighty.truth_validation import (
        EvidenceCategory,
        EvidenceOutcome,
        TruthEvidence,
    )

    return TruthEvidence(
        id=_EMPTY_TIMELINE_EVENT_ID,
        timestamp=timestamp,
        category=EvidenceCategory.VERIFICATION,
        description=EMPTY_CORRELATED_TIMELINE_MESSAGE,
        outcome=EvidenceOutcome.UNKNOWN,
        confidence_contribution=0,
        metadata={
            k: v
            for k, v in {
                "verification_id": verification_id,
                "access_cycle_id": access_cycle_id,
                "source": "empty_correlated_timeline",
            }.items()
            if v
        },
    )


def correlate_presentation_timeline(
    view: CapabilityView,
    *,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
    empty_if_none: bool = True,
) -> CapabilityView:
    """Keep only timeline/evidence rows for the presented verification cycle."""
    truth = view.truth_validation
    if truth is None:
        return view

    vid = _norm_id(verification_id) or _norm_id(
        (truth.developer_ids or {}).get("verification_id")
    ) or _norm_id(view.current_verification_id)
    cycle = _norm_id(access_cycle_id) or _norm_id(
        (truth.developer_ids or {}).get("access_cycle_id")
    ) or vid

    timeline, _omitted_tl = filter_correlated_timeline_events(
        truth.timeline,
        verification_id=vid,
        access_cycle_id=cycle,
    )
    evidence, _omitted_ev = filter_correlated_timeline_events(
        truth.evidence,
        verification_id=vid,
        access_cycle_id=cycle,
    )
    if empty_if_none and vid and not timeline:
        timeline = (
            _empty_correlated_timeline_event(
                verification_id=vid,
                access_cycle_id=cycle,
                timestamp=view.last_verified,
            ),
        )

    ids = dict(truth.developer_ids or {})
    if vid:
        ids["verification_id"] = vid
    if cycle:
        ids["access_cycle_id"] = cycle

    new_truth = replace(
        truth,
        timeline=timeline,
        evidence=evidence,
        developer_ids=ids,
    )
    return replace(
        view,
        truth_validation=new_truth,
        current_verification_id=vid or view.current_verification_id,
    )


def build_timeline_correlation_record(view: CapabilityView) -> dict[str, Any]:
    """Sanitized correlation diagnostic for Dashboard/API parity asserts."""
    truth = view.truth_validation
    ids = dict(truth.developer_ids or {}) if truth else {}
    presentation_vid = _norm_id(
        ids.get("verification_id") or view.current_verification_id
    )
    presentation_cycle = _norm_id(ids.get("access_cycle_id")) or presentation_vid

    current_vids: list[str] = []
    current_cycles: list[str] = []
    previous_vids: list[str] = []
    mismatched = 0
    omitted = 0

    if truth is not None:
        for event in truth.timeline:
            event_vid = _event_verification_id(event)
            event_cycle = _event_access_cycle_id(event)
            if event_vid:
                current_vids.append(event_vid)
            if event_cycle:
                current_cycles.append(event_cycle)
            if not event_matches_presentation(
                event,
                verification_id=presentation_vid,
                access_cycle_id=presentation_cycle,
            ):
                mismatched += 1
                if event_vid and event_vid != presentation_vid:
                    previous_vids.append(event_vid)

    for section in view.timeline_sections:
        is_previous = "previous" in (section.label or "").lower()
        for event in section.events:
            # Presentation events may lack metadata; section label is authoritative.
            if is_previous:
                continue
            # Current-check synthetic rows should not carry prior terminal ids.
            pass

    return {
        "presentation_verification_id": presentation_vid,
        "presentation_access_cycle_id": presentation_cycle,
        "current_timeline_verification_ids": sorted(set(current_vids)),
        "current_timeline_access_cycle_ids": sorted(set(current_cycles)),
        "previous_timeline_verification_ids": sorted(set(previous_vids)),
        "mismatched_event_count": mismatched,
        "omitted_event_count": omitted,
    }


def resolve_order_meta_for_view(
    view: CapabilityView,
    *,
    db: Any,
    user_id: str,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    account_identity: str | None = None,
    order_meta: PresentationOrderMeta | None = None,
) -> PresentationOrderMeta:
    """Build order meta and fill completion time from the verification row."""
    meta = order_meta or order_meta_from_capability(
        view,
        access_view=access_view,
        verification_lifecycle=verification_lifecycle,
        account_identity=account_identity,
    )
    if account_identity and not meta.account_identity:
        meta = PresentationOrderMeta(
            verification_id=meta.verification_id,
            access_cycle_id=meta.access_cycle_id,
            verification_completed_at=meta.verification_completed_at,
            lifecycle=meta.lifecycle,
            terminal_reason=meta.terminal_reason,
            account_identity=account_identity,
        )
    return enrich_order_meta_from_db(
        db, user_id, meta, provider=view.provider,
    )


def fingerprint_account_identity(username_material: str | None) -> str | None:
    """Stable non-reversible identity fingerprint for a provider credential."""
    text = (username_material or "").strip()
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def resolve_account_identity(
    db: Any,
    user_id: str,
    provider: str,
    *,
    decrypt_username: Any | None = None,
) -> str | None:
    """Fingerprint the current credential identity for ``user_id`` + ``provider``."""
    try:
        row = db.execute(
            """
            SELECT username_enc, created_at FROM account_credentials
            WHERE user_id = ? AND source = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if row is None:
        return None
    username_enc = row["username_enc"] if hasattr(row, "keys") else row[0]
    created_at = row["created_at"] if hasattr(row, "keys") else row[1]
    material = None
    if username_enc and decrypt_username is not None:
        try:
            material = decrypt_username(user_id, username_enc) or None
        except Exception:  # noqa: BLE001
            material = None
    if not material:
        # Fall back to ciphertext / created_at so empty usernames still isolate rows.
        material = (username_enc or "") + "|" + (created_at or "")
    return fingerprint_account_identity(material)


# ── In-flight / present ──────────────────────────────────────────────────────


def is_customer_refresh_in_flight(
    access_view: Any | None,
    *,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
) -> bool:
    """True while a verification/extraction cycle has not reached a terminal outcome."""
    lifecycle = (
        verification_lifecycle
        if verification_lifecycle is not None
        else (
            getattr(access_view, "active_verification_lifecycle", None)
            if access_view
            else None
        )
    )
    lifecycle = (lifecycle or "").strip()
    if lifecycle in TERMINAL_VERIFICATION_LIFECYCLES:
        return False

    bg = (
        background_verification
        if background_verification is not None
        else bool(
            getattr(access_view, "background_verification", False) if access_view else False
        )
    )
    if bg:
        return True
    if lifecycle in ACTIVE_VERIFICATION_LIFECYCLES:
        return True
    if access_view is None:
        return False

    readiness = (getattr(access_view, "readiness", None) or "").strip()
    live_access = (getattr(access_view, "live_access", None) or "").strip()
    session_state = (getattr(access_view, "session_state", None) or "").strip()
    if readiness == "checking" or live_access == _LIVE_CHECKING or session_state == "checking":
        return True
    return False


def customer_visible_signature(view: CapabilityView) -> tuple[Any, ...]:
    """Identity of customer-visible card content (excludes timestamps / IDs)."""
    return (
        view.presentation_phase,
        view.state.value,
        view.primary_headline or view.headline,
        view.explanations,
        view.historical_summary,
        tuple((e.text, e.ok) for e in view.evidence),
        view.confidence,
        tuple((f.label, f.value) for f in view.extracted_fields),
        view.action_required,
        view.action_label,
        view.action_url,
        view.status_is_historical,
    )


def customer_visible_same(left: CapabilityView, right: CapabilityView) -> bool:
    return customer_visible_signature(left) == customer_visible_signature(right)


def is_result_within_customer_truth_freshness(
    confirmed_at: str | None,
    *,
    now: datetime | None = None,
    freshness_seconds: int = CUSTOMER_TRUTH_FRESHNESS_SECONDS,
) -> bool:
    """True when a completed result may still be phrased as current truth."""
    dt = parse_admin_timestamp(confirmed_at)
    if dt is None:
        return False
    clock = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    age = (clock - dt).total_seconds()
    return 0 <= age <= freshness_seconds


def _verification_id_from(
    view: CapabilityView,
    access_view: Any | None,
) -> str | None:
    ids: dict[str, Any] = {}
    if view.truth_validation is not None:
        ids = dict(view.truth_validation.developer_ids or {})
    return _norm_id(
        ids.get("verification_id")
        or view.current_verification_id
        or (getattr(access_view, "verification_id", None) if access_view else None)
        or (
            getattr(access_view, "access_cycle_id", None) if access_view else None
        )
    )


def _check_started_at(access_view: Any | None, live: CapabilityView) -> str | None:
    if access_view is not None:
        for attr in (
            "verification_requested_at",
            "verification_started_at",
            "current_attempt_at",
            "session_evidence_at",
        ):
            value = getattr(access_view, attr, None)
            if value:
                return str(value)
    return live.current_check_started_at


def _historical_outcome_label(state: CapabilityState) -> str:
    """Short previous-check outcome for timeline (never “Last confirmed:”)."""
    if state == CapabilityState.SIGNED_OUT:
        return "Signed out"
    if state == CapabilityState.EXTRACTION_SUCCESS:
        return "Connected — account data extracted"
    if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return "Logged in — no account data"
    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return "Logged in — extraction failed"
    return "Check inconclusive"


def _historical_copy(state: CapabilityState) -> tuple[str, str]:
    """Return (historical_summary, timestamp_label_verb) for stale terminal cards."""
    if state == CapabilityState.SIGNED_OUT:
        return "Last confirmed: Signed out", "Confirmed"
    if state == CapabilityState.EXTRACTION_SUCCESS:
        return (
            "Last confirmed: Mighty could access and extract your account data",
            "Confirmed",
        )
    if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return (
            "Last confirmed: Mighty could tell you were logged in, "
            "but could not see account information",
            "Confirmed",
        )
    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return (
            "Last confirmed: Mighty could tell you were logged in, "
            "but could not extract account information",
            "Confirmed",
        )
    return "Previous check was inconclusive", "Checked"


def _determining_headline(previous: CapabilityView | None) -> str:
    del previous  # One Checking mode — prior results live in the timeline only.
    return DETERMINING_HEADLINE


def _events_from_truth_timeline(
    view: CapabilityView,
) -> tuple[PresentationTimelineEvent, ...]:
    truth = view.truth_validation
    if truth is None or not truth.timeline:
        return ()
    vid = _norm_id(
        (truth.developer_ids or {}).get("verification_id")
        or view.current_verification_id
    )
    cycle = _norm_id((truth.developer_ids or {}).get("access_cycle_id")) or vid
    events, _ = filter_correlated_timeline_events(
        truth.timeline,
        verification_id=vid,
        access_cycle_id=cycle,
    )
    return tuple(
        PresentationTimelineEvent(
            description=item.description,
            timestamp=item.timestamp,
            outcome=item.outcome.value,
        )
        for item in events
        if item.id != _EMPTY_TIMELINE_EVENT_ID
    )


def _current_check_timeline(
    *,
    started_at: str | None,
) -> PresentationTimelineSection:
    return PresentationTimelineSection(
        label=CURRENT_CHECK_SECTION_LABEL,
        events=(
            PresentationTimelineEvent(
                description="Verification started",
                timestamp=started_at,
                outcome="UNKNOWN",
            ),
            PresentationTimelineEvent(
                description="Checking login state",
                timestamp=started_at,
                outcome="UNKNOWN",
            ),
        ),
    )


def _previous_check_timeline(
    previous: CapabilityView,
) -> PresentationTimelineSection | None:
    """Previous terminal verification only — never mixed into Current check."""
    outcome = _historical_outcome_label(previous.state)
    summary = PresentationTimelineEvent(
        description=outcome,
        timestamp=previous.last_verified,
        outcome=(
            "PASS"
            if previous.state
            in (
                CapabilityState.SIGNED_OUT,
                CapabilityState.EXTRACTION_SUCCESS,
            )
            else "UNKNOWN"
        ),
    )
    detail_events = _events_from_truth_timeline(previous)
    # Prefer the explicit outcome row; keep correlated detail events after it.
    events = (summary,) + tuple(
        e for e in detail_events if e.description != outcome
    )
    return PresentationTimelineSection(
        label=PREVIOUS_CHECK_SECTION_LABEL,
        events=events,
    )


def _as_stable(view: CapabilityView) -> CapabilityView:
    explanations = view.explanations
    return replace(
        view,
        is_refreshing=False,
        refresh_label=None,
        presentation_phase="terminal",
        current_verification_active=False,
        terminal_capability_state=view.state.value,
        previous_capability_state=None,
        previous_confirmed_at=None,
        status_is_historical=False,
        primary_headline=view.headline,
        primary_explanation=(" ".join(explanations) if explanations else None),
        timestamp_label="Latest check completed" if view.last_verified else None,
        historical_summary=None,
        historical_timestamp_label=None,
        timeline_sections=(),
    )


def _needs_freshness_demotion(
    view: CapabilityView,
    *,
    now: datetime | None = None,
) -> bool:
    """True when a completed definitive result is too old to phrase as current."""
    # Terminal inconclusive copy is already about the latest check, not a live claim.
    if view.state == CapabilityState.LOGIN_UNKNOWN:
        return False
    # Without a completion timestamp the freshness window cannot be applied here.
    if not view.last_verified:
        return False
    return not is_result_within_customer_truth_freshness(
        view.last_verified,
        now=now,
    )


def persistable_terminal_capability(
    live: CapabilityView,
    presented: CapabilityView,
) -> CapabilityView | None:
    """Canonical terminal card to persist, or None while determining/in-flight.

    Freshness demotion is presentation-only and must not be written back.
    """
    if presented.is_refreshing or presented.presentation_phase == "determining":
        return None
    if presented.status_is_historical and not presented.current_verification_active:
        return _as_stable(live)
    return presented


def _stale_historical_presentation(view: CapabilityView) -> CapabilityView:
    """Completed but stale — historical wording only, never present-tense current truth.

    Primary headline carries the historical claim. Do not also populate
    ``historical_summary`` (that would duplicate “Last confirmed” on the card).
    """
    summary, _verb = _historical_copy(view.state)
    # Last confirmed signed-out may still offer sign-in; other stale states must not
    # imply a fresh current conclusion via CTA or extracted "current" fields.
    keep_signed_out_cta = (
        view.state == CapabilityState.SIGNED_OUT
        and bool(view.action_required)
        and bool(view.action_label)
        and bool(view.action_url)
    )
    return replace(
        view,
        headline=summary,
        explanations=(STALE_RECONFIRM_EXPLANATION,),
        evidence=(EvidenceItem(STALE_RECONFIRM_EXPLANATION, None),),
        confidence=None,
        action_required=keep_signed_out_cta,
        action_label=view.action_label if keep_signed_out_cta else None,
        action_url=view.action_url if keep_signed_out_cta else None,
        extracted_fields=(),
        is_refreshing=False,
        refresh_label=None,
        presentation_phase="terminal",
        current_verification_active=False,
        terminal_capability_state=view.state.value,
        previous_capability_state=view.state.value,
        previous_confirmed_at=view.last_verified,
        status_is_historical=True,
        primary_headline=summary,
        primary_explanation=STALE_RECONFIRM_EXPLANATION,
        timestamp_label="Last confirmed" if view.last_verified else None,
        # Primary already is the historical claim — no second card block.
        historical_summary=None,
        historical_timestamp_label=None,
        timeline_sections=(),
    )


def _determining_view(
    live: CapabilityView,
    *,
    previous: CapabilityView | None,
    access_view: Any | None,
) -> CapabilityView:
    """Primary status is Checking; prior result lives only in the timeline.

    Illegal: primary stack showing Checking plus “Last confirmed: Signed out”.
    Illegal: capability_state retained as a prior terminal conclusion mid-check.
    """
    headline = _determining_headline(previous)
    started_at = _check_started_at(access_view, live)
    verification_id = _verification_id_from(live, access_view)
    sections: list[PresentationTimelineSection] = []
    if previous is not None:
        prev_section = _previous_check_timeline(previous)
        if prev_section is not None:
            sections.append(prev_section)
    sections.append(_current_check_timeline(started_at=started_at))

    previous_state = previous.state.value if previous is not None else None
    previous_confirmed_at = previous.last_verified if previous is not None else None

    return replace(
        live,
        # Neutral while Checking — never flash prior signed_out/connected as current.
        state=CapabilityState.LOGIN_UNKNOWN,
        headline=headline,
        explanations=(DETERMINING_BODY,),
        evidence=(EvidenceItem(DETERMINING_BODY, None),),
        confidence=None,
        action_required=False,
        action_label=None,
        action_url=None,
        extracted_fields=(),
        # Current-check clock only; prior completion stays on previous_confirmed_at.
        last_verified=started_at,
        is_refreshing=True,
        refresh_label=headline,
        presentation_phase="determining",
        current_verification_active=True,
        current_verification_id=verification_id,
        current_check_started_at=started_at,
        terminal_capability_state=None,
        previous_capability_state=previous_state,
        previous_confirmed_at=previous_confirmed_at,
        # Previous is timeline-only — not a card-level historical block.
        status_is_historical=False,
        primary_headline=headline,
        primary_explanation=DETERMINING_BODY,
        timestamp_label="Checking started" if started_at else None,
        historical_summary=None,
        historical_timestamp_label=None,
        timeline_sections=tuple(sections),
        truth_validation=_determining_truth_validation(live, started_at=started_at),
    )


def _determining_truth_validation(
    live: CapabilityView,
    *,
    started_at: str | None,
) -> Any:
    """Replace terminal capability timeline with in-flight current-check events."""
    truth = live.truth_validation
    if truth is None:
        return None
    from mighty.truth_validation import EvidenceCategory, EvidenceOutcome, TruthEvidence

    vid = _norm_id(
        (truth.developer_ids or {}).get("verification_id")
        or live.current_verification_id
    )
    cycle = _norm_id((truth.developer_ids or {}).get("access_cycle_id")) or vid
    corr = {
        k: v
        for k, v in {
            "verification_id": vid,
            "access_cycle_id": cycle,
            "source": "current_check",
        }.items()
        if v
    }
    events = (
        TruthEvidence(
            id="current-verification-started",
            timestamp=started_at,
            category=EvidenceCategory.VERIFICATION,
            description="Verification started",
            outcome=EvidenceOutcome.UNKNOWN,
            confidence_contribution=0,
            metadata=corr,
        ),
        TruthEvidence(
            id="current-determining-login",
            timestamp=started_at,
            category=EvidenceCategory.VERIFICATION,
            description="Checking login state",
            outcome=EvidenceOutcome.UNKNOWN,
            confidence_contribution=0,
            metadata=corr,
        ),
    )
    ids = dict(truth.developer_ids or {})
    if vid:
        ids["verification_id"] = vid
    if cycle:
        ids["access_cycle_id"] = cycle
    return replace(
        truth,
        capability_state="determining",
        explanation=DETERMINING_BODY,
        evidence=events,
        timeline=events,
        confidence="Low",
        confidence_score=0,
        developer_ids=ids,
    )


def merge_unchanged_presentation(
    previous: CapabilityView,
    live: CapabilityView,
) -> CapabilityView:
    """Keep prior visual card only when the same verification is still current.

    When the selected verification identity or completion clock changes, the
    Truth Timeline / evidence / pipeline must swap atomically with the headline
    timestamp — never retain a previous verification's timeline under a newer
    ``last_verified``.
    """
    prev_tv = previous.truth_validation
    live_tv = live.truth_validation
    prev_vid = _norm_id(
        (
            (prev_tv.developer_ids or {}).get("verification_id")
            if prev_tv is not None
            else None
        )
        or previous.current_verification_id
    )
    live_vid = _norm_id(
        (
            (live_tv.developer_ids or {}).get("verification_id")
            if live_tv is not None
            else None
        )
        or live.current_verification_id
    )
    verification_changed = bool(
        (live_vid and live_vid != prev_vid)
        or (
            live.last_verified
            and previous.last_verified
            and live.last_verified != previous.last_verified
        )
    )

    if verification_changed:
        # Atomically publish the live verification's presentation unit.
        last_verified = live.last_verified or previous.last_verified
        return _as_stable(
            replace(
                live,
                last_verified=last_verified,
                pipeline=live.pipeline,
                truth_validation=live_tv,
                current_verification_id=(
                    live.current_verification_id or previous.current_verification_id
                ),
            )
        )

    truth = prev_tv
    if prev_tv is not None and live_tv is not None:
        truth = replace(
            prev_tv,
            generated_at=live_tv.generated_at,
            developer_ids=dict(live_tv.developer_ids),
            transition=live_tv.transition,
            # Same verification — live timeline clocks already match.
            timeline=live_tv.timeline or prev_tv.timeline,
            evidence=live_tv.evidence or prev_tv.evidence,
            pipeline=live_tv.pipeline or prev_tv.pipeline,
        )
    elif live_tv is not None:
        truth = live_tv

    if len(previous.pipeline) == len(live.pipeline) and all(
        a.name == b.name and a.verdict == b.verdict
        for a, b in zip(previous.pipeline, live.pipeline)
    ):
        pipeline = live.pipeline
    else:
        pipeline = previous.pipeline

    # Prefer live (already wired to selected verification completed_at when
    # available). Do not retain a stale previous.last_verified merely because
    # live.last_verified was still last_confirmed_ready_at.
    last_verified = live.last_verified or previous.last_verified
    return _as_stable(
        replace(
            previous,
            last_verified=last_verified,
            pipeline=pipeline,
            truth_validation=truth,
            current_verification_id=(
                live.current_verification_id or previous.current_verification_id
            ),
        )
    )


def present_customer_capability(
    live: CapabilityView,
    *,
    previous_stable: CapabilityView | None = None,
    access_view: Any | None = None,
    verification_lifecycle: str | None = None,
    background_verification: bool | None = None,
    force_unknown: bool = False,
    now: datetime | None = None,
) -> CapabilityView:
    """Gate live capability into the customer-visible Truth card.

    force_unknown: developer override — show live immediately. Never persisted
    by callers that honor ``persist=False`` / ``force_unknown`` on save.

    Freshness: a completed definitive result may be phrased as current only while
    inside CUSTOMER_TRUTH_FRESHNESS_SECONDS and no newer verification is active.
    """
    if force_unknown:
        return _finalize_terminal_presentation(_as_stable(live))

    refreshing = is_customer_refresh_in_flight(
        access_view,
        verification_lifecycle=verification_lifecycle,
        background_verification=background_verification,
    )

    if refreshing:
        # A retained prior (persisted or SWR live face) is historical only.
        prior = previous_stable
        if prior is None and live.state != CapabilityState.LOGIN_UNKNOWN:
            # SWR/live non-unknown during an active check is not current truth.
            prior = _as_stable(live)
        return _determining_view(live, previous=prior, access_view=access_view)

    if previous_stable is not None and customer_visible_same(
        _as_stable(previous_stable), _as_stable(live)
    ):
        merged = merge_unchanged_presentation(previous_stable, live)
        if _needs_freshness_demotion(merged, now=now):
            return _finalize_terminal_presentation(
                _stale_historical_presentation(merged)
            )
        return _finalize_terminal_presentation(merged)

    stable = _as_stable(live)
    if _needs_freshness_demotion(stable, now=now):
        return _finalize_terminal_presentation(
            _stale_historical_presentation(stable)
        )
    return _finalize_terminal_presentation(stable)


def _finalize_terminal_presentation(view: CapabilityView) -> CapabilityView:
    """Ensure terminal cards expose only the selected verification's timeline."""
    return correlate_presentation_timeline(
        view,
        verification_id=view.current_verification_id,
        empty_if_none=bool(view.current_verification_id),
    )


# ── Persistence ──────────────────────────────────────────────────────────────


def _utc_now_iso() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


_SCHEMA_COLUMNS = (
    ("verification_id", "TEXT"),
    ("access_cycle_id", "TEXT"),
    ("verification_completed_at", "TEXT"),
    ("lifecycle", "TEXT"),
    ("terminal_reason", "TEXT"),
    ("account_identity", "TEXT"),
)


def ensure_customer_capability_presentation_tables(
    db: Any, *, commit: bool = True,
) -> bool:
    """Create/migrate presentation schema. Commits only when DDL was applied.

    After init_db / startup, subsequent calls are no-ops and do not commit when
    nothing changed (or when ``commit=False``).
    """
    mutated = False
    existing_tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "customer_capability_presentation" not in existing_tables:
        mutated = True
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS customer_capability_presentation (
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            capability_state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            verification_id TEXT,
            access_cycle_id TEXT,
            verification_completed_at TEXT,
            lifecycle TEXT,
            terminal_reason TEXT,
            account_identity TEXT,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    # Migrate older installs that only had the original columns.
    existing = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info(customer_capability_presentation)"
        ).fetchall()
    }
    for name, col_type in _SCHEMA_COLUMNS:
        if name not in existing:
            mutated = True
            try:
                db.execute(
                    f"ALTER TABLE customer_capability_presentation "
                    f"ADD COLUMN {name} {col_type}"
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "duplicate column" not in msg and "already exists" not in msg:
                    raise
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_ccp_user "
        "ON customer_capability_presentation(user_id, updated_at DESC)"
    )
    if commit and mutated:
        db.commit()
    return mutated


def capability_view_to_payload(view: CapabilityView) -> dict[str, Any]:
    payload = view.to_dict()
    payload["is_refreshing"] = False
    payload["refresh_label"] = None
    payload["presentation_phase"] = "terminal"
    payload["current_verification_active"] = False
    payload["status_is_historical"] = False
    payload["historical_summary"] = None
    payload["historical_timestamp_label"] = None
    payload["timeline_sections"] = []
    payload["previous_capability_state"] = None
    payload["previous_confirmed_at"] = None
    payload["terminal_capability_state"] = view.state.value
    payload["timestamp_label"] = (
        "Latest check completed" if view.last_verified else None
    )
    return payload


def capability_view_from_payload(payload: dict[str, Any]) -> CapabilityView:
    """Rehydrate a CapabilityView from a stored payload (presentation only)."""
    from mighty.truth_validation import (
        ConfidenceLevel,
        EvidenceCategory,
        EvidenceOutcome,
        TruthEvidence,
        TruthPipelineStage,
        TruthTransition,
        TruthValidation,
    )

    state_raw = payload.get("capability_state") or payload.get("state") or "login_unknown"
    try:
        state = CapabilityState(state_raw)
    except ValueError:
        state = CapabilityState.LOGIN_UNKNOWN

    evidence = tuple(
        EvidenceItem(text=str(e.get("text") or ""), ok=e.get("ok"))
        for e in (payload.get("evidence") or [])
        if isinstance(e, dict)
    )
    extracted = tuple(
        ExtractedField(label=str(f.get("label") or ""), value=str(f.get("value") or ""))
        for f in (payload.get("extracted_fields") or [])
        if isinstance(f, dict) and f.get("label")
    )
    pipeline_stages: list[PipelineStage] = []
    for s in payload.get("pipeline") or []:
        if not isinstance(s, dict):
            continue
        verdict = str(s.get("verdict") or "UNKNOWN")
        if verdict not in ("PASS", "FAIL", "UNKNOWN", "NOT_RUN"):
            verdict = "UNKNOWN"
        pipeline_stages.append(
            PipelineStage(
                name=str(s.get("name") or ""),
                verdict=verdict,  # type: ignore[arg-type]
                timestamp=s.get("timestamp"),
                detail=s.get("detail"),
                id_label=s.get("id_label"),
            )
        )

    truth = None
    tv_raw = payload.get("truth_validation")
    if isinstance(tv_raw, dict):
        def _ev(item: dict[str, Any]) -> TruthEvidence:
            try:
                cat = EvidenceCategory(str(item.get("category") or "session"))
            except ValueError:
                cat = EvidenceCategory.SESSION
            try:
                outcome = EvidenceOutcome(str(item.get("outcome") or "UNKNOWN"))
            except ValueError:
                outcome = EvidenceOutcome.UNKNOWN
            return TruthEvidence(
                id=str(item.get("id") or ""),
                timestamp=item.get("timestamp"),
                category=cat,
                description=str(item.get("description") or ""),
                outcome=outcome,
                confidence_contribution=int(item.get("confidence_contribution") or 0),
                metadata=dict(item.get("metadata") or {}),
            )

        transition = None
        tr = tv_raw.get("transition")
        if isinstance(tr, dict) and tr.get("current_state"):
            transition = TruthTransition(
                previous_state=tr.get("previous_state"),
                current_state=str(tr["current_state"]),
                reason=str(tr.get("reason") or ""),
                timestamp=tr.get("timestamp"),
            )

        pipe: list[TruthPipelineStage] = []
        for s in tv_raw.get("pipeline") or []:
            if not isinstance(s, dict):
                continue
            try:
                verdict = EvidenceOutcome(str(s.get("verdict") or "UNKNOWN"))
            except ValueError:
                verdict = EvidenceOutcome.UNKNOWN
            pipe.append(
                TruthPipelineStage(
                    name=str(s.get("name") or ""),
                    verdict=verdict,
                    timestamp=s.get("timestamp"),
                    duration_ms=s.get("duration_ms"),
                    evidence_ids=tuple(s.get("evidence_ids") or ()),
                    detail=s.get("detail"),
                )
            )

        conf = str(tv_raw.get("confidence") or "Low")
        if conf not in {c.value for c in ConfidenceLevel}:
            conf = "Low"

        truth = TruthValidation(
            capability_state=str(tv_raw.get("capability_state") or state.value),
            confidence=conf,
            confidence_score=int(tv_raw.get("confidence_score") or 0),
            generated_at=str(tv_raw.get("generated_at") or ""),
            explanation=str(tv_raw.get("explanation") or ""),
            evidence=tuple(
                _ev(e) for e in (tv_raw.get("evidence") or []) if isinstance(e, dict)
            ),
            pipeline=tuple(pipe),
            timeline=tuple(
                _ev(e) for e in (tv_raw.get("timeline") or []) if isinstance(e, dict)
            ),
            transition=transition,
            developer_ids={
                str(k): (None if v is None else str(v))
                for k, v in dict(tv_raw.get("developer_ids") or {}).items()
            },
        )

    explanations = payload.get("explanations") or payload.get("explanation") or ()
    if isinstance(explanations, str):
        explanations = (explanations,)
    explanations = tuple(str(x) for x in explanations)
    headline = str(payload.get("headline") or payload.get("title") or "")
    primary_headline = payload.get("primary_headline")
    if primary_headline is None:
        primary_headline = headline
    primary_explanation = payload.get("primary_explanation")
    if primary_explanation is None and explanations:
        primary_explanation = " ".join(explanations)

    timeline_sections: list[PresentationTimelineSection] = []
    for section in payload.get("timeline_sections") or []:
        if not isinstance(section, dict):
            continue
        events = tuple(
            PresentationTimelineEvent(
                description=str(event.get("description") or ""),
                timestamp=event.get("timestamp"),
                outcome=str(event.get("outcome") or "UNKNOWN"),
            )
            for event in (section.get("events") or [])
            if isinstance(event, dict)
        )
        timeline_sections.append(
            PresentationTimelineSection(
                label=str(section.get("label") or ""),
                events=events,
            )
        )

    return CapabilityView(
        provider=str(payload.get("provider") or "amex"),
        display_name=str(payload.get("display_name") or "American Express"),
        state=state,
        headline=headline,
        explanations=explanations,
        evidence=evidence,
        last_verified=payload.get("last_verified"),
        confidence=payload.get("confidence"),
        action_label=payload.get("action_label"),
        action_url=payload.get("action_url"),
        action_required=bool(payload.get("action_required")),
        extracted_fields=extracted,
        pipeline=tuple(pipeline_stages),
        truth_validation=truth,
        is_refreshing=False,
        refresh_label=None,
        presentation_phase="terminal",
        current_verification_active=False,
        current_verification_id=payload.get("current_verification_id"),
        current_check_started_at=None,
        terminal_capability_state=payload.get("terminal_capability_state") or state.value,
        previous_capability_state=None,
        previous_confirmed_at=None,
        status_is_historical=False,
        primary_headline=str(primary_headline) if primary_headline is not None else None,
        primary_explanation=(
            str(primary_explanation) if primary_explanation is not None else None
        ),
        timestamp_label=payload.get("timestamp_label") or (
            "Latest check completed" if payload.get("last_verified") else None
        ),
        historical_summary=None,
        historical_timestamp_label=None,
        timeline_sections=tuple(timeline_sections),
    )


def _row_order_meta(row: Any) -> PresentationOrderMeta:
    if hasattr(row, "keys"):
        return PresentationOrderMeta(
            verification_id=row["verification_id"] if "verification_id" in row.keys() else None,
            access_cycle_id=row["access_cycle_id"] if "access_cycle_id" in row.keys() else None,
            verification_completed_at=(
                row["verification_completed_at"]
                if "verification_completed_at" in row.keys()
                else None
            ),
            lifecycle=row["lifecycle"] if "lifecycle" in row.keys() else None,
            terminal_reason=(
                row["terminal_reason"] if "terminal_reason" in row.keys() else None
            ),
            account_identity=(
                row["account_identity"] if "account_identity" in row.keys() else None
            ),
        )
    # Positional fallback unused in practice.
    return PresentationOrderMeta()


def load_valid_stable_capability(
    db: Any,
    user_id: str,
    provider: str,
    *,
    account_identity: str | None = None,
) -> CapabilityView | None:
    """Pure read of persisted presentation. Never deletes or ensures schema.

    Mismatched or legacy-null identity rows are ignored for the response
    (treated as missing) but left in storage. Cleanup is command-side only
    (credentials changed / reconnect / disconnect / migration).
    """
    try:
        row = db.execute(
            """
            SELECT payload_json, verification_id, access_cycle_id,
                   verification_completed_at, lifecycle, terminal_reason,
                   account_identity
            FROM customer_capability_presentation
            WHERE user_id = ? AND provider = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001 — table may be absent in unit fixtures
        if "customer_capability_presentation" not in str(exc):
            raise
        return None
    if row is None:
        return None

    stored_identity = None
    if hasattr(row, "keys") and "account_identity" in row.keys():
        stored_identity = row["account_identity"]
    # Identity mismatch / legacy null → ignore for response; do not DELETE.
    if account_identity is not None and stored_identity and stored_identity != account_identity:
        return None
    if account_identity is not None and stored_identity is None and account_identity:
        return None

    raw = row["payload_json"] if hasattr(row, "keys") else row[0]
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        view = capability_view_from_payload(payload)
    except Exception:  # noqa: BLE001 — corrupt row must not break dashboard
        return None
    # Stored column IDs are authoritative for correlation. Discard timeline
    # events that belong to another verification (or lack IDs on a newer card).
    order = _row_order_meta(row)
    vid = _norm_id(order.verification_id) or _norm_id(view.current_verification_id)
    cycle = _norm_id(order.access_cycle_id) or vid
    if order.verification_completed_at and view.last_verified != order.verification_completed_at:
        # Prefer the persisted ordering clock when payload last_verified drifted.
        view = replace(view, last_verified=order.verification_completed_at)
    return correlate_presentation_timeline(
        view,
        verification_id=vid,
        access_cycle_id=cycle,
        empty_if_none=bool(vid),
    )


def load_stable_capability(
    db: Any,
    user_id: str,
    provider: str,
    *,
    account_identity: str | None = None,
) -> CapabilityView | None:
    """Alias for :func:`load_valid_stable_capability` (read-only)."""
    return load_valid_stable_capability(
        db, user_id, provider, account_identity=account_identity,
    )


def load_stable_order_meta(
    db: Any,
    user_id: str,
    provider: str,
    *,
    ensure_schema: bool = True,
) -> PresentationOrderMeta | None:
    if ensure_schema:
        ensure_customer_capability_presentation_tables(db)
    row = db.execute(
        """
        SELECT verification_id, access_cycle_id, verification_completed_at,
               lifecycle, terminal_reason, account_identity
        FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    ).fetchone()
    if row is None:
        return None
    return _row_order_meta(row)


def save_stable_capability(
    db: Any,
    user_id: str,
    view: CapabilityView,
    *,
    order_meta: PresentationOrderMeta | None = None,
    access_view: Any | None = None,
    force_unknown: bool = False,
) -> bool:
    """Persist a terminal customer-visible card if newer than the stored one.

    Returns True when the row was written. Never stores in-flight holds or
    debug force_unknown overrides.

    Uses BEGIN IMMEDIATE so concurrent writers serialize the compare-and-swap
    against first-class ordering metadata.
    """
    if force_unknown or view.is_refreshing or view.presentation_phase == "determining":
        return False

    ensure_customer_capability_presentation_tables(db)
    stable = _as_stable(view)
    meta = order_meta or order_meta_from_capability(stable, access_view=access_view)
    if not meta.verification_completed_at:
        meta = PresentationOrderMeta(
            verification_id=meta.verification_id,
            access_cycle_id=meta.access_cycle_id,
            verification_completed_at=_utc_now_iso(),
            lifecycle=meta.lifecycle,
            terminal_reason=meta.terminal_reason,
            account_identity=meta.account_identity,
        )

    payload = capability_view_to_payload(stable)
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    updated_at = _utc_now_iso()

    try:
        db.execute("BEGIN IMMEDIATE")
    except Exception:  # noqa: BLE001 — some test doubles lack transactions
        pass

    try:
        existing = load_stable_order_meta(
            db, user_id, stable.provider, ensure_schema=False,
        )
        if not is_newer_presentation(meta, existing):
            try:
                db.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            return False

        db.execute(
            """
            INSERT INTO customer_capability_presentation (
                user_id, provider, capability_state, payload_json, updated_at,
                verification_id, access_cycle_id, verification_completed_at,
                lifecycle, terminal_reason, account_identity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, provider) DO UPDATE SET
                capability_state = excluded.capability_state,
                payload_json = excluded.payload_json,
                updated_at = excluded.updated_at,
                verification_id = excluded.verification_id,
                access_cycle_id = excluded.access_cycle_id,
                verification_completed_at = excluded.verification_completed_at,
                lifecycle = excluded.lifecycle,
                terminal_reason = excluded.terminal_reason,
                account_identity = excluded.account_identity
            """,
            (
                user_id,
                stable.provider,
                stable.state.value,
                payload_json,
                updated_at,
                meta.verification_id,
                meta.access_cycle_id,
                meta.verification_completed_at,
                meta.lifecycle,
                meta.terminal_reason,
                meta.account_identity,
            ),
        )
        db.commit()
        return True
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:  # noqa: BLE001
            pass
        raise


def clear_stable_capability(db: Any, user_id: str, provider: str) -> None:
    """Invalidate persisted presentation for one user/provider (disconnect / identity)."""
    ensure_customer_capability_presentation_tables(db)
    db.execute(
        """
        DELETE FROM customer_capability_presentation
        WHERE user_id = ? AND provider = ?
        """,
        (user_id, provider),
    )
    db.commit()


def clear_all_stable_capabilities_for_user(db: Any, user_id: str) -> None:
    ensure_customer_capability_presentation_tables(db)
    db.execute(
        "DELETE FROM customer_capability_presentation WHERE user_id = ?",
        (user_id,),
    )
    db.commit()


def build_presented_capability_view(
    access_view: Any | None,
    *,
    previous_stable: CapabilityView | None = None,
    force_unknown: bool = False,
    persist_db: Any | None = None,
    persist_user_id: str | None = None,
    account_identity: str | None = None,
    order_meta: PresentationOrderMeta | None = None,
    write_persist: bool = True,
    **build_kwargs: Any,
) -> CapabilityView:
    """Build live capability, apply presentation gate, optionally persist terminal.

    ``persist_db`` is used for loading prior stable presentation and wiring
    selected-verification timestamps. Writes only occur when
    ``write_persist=True`` (command paths). Customer-facing GETs must pass
    ``write_persist=False``.
    """
    from mighty.capability_state import build_capability_view

    live = build_capability_view(access_view, **build_kwargs)

    previous = previous_stable
    provider = build_kwargs.get("provider") or (
        getattr(access_view, "provider", None) if access_view else "amex"
    )
    identity = account_identity
    meta: PresentationOrderMeta | None = order_meta
    if persist_db is not None and persist_user_id and not force_unknown:
        if identity is None:
            identity = resolve_account_identity(persist_db, persist_user_id, provider)
        if previous is None:
            previous = load_valid_stable_capability(
                persist_db,
                persist_user_id,
                provider,
                account_identity=identity,
            )
        # Wire last_verified from selected verification completed_at before
        # freshness / merge so Dashboard timestamps match the selected cycle.
        meta = resolve_order_meta_for_view(
            live,
            db=persist_db,
            user_id=persist_user_id,
            access_view=access_view,
            account_identity=identity,
            order_meta=meta,
        )
        live = apply_selected_verification_timestamp(live, meta)

    presented = present_customer_capability(
        live,
        previous_stable=previous,
        access_view=access_view,
        force_unknown=force_unknown,
    )
    persist_view = None if force_unknown else persistable_terminal_capability(
        live, presented,
    )
    if (
        write_persist
        and persist_db is not None
        and persist_user_id
        and persist_view is not None
    ):
        if meta is None:
            meta = resolve_order_meta_for_view(
                persist_view,
                db=persist_db,
                user_id=persist_user_id,
                access_view=access_view,
                account_identity=identity,
                order_meta=order_meta,
            )
        persist_view = apply_selected_verification_timestamp(persist_view, meta)
        save_stable_capability(
            persist_db,
            persist_user_id,
            persist_view,
            order_meta=meta,
            access_view=access_view,
            force_unknown=False,
        )
    return presented
