"""Railway persistence and presentation for published local AccessState.

Stores the latest AccessState per (user_id, provider, runtime_instance_id),
computes stale/offline presentation, and renders the dashboard access card.

This is intentionally separate from provider_session_state / login_truth —
those model extension-driven product session truth. This models Control Center
Access Supervisor health reported from the local Provider Runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.access_state_publication import SCHEMA_VERSION, assert_no_sensitive_fields
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_RECOVERING,
    parse_iso,
)

# Presentation statuses returned to the dashboard.
STATUS_HEALTHY = "healthy"
STATUS_RECOVERING = "recovering"
STATUS_AWAITING_USER = "awaiting_user"
STATUS_STALE = "stale"
STATUS_RUNTIME_OFFLINE = "runtime_offline"
STATUS_NEVER_REPORTED = "never_reported"

DEFAULT_STALE_AFTER_SECONDS = 180.0  # 3× default 60s heartbeat
PROVIDER_DISPLAY = {
    "amex": "American Express",
}


def ensure_runtime_access_state_tables(db: Any, *, commit: bool = True) -> bool:
    """Create runtime_access_state schema if missing."""
    existing_tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    mutated = "runtime_access_state" not in existing_tables
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_access_state (
            user_id              TEXT NOT NULL,
            provider             TEXT NOT NULL,
            runtime_instance_id  TEXT NOT NULL,
            schema_version       INTEGER NOT NULL,
            payload_json         TEXT NOT NULL,
            updated_at           TEXT NOT NULL,
            received_at          TEXT NOT NULL,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_runtime_access_user "
        "ON runtime_access_state(user_id)"
    )
    if commit and mutated:
        db.commit()
    return mutated


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_ingest_payload(data: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Validate and normalize an ingest body. Returns (payload, error)."""
    if not isinstance(data, dict):
        return None, "payload must be an object"
    try:
        schema_version = int(data.get("schema_version") or 0)
    except (TypeError, ValueError):
        return None, "invalid schema_version"
    if schema_version != SCHEMA_VERSION:
        return None, f"unsupported schema_version: {schema_version}"
    provider = str(data.get("provider") or "").strip().lower()
    if not provider:
        return None, "provider is required"
    runtime_instance_id = str(data.get("runtime_instance_id") or "").strip()
    if not runtime_instance_id:
        return None, "runtime_instance_id is required"
    updated_at = str(data.get("updated_at") or "").strip()
    if not updated_at or parse_iso(updated_at) is None:
        return None, "updated_at must be a valid ISO timestamp"
    required_strings = (
        "authentication_state",
        "access_health",
        "runtime_state",
        "browser_state",
        "recovery_state",
    )
    for key in required_strings:
        if data.get(key) in (None, ""):
            return None, f"{key} is required"
    try:
        assert_no_sensitive_fields(data)
    except ValueError as exc:
        return None, str(exc)

    payload = {
        "schema_version": schema_version,
        "provider": provider,
        "authentication_state": str(data["authentication_state"]),
        "access_health": str(data["access_health"]),
        "runtime_state": str(data["runtime_state"]),
        "browser_state": str(data["browser_state"]),
        "recovery_state": str(data["recovery_state"]),
        "recovery_attempts": int(data.get("recovery_attempts") or 0),
        "recovery_successes": int(data.get("recovery_successes") or 0),
        "recovery_failures": int(data.get("recovery_failures") or 0),
        "last_recovery_action": data.get("last_recovery_action"),
        "last_recovery_result": data.get("last_recovery_result"),
        "escalation_reason": data.get("escalation_reason"),
        "session_started_at": data.get("session_started_at"),
        "last_verified_at": data.get("last_verified_at"),
        "last_keepalive_at": data.get("last_keepalive_at"),
        "ready_for_extraction": bool(data.get("ready_for_extraction")),
        "ready_for_connector": bool(data.get("ready_for_connector")),
        "initial_authentication_prompt_count": int(
            data.get("initial_authentication_prompt_count") or 0
        ),
        "mid_run_user_intervention_count": int(
            data.get("mid_run_user_intervention_count") or 0
        ),
        "updated_at": updated_at,
        "runtime_instance_id": runtime_instance_id,
        "published_at": str(data.get("published_at") or updated_at),
    }
    return payload, None


