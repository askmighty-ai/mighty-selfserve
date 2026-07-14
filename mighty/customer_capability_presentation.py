"""
mighty.customer_capability_presentation
───────────────────────────────────────
Presentation-layer gate for the Truth Dashboard.

Capability resolution (resolve_capability_state / build_capability_view) still
computes live pipeline truth. This module decides *how* that truth is phrased
for the customer and enforces one current-verification correlation invariant:

  A. Current check — active verification identity + its timestamps/evidence only
  B. Previous completed check — prior terminal verification only

Never combine fields from A and B into one apparent result.

Selection algorithm (deterministic; shared by Dashboard HTML and /api/account-status):

  1. Find the newest active verification for the user/provider.
  2. Find the newest terminal verification for the user/provider.
  3. If an active verification exists:
       primary phase = determining
       current section = active verification only
       historical section = newest terminal older than the active one
  4. If no active verification exists:
       primary phase = terminal
       display the newest terminal verification
  5. Never select terminal capability/evidence solely by provider-level recency
     without verification identity.
  6. Break ties using requested_at / started_at / completed_at + verification_id.
  7. Older or late writes must not replace a newer selected verification.

Freshness calculations may remain internal but must not substitute for actual
timestamps in customer-facing copy.

Does not change capability precedence, verification FSM, extraction, snapshots,
provider adapters, or truth-validation scoring.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal, Sequence

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

DETERMINING_HEADLINE = "Determining your login state…"
DETERMINING_HEADLINE_CURRENT = "Determining your current login state…"
DETERMINING_BODY = (
    "Mighty is checking whether your American Express session is signed in."
)
REFRESHING_STATUS_HEADLINE = "Refreshing current status…"
CHECKING_AGAIN_NOW = "Mighty is checking again now."
STALE_LAST_CONFIRMED_PREFIX = "This result was last confirmed at"
# Kept as alias so older imports keep resolving; never shown to customers.
STALE_RECONFIRM_EXPLANATION = CHECKING_AGAIN_NOW

# Backward-compatible aliases (tests / callers from PR #102).
REFRESH_LABEL = DETERMINING_HEADLINE_CURRENT
REFRESH_LABEL_VERBOSE = DETERMINING_HEADLINE
FIRST_EVER_CHECKING_HEADLINE = DETERMINING_HEADLINE
FIRST_EVER_CHECKING_EVIDENCE = DETERMINING_BODY

_LIVE_CHECKING = "Checking"

TimestampSource = Literal[
    "verification_completed_at",
    "verification_started_at",
    "verification_requested_at",
    "stable_card_completed_at",
    "none",
]


# ── Cycle identity / selection ────────────────────────────────────────────────


@dataclass(frozen=True)
class VerificationCycleIdentity:
    """First-class identity + timing for one verification/access cycle."""

    verification_id: str | None = None
    access_cycle_id: str | None = None
    requested_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    lifecycle: str | None = None
    terminal_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return (self.lifecycle or "") in ACTIVE_VERIFICATION_LIFECYCLES

    @property
    def is_terminal(self) -> bool:
        return (self.lifecycle or "") in TERMINAL_VERIFICATION_LIFECYCLES


@dataclass(frozen=True)
class PresentationSelectionRecord:
    """Sanitized debug record for one presentation resolution."""

    provider: str
    active_verification_id: str | None
    selected_terminal_verification_id: str | None
    previous_verification_id: str | None
    presentation_phase: Literal["determining", "terminal"]
    selected_timestamp_source: TimestampSource
    selected_completed_at: str | None
    current_timeline_event_count: int = 0
    previous_timeline_event_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "active_verification_id": self.active_verification_id,
            "selected_terminal_verification_id": self.selected_terminal_verification_id,
            "previous_verification_id": self.previous_verification_id,
            "presentation_phase": self.presentation_phase,
            "selected_timestamp_source": self.selected_timestamp_source,
            "selected_completed_at": self.selected_completed_at,
            "current_timeline_event_count": self.current_timeline_event_count,
            "previous_timeline_event_count": self.previous_timeline_event_count,
        }


def _norm_iso(value: str | None) -> str | None:
    if not value:
        return None
    dt = parse_admin_timestamp(value)
    if dt is None:
        text = str(value).strip()
        return text or None
    return to_utc_iso_z(dt)


def _cycle_sort_key(cycle: VerificationCycleIdentity) -> tuple:
    """Newer cycles sort higher. Ties broken by verification_id ascending."""
    completed = parse_admin_timestamp(cycle.completed_at)
    started = parse_admin_timestamp(cycle.started_at)
    requested = parse_admin_timestamp(cycle.requested_at)
    # Use epoch floats; missing → -inf so known timestamps win.
    def _epoch(dt: datetime | None) -> float:
        if dt is None:
            return float("-inf")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()

    return (
        _epoch(completed),
        _epoch(started),
        _epoch(requested),
        cycle.verification_id or "",
    )


def cycle_identity_from_access_view(
    access_view: Any | None,
    *,
    live: CapabilityView | None = None,
) -> VerificationCycleIdentity:
    """Extract cycle identity from the access view / live capability."""
    ids: dict[str, Any] = {}
    if live is not None and live.truth_validation is not None:
        ids = dict(live.truth_validation.developer_ids or {})

    verification_id = _norm_id(
        (getattr(access_view, "verification_id", None) if access_view else None)
        or (live.current_verification_id if live else None)
        or ids.get("verification_id")
    )
    access_cycle_id = _norm_id(
        (getattr(access_view, "access_cycle_id", None) if access_view else None)
        or (live.current_access_cycle_id if live else None)
        or ids.get("access_cycle_id")
        or verification_id
    )
    lifecycle = _norm_id(
        (getattr(access_view, "active_verification_lifecycle", None) if access_view else None)
        or (live.verification_lifecycle if live else None)
    )
    requested = None
    started = None
    completed = None
    reason = None
    if access_view is not None:
        requested = getattr(access_view, "verification_requested_at", None)
        started = getattr(access_view, "verification_started_at", None)
        completed = getattr(access_view, "verification_completed_at", None)
        reason = getattr(access_view, "terminal_reason", None)
    if live is not None:
        requested = requested or live.current_check_requested_at
        started = started or live.verification_started_at or live.current_check_started_at
        completed = completed or live.verification_completed_at
        reason = reason or live.terminal_reason
    return VerificationCycleIdentity(
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
        requested_at=_norm_iso(requested),
        started_at=_norm_iso(started),
        completed_at=_norm_iso(completed),
        lifecycle=lifecycle,
        terminal_reason=_norm_id(reason),
    )


def cycle_identity_from_capability(view: CapabilityView) -> VerificationCycleIdentity:
    ids: dict[str, Any] = {}
    if view.truth_validation is not None:
        ids = dict(view.truth_validation.developer_ids or {})
    verification_id = _norm_id(
        view.current_verification_id or ids.get("verification_id")
    )
    return VerificationCycleIdentity(
        verification_id=verification_id,
        access_cycle_id=_norm_id(
            view.current_access_cycle_id
            or ids.get("access_cycle_id")
            or verification_id
        ),
        requested_at=_norm_iso(view.current_check_requested_at),
        started_at=_norm_iso(view.verification_started_at or view.current_check_started_at),
        completed_at=_norm_iso(
            view.verification_completed_at or view.last_verified
        ),
        lifecycle=_norm_id(view.verification_lifecycle),
        terminal_reason=_norm_id(view.terminal_reason),
    )


def select_presentation_cycles(
    *,
    provider: str,
    active: VerificationCycleIdentity | None,
    terminals: Sequence[VerificationCycleIdentity] = (),
    previous_stable: CapabilityView | None = None,
) -> tuple[
    VerificationCycleIdentity | None,
    VerificationCycleIdentity | None,
    VerificationCycleIdentity | None,
    Literal["determining", "terminal"],
]:
    """Deterministic prior/current selector.

    Returns (active_cycle, selected_terminal, previous_terminal, phase).
    """
    active_cycle = active if active is not None and active.is_active else None
    terminal_list = [t for t in terminals if t.is_terminal and _norm_id(t.verification_id)]
    if previous_stable is not None:
        prior = cycle_identity_from_capability(previous_stable)
        if prior.verification_id and prior.verification_id not in {
            t.verification_id for t in terminal_list
        }:
            # Stable card is a terminal presentation even if lifecycle was cleared.
            terminal_list.append(
                VerificationCycleIdentity(
                    verification_id=prior.verification_id,
                    access_cycle_id=prior.access_cycle_id or prior.verification_id,
                    requested_at=prior.requested_at,
                    started_at=prior.started_at,
                    completed_at=prior.completed_at or previous_stable.last_verified,
                    lifecycle=prior.lifecycle or "completed",
                    terminal_reason=prior.terminal_reason,
                )
            )

    terminal_list.sort(key=_cycle_sort_key, reverse=True)

    if active_cycle is not None:
        previous = None
        for candidate in terminal_list:
            if candidate.verification_id == active_cycle.verification_id:
                continue
            # Prefer terminals strictly older than the active cycle.
            if _cycle_sort_key(candidate) < _cycle_sort_key(active_cycle):
                previous = candidate
                break
            if previous is None:
                previous = candidate
        return active_cycle, None, previous, "determining"

    selected = terminal_list[0] if terminal_list else None
    previous = terminal_list[1] if len(terminal_list) > 1 else None
    return None, selected, previous, "terminal"


def build_presentation_selection_record(
    *,
    provider: str,
    phase: Literal["determining", "terminal"],
    active: VerificationCycleIdentity | None,
    selected_terminal: VerificationCycleIdentity | None,
    previous: VerificationCycleIdentity | None,
    timestamp_source: TimestampSource,
    completed_at: str | None,
    current_timeline_event_count: int = 0,
    previous_timeline_event_count: int = 0,
) -> PresentationSelectionRecord:
    return PresentationSelectionRecord(
        provider=provider,
        active_verification_id=active.verification_id if active else None,
        selected_terminal_verification_id=(
            selected_terminal.verification_id if selected_terminal else None
        ),
        previous_verification_id=previous.verification_id if previous else None,
        presentation_phase=phase,
        selected_timestamp_source=timestamp_source,
        selected_completed_at=_norm_iso(completed_at),
        current_timeline_event_count=current_timeline_event_count,
        previous_timeline_event_count=previous_timeline_event_count,
    )


def check_presentation_invariants(
    view: CapabilityView,
    *,
    debug: bool = False,
) -> list[str]:
    """Return invariant violations for a presented CapabilityView.

    Used in tests and when debug=True on presentation resolution.
    """
    del debug  # Same checks always; callers decide whether to raise/log.
    violations: list[str] = []
    current_id = _norm_id(view.current_verification_id)
    previous_id = _norm_id(view.previous_verification_id)

    if (
        view.presentation_phase == "determining"
        and view.current_verification_active
        and view.terminal_capability_state
    ):
        violations.append(
            "active determining card with terminal_capability_state as primary"
        )

    if view.presentation_phase == "terminal" and not view.current_verification_active:
        if not view.verification_completed_at and not view.last_verified:
            violations.append("terminal card without completed_at")

    for section in view.timeline_sections:
        section_id = _norm_id(section.verification_id)
        is_current = section.label == "Current check"
        is_previous = section.label == "Previous completed check"
        for event in section.events:
            event_id = _norm_id(event.verification_id)
            if not event_id:
                continue
            if is_current and previous_id and event_id == previous_id:
                violations.append(
                    "current-check item with previous verification ID"
                )
            if is_previous and current_id and event_id == current_id:
                violations.append(
                    "previous-check item with current verification ID"
                )
            if section_id and event_id != section_id:
                violations.append(
                    f"timeline event verification_id {event_id} "
                    f"does not match section {section_id}"
                )

    if (
        view.presentation_phase == "terminal"
        and view.verification_completed_at
        and view.last_verified
        and _norm_iso(view.verification_completed_at) != _norm_iso(view.last_verified)
        and view.selected_timestamp_source == "verification_completed_at"
    ):
        violations.append("terminal timestamp from a different verification")

    return violations


def assert_presentation_invariants(view: CapabilityView) -> None:
    violations = check_presentation_invariants(view)
    if violations:
        raise AssertionError(
            "presentation invariant violations: " + "; ".join(violations)
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
    return cycle_identity_from_access_view(access_view, live=view).verification_id


def _access_cycle_id_from(
    view: CapabilityView,
    access_view: Any | None,
) -> str | None:
    return cycle_identity_from_access_view(access_view, live=view).access_cycle_id


def _check_timing(
    access_view: Any | None,
    live: CapabilityView,
) -> tuple[str | None, str | None, str | None, TimestampSource]:
    """Return (display_at, started_at, requested_at, source).

    Check started = started_at, falling back to requested_at only if not yet claimed.
    Never uses session evidence / snapshot / account_data timestamps.
    """
    cycle = cycle_identity_from_access_view(access_view, live=live)
    started = cycle.started_at
    requested = cycle.requested_at
    if started:
        return started, started, requested, "verification_started_at"
    if requested:
        return requested, None, requested, "verification_requested_at"
    return None, None, None, "none"


def _check_started_at(access_view: Any | None, live: CapabilityView) -> str | None:
    display_at, _started, _requested, _source = _check_timing(access_view, live)
    return display_at


def _canonical_completion_timestamp(
    view: CapabilityView,
    access_view: Any | None = None,
) -> tuple[str | None, TimestampSource]:
    """Prefer verification completed_at; never invent from snapshot/PSS/account_data."""
    cycle = cycle_identity_from_access_view(access_view, live=view)
    if cycle.completed_at:
        return cycle.completed_at, "verification_completed_at"
    if view.verification_completed_at:
        return _norm_iso(view.verification_completed_at), "verification_completed_at"
    if view.last_verified and view.selected_timestamp_source == "verification_completed_at":
        return _norm_iso(view.last_verified), "verification_completed_at"
    # Stable-card completion already correlated to a verification identity.
    if view.last_verified and _norm_id(view.current_verification_id):
        return _norm_iso(view.last_verified), "stable_card_completed_at"
    return None, "none"


def _historical_copy(state: CapabilityState) -> tuple[str, str]:
    """Return (historical_summary, timestamp_label_verb)."""
    if state == CapabilityState.SIGNED_OUT:
        return "Last confirmed signed out", "Last confirmed"
    if state == CapabilityState.EXTRACTION_SUCCESS:
        return (
            "Last confirmed: Mighty could access and extract your account data",
            "Last confirmed",
        )
    if state == CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA:
        return (
            "Last confirmed: Mighty could tell you were logged in, "
            "but could not see account information",
            "Last confirmed",
        )
    if state == CapabilityState.LOGIN_VISIBLE_EXTRACTION_FAILED:
        return (
            "Last confirmed: Mighty could tell you were logged in, "
            "but could not extract account information",
            "Last confirmed",
        )
    return "Previous check was inconclusive", "Last confirmed"


def _stale_explanation(confirmed_at: str | None) -> str:
    if confirmed_at:
        return f"{STALE_LAST_CONFIRMED_PREFIX} {confirmed_at}."
    return f"{STALE_LAST_CONFIRMED_PREFIX}."


def _determining_headline(previous: CapabilityView | None) -> str:
    if previous is None:
        return DETERMINING_HEADLINE
    if previous.state == CapabilityState.EXTRACTION_SUCCESS:
        return REFRESHING_STATUS_HEADLINE
    return DETERMINING_HEADLINE_CURRENT


def _filter_timeline_events(
    events: Sequence[PresentationTimelineEvent],
    *,
    verification_id: str | None,
    access_cycle_id: str | None,
) -> tuple[PresentationTimelineEvent, ...]:
    """Keep only events whose IDs match the section (or lack IDs when section has none)."""
    expected = _norm_id(verification_id)
    expected_cycle = _norm_id(access_cycle_id) or expected
    kept: list[PresentationTimelineEvent] = []
    for event in events:
        event_id = _norm_id(event.verification_id)
        event_cycle = _norm_id(event.access_cycle_id)
        if expected and event_id and event_id != expected:
            continue
        if expected_cycle and event_cycle and event_cycle != expected_cycle:
            continue
        if expected and not event_id:
            # Untagged legacy events may be assigned to the section identity.
            event = replace(
                event,
                verification_id=expected,
                access_cycle_id=expected_cycle,
            )
        kept.append(event)
    return tuple(kept)


def _events_from_truth_timeline(
    view: CapabilityView,
    *,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
) -> tuple[PresentationTimelineEvent, ...]:
    truth = view.truth_validation
    if truth is None or not truth.timeline:
        return ()
    vid = _norm_id(verification_id) or _norm_id(view.current_verification_id)
    cid = _norm_id(access_cycle_id) or _norm_id(view.current_access_cycle_id) or vid
    events = tuple(
        PresentationTimelineEvent(
            description=item.description,
            timestamp=item.timestamp,
            outcome=item.outcome.value,
            result=item.outcome.value,
            verification_id=vid,
            access_cycle_id=cid,
            source="truth_timeline",
        )
        for item in truth.timeline
    )
    return _filter_timeline_events(
        events, verification_id=vid, access_cycle_id=cid,
    )


def _current_check_timeline(
    *,
    display_at: str | None,
    started_at: str | None,
    requested_at: str | None,
    verification_id: str | None,
    access_cycle_id: str | None,
    timestamp_source: TimestampSource,
) -> PresentationTimelineSection:
    vid = _norm_id(verification_id)
    cid = _norm_id(access_cycle_id) or vid
    # Prefer started; fall back to requested so rows never render as em dash.
    event_ts = started_at or requested_at or display_at
    start_desc = (
        "Verification started"
        if started_at or timestamp_source == "verification_started_at"
        else "Verification requested"
    )
    return PresentationTimelineSection(
        label="Current check",
        verification_id=vid,
        access_cycle_id=cid,
        events=(
            PresentationTimelineEvent(
                description=start_desc,
                timestamp=event_ts,
                outcome="UNKNOWN",
                result="UNKNOWN",
                verification_id=vid,
                access_cycle_id=cid,
                source="verification",
            ),
            PresentationTimelineEvent(
                description="Determining login state",
                timestamp=event_ts,
                outcome="UNKNOWN",
                result="UNKNOWN",
                verification_id=vid,
                access_cycle_id=cid,
                source="verification",
            ),
        ),
    )


def _previous_check_timeline(
    previous: CapabilityView,
) -> PresentationTimelineSection | None:
    vid = _norm_id(previous.current_verification_id)
    cid = _norm_id(previous.current_access_cycle_id) or vid
    events = _events_from_truth_timeline(
        previous, verification_id=vid, access_cycle_id=cid,
    )
    if not events:
        return None
    return PresentationTimelineSection(
        label="Previous completed check",
        verification_id=vid,
        access_cycle_id=cid,
        events=events,
    )


def _as_stable(view: CapabilityView) -> CapabilityView:
    explanations = view.explanations
    completed, source = _canonical_completion_timestamp(view)
    last_verified = completed or view.last_verified
    return replace(
        view,
        is_refreshing=False,
        refresh_label=None,
        presentation_phase="terminal",
        current_verification_active=False,
        terminal_capability_state=view.state.value,
        previous_capability_state=None,
        previous_confirmed_at=None,
        previous_verification_id=None,
        previous_access_cycle_id=None,
        status_is_historical=False,
        primary_headline=view.headline,
        primary_explanation=(" ".join(explanations) if explanations else None),
        last_verified=last_verified,
        verification_completed_at=completed or view.verification_completed_at,
        timestamp_label="Latest check completed" if last_verified else None,
        historical_summary=None,
        historical_timestamp_label=None,
        timeline_sections=(),
        selected_timestamp_source=source if last_verified else "none",
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
    Never persist a derived terminal card if its verification identity cannot
    be proven.
    """
    if presented.is_refreshing or presented.presentation_phase == "determining":
        return None
    if presented.status_is_historical and not presented.current_verification_active:
        candidate = _as_stable(live)
    else:
        candidate = presented
    if not _norm_id(candidate.current_verification_id):
        return None
    return candidate


