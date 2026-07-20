"""Append-only AccessTimeline for Provider Operations.

Records significant AccessState lifecycle events for the dashboard details panel.
Does not own or mutate Provider Runtime behavior — observers only derive events
from published AccessState transitions and persist a bounded recent history.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mighty.access_state_publication import MATERIAL_FIELDS, assert_no_sensitive_fields
from mighty.provider_runtime_control_center import (
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RECOVERY_STATUS_PLANNING,
    RECOVERY_STATUS_RECOVERING,
    RUNTIME_STATUS_RUNNING,
    RUNTIME_STATUS_STOPPED,
    parse_iso,
)

DEFAULT_TIMELINE_LIMIT = 100

# Significant lifecycle event types (Provider Operations).
EVENT_RUNTIME_STARTED = "runtime_started"
EVENT_RUNTIME_STOPPED = "runtime_stopped"
EVENT_AUTHENTICATION_CHANGED = "authentication_changed"
EVENT_VERIFICATION_SUCCEEDED = "verification_succeeded"
EVENT_VERIFICATION_FAILED = "verification_failed"
EVENT_KEEPALIVE_SUCCEEDED = "keepalive_succeeded"
EVENT_KEEPALIVE_FAILED = "keepalive_failed"
EVENT_RECOVERY_STARTED = "recovery_started"
EVENT_RECOVERY_COMPLETED = "recovery_completed"
EVENT_AWAITING_USER = "awaiting_user"
EVENT_SNAPSHOT_REFRESHED = "snapshot_refreshed"

TIMELINE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EVENT_RUNTIME_STARTED,
        EVENT_RUNTIME_STOPPED,
        EVENT_AUTHENTICATION_CHANGED,
        EVENT_VERIFICATION_SUCCEEDED,
        EVENT_VERIFICATION_FAILED,
        EVENT_KEEPALIVE_SUCCEEDED,
        EVENT_KEEPALIVE_FAILED,
        EVENT_RECOVERY_STARTED,
        EVENT_RECOVERY_COMPLETED,
        EVENT_AWAITING_USER,
        EVENT_SNAPSHOT_REFRESHED,
    }
)

_RECOVERY_ACTIVE = frozenset(
    {RECOVERY_STATUS_RECOVERING, RECOVERY_STATUS_PLANNING}
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _material_fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(payload.get(key) for key in MATERIAL_FIELDS)


@dataclass(frozen=True)
class AccessTimelineEvent:
    """One append-only, timestamped lifecycle event."""

    event_type: str
    message: str
    observed_at: str
    ok: bool = True
    details: dict[str, Any] = field(default_factory=dict)
    event_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "message": self.message,
            "observed_at": self.observed_at,
            "ok": self.ok,
            "details": dict(self.details),
        }


def make_timeline_event(
    event_type: str,
    message: str,
    *,
    observed_at: str | None = None,
    ok: bool = True,
    details: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> AccessTimelineEvent:
    payload = dict(details or {})
    assert_no_sensitive_fields({"details": payload})
    return AccessTimelineEvent(
        event_type=str(event_type),
        message=str(message),
        observed_at=observed_at or _iso_now(),
        ok=bool(ok),
        details=payload,
        event_id=event_id or str(uuid.uuid4()),
    )


class AccessTimeline:
    """In-memory append-only rolling history (newest last). Bounded per provider."""

    def __init__(self, limit: int = DEFAULT_TIMELINE_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._events: deque[AccessTimelineEvent] = deque(maxlen=self._limit)
        self._lock = threading.RLock()

    @property
    def limit(self) -> int:
        return self._limit

    def append(self, event: AccessTimelineEvent) -> AccessTimelineEvent:
        with self._lock:
            self._events.append(event)
        return event

    def record(
        self,
        event_type: str,
        message: str,
        *,
        observed_at: str | None = None,
        ok: bool = True,
        details: dict[str, Any] | None = None,
    ) -> AccessTimelineEvent:
        event = make_timeline_event(
            event_type,
            message,
            observed_at=observed_at,
            ok=ok,
            details=details,
        )
        return self.append(event)

    def list_events(
        self,
        *,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[AccessTimelineEvent]:
        with self._lock:
            events = list(self._events)
        if limit is not None:
            if limit <= 0:
                return []
            events = events[-int(limit) :]
        if newest_first:
            events = list(reversed(events))
        return events

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


def _verification_ok(result: Any) -> bool:
    text = str(result or "").strip().upper()
    return text in {"SIGNED_IN", "OK", "SUCCESS", "SUCCEEDED", "PASS", "PASSED"}


def _keepalive_ok(result: Any) -> bool:
    text = str(result or "").strip().lower()
    return text in {"ok", "success", "succeeded", "pass", "passed"}


def derive_access_timeline_events(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    observed_at: str | None = None,
) -> list[AccessTimelineEvent]:
    """Derive append-only lifecycle events from an AccessState transition.

    Does not dump current AccessState into the timeline — only historical
    lifecycle facts. Heartbeats with no material change yield no events.
    """
    if not isinstance(current, dict):
        return []
    stamp = (
        observed_at
        or str(current.get("updated_at") or current.get("published_at") or "")
        or _iso_now()
    )
    events: list[AccessTimelineEvent] = []

    if previous is None:
        runtime = str(current.get("runtime_state") or "").lower()
        if runtime == RUNTIME_STATUS_RUNNING:
            events.append(
                make_timeline_event(
                    EVENT_RUNTIME_STARTED,
                    "Runtime reported running",
                    observed_at=stamp,
                    details={"runtime_state": runtime},
                )
            )
        events.append(
            make_timeline_event(
                EVENT_SNAPSHOT_REFRESHED,
                "AccessState snapshot received",
                observed_at=stamp,
            )
        )
        return events

    if not isinstance(previous, dict):
        previous = {}

    material_changed = _material_fingerprint(previous) != _material_fingerprint(current)

    prev_runtime = str(previous.get("runtime_state") or "").lower()
    cur_runtime = str(current.get("runtime_state") or "").lower()
    if prev_runtime != cur_runtime:
        if cur_runtime == RUNTIME_STATUS_RUNNING:
            events.append(
                make_timeline_event(
                    EVENT_RUNTIME_STARTED,
                    "Runtime started",
                    observed_at=stamp,
                    details={"from": prev_runtime, "to": cur_runtime},
                )
            )
        elif prev_runtime == RUNTIME_STATUS_RUNNING and cur_runtime != RUNTIME_STATUS_RUNNING:
            events.append(
                make_timeline_event(
                    EVENT_RUNTIME_STOPPED,
                    "Runtime stopped",
                    observed_at=stamp,
                    ok=cur_runtime == RUNTIME_STATUS_STOPPED,
                    details={"from": prev_runtime, "to": cur_runtime},
                )
            )

    prev_auth = str(previous.get("authentication_state") or "")
    cur_auth = str(current.get("authentication_state") or "")
    if prev_auth != cur_auth:
        events.append(
            make_timeline_event(
                EVENT_AUTHENTICATION_CHANGED,
                f"Authentication changed: {prev_auth or '—'} → {cur_auth or '—'}",
                observed_at=stamp,
                ok=cur_auth == "SIGNED_IN",
                details={"from": prev_auth, "to": cur_auth},
            )
        )

    prev_verified = previous.get("last_verified_at")
    cur_verified = current.get("last_verified_at")
    if cur_verified and cur_verified != prev_verified:
        result = current.get("last_verification_result")
        ok = _verification_ok(result) if result is not None else cur_auth == "SIGNED_IN"
        events.append(
            make_timeline_event(
                EVENT_VERIFICATION_SUCCEEDED if ok else EVENT_VERIFICATION_FAILED,
                "Verification succeeded" if ok else "Verification failed",
                observed_at=str(cur_verified) or stamp,
                ok=ok,
                details={
                    "result": result,
                    "authentication_state": cur_auth,
                },
            )
        )

    prev_keepalive = previous.get("last_keepalive_at")
    cur_keepalive = current.get("last_keepalive_at")
    if cur_keepalive and cur_keepalive != prev_keepalive:
        result = current.get("last_keepalive_result")
        ok = _keepalive_ok(result) if result is not None else True
        events.append(
            make_timeline_event(
                EVENT_KEEPALIVE_SUCCEEDED if ok else EVENT_KEEPALIVE_FAILED,
                "Keepalive succeeded" if ok else "Keepalive failed",
                observed_at=str(cur_keepalive) or stamp,
                ok=ok,
                details={"result": result},
            )
        )

    prev_recovery = str(previous.get("recovery_state") or "").lower()
    cur_recovery = str(current.get("recovery_state") or "").lower()
    if prev_recovery != cur_recovery:
        if cur_recovery in _RECOVERY_ACTIVE and prev_recovery not in _RECOVERY_ACTIVE:
            events.append(
                make_timeline_event(
                    EVENT_RECOVERY_STARTED,
                    "Recovery started",
                    observed_at=stamp,
                    details={
                        "from": prev_recovery,
                        "to": cur_recovery,
                        "action": current.get("last_recovery_action"),
                    },
                )
            )
        if prev_recovery in _RECOVERY_ACTIVE and cur_recovery == RECOVERY_STATUS_IDLE:
            events.append(
                make_timeline_event(
                    EVENT_RECOVERY_COMPLETED,
                    "Recovery completed",
                    observed_at=stamp,
                    ok=True,
                    details={
                        "result": current.get("last_recovery_result"),
                        "action": current.get("last_recovery_action"),
                    },
                )
            )
        if prev_recovery in _RECOVERY_ACTIVE and cur_recovery == "failed":
            events.append(
                make_timeline_event(
                    EVENT_RECOVERY_COMPLETED,
                    "Recovery completed with failure",
                    observed_at=stamp,
                    ok=False,
                    details={
                        "result": current.get("last_recovery_result"),
                        "action": current.get("last_recovery_action"),
                    },
                )
            )
        if cur_recovery == RECOVERY_STATUS_AWAITING_USER:
            events.append(
                make_timeline_event(
                    EVENT_AWAITING_USER,
                    "Escalated to awaiting user",
                    observed_at=stamp,
                    ok=False,
                    details={
                        "escalation_reason": current.get("escalation_reason"),
                        "from": prev_recovery,
                    },
                )
            )

    if not events and not material_changed:
        # Pure heartbeat — keep timeline quiet.
        return []

    if material_changed:
        events.append(
            make_timeline_event(
                EVENT_SNAPSHOT_REFRESHED,
                "AccessState snapshot refreshed",
                observed_at=stamp,
            )
        )
    return events


def ensure_access_timeline_tables(db: Any, *, commit: bool = True) -> bool:
    """Create runtime_access_timeline schema if missing."""
    existing_tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    mutated = "runtime_access_timeline" not in existing_tables
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_access_timeline (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT NOT NULL,
            provider      TEXT NOT NULL,
            event_id      TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            message       TEXT NOT NULL,
            observed_at   TEXT NOT NULL,
            ok            INTEGER NOT NULL,
            details_json  TEXT NOT NULL,
            created_at    TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_access_timeline_lookup "
        "ON runtime_access_timeline(user_id, provider, id DESC)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_access_timeline_event "
        "ON runtime_access_timeline(user_id, provider, event_id)"
    )
    if commit and mutated:
        db.commit()
    return mutated


def append_access_timeline_events(
    db: Any,
    user_id: str,
    provider: str,
    events: list[AccessTimelineEvent],
    *,
    limit: int = DEFAULT_TIMELINE_LIMIT,
    created_at: str | None = None,
    commit: bool = True,
) -> list[AccessTimelineEvent]:
    """Persist events append-only and retain only the newest ``limit`` per provider."""
    if not events:
        return []
    ensure_access_timeline_tables(db, commit=False)
    provider_key = str(provider).lower()
    created = created_at or _iso_now()
    stored: list[AccessTimelineEvent] = []
    for event in events:
        details_json = json.dumps(event.details, separators=(",", ":"), sort_keys=True)
        assert_no_sensitive_fields(json.loads(details_json) if details_json else {})
        try:
            db.execute(
                """
                INSERT INTO runtime_access_timeline (
                    user_id, provider, event_id, event_type, message,
                    observed_at, ok, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(user_id),
                    provider_key,
                    event.event_id,
                    event.event_type,
                    event.message,
                    event.observed_at,
                    1 if event.ok else 0,
                    details_json,
                    created,
                ),
            )
            stored.append(event)
        except Exception:
            # Unique event_id collision — skip duplicate append (idempotent).
            continue

    # Retention: keep newest ``limit`` rows (by id / insert order).
    retain = max(1, int(limit))
    db.execute(
        """
        DELETE FROM runtime_access_timeline
        WHERE user_id=? AND provider=? AND id NOT IN (
            SELECT id FROM runtime_access_timeline
            WHERE user_id=? AND provider=?
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (str(user_id), provider_key, str(user_id), provider_key, retain),
    )
    if commit:
        db.commit()
    return stored


def list_access_timeline_events(
    db: Any,
    user_id: str,
    provider: str,
    *,
    limit: int = DEFAULT_TIMELINE_LIMIT,
    newest_first: bool = True,
) -> list[AccessTimelineEvent]:
    ensure_access_timeline_tables(db, commit=False)
    provider_key = str(provider).lower()
    cap = max(0, int(limit))
    if cap == 0:
        return []
    rows = db.execute(
        """
        SELECT event_id, event_type, message, observed_at, ok, details_json
        FROM runtime_access_timeline
        WHERE user_id=? AND provider=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(user_id), provider_key, cap),
    ).fetchall()
    events: list[AccessTimelineEvent] = []
    for row in rows:
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        events.append(
            AccessTimelineEvent(
                event_id=str(row["event_id"]),
                event_type=str(row["event_type"]),
                message=str(row["message"]),
                observed_at=str(row["observed_at"]),
                ok=bool(row["ok"]),
                details=details,
            )
        )
    if not newest_first:
        events = list(reversed(events))
    return events