def get_runtime_access_state(
    db: Any,
    user_id: str,
    provider: str,
) -> dict[str, Any] | None:
    ensure_runtime_access_state_tables(db, commit=False)
    row = db.execute(
        """
        SELECT user_id, provider, runtime_instance_id, schema_version,
               payload_json, updated_at, received_at
        FROM runtime_access_state
        WHERE user_id=? AND provider=?
        """,
        (str(user_id), str(provider).lower()),
    ).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["payload_json"])
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "user_id": row["user_id"],
        "provider": row["provider"],
        "runtime_instance_id": row["runtime_instance_id"],
        "schema_version": row["schema_version"],
        "payload": payload,
        "updated_at": row["updated_at"],
        "received_at": row["received_at"],
    }


def upsert_runtime_access_state(
    db: Any,
    user_id: str,
    payload: dict[str, Any],
    *,
    received_at: str | None = None,
    commit: bool = True,
) -> dict[str, Any]:
    """Replace latest state when newer; reject out-of-order updates safely."""
    ensure_runtime_access_state_tables(db, commit=False)
    provider = str(payload["provider"]).lower()
    updated_at = str(payload["updated_at"])
    incoming_ts = parse_iso(updated_at)
    received = received_at or _iso_now()
    existing = get_runtime_access_state(db, user_id, provider)
    if existing is not None:
        existing_ts = parse_iso(str(existing.get("updated_at") or ""))
        if incoming_ts is not None and existing_ts is not None and incoming_ts < existing_ts:
            return {
                "ok": True,
                "accepted": False,
                "reason": "out_of_order",
                "provider": provider,
                "updated_at": existing.get("updated_at"),
                "stored_updated_at": existing.get("updated_at"),
            }

    db.execute(
        """
        INSERT INTO runtime_access_state (
            user_id, provider, runtime_instance_id, schema_version,
            payload_json, updated_at, received_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            runtime_instance_id=excluded.runtime_instance_id,
            schema_version=excluded.schema_version,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at,
            received_at=excluded.received_at
        """,
        (
            str(user_id),
            provider,
            str(payload["runtime_instance_id"]),
            int(payload["schema_version"]),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            updated_at,
            received,
        ),
    )
    if commit:
        db.commit()
    return {
        "ok": True,
        "accepted": True,
        "reason": "replaced" if existing is not None else "created",
        "provider": provider,
        "updated_at": updated_at,
        "runtime_instance_id": payload["runtime_instance_id"],
    }


@dataclass(frozen=True)
class RuntimeAccessPresentation:
    provider: str
    display_name: str
    status: str
    status_label: str
    authentication_state: str
    access_health: str
    recovery_state: str
    recovery_attempts: int
    recovery_successes: int
    recovery_failures: int
    ready_for_extraction: bool
    ready_for_connector: bool
    user_action_required: bool
    last_verified_at: str | None
    session_started_at: str | None
    session_age_label: str
    last_update_at: str | None
    last_update_label: str
    stale: bool
    runtime_instance_id: str | None
    escalation_reason: str | None
    headline: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "display_name": self.display_name,
            "status": self.status,
            "status_label": self.status_label,
            "authentication_state": self.authentication_state,
            "access_health": self.access_health,
            "recovery_state": self.recovery_state,
            "recovery_attempts": self.recovery_attempts,
            "recovery_successes": self.recovery_successes,
            "recovery_failures": self.recovery_failures,
            "ready_for_extraction": self.ready_for_extraction,
            "ready_for_connector": self.ready_for_connector,
            "user_action_required": self.user_action_required,
            "last_verified_at": self.last_verified_at,
            "session_started_at": self.session_started_at,
            "session_age_label": self.session_age_label,
            "last_update_at": self.last_update_at,
            "last_update_label": self.last_update_label,
            "stale": self.stale,
            "runtime_instance_id": self.runtime_instance_id,
            "escalation_reason": self.escalation_reason,
            "headline": self.headline,
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


