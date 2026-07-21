"""AttentionStore — overlay persistence + commands (PR 2E).

Owns snooze / dismiss / in_flight / clear writes. Does not create AttentionItems,
rank, deliver, or call Access Manager / Runtime.

See docs/ATTENTION_STORE.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from mighty.attention import AttentionClass, AttentionItem
from mighty.attention_overlay import (
    IN_FLIGHT_TIMEOUT_SECONDS,
    AttentionOverlay,
    OverlayStatus,
)

# RFC §5.2 — blockers may snooze at most 1h.
MAX_SNOOZE = timedelta(hours=1)


class AttentionStoreCommandError(ValueError):
    """Raised when an overlay command violates store policy."""


def build_snooze_overlay(
    item: AttentionItem,
    *,
    now: datetime,
    duration: timedelta,
) -> AttentionOverlay:
    """Build a snoozed overlay. Max duration 1 hour (RFC §5.2)."""
    _require_item(item)
    now = _ensure_aware(now)
    if duration <= timedelta(0):
        raise AttentionStoreCommandError("snooze duration must be positive")
    if duration > MAX_SNOOZE:
        raise AttentionStoreCommandError(
            f"snooze duration must be <= {int(MAX_SNOOZE.total_seconds())}s"
        )
    until = now + duration
    stamp = _iso(now)
    return AttentionOverlay(
        attention_id=item.attention_id,
        status=OverlayStatus.SNOOZED,
        until=_iso(until),
        started_at=None,
        updated_at=stamp,
    )


def build_dismiss_overlay(
    item: AttentionItem,
    *,
    now: datetime,
) -> AttentionOverlay:
    """Build a durable dismiss. Opportunity class only (RFC §5.2)."""
    _require_item(item)
    now = _ensure_aware(now)
    if item.attention_class is not AttentionClass.OPPORTUNITY:
        raise AttentionStoreCommandError(
            "durable dismiss is allowed only for opportunity attention"
        )
    stamp = _iso(now)
    return AttentionOverlay(
        attention_id=item.attention_id,
        status=OverlayStatus.DURABLE_DISMISSED,
        until=None,
        started_at=None,
        updated_at=stamp,
    )


def build_in_flight_overlay(
    item: AttentionItem,
    *,
    now: datetime,
) -> AttentionOverlay:
    """Build an in_flight overlay (CTA started). No adapter side effects here."""
    _require_item(item)
    now = _ensure_aware(now)
    stamp = _iso(now)
    return AttentionOverlay(
        attention_id=item.attention_id,
        status=OverlayStatus.IN_FLIGHT,
        until=None,
        started_at=stamp,
        updated_at=stamp,
    )


def ensure_attention_overlay_tables(db: Any, *, commit: bool = True) -> None:
    """Create the AttentionStore overlay table."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attention_overlay (
            user_id      TEXT NOT NULL,
            attention_id TEXT NOT NULL,
            status       TEXT NOT NULL,
            until_at     TEXT,
            started_at   TEXT,
            updated_at   TEXT NOT NULL,
            overlay_json TEXT NOT NULL,
            PRIMARY KEY (user_id, attention_id)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_attention_overlay_user "
        "ON attention_overlay(user_id)"
    )
    if commit:
        db.commit()


def upsert_attention_overlay(
    db: Any,
    user_id: str,
    overlay: AttentionOverlay,
    *,
    commit: bool = True,
) -> None:
    """Persist an overlay for a user. Does not validate command policy."""
    user_id = _require_user_id(user_id)
    if not isinstance(overlay, AttentionOverlay):
        raise AttentionStoreCommandError("overlay must be an AttentionOverlay")
    ensure_attention_overlay_tables(db, commit=False)
    payload = json.dumps(overlay.to_dict(), separators=(",", ":"), sort_keys=True)
    db.execute(
        """
        INSERT INTO attention_overlay (
            user_id, attention_id, status, until_at, started_at, updated_at, overlay_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, attention_id) DO UPDATE SET
            status=excluded.status,
            until_at=excluded.until_at,
            started_at=excluded.started_at,
            updated_at=excluded.updated_at,
            overlay_json=excluded.overlay_json
        """,
        (
            user_id,
            overlay.attention_id,
            overlay.status.value,
            overlay.until,
            overlay.started_at,
            overlay.updated_at,
            payload,
        ),
    )
    if commit:
        db.commit()