def record_timeline_from_transition(
    db: Any,
    user_id: str,
    *,
    previous_payload: dict[str, Any] | None,
    current_payload: dict[str, Any],
    limit: int = DEFAULT_TIMELINE_LIMIT,
    commit: bool = True,
) -> list[AccessTimelineEvent]:
    """Derive + persist timeline events for an accepted AccessState upsert."""
    provider = str(current_payload.get("provider") or "amex").lower()
    events = derive_access_timeline_events(previous_payload, current_payload)
    return append_access_timeline_events(
        db,
        user_id,
        provider,
        events,
        limit=limit,
        commit=commit,
    )


@dataclass(frozen=True)
class ProviderOperationsDetails:
    """Expanded Provider Operations panel model (details on request)."""

    provider: str
    autonomous_uptime_label: str
    last_user_intervention_label: str
    last_user_intervention_at: str | None
    snapshot_freshness_label: str
    snapshot_updated_at: str | None
    verification_last_at: str | None
    verification_last_label: str
    verification_last_result: str | None
    keepalive_last_at: str | None
    keepalive_last_label: str
    keepalive_last_result: str | None
    recovery_attempts: int
    recovery_successes: int
    recovery_failures: int
    recovery_state: str
    last_recovery_action: str | None
    last_recovery_result: str | None
    timeline: tuple[AccessTimelineEvent, ...] = ()
    work_queue: tuple[Any, ...] = ()
    work_queue_evaluated_at: str | None = None
    orchestration_gaps: tuple[str, ...] = ()
    meets_goal: bool = True

    def to_dict(self) -> dict[str, Any]:
        queue_items = []
        for item in self.work_queue:
            if hasattr(item, "to_dict"):
                queue_items.append(item.to_dict())
            else:
                queue_items.append(dict(item))
        return {
            "provider": self.provider,
            "autonomous_uptime_label": self.autonomous_uptime_label,
            "last_user_intervention_label": self.last_user_intervention_label,
            "last_user_intervention_at": self.last_user_intervention_at,
            "snapshot_freshness_label": self.snapshot_freshness_label,
            "snapshot_updated_at": self.snapshot_updated_at,
            "verification_last_at": self.verification_last_at,
            "verification_last_label": self.verification_last_label,
            "verification_last_result": self.verification_last_result,
            "keepalive_last_at": self.keepalive_last_at,
            "keepalive_last_label": self.keepalive_last_label,
            "keepalive_last_result": self.keepalive_last_result,
            "recovery_attempts": self.recovery_attempts,
            "recovery_successes": self.recovery_successes,
            "recovery_failures": self.recovery_failures,
            "recovery_state": self.recovery_state,
            "last_recovery_action": self.last_recovery_action,
            "last_recovery_result": self.last_recovery_result,
            "timeline": [event.to_dict() for event in self.timeline],
            "work_queue": queue_items,
            "work_queue_evaluated_at": self.work_queue_evaluated_at,
            "orchestration_gaps": list(self.orchestration_gaps),
            "meets_goal": self.meets_goal,
        }