def compute_presentation_status(
    payload: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> str:
    """Derive dashboard status; never treat an old green state as currently healthy."""
    if payload is None:
        return STATUS_NEVER_REPORTED
    current = now or datetime.now(timezone.utc)
    updated = parse_iso(str(payload.get("updated_at") or payload.get("published_at") or ""))
    if updated is None:
        return STATUS_STALE
    age = (current - updated).total_seconds()
    if age > float(stale_after_seconds):
        # Distinguish quiet offline from merely stale when runtime_state says stopped.
        runtime_state = str(payload.get("runtime_state") or "").lower()
        if runtime_state in {"stopped", "unhealthy"} or age > float(stale_after_seconds) * 2:
            return STATUS_RUNTIME_OFFLINE
        return STATUS_STALE

    recovery = str(payload.get("recovery_state") or "").lower()
    if recovery == RECOVERY_STATUS_AWAITING_USER:
        return STATUS_AWAITING_USER
    if recovery == RECOVERY_STATUS_RECOVERING or str(payload.get("access_health") or "") == "recovering":
        return STATUS_RECOVERING
    if str(payload.get("access_health") or "") == ACCESS_HEALTH_HEALTHY and str(
        payload.get("authentication_state") or ""
    ) == "SIGNED_IN":
        return STATUS_HEALTHY
    if recovery in {"failed", "planning"}:
        return STATUS_RECOVERING
    # Degraded / unavailable but fresh → recovering-ish attention, not healthy.
    return STATUS_RECOVERING


STATUS_LABELS = {
    STATUS_HEALTHY: "Healthy",
    STATUS_RECOVERING: "Recovering",
    STATUS_AWAITING_USER: "User action required",
    STATUS_STALE: "Stale",
    STATUS_RUNTIME_OFFLINE: "Runtime offline",
    STATUS_NEVER_REPORTED: "No state received yet",
}

STATUS_HEADLINES = {
    STATUS_HEALTHY: "Local Amex access is healthy.",
    STATUS_RECOVERING: "Local Amex access is recovering.",
    STATUS_AWAITING_USER: "Local Amex access needs your attention.",
    STATUS_STALE: "Local Amex access report is stale.",
    STATUS_RUNTIME_OFFLINE: "Local Provider Runtime appears offline.",
    STATUS_NEVER_REPORTED: "No local AccessState has been reported yet.",
}


def build_runtime_access_presentation(
    row: dict[str, Any] | None,
    *,
    provider: str = "amex",
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> RuntimeAccessPresentation:
    current = now or datetime.now(timezone.utc)
    display = PROVIDER_DISPLAY.get(provider, provider.title())
    if row is None:
        status = STATUS_NEVER_REPORTED
        return RuntimeAccessPresentation(
            provider=provider,
            display_name=display,
            status=status,
            status_label=STATUS_LABELS[status],
            authentication_state="—",
            access_health="—",
            recovery_state="—",
            recovery_attempts=0,
            recovery_successes=0,
            recovery_failures=0,
            ready_for_extraction=False,
            ready_for_connector=False,
            user_action_required=False,
            last_verified_at=None,
            session_started_at=None,
            session_age_label="—",
            last_update_at=None,
            last_update_label="never",
            stale=True,
            runtime_instance_id=None,
            escalation_reason=None,
            headline=STATUS_HEADLINES[status],
        )

    payload = dict(row.get("payload") or {})
    status = compute_presentation_status(
        payload,
        now=current,
        stale_after_seconds=stale_after_seconds,
    )
    session_started = payload.get("session_started_at")
    started_ts = parse_iso(str(session_started)) if session_started else None
    session_age = (
        max(0.0, (current - started_ts).total_seconds()) if started_ts is not None else None
    )
    last_update = str(payload.get("updated_at") or row.get("updated_at") or "") or None
    user_action = status == STATUS_AWAITING_USER
    return RuntimeAccessPresentation(
        provider=str(payload.get("provider") or provider),
        display_name=display,
        status=status,
        status_label=STATUS_LABELS.get(status, status),
        authentication_state=str(payload.get("authentication_state") or "—"),
        access_health=str(payload.get("access_health") or "—"),
        recovery_state=str(payload.get("recovery_state") or "—"),
        recovery_attempts=int(payload.get("recovery_attempts") or 0),
        recovery_successes=int(payload.get("recovery_successes") or 0),
        recovery_failures=int(payload.get("recovery_failures") or 0),
        ready_for_extraction=bool(payload.get("ready_for_extraction")),
        ready_for_connector=bool(payload.get("ready_for_connector")),
        user_action_required=user_action,
        last_verified_at=payload.get("last_verified_at"),
        session_started_at=session_started,
        session_age_label=_format_age_seconds(session_age) if session_age is not None else "—",
        last_update_at=last_update,
        last_update_label=_age_label(last_update, now=current),
        stale=status in {STATUS_STALE, STATUS_RUNTIME_OFFLINE, STATUS_NEVER_REPORTED},
        runtime_instance_id=payload.get("runtime_instance_id") or row.get("runtime_instance_id"),
        escalation_reason=payload.get("escalation_reason"),
        headline=STATUS_HEADLINES.get(status, STATUS_HEADLINES[STATUS_STALE]),
    )


def render_runtime_access_card(
    presentation: RuntimeAccessPresentation,
    *,
    escape: Callable[[Any], str],
) -> str:
    """Render Amex Access card using Truth Dashboard visual language."""
    p = presentation
    yes_no = lambda v: "yes" if v else "no"
    rows = [
        ("Access status", p.status_label),
        ("Authentication", p.authentication_state),
        ("Last verified", _age_label(p.last_verified_at, now=datetime.now(timezone.utc))
         if p.last_verified_at
         else "never"),
        ("Session age", p.session_age_label),
        ("Recovery state", p.recovery_state),
        (
            "Recovery counts",
            f"{p.recovery_attempts} attempts · {p.recovery_successes} ok · {p.recovery_failures} fail",
        ),
        ("Ready for extraction", yes_no(p.ready_for_extraction)),
        ("Ready for connector", yes_no(p.ready_for_connector)),
        ("User action required", yes_no(p.user_action_required)),
        ("Last update", p.last_update_label),
    ]
    if p.escalation_reason:
        rows.append(("Escalation", str(p.escalation_reason)))

    dl = "".join(
        f'<div class="dash-access-row">'
        f'<dt>{escape(label)}</dt>'
        f"<dd>{escape(value)}</dd>"
        f"</div>"
        for label, value in rows
    )
    return (
        f'<section class="dash-access-card" '
        f'data-runtime-access="1" '
        f'data-provider="{escape(p.provider)}" '
        f'data-access-status="{escape(p.status)}" '
        f'aria-label="American Express local access">'
        f'<p class="dash-truth-section-label">Local Provider Runtime</p>'
        f'<h2 class="dash-access-title">{escape(p.display_name)} access</h2>'
        f'<p class="dash-access-headline" data-access-headline="1">{escape(p.headline)}</p>'
        f'<p class="dash-access-badge" data-access-badge="1">{escape(p.status_label)}</p>'
        f'<dl class="dash-access-grid">{dl}</dl>'
        f"</section>"
    )


def load_runtime_access_presentation(
    db: Any,
    user_id: str,
    provider: str = "amex",
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> RuntimeAccessPresentation:
    row = get_runtime_access_state(db, user_id, provider)
    return build_runtime_access_presentation(
        row,
        provider=provider,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