def _stale_historical_presentation(view: CapabilityView) -> CapabilityView:
    """Completed but stale — historical wording only, never present-tense current truth."""
    summary, verb = _historical_copy(view.state)
    confirmed_at = view.verification_completed_at or view.last_verified
    explanation = _stale_explanation(confirmed_at)
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
        explanations=(explanation,),
        evidence=(EvidenceItem(explanation, None),),
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
        previous_confirmed_at=confirmed_at,
        status_is_historical=True,
        primary_headline=summary,
        primary_explanation=explanation,
        timestamp_label="Last confirmed" if confirmed_at else None,
        historical_summary=summary,
        historical_timestamp_label=verb,
        timeline_sections=(),
        selected_timestamp_source=(
            view.selected_timestamp_source or "stable_card_completed_at"
        ),
    )


def _determining_view(
    live: CapabilityView,
    *,
    previous: CapabilityView | None,
    access_view: Any | None,
    selection: PresentationSelectionRecord | None = None,
) -> CapabilityView:
    """Primary status is determining; prior result is historical only."""
    headline = _determining_headline(previous)
    display_at, started_at, requested_at, ts_source = _check_timing(access_view, live)
    cycle = cycle_identity_from_access_view(access_view, live=live)
    verification_id = cycle.verification_id or _verification_id_from(live, access_view)
    access_cycle_id = cycle.access_cycle_id or verification_id
    sections: list[PresentationTimelineSection] = []
    previous_event_count = 0
    if previous is not None:
        prev_section = _previous_check_timeline(previous)
        if prev_section is not None:
            sections.append(prev_section)
            previous_event_count = len(prev_section.events)
    current_section = _current_check_timeline(
        display_at=display_at,
        started_at=started_at,
        requested_at=requested_at,
        verification_id=verification_id,
        access_cycle_id=access_cycle_id,
        timestamp_source=ts_source,
    )
    sections.append(current_section)

    historical_summary = None
    historical_timestamp_label = None
    previous_state = None
    previous_confirmed_at = None
    previous_verification_id = None
    previous_access_cycle_id = None
    # Determining must not carry a prior terminal CapabilityState as primary.
    determining_state = CapabilityState.LOGIN_UNKNOWN
    if previous is not None:
        historical_summary, historical_timestamp_label = _historical_copy(previous.state)
        previous_state = previous.state.value
        previous_confirmed_at = (
            previous.verification_completed_at or previous.last_verified
        )
        previous_verification_id = previous.current_verification_id
        previous_access_cycle_id = (
            previous.current_access_cycle_id or previous.current_verification_id
        )

    if started_at:
        timestamp_label = "Check started"
    elif requested_at:
        timestamp_label = "Requested at"
    else:
        timestamp_label = None

    selection_record = selection or build_presentation_selection_record(
        provider=live.provider,
        phase="determining",
        active=cycle if cycle.is_active or verification_id else None,
        selected_terminal=None,
        previous=(
            cycle_identity_from_capability(previous) if previous is not None else None
        ),
        timestamp_source=ts_source,
        completed_at=None,
        current_timeline_event_count=len(current_section.events),
        previous_timeline_event_count=previous_event_count,
    )

    return replace(
        live,
        state=determining_state,
        headline=headline,
        explanations=(CHECKING_AGAIN_NOW if previous is not None else DETERMINING_BODY,),
        evidence=(
            EvidenceItem(
                CHECKING_AGAIN_NOW if previous is not None else DETERMINING_BODY,
                None,
            ),
        ),
        confidence=None,
        action_required=False,
        action_label=None,
        action_url=None,
        extracted_fields=(),
        last_verified=previous_confirmed_at if previous is not None else display_at,
        is_refreshing=True,
        refresh_label=headline,
        presentation_phase="determining",
        current_verification_active=True,
        current_verification_id=verification_id,
        current_access_cycle_id=access_cycle_id,
        current_check_started_at=started_at or display_at,
        current_check_requested_at=requested_at,
        verification_started_at=started_at,
        verification_completed_at=None,
        verification_lifecycle=cycle.lifecycle,
        terminal_reason=None,
        terminal_capability_state=None,
        previous_verification_id=previous_verification_id,
        previous_access_cycle_id=previous_access_cycle_id,
        previous_capability_state=previous_state,
        previous_confirmed_at=previous_confirmed_at,
        status_is_historical=previous is not None,
        primary_headline=headline,
        primary_explanation=(
            CHECKING_AGAIN_NOW if previous is not None else DETERMINING_BODY
        ),
        timestamp_label=timestamp_label,
        historical_summary=historical_summary,
        historical_timestamp_label=historical_timestamp_label,
        timeline_sections=tuple(sections),
        selected_timestamp_source=ts_source,
        presentation_selection=selection_record.to_dict(),
        # Current-check timeline only on the live truth object — never a terminal
        # capability event while determining.
        truth_validation=_determining_truth_validation(
            live,
            started_at=started_at or display_at,
            verification_id=verification_id,
        ),
    )