def _format_age_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem_m = minutes % 60
    if hours < 48:
        return f"{hours}h {rem_m}m" if rem_m else f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _age_label(ts: str | None, *, now: datetime) -> str:
    parsed = parse_iso(ts) if ts else None
    if parsed is None:
        return "never"
    age = max(0.0, (now - parsed).total_seconds())
    return f"{_format_age_seconds(age)} ago"


def _orchestration_for_payload(
    payload: dict[str, Any] | None,
    *,
    provider: str,
    now: datetime,
) -> tuple[tuple[Any, ...], str | None, tuple[str, ...], bool]:
    """Observational WorkQueue for the ops panel (does not execute actions)."""
    from mighty.provider_orchestrator import ProviderOrchestrator
    from mighty.provider_runtime_control_center import iso_now

    evaluation = ProviderOrchestrator().evaluate_provider(provider, payload, now=now)
    return (
        evaluation.work_items,
        iso_now(),
        evaluation.gaps,
        evaluation.meets_goal,
    )


def build_provider_operations_details(
    payload: dict[str, Any] | None,
    timeline: list[AccessTimelineEvent] | None = None,
    *,
    provider: str = "amex",
    now: datetime | None = None,
) -> ProviderOperationsDetails:
    """Build expanded ops metrics from latest AccessState + timeline history."""
    current = now or datetime.now(timezone.utc)
    events = tuple(timeline or ())
    work_items, evaluated_at, gaps, meets_goal = _orchestration_for_payload(
        payload, provider=provider, now=current
    )
    if payload is None:
        return ProviderOperationsDetails(
            provider=provider,
            autonomous_uptime_label="—",
            last_user_intervention_label="never",
            last_user_intervention_at=None,
            snapshot_freshness_label="never",
            snapshot_updated_at=None,
            verification_last_at=None,
            verification_last_label="never",
            verification_last_result=None,
            keepalive_last_at=None,
            keepalive_last_label="never",
            keepalive_last_result=None,
            recovery_attempts=0,
            recovery_successes=0,
            recovery_failures=0,
            recovery_state="—",
            last_recovery_action=None,
            last_recovery_result=None,
            timeline=events,
            work_queue=work_items,
            work_queue_evaluated_at=evaluated_at,
            orchestration_gaps=gaps,
            meets_goal=meets_goal,
        )

    auth = str(payload.get("authentication_state") or "")
    recovery = str(payload.get("recovery_state") or "")
    session_started = payload.get("session_started_at")
    started_ts = parse_iso(str(session_started)) if session_started else None
    autonomous = "—"
    if (
        auth == "SIGNED_IN"
        and recovery != RECOVERY_STATUS_AWAITING_USER
        and started_ts is not None
    ):
        autonomous = _format_age_seconds(
            max(0.0, (current - started_ts).total_seconds())
        )

    intervention_at: str | None = None
    for event in events:
        if event.event_type == EVENT_AWAITING_USER:
            intervention_at = event.observed_at
            break
    mid_run = int(payload.get("mid_run_user_intervention_count") or 0)
    if intervention_at:
        intervention_label = _age_label(intervention_at, now=current)
    elif mid_run > 0:
        intervention_label = f"{mid_run} recorded"
    else:
        intervention_label = "never"

    updated_at = str(payload.get("updated_at") or "") or None
    verified_at = payload.get("last_verified_at")
    keepalive_at = payload.get("last_keepalive_at")

    return ProviderOperationsDetails(
        provider=str(payload.get("provider") or provider),
        autonomous_uptime_label=autonomous,
        last_user_intervention_label=intervention_label,
        last_user_intervention_at=intervention_at,
        snapshot_freshness_label=_age_label(updated_at, now=current),
        snapshot_updated_at=updated_at,
        verification_last_at=verified_at,
        verification_last_label=_age_label(
            str(verified_at) if verified_at else None, now=current
        ),
        verification_last_result=payload.get("last_verification_result"),
        keepalive_last_at=keepalive_at,
        keepalive_last_label=_age_label(
            str(keepalive_at) if keepalive_at else None, now=current
        ),
        keepalive_last_result=payload.get("last_keepalive_result"),
        recovery_attempts=int(payload.get("recovery_attempts") or 0),
        recovery_successes=int(payload.get("recovery_successes") or 0),
        recovery_failures=int(payload.get("recovery_failures") or 0),
        recovery_state=recovery or "—",
        last_recovery_action=payload.get("last_recovery_action"),
        last_recovery_result=payload.get("last_recovery_result"),
        timeline=events,
        work_queue=work_items,
        work_queue_evaluated_at=evaluated_at,
        orchestration_gaps=gaps,
        meets_goal=meets_goal,
    )