def get_attention_overlay(
    db: Any,
    user_id: str,
    attention_id: str,
) -> AttentionOverlay | None:
    user_id = _require_user_id(user_id)
    attention_id = str(attention_id or "").strip()
    if not attention_id:
        raise AttentionStoreCommandError("attention_id must be a non-empty string")
    ensure_attention_overlay_tables(db, commit=False)
    row = db.execute(
        """
        SELECT overlay_json FROM attention_overlay
        WHERE user_id = ? AND attention_id = ?
        """,
        (user_id, attention_id),
    ).fetchone()
    if not row:
        return None
    return AttentionOverlay.from_dict(json.loads(row[0]))


def list_attention_overlays(db: Any, user_id: str) -> list[AttentionOverlay]:
    user_id = _require_user_id(user_id)
    ensure_attention_overlay_tables(db, commit=False)
    rows = db.execute(
        """
        SELECT overlay_json FROM attention_overlay
        WHERE user_id = ?
        ORDER BY attention_id ASC
        """,
        (user_id,),
    ).fetchall()
    return [AttentionOverlay.from_dict(json.loads(row[0])) for row in rows]


def delete_attention_overlay(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    commit: bool = True,
) -> None:
    user_id = _require_user_id(user_id)
    attention_id = str(attention_id or "").strip()
    if not attention_id:
        raise AttentionStoreCommandError("attention_id must be a non-empty string")
    ensure_attention_overlay_tables(db, commit=False)
    db.execute(
        """
        DELETE FROM attention_overlay
        WHERE user_id = ? AND attention_id = ?
        """,
        (user_id, attention_id),
    )
    if commit:
        db.commit()


def snooze_attention(
    db: Any,
    item: AttentionItem,
    *,
    now: datetime,
    duration: timedelta,
) -> AttentionOverlay:
    """Validate, build, and persist a snooze overlay."""
    _assert_user_match(item)
    overlay = build_snooze_overlay(item, now=now, duration=duration)
    upsert_attention_overlay(db, item.user_id, overlay)
    return overlay


def dismiss_attention(
    db: Any,
    item: AttentionItem,
    *,
    now: datetime,
) -> AttentionOverlay:
    """Validate, build, and persist a durable dismiss overlay."""
    _assert_user_match(item)
    overlay = build_dismiss_overlay(item, now=now)
    upsert_attention_overlay(db, item.user_id, overlay)
    return overlay


def start_attention_cta(
    db: Any,
    item: AttentionItem,
    *,
    now: datetime,
) -> AttentionOverlay:
    """Validate, build, and persist an in_flight overlay (no adapter I/O)."""
    _assert_user_match(item)
    overlay = build_in_flight_overlay(item, now=now)
    upsert_attention_overlay(db, item.user_id, overlay)
    return overlay


def clear_attention_overlay(
    db: Any,
    user_id: str,
    attention_id: str,
) -> None:
    """Remove overlay (≡ clear) for supervisor / root-cause-gone paths."""
    delete_attention_overlay(db, user_id, attention_id)


def _require_item(item: AttentionItem) -> None:
    if not isinstance(item, AttentionItem):
        raise AttentionStoreCommandError("item must be an AttentionItem")
    if not str(item.user_id or "").strip():
        raise AttentionStoreCommandError("item.user_id must be a non-empty string")


def _assert_user_match(item: AttentionItem) -> None:
    _require_item(item)


def _require_user_id(user_id: str) -> str:
    text = str(user_id or "").strip()
    if not text:
        raise AttentionStoreCommandError("user_id must be a non-empty string")
    return text


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime) -> str:
    return _ensure_aware(value).isoformat()


# Re-export for Store/Supervisor writers.
__all__ = [
    "IN_FLIGHT_TIMEOUT_SECONDS",
    "MAX_SNOOZE",
    "AttentionStoreCommandError",
    "build_dismiss_overlay",
    "build_in_flight_overlay",
    "build_snooze_overlay",
    "clear_attention_overlay",
    "delete_attention_overlay",
    "dismiss_attention",
    "ensure_attention_overlay_tables",
    "get_attention_overlay",
    "list_attention_overlays",
    "snooze_attention",
    "start_attention_cta",
    "upsert_attention_overlay",
]