def _determining_truth_validation(
    live: CapabilityView,
    *,
    started_at: str | None,
    verification_id: str | None = None,
) -> Any:
    """Replace terminal capability timeline with in-flight current-check events."""
    truth = live.truth_validation
    if truth is None:
        return None
    from mighty.truth_validation import EvidenceCategory, EvidenceOutcome, TruthEvidence

    events = (
        TruthEvidence(
            id="current-verification-started",
            timestamp=started_at,
            category=EvidenceCategory.VERIFICATION,
            description="Verification started",
            outcome=EvidenceOutcome.UNKNOWN,
            confidence_contribution=0,
            metadata={"verification_id": verification_id} if verification_id else {},
        ),
        TruthEvidence(
            id="current-determining-login",
            timestamp=started_at,
            category=EvidenceCategory.VERIFICATION,
            description="Determining login state",
            outcome=EvidenceOutcome.UNKNOWN,
            confidence_contribution=0,
            metadata={"verification_id": verification_id} if verification_id else {},
        ),
    )
    return replace(truth, timeline=events, capability_state="determining")


def merge_unchanged_presentation(
    previous: CapabilityView,
    live: CapabilityView,
) -> CapabilityView:
    """Keep prior visual card; refresh only last-verified / ID-bearing meta."""
    prev_tv = previous.truth_validation
    live_tv = live.truth_validation
    truth = prev_tv
    if prev_tv is not None and live_tv is not None:
        truth = replace(
            prev_tv,
            generated_at=live_tv.generated_at,
            developer_ids=dict(live_tv.developer_ids),
            transition=live_tv.transition,
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

    # Same customer-visible card: adopt the newer correlated completion time.
    # Never keep an older timestamp when live provides a newer one for this
    # (or an equally identified) cycle.
    live_completed, live_source = _canonical_completion_timestamp(live)
    prev_completed = previous.verification_completed_at or previous.last_verified
    last_verified = prev_completed
    source = previous.selected_timestamp_source or live_source
    if live_completed:
        prev_dt = parse_admin_timestamp(prev_completed)
        live_dt = parse_admin_timestamp(live_completed)
        same_id = (
            _norm_id(live.current_verification_id)
            and _norm_id(live.current_verification_id)
            == _norm_id(previous.current_verification_id)
        )
        if prev_dt is None or (live_dt is not None and live_dt >= prev_dt) or same_id:
            last_verified = live_completed
            source = live_source

    # When live has a distinct verification identity, this is a new terminal
    # publication with the same visual face — adopt live identity + completion.
    live_vid = _norm_id(live.current_verification_id)
    prev_vid = _norm_id(previous.current_verification_id)
    if live_vid and live_vid != prev_vid:
        return _as_stable(
            replace(
                live,
                last_verified=live_completed or live.last_verified or prev_completed,
                verification_completed_at=(
                    live_completed or live.verification_completed_at or prev_completed
                ),
                pipeline=pipeline,
                truth_validation=truth if truth is not None else live.truth_validation,
                current_verification_id=live_vid,
                current_access_cycle_id=(
                    live.current_access_cycle_id or live_vid
                ),
                selected_timestamp_source=live_source or "verification_completed_at",
            )
        )

    verification_id = live_vid or prev_vid
    access_cycle_id = (
        live.current_access_cycle_id
        or previous.current_access_cycle_id
        or verification_id
    )

    return _as_stable(
        replace(
            previous,
            last_verified=last_verified,
            verification_completed_at=last_verified,
            pipeline=pipeline,
            truth_validation=truth,
            current_verification_id=verification_id,
            current_access_cycle_id=access_cycle_id,
            selected_timestamp_source=source,
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
    active_cycle: VerificationCycleIdentity | None = None,
    terminal_cycles: Sequence[VerificationCycleIdentity] = (),
    debug: bool = False,
) -> CapabilityView:
    """Gate live capability into the customer-visible Truth card.

    force_unknown: developer override — show live immediately. Never persisted
    by callers that honor ``persist=False`` / ``force_unknown`` on save.

    Dashboard HTML and /api/account-status must call this same selector path.
    """
    cycle = active_cycle or cycle_identity_from_access_view(access_view, live=live)
    if verification_lifecycle:
        cycle = VerificationCycleIdentity(
            verification_id=cycle.verification_id,
            access_cycle_id=cycle.access_cycle_id,
            requested_at=cycle.requested_at,
            started_at=cycle.started_at,
            completed_at=cycle.completed_at,
            lifecycle=_norm_id(verification_lifecycle),
            terminal_reason=cycle.terminal_reason,
        )

    active, selected_terminal, previous_cycle, phase = select_presentation_cycles(
        provider=live.provider,
        active=cycle if cycle.is_active else (
            cycle if is_customer_refresh_in_flight(
                access_view,
                verification_lifecycle=verification_lifecycle or cycle.lifecycle,
                background_verification=background_verification,
            ) and not cycle.is_terminal else None
        ),
        terminals=terminal_cycles,
        previous_stable=previous_stable,
    )

    if force_unknown:
        presented = _as_stable(live)
        if debug:
            assert_presentation_invariants(presented)
        return presented

    refreshing = (
        phase == "determining"
        or is_customer_refresh_in_flight(
            access_view,
            verification_lifecycle=verification_lifecycle or cycle.lifecycle,
            background_verification=background_verification,
        )
    )

    if refreshing:
        # A retained prior (persisted or SWR live face) is historical only.
        prior = previous_stable
        if prior is None and live.state != CapabilityState.LOGIN_UNKNOWN:
            # SWR/live non-unknown during an active check is not current truth.
            prior = _as_stable(live)
        selection = build_presentation_selection_record(
            provider=live.provider,
            phase="determining",
            active=active or cycle,
            selected_terminal=None,
            previous=previous_cycle or (
                cycle_identity_from_capability(prior) if prior is not None else None
            ),
            timestamp_source="none",
            completed_at=None,
        )
        presented = _determining_view(
            live,
            previous=prior,
            access_view=access_view,
            selection=selection,
        )
        if debug:
            assert_presentation_invariants(presented)
        return presented

    # Atomic terminal publication for the selected cycle.
    if previous_stable is not None and customer_visible_same(
        _as_stable(previous_stable), _as_stable(live)
    ):
        merged = merge_unchanged_presentation(previous_stable, live)
        if _needs_freshness_demotion(merged, now=now):
            presented = _stale_historical_presentation(merged)
        else:
            presented = merged
    else:
        stable = _as_stable(live)
        # Bind completion to this verification when known.
        completed, source = _canonical_completion_timestamp(stable, access_view)
        if completed:
            stable = replace(
                stable,
                last_verified=completed,
                verification_completed_at=completed,
                selected_timestamp_source=source,
                timestamp_label="Latest check completed",
            )
        if _needs_freshness_demotion(stable, now=now):
            presented = _stale_historical_presentation(stable)
        else:
            presented = stable

        # Bind selected terminal identity only when it matches the live cycle
        # (or live has no identity yet). Never let a prior cycle overwrite a
        # newer live terminal identity.
        live_vid = _norm_id(presented.current_verification_id) or _norm_id(
            cycle.verification_id
        )
        selected_vid = (
            _norm_id(selected_terminal.verification_id) if selected_terminal else None
        )
        if selected_terminal and selected_vid and (
            live_vid is None or selected_vid == live_vid
        ):
            presented = replace(
                presented,
                current_verification_id=selected_terminal.verification_id,
                current_access_cycle_id=(
                    selected_terminal.access_cycle_id
                    or selected_terminal.verification_id
                ),
                verification_lifecycle=(
                    selected_terminal.lifecycle or presented.verification_lifecycle
                ),
                terminal_reason=(
                    selected_terminal.terminal_reason or presented.terminal_reason
                ),
            )

    selection = build_presentation_selection_record(
        provider=live.provider,
        phase="terminal",
        active=None,
        selected_terminal=selected_terminal or cycle_identity_from_capability(presented),
        previous=previous_cycle,
        timestamp_source=(
            presented.selected_timestamp_source  # type: ignore[arg-type]
            if presented.selected_timestamp_source in {
                "verification_completed_at",
                "verification_started_at",
                "verification_requested_at",
                "stable_card_completed_at",
                "none",
            }
            else "none"
        ),
        completed_at=presented.verification_completed_at or presented.last_verified,
    )
    presented = replace(presented, presentation_selection=selection.to_dict())
    if debug:
        assert_presentation_invariants(presented)
    return presented


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


def ensure_customer_capability_presentation_tables(db: Any) -> None:
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
    db.commit()


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
    payload["previous_verification_id"] = None
    payload["previous_access_cycle_id"] = None
    payload["current_check_started_at"] = None
    payload["current_check_requested_at"] = None
    payload["terminal_capability_state"] = view.state.value
    payload["verification_completed_at"] = (
        view.verification_completed_at or view.last_verified
    )
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
                timestamp=event.get("timestamp") or event.get("occurred_at"),
                outcome=str(event.get("outcome") or event.get("result") or "UNKNOWN"),
                result=str(event.get("result") or event.get("outcome") or "UNKNOWN"),
                verification_id=event.get("verification_id"),
                access_cycle_id=event.get("access_cycle_id"),
                source=event.get("source"),
            )
            for event in (section.get("events") or [])
            if isinstance(event, dict)
        )
        timeline_sections.append(
            PresentationTimelineSection(
                label=str(section.get("label") or ""),
                events=events,
                verification_id=section.get("verification_id"),
                access_cycle_id=section.get("access_cycle_id"),
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
        current_access_cycle_id=payload.get("current_access_cycle_id"),
        current_check_started_at=None,
        current_check_requested_at=None,
        verification_started_at=payload.get("verification_started_at"),
        verification_completed_at=(
            payload.get("verification_completed_at") or payload.get("last_verified")
        ),
        verification_lifecycle=payload.get("verification_lifecycle"),
        terminal_reason=payload.get("terminal_reason"),
        terminal_capability_state=payload.get("terminal_capability_state") or state.value,
        previous_verification_id=None,
        previous_access_cycle_id=None,
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
        selected_timestamp_source=payload.get("selected_timestamp_source"),
        presentation_selection=payload.get("presentation_selection"),
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


def load_stable_capability(
    db: Any,
    user_id: str,
    provider: str,
    *,
    account_identity: str | None = None,
) -> CapabilityView | None:
    ensure_customer_capability_presentation_tables(db)
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
    if row is None:
        return None

    stored_identity = None
    if hasattr(row, "keys") and "account_identity" in row.keys():
        stored_identity = row["account_identity"]
    # Identity mismatch → treat as no prior stable card (do not leak across accounts).
    if account_identity is not None and stored_identity and stored_identity != account_identity:
        return None
    if account_identity is not None and stored_identity is None and account_identity:
        # Legacy row without identity: invalidate rather than risk cross-account reuse.
        clear_stable_capability(db, user_id, provider)
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
    # Overlay first-class columns so identity/timestamps survive payload drift.
    if hasattr(row, "keys"):
        vid = row["verification_id"] if "verification_id" in row.keys() else None
        cid = row["access_cycle_id"] if "access_cycle_id" in row.keys() else None
        completed = (
            row["verification_completed_at"]
            if "verification_completed_at" in row.keys()
            else None
        )
        lifecycle = row["lifecycle"] if "lifecycle" in row.keys() else None
        reason = row["terminal_reason"] if "terminal_reason" in row.keys() else None
        if vid or cid or completed:
            view = replace(
                view,
                current_verification_id=vid or view.current_verification_id,
                current_access_cycle_id=cid or view.current_access_cycle_id,
                verification_completed_at=_norm_iso(completed)
                or view.verification_completed_at,
                last_verified=_norm_iso(completed) or view.last_verified,
                verification_lifecycle=lifecycle or view.verification_lifecycle,
                terminal_reason=reason or view.terminal_reason,
                selected_timestamp_source=(
                    "verification_completed_at"
                    if completed
                    else view.selected_timestamp_source
                ),
            )
    return view


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
    # Refuse to persist without a proven verification identity.
    if not _norm_id(meta.verification_id):
        return False
    if not meta.verification_completed_at:
        completed = (
            stable.verification_completed_at
            or stable.last_verified
            or _utc_now_iso()
        )
        meta = PresentationOrderMeta(
            verification_id=meta.verification_id,
            access_cycle_id=meta.access_cycle_id or meta.verification_id,
            verification_completed_at=_norm_iso(completed),
            lifecycle=meta.lifecycle,
            terminal_reason=meta.terminal_reason,
            account_identity=meta.account_identity,
        )
        stable = replace(
            stable,
            last_verified=_norm_iso(completed),
            verification_completed_at=_norm_iso(completed),
            selected_timestamp_source=(
                stable.selected_timestamp_source or "verification_completed_at"
            ),
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


def load_verification_cycle_identities(
    db: Any,
    user_id: str,
    provider: str,
) -> tuple[VerificationCycleIdentity | None, list[VerificationCycleIdentity]]:
    """Return (active_cycle, terminal_cycles newest-first) for selection."""
    active: VerificationCycleIdentity | None = None
    terminals: list[VerificationCycleIdentity] = []
    try:
        from mighty.session_verification import (
            get_active_session_verification,
            get_latest_session_verification,
            ensure_session_verification_tables,
        )

        ensure_session_verification_tables(db)
        active_row = get_active_session_verification(db, user_id, provider)
        if active_row is not None:
            active = VerificationCycleIdentity(
                verification_id=active_row.verification_id,
                access_cycle_id=active_row.verification_id,
                requested_at=_norm_iso(active_row.requested_at),
                started_at=_norm_iso(active_row.started_at),
                completed_at=_norm_iso(active_row.completed_at),
                lifecycle=active_row.lifecycle,
                terminal_reason=active_row.terminal_reason,
            )
        rows = db.execute(
            """
            SELECT verification_id, lifecycle, requested_at, started_at,
                   completed_at, terminal_reason
            FROM provider_session_verification
            WHERE user_id = ? AND provider = ?
              AND lifecycle IN ('completed', 'failed', 'timed_out')
            ORDER BY COALESCE(completed_at, started_at, requested_at) DESC
            LIMIT 5
            """,
            (user_id, provider),
        ).fetchall()
        for row in rows:
            if hasattr(row, "keys"):
                terminals.append(
                    VerificationCycleIdentity(
                        verification_id=row["verification_id"],
                        access_cycle_id=row["verification_id"],
                        requested_at=_norm_iso(row["requested_at"]),
                        started_at=_norm_iso(row["started_at"]),
                        completed_at=_norm_iso(row["completed_at"]),
                        lifecycle=row["lifecycle"],
                        terminal_reason=row["terminal_reason"],
                    )
                )
            else:
                terminals.append(
                    VerificationCycleIdentity(
                        verification_id=row[0],
                        access_cycle_id=row[0],
                        requested_at=_norm_iso(row[2]),
                        started_at=_norm_iso(row[3]),
                        completed_at=_norm_iso(row[4]),
                        lifecycle=row[1],
                        terminal_reason=row[5],
                    )
                )
        # Ensure latest terminal is present even if query shape differs.
        if not terminals:
            latest = get_latest_session_verification(db, user_id, provider)
            if latest is not None and latest.lifecycle in TERMINAL_VERIFICATION_LIFECYCLES:
                terminals.append(
                    VerificationCycleIdentity(
                        verification_id=latest.verification_id,
                        access_cycle_id=latest.verification_id,
                        requested_at=_norm_iso(latest.requested_at),
                        started_at=_norm_iso(latest.started_at),
                        completed_at=_norm_iso(latest.completed_at),
                        lifecycle=latest.lifecycle,
                        terminal_reason=latest.terminal_reason,
                    )
                )
    except Exception:  # noqa: BLE001 — unit tests may lack verification tables
        return active, terminals
    terminals.sort(key=_cycle_sort_key, reverse=True)
    return active, terminals


def enrich_access_view_with_cycle(
    access_view: Any | None,
    cycle: VerificationCycleIdentity | None,
) -> Any | None:
    """Overlay verification timing onto an access view when fields are missing."""
    if access_view is None or cycle is None:
        return access_view
    updates: dict[str, Any] = {}
    if not getattr(access_view, "verification_id", None) and cycle.verification_id:
        updates["verification_id"] = cycle.verification_id
    if not getattr(access_view, "verification_requested_at", None) and cycle.requested_at:
        updates["verification_requested_at"] = cycle.requested_at
    if not getattr(access_view, "verification_started_at", None) and cycle.started_at:
        updates["verification_started_at"] = cycle.started_at
    if not getattr(access_view, "verification_completed_at", None) and cycle.completed_at:
        updates["verification_completed_at"] = cycle.completed_at
    if not getattr(access_view, "terminal_reason", None) and cycle.terminal_reason:
        updates["terminal_reason"] = cycle.terminal_reason
    if (
        not getattr(access_view, "active_verification_lifecycle", None)
        and cycle.lifecycle
    ):
        updates["active_verification_lifecycle"] = cycle.lifecycle
    if not updates:
        return access_view
    try:
        return replace(access_view, **updates)
    except TypeError:
        return access_view


def build_presented_capability_view(
    access_view: Any | None,
    *,
    previous_stable: CapabilityView | None = None,
    force_unknown: bool = False,
    persist_db: Any | None = None,
    persist_user_id: str | None = None,
    account_identity: str | None = None,
    order_meta: PresentationOrderMeta | None = None,
    debug: bool = False,
    **build_kwargs: Any,
) -> CapabilityView:
    """Build live capability, apply presentation gate, optionally persist terminal."""
    from mighty.capability_state import build_capability_view

    provider = build_kwargs.get("provider") or (
        getattr(access_view, "provider", None) if access_view else "amex"
    )
    active_cycle = None
    terminal_cycles: list[VerificationCycleIdentity] = []
    enriched_access = access_view
    if persist_db is not None and persist_user_id:
        active_cycle, terminal_cycles = load_verification_cycle_identities(
            persist_db, persist_user_id, provider,
        )
        enrich_cycle = active_cycle or (terminal_cycles[0] if terminal_cycles else None)
        enriched_access = enrich_access_view_with_cycle(access_view, enrich_cycle)

    live = build_capability_view(enriched_access, **build_kwargs)

    previous = previous_stable
    if (
        previous is None
        and persist_db is not None
        and persist_user_id
        and not force_unknown
    ):
        identity = account_identity
        if identity is None:
            identity = resolve_account_identity(persist_db, persist_user_id, provider)
        previous = load_stable_capability(
            persist_db,
            persist_user_id,
            provider,
            account_identity=identity,
        )

    presented = present_customer_capability(
        live,
        previous_stable=previous,
        access_view=enriched_access,
        force_unknown=force_unknown,
        active_cycle=active_cycle,
        terminal_cycles=terminal_cycles,
        debug=debug,
    )
    persist_view = None if force_unknown else persistable_terminal_capability(
        live, presented,
    )
    if persist_db is not None and persist_user_id and persist_view is not None:
        meta = order_meta or order_meta_from_capability(
            persist_view,
            access_view=enriched_access,
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
        meta = enrich_order_meta_from_db(
            persist_db,
            persist_user_id,
            meta,
            provider=persist_view.provider,
        )
        save_stable_capability(
            persist_db,
            persist_user_id,
            persist_view,
            order_meta=meta,
            access_view=enriched_access,
            force_unknown=False,
        )
    return presented
