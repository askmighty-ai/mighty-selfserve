"""AttentionSupervisor — in_flight timeout + orphan overlay GC (Milestone 4).

Owns persist-clear of timed-out in_flight overlays and deletion of overlays
whose attention_id is absent from the current candidate set.

No browser I/O, ranking, or delivery. Failures must never raise to callers.

See docs/ATTENTION_SUPERVISOR.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.attention_engine import read_attention_snapshot
from mighty.attention_overlay import IN_FLIGHT_TIMEOUT_SECONDS, OverlayStatus
from mighty.attention_store import (
    clear_attention_overlay,
    ensure_attention_overlay_tables,
    list_attention_overlay_user_ids,
    list_attention_overlays,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttentionSupervisorResult:
    """Counts from one supervisor sweep."""

    users_scanned: int
    in_flight_cleared: int
    orphans_deleted: int
    errors: int


def run_attention_supervisor(
    db: Any,
    *,
    now: datetime,
    user_ids: list[str] | None = None,
) -> AttentionSupervisorResult:
    """Clear timed-out in_flight overlays and GC orphan overlays.

    ``now`` must be supplied by the caller. Never raises.
    """
    try:
        now = _ensure_aware(now)
        ensure_attention_overlay_tables(db, commit=False)
        if user_ids is None:
            user_ids = list_attention_overlay_user_ids(db)
    except Exception:
        logger.exception("attention_supervisor_init_failed")
        return AttentionSupervisorResult(0, 0, 0, 1)

    users_scanned = 0
    in_flight_cleared = 0
    orphans_deleted = 0
    errors = 0

    for raw_uid in user_ids:
        uid = str(raw_uid or "").strip()
        if not uid:
            continue
        users_scanned += 1
        try:
            cleared, deleted = _supervise_user(db, uid, now=now)
            in_flight_cleared += cleared
            orphans_deleted += deleted
        except Exception:
            errors += 1
            logger.exception("attention_supervisor_user_failed user_id=%s", uid)

    return AttentionSupervisorResult(
        users_scanned=users_scanned,
        in_flight_cleared=in_flight_cleared,
        orphans_deleted=orphans_deleted,
        errors=errors,
    )


def _supervise_user(db: Any, user_id: str, *, now: datetime) -> tuple[int, int]:
    overlays = list_attention_overlays(db, user_id)
    if not overlays:
        return 0, 0

    snap = read_attention_snapshot(db, user_id, now=now)
    live_ids = {item.attention_id for item in snap.candidates}

    cleared = 0
    deleted = 0
    for overlay in overlays:
        # Orphan GC: root cause gone → delete overlay.
        if overlay.attention_id not in live_ids:
            clear_attention_overlay(db, user_id, overlay.attention_id)
            deleted += 1
            continue

        if overlay.status is not OverlayStatus.IN_FLIGHT:
            continue
        started = _parse_iso(overlay.started_at)
        if started is None:
            continue
        age = (now - started).total_seconds()
        if age >= IN_FLIGHT_TIMEOUT_SECONDS:
            clear_attention_overlay(db, user_id, overlay.attention_id)
            cleared += 1

    return cleared, deleted


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