def render_timeline_events_html(
    events: list[AccessTimelineEvent] | tuple[AccessTimelineEvent, ...],
    *,
    escape: Any,
) -> str:
    if not events:
        return '<p class="dash-access-ops-empty">No timeline events yet.</p>'
    items = []
    for event in events:
        ok_class = "ok" if event.ok else "fail"
        items.append(
            f'<li class="dash-access-timeline-item" data-ok="{ok_class}" '
            f'data-event-type="{escape(event.event_type)}">'
            f'<span class="dash-access-timeline-time">{escape(event.observed_at)}</span>'
            f'<span class="dash-access-timeline-type">{escape(event.event_type)}</span>'
            f'<span class="dash-access-timeline-msg">{escape(event.message)}</span>'
            f"</li>"
        )
    return (
        f'<ol class="dash-access-timeline" data-access-timeline="1">'
        f'{"".join(items)}'
        f"</ol>"
    )


def render_work_queue_html(
    work_queue: list[Any] | tuple[Any, ...],
    *,
    escape: Any,
    meets_goal: bool = True,
) -> str:
    """Render the observational WorkQueue (intended next actions)."""
    if not work_queue:
        label = (
            "Goal met — no intended work."
            if meets_goal
            else "No intended work."
        )
        return f'<p class="dash-access-ops-empty" data-work-queue-empty="1">{escape(label)}</p>'
    items = []
    for entry in work_queue:
        if hasattr(entry, "to_dict"):
            data = entry.to_dict()
        else:
            data = dict(entry)
        action = str(data.get("action") or "")
        reason = str(data.get("reason") or "")
        policy_trigger = str(
            data.get("policy_trigger")
            or data.get("policy_key")
            or ""
        )
        priority = data.get("priority")
        priority_label = str(priority) if priority is not None else "—"
        work_id = str(data.get("work_item_id") or "")
        items.append(
            f'<li class="dash-access-work-item" data-work-action="{escape(action)}"'
            f'{f" data-work-item-id=\"{escape(work_id)}\"" if work_id else ""}>'
            f'<span class="dash-access-work-priority">P{escape(priority_label)}</span>'
            f'<span class="dash-access-work-action">{escape(action)}</span>'
            f'<span class="dash-access-work-reason">{escape(reason)}</span>'
            f'<span class="dash-access-work-policy">{escape(policy_trigger)}</span>'
            f"</li>"
        )
    return (
        f'<ol class="dash-access-work-queue" data-work-queue="1">'
        f'{"".join(items)}'
        f"</ol>"
    )


