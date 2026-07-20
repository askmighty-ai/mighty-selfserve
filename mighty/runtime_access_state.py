"""Railway persistence and presentation for published local AccessState.

Stores the latest AccessState per (user_id, provider, runtime_instance_id),
computes stale/offline presentation, and renders dashboard provider cards.

Provider discovery and display names come from the Provider Registry. AccessState
and timeline contracts are unchanged — this module only presents published state.

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
from mighty.access_timeline import (
    build_provider_operations_details,
    ensure_access_timeline_tables,
    list_access_timeline_events,
    record_timeline_from_transition,
    render_provider_operations_details_html,
)
from mighty.provider_registry import (
    ManagedProvider,
    ProviderPlatformCapabilities,
    get_provider_registry,
)
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

# Backward-compatible display map; prefer Provider Registry for new code.
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
    timeline_mutated = ensure_access_timeline_tables(db, commit=False)
    mutated = mutated or timeline_mutated
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
        "runtime_started_at": data.get("runtime_started_at"),
        "authenticated_session_started_at": data.get("authenticated_session_started_at"),
        "autonomous_since_at": data.get("autonomous_since_at"),
        "authentication_state_changed_at": data.get("authentication_state_changed_at"),
        "last_user_intervention_at": data.get("last_user_intervention_at"),
        "last_verified_at": data.get("last_verified_at"),
        "last_verification_result": data.get("last_verification_result"),
        "last_keepalive_at": data.get("last_keepalive_at"),
        "last_keepalive_result": data.get("last_keepalive_result"),
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

    previous_payload = dict(existing.get("payload") or {}) if existing else None
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
    timeline_events = record_timeline_from_transition(
        db,
        user_id,
        previous_payload=previous_payload,
        current_payload=payload,
        commit=False,
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
        "timeline_events_recorded": len(timeline_events),
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
    runtime_started_at: str | None
    runtime_uptime_label: str
    authenticated_session_started_at: str | None
    authenticated_session_age_label: str
    autonomous_since_at: str | None
    autonomous_duration_label: str
    authentication_state_changed_at: str | None
    authentication_state_age_label: str
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
            "runtime_started_at": self.runtime_started_at,
            "runtime_uptime_label": self.runtime_uptime_label,
            "authenticated_session_started_at": self.authenticated_session_started_at,
            "authenticated_session_age_label": self.authenticated_session_age_label,
            "autonomous_since_at": self.autonomous_since_at,
            "autonomous_duration_label": self.autonomous_duration_label,
            "authentication_state_changed_at": self.authentication_state_changed_at,
            "authentication_state_age_label": self.authentication_state_age_label,
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


def _provider_display_name(provider: str) -> str:
    registry = get_provider_registry()
    registered = registry.get(provider)
    if registered is not None:
        return registered.display_name
    return PROVIDER_DISPLAY.get(provider, provider.title() if provider else "Provider")


def status_headline_for(status: str, display_name: str) -> str:
    """Provider-generic status headline (no hard-coded Amex copy)."""
    name = display_name or "Provider"
    if status == STATUS_HEALTHY:
        return f"Local {name} access is healthy."
    if status == STATUS_RECOVERING:
        return f"Local {name} access is recovering."
    if status == STATUS_AWAITING_USER:
        return f"Local {name} access needs your attention."
    if status == STATUS_STALE:
        return f"Local {name} access report is stale."
    if status == STATUS_RUNTIME_OFFLINE:
        return "Local Provider Runtime appears offline."
    if status == STATUS_NEVER_REPORTED:
        return "No local AccessState has been reported yet."
    return f"Local {name} access report is stale."


# Backward-compatible Amex-oriented headlines (derived from registry display name).
STATUS_HEADLINES = {
    STATUS_HEALTHY: status_headline_for(STATUS_HEALTHY, "American Express"),
    STATUS_RECOVERING: status_headline_for(STATUS_RECOVERING, "American Express"),
    STATUS_AWAITING_USER: status_headline_for(STATUS_AWAITING_USER, "American Express"),
    STATUS_STALE: status_headline_for(STATUS_STALE, "American Express"),
    STATUS_RUNTIME_OFFLINE: status_headline_for(STATUS_RUNTIME_OFFLINE, "American Express"),
    STATUS_NEVER_REPORTED: status_headline_for(STATUS_NEVER_REPORTED, "American Express"),
}


def _duration_label_from_ts(ts: Any, *, now: datetime) -> str:
    parsed = parse_iso(str(ts)) if ts else None
    if parsed is None:
        return "—"
    return _format_age_seconds(max(0.0, (now - parsed).total_seconds()))


def build_runtime_access_presentation(
    row: dict[str, Any] | None,
    *,
    provider: str = "amex",
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> RuntimeAccessPresentation:
    current = now or datetime.now(timezone.utc)
    display = _provider_display_name(provider)
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
            runtime_started_at=None,
            runtime_uptime_label="—",
            authenticated_session_started_at=None,
            authenticated_session_age_label="—",
            autonomous_since_at=None,
            autonomous_duration_label="—",
            authentication_state_changed_at=None,
            authentication_state_age_label="—",
            last_update_at=None,
            last_update_label="never",
            stale=True,
            runtime_instance_id=None,
            escalation_reason=None,
            headline=status_headline_for(status, display),
        )

    payload = dict(row.get("payload") or {})
    status = compute_presentation_status(
        payload,
        now=current,
        stale_after_seconds=stale_after_seconds,
    )
    runtime_started = payload.get("runtime_started_at")
    auth_session_started = payload.get("authenticated_session_started_at")
    autonomous_since = payload.get("autonomous_since_at")
    auth_state_changed = payload.get("authentication_state_changed_at")
    auth_state = str(payload.get("authentication_state") or "")
    # Auth session age is only meaningful while currently SIGNED_IN.
    auth_session_age_label = (
        _duration_label_from_ts(auth_session_started, now=current)
        if auth_state == "SIGNED_IN"
        else "—"
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
        runtime_started_at=runtime_started,
        runtime_uptime_label=_duration_label_from_ts(runtime_started, now=current),
        authenticated_session_started_at=auth_session_started,
        authenticated_session_age_label=auth_session_age_label,
        autonomous_since_at=autonomous_since,
        autonomous_duration_label=_duration_label_from_ts(autonomous_since, now=current),
        authentication_state_changed_at=auth_state_changed,
        authentication_state_age_label=_duration_label_from_ts(auth_state_changed, now=current),
        last_update_at=last_update,
        last_update_label=_age_label(last_update, now=current),
        stale=status in {STATUS_STALE, STATUS_RUNTIME_OFFLINE, STATUS_NEVER_REPORTED},
        runtime_instance_id=payload.get("runtime_instance_id") or row.get("runtime_instance_id"),
        escalation_reason=payload.get("escalation_reason"),
        headline=status_headline_for(status, display),
    )


def load_provider_operations_details(
    db: Any,
    user_id: str,
    provider: str = "amex",
    *,
    now: datetime | None = None,
    timeline_limit: int = 20,
) -> Any:
    """Load expanded Provider Operations details for a managed provider card."""
    row = get_runtime_access_state(db, user_id, provider)
    payload = dict(row.get("payload") or {}) if row else None
    timeline = list_access_timeline_events(
        db,
        user_id,
        provider,
        limit=timeline_limit,
        newest_first=True,
    )
    return build_provider_operations_details(
        payload,
        timeline,
        provider=provider,
        now=now,
    )


def _capabilities_for_provider(provider: str) -> ProviderPlatformCapabilities:
    return get_provider_registry().capabilities_for(provider)


def _compact_access_rows(
    presentation: RuntimeAccessPresentation,
    capabilities: ProviderPlatformCapabilities,
    *,
    now: datetime,
) -> list[tuple[str, str]]:
    """Build compact card rows, omitting unsupported capability fields."""
    p = presentation
    yes_no = lambda v: "yes" if v else "no"
    rows: list[tuple[str, str]] = [
        ("Access status", p.status_label),
        ("Authentication", p.authentication_state),
    ]
    if capabilities.verification:
        rows.append(
            (
                "Last verified",
                _age_label(p.last_verified_at, now=now) if p.last_verified_at else "never",
            )
        )
    rows.append(("Runtime uptime", p.runtime_uptime_label))
    rows.append(("Autonomous duration", p.autonomous_duration_label))
    rows.append(("Auth session age", p.authenticated_session_age_label))
    if capabilities.recovery:
        rows.append(("Recovery state", p.recovery_state))
        rows.append(
            (
                "Recovery counts",
                (
                    f"{p.recovery_attempts} attempts · "
                    f"{p.recovery_successes} ok · {p.recovery_failures} fail"
                ),
            )
        )
    if capabilities.connector_readiness:
        rows.append(("Ready for extraction", yes_no(p.ready_for_extraction)))
        rows.append(("Ready for connector", yes_no(p.ready_for_connector)))
    rows.append(("User action required", yes_no(p.user_action_required)))
    rows.append(("Last update", p.last_update_label))
    if p.escalation_reason:
        rows.append(("Escalation", str(p.escalation_reason)))
    return rows


def render_runtime_access_card(
    presentation: RuntimeAccessPresentation,
    *,
    escape: Callable[[Any], str],
    operations: Any | None = None,
    capabilities: ProviderPlatformCapabilities | None = None,
) -> str:
    """Render one managed-provider Access card using Truth Dashboard visual language.

    Compact by default; Provider Operations details expand via View details.
    Capability flags control which rows appear (no hard-coded Amex assumptions).
    """
    p = presentation
    caps = capabilities if capabilities is not None else _capabilities_for_provider(p.provider)
    now = datetime.now(timezone.utc)
    rows = _compact_access_rows(p, caps, now=now)
    dl = "".join(
        f'<div class="dash-access-row">'
        f'<dt>{escape(label)}</dt>'
        f"<dd>{escape(value)}</dd>"
        f"</div>"
        for label, value in rows
    )
    if operations is None:
        operations = build_provider_operations_details(
            None,
            [],
            provider=p.provider,
        )
    ops_html = render_provider_operations_details_html(
        operations,
        escape=escape,
        capabilities=caps,
    )
    details = (
        f'<details class="dash-access-details" data-access-details="1">'
        f'<summary class="dash-access-details-summary">View details</summary>'
        f"{ops_html}"
        f"</details>"
    )
    caps_attr = ",".join(caps.enabled_names())
    return (
        f'<section class="dash-access-card" '
        f'data-runtime-access="1" '
        f'data-provider="{escape(p.provider)}" '
        f'data-access-status="{escape(p.status)}" '
        f'data-provider-capabilities="{escape(caps_attr)}" '
        f'aria-label="{escape(p.display_name)} local access">'
        f'<p class="dash-truth-section-label">Local Provider Runtime</p>'
        f'<h2 class="dash-access-title">{escape(p.display_name)} access</h2>'
        f'<p class="dash-access-headline" data-access-headline="1">{escape(p.headline)}</p>'
        f'<p class="dash-access-badge" data-access-badge="1">{escape(p.status_label)}</p>'
        f'<dl class="dash-access-grid">{dl}</dl>'
        f"{details}"
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


def load_runtime_access_card_model(
    db: Any,
    user_id: str,
    provider: str = "amex",
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> tuple[RuntimeAccessPresentation, Any]:
    """Load compact presentation + expanded operations details together."""
    presentation = load_runtime_access_presentation(
        db,
        user_id,
        provider,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    operations = load_provider_operations_details(
        db,
        user_id,
        provider,
        now=now,
    )
    return presentation, operations


@dataclass(frozen=True)
class RuntimeAccessProviderCard:
    """One registry-backed provider card: managed provider + presentation + ops."""

    managed: ManagedProvider
    presentation: RuntimeAccessPresentation
    operations: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.managed.to_dict(),
            "access": self.presentation.to_dict(),
            "operations": self.operations.to_dict(),
            "capabilities": self.managed.capabilities.to_dict(),
        }


def load_runtime_access_provider_cards(
    db: Any,
    user_id: str,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    providers: tuple[ManagedProvider, ...] | None = None,
) -> list[RuntimeAccessProviderCard]:
    """Load one access card model per registered (or supplied) managed provider."""
    managed_list = providers if providers is not None else get_provider_registry().list_providers()
    cards: list[RuntimeAccessProviderCard] = []
    for managed in managed_list:
        presentation, operations = load_runtime_access_card_model(
            db,
            user_id,
            managed.provider_id,
            now=now,
            stale_after_seconds=stale_after_seconds,
        )
        cards.append(
            RuntimeAccessProviderCard(
                managed=managed,
                presentation=presentation,
                operations=operations,
            )
        )
    return cards


def render_runtime_access_provider_list(
    cards: list[RuntimeAccessProviderCard] | tuple[RuntimeAccessProviderCard, ...],
    *,
    escape: Callable[[Any], str],
) -> str:
    """Render the Provider Manager list — one Access card per managed provider."""
    if not cards:
        return (
            '<section class="dash-access-provider-list" data-provider-manager="1" '
            'aria-label="Managed providers">'
            '<p class="dash-access-empty">No managed providers are registered.</p>'
            "</section>"
        )
    body = "".join(
        render_runtime_access_card(
            card.presentation,
            escape=escape,
            operations=card.operations,
            capabilities=card.managed.capabilities,
        )
        for card in cards
    )
    return (
        f'<section class="dash-access-provider-list" data-provider-manager="1" '
        f'aria-label="Managed providers">'
        f"{body}"
        f"</section>"
    )


def load_and_render_runtime_access_provider_list(
    db: Any,
    user_id: str,
    *,
    escape: Callable[[Any], str],
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> str:
    """Convenience: load registry providers and render the dashboard list."""
    cards = load_runtime_access_provider_cards(
        db,
        user_id,
        now=now,
        stale_after_seconds=stale_after_seconds,
    )
    return render_runtime_access_provider_list(cards, escape=escape)
