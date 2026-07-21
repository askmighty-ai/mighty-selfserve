"""Minimal Runtime AccessState store — Attention / AuthTruth read path (M5).

Owns the ``runtime_access_state`` table used by managed_runtime AuthTruth
projection and the Trust attention producer. Does **not** restore the full
Provider Runtime Control Center.

See docs/ATTENTION_COMPILER_TRUST.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = 2
DEFAULT_STALE_AFTER_SECONDS = 15 * 60

STATUS_HEALTHY = "healthy"
STATUS_RECOVERING = "recovering"
STATUS_AWAITING_USER = "awaiting_user"
STATUS_STALE = "stale"
STATUS_RUNTIME_OFFLINE = "runtime_offline"
STATUS_NEVER_REPORTED = "never_reported"

ACCESS_HEALTH_HEALTHY = "healthy"
RECOVERY_STATUS_AWAITING_USER = "awaiting_user"
RECOVERY_STATUS_RECOVERING = "recovering"


def ensure_runtime_access_state_tables(db: Any, *, commit: bool = True) -> None:
    """Create runtime_access_state schema if missing."""
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
    if commit:
        db.commit()


def get_runtime_access_state(
    db: Any,
    user_id: str,
    provider: str,
) -> dict[str, Any] | None:
    """Return latest stored Runtime publication for (user, provider)."""
    ensure_runtime_access_state_tables(db, commit=False)
    uid = str(user_id or "").strip()
    prov = str(provider or "").strip().lower()
    if not uid or not prov:
        return None
    row = db.execute(
        """
        SELECT user_id, provider, runtime_instance_id, schema_version,
               payload_json, updated_at, received_at
        FROM runtime_access_state
        WHERE user_id=? AND provider=?
        """,
        (uid, prov),
    ).fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        mapping = dict(row)
    else:
        try:
            mapping = dict(row)
        except Exception:
            mapping = {
                "user_id": row[0],
                "provider": row[1],
                "runtime_instance_id": row[2],
                "schema_version": row[3],
                "payload_json": row[4],
                "updated_at": row[5],
                "received_at": row[6],
            }
    try:
        payload = json.loads(mapping.get("payload_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "user_id": mapping.get("user_id"),
        "provider": mapping.get("provider"),
        "runtime_instance_id": mapping.get("runtime_instance_id"),
        "schema_version": mapping.get("schema_version"),
        "payload": payload,
        "updated_at": mapping.get("updated_at"),
        "received_at": mapping.get("received_at"),
    }


def upsert_runtime_access_state(
    db: Any,
    user_id: str,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> str:
    """Insert or replace latest state for tests / thin publishers.

    Returns ``created`` or ``replaced``.
    """
    ensure_runtime_access_state_tables(db, commit=False)
    uid = str(user_id or "").strip()
    provider = str(payload.get("provider") or "").strip().lower()
    if not uid or not provider:
        raise ValueError("user_id and payload.provider are required")
    instance_id = str(payload.get("runtime_instance_id") or "runtime").strip()
    updated_at = str(payload.get("updated_at") or _iso_now())
    received_at = _iso_now()
    existing = get_runtime_access_state(db, uid, provider)
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
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
            uid,
            provider,
            instance_id,
            int(payload.get("schema_version") or SCHEMA_VERSION),
            body,
            updated_at,
            received_at,
        ),
    )
    if commit:
        db.commit()
    return "replaced" if existing else "created"


def compute_presentation_status(
    row: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> str:
    """Derive product presentation status from a stored Runtime row."""
    if row is None:
        return STATUS_NEVER_REPORTED
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    payload = dict(row.get("payload") or {})
    stamp = (
        payload.get("updated_at")
        or payload.get("published_at")
        or row.get("updated_at")
        or ""
    )
    age = _age_seconds(str(stamp), now=now)
    if age is not None and age > float(stale_after_seconds):
        return STATUS_STALE

    runtime_state = str(payload.get("runtime_state") or "").strip().lower()
    if runtime_state in {"offline", "stopped", "dead"}:
        return STATUS_RUNTIME_OFFLINE

    recovery = str(payload.get("recovery_state") or "").strip().lower()
    if recovery == RECOVERY_STATUS_AWAITING_USER:
        return STATUS_AWAITING_USER
    if recovery == RECOVERY_STATUS_RECOVERING or runtime_state == "recovering":
        return STATUS_RECOVERING

    access_health = str(payload.get("access_health") or "").strip().lower()
    auth_state = str(payload.get("authentication_state") or "").strip().upper()
    if access_health == ACCESS_HEALTH_HEALTHY and auth_state == "SIGNED_IN":
        return STATUS_HEALTHY
    if not auth_state and not access_health and not recovery:
        return STATUS_NEVER_REPORTED
    if access_health and access_health != ACCESS_HEALTH_HEALTHY:
        return STATUS_RUNTIME_OFFLINE
    return STATUS_RECOVERING


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _age_seconds(value: str, *, now: datetime) -> float | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds()