def render_provider_operations_details_html(
    details: ProviderOperationsDetails,
    *,
    escape: Any,
    capabilities: Any | None = None,
) -> str:
    """Render the expandable Provider Operations details body.

    When ``capabilities`` is provided (ProviderPlatformCapabilities), only rows for
    supported capabilities are shown. When omitted, all historical rows render so
    existing Amex callers stay unchanged.
    """
    d = details
    caps = capabilities
    show_all = caps is None

    def _cap(name: str) -> bool:
        if show_all:
            return True
        return bool(getattr(caps, name, False))

    metric_rows: list[tuple[str, str]] = [
        ("Autonomous uptime", d.autonomous_uptime_label),
        ("Last user intervention", d.last_user_intervention_label),
    ]
    if _cap("snapshots"):
        metric_rows.append(("Snapshot freshness", d.snapshot_freshness_label))
    if _cap("verification"):
        metric_rows.append(
            (
                "Last verification",
                (
                    f"{d.verification_last_label}"
                    + (
                        f" ({d.verification_last_result})"
                        if d.verification_last_result
                        else ""
                    )
                ),
            )
        )
    if _cap("keepalive"):
        metric_rows.append(
            (
                "Last keepalive",
                (
                    f"{d.keepalive_last_label}"
                    + (
                        f" ({d.keepalive_last_result})"
                        if d.keepalive_last_result
                        else ""
                    )
                ),
            )
        )
    if _cap("recovery"):
        metric_rows.append(("Recovery state", d.recovery_state))
        metric_rows.append(
            (
                "Recovery metrics",
                (
                    f"{d.recovery_attempts} attempts · "
                    f"{d.recovery_successes} ok · {d.recovery_failures} fail"
                ),
            )
        )
        if d.last_recovery_action:
            metric_rows.append(
                (
                    "Last recovery",
                    f"{d.last_recovery_action}"
                    + (
                        f" — {d.last_recovery_result}"
                        if d.last_recovery_result
                        else ""
                    ),
                )
            )
    metrics = "".join(
        f'<div class="dash-access-ops-row">'
        f"<dt>{escape(label)}</dt>"
        f"<dd>{escape(value)}</dd>"
        f"</div>"
        for label, value in metric_rows
    )
    work_queue_html = render_work_queue_html(
        d.work_queue,
        escape=escape,
        meets_goal=d.meets_goal,
    )
    timeline_html = render_timeline_events_html(d.timeline, escape=escape)
    return (
        f'<div class="dash-access-ops" data-access-ops="1">'
        f'<p class="dash-truth-section-label">Provider Operations</p>'
        f'<dl class="dash-access-ops-grid">{metrics}</dl>'
        f'<p class="dash-truth-section-label">Intended work</p>'
        f"{work_queue_html}"
        f'<p class="dash-truth-section-label">Recent timeline</p>'
        f"{timeline_html}"
        f"</div>"
    )
