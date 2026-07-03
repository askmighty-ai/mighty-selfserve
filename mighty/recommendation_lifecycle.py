"""
mighty.recommendation_lifecycle
───────────────────────────────
Persisted lifecycle for contextual recommendations.

States (monotonic within a path):
  generated  — recommendation produced by an advisor
  shown      — surfaced to the user
  clicked    — user opened the primary CTA
  dismissed  — user explicitly dismissed (terminal)
  completed  — user marked done (terminal)
  expired    — stale or no longer applicable (terminal)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any


GENERATED = "generated"
SHOWN = "shown"
CLICKED = "clicked"
DISMISSED = "dismissed"
COMPLETED = "completed"
EXPIRED = "expired"

ALL_STATES = (GENERATED, SHOWN, CLICKED, DISMISSED, COMPLETED, EXPIRED)
TERMINAL_STATES = frozenset({DISMISSED, COMPLETED, EXPIRED})

DEFAULT_TTL_DAYS = 30


class RecommendationLifecycleState(str, Enum):
    GENERATED = GENERATED
    SHOWN = SHOWN
    CLICKED = CLICKED
    DISMISSED = DISMISSED
    COMPLETED = COMPLETED
    EXPIRED = EXPIRED


@dataclass(frozen=True)
class RecommendationLifecycleRecord:
    recommendation_key: str
    title: str
    recommendation_type: str
    source: str
    state: str
    generated_at: str
    shown_at: str | None = None
    clicked_at: str | None = None
    dismissed_at: str | None = None
    completed_at: str | None = None
    expired_at: str | None = None
    row_id: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_visible(self) -> bool:
        return self.state not in TERMINAL_STATES


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", (value or "").strip().lower())
    return text.strip("_") or "general"


def recommendation_key_for(rec: Any, *, source: str = "dashboard") -> str:
    """Stable identifier for upserting lifecycle rows."""
    explicit = str(getattr(rec, "id", "") or "").strip()
    if explicit:
        return explicit
    title = str(getattr(rec, "title", "") or "").strip()
    rec_type = str(getattr(rec, "recommendation_type", "") or "general").strip().lower()
    digest = hashlib.sha256(f"{source}|{rec_type}|{title}".encode()).hexdigest()[:16]
    return f"{_slug(rec_type)}_{digest}"


def recommendation_snapshot(rec: Any, *, source: str = "dashboard") -> dict[str, str]:
    return {
        "recommendation_key": recommendation_key_for(rec, source=source),
        "title": str(getattr(rec, "title", "") or "").strip(),
        "recommendation_type": str(
            getattr(rec, "recommendation_type", "") or "general"
        ).strip().lower()
        or "general",
        "source": source,
    }


def _utcnow() -> str:
    return datetime.utcnow().isoformat()


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def _row_to_record(row: Any) -> RecommendationLifecycleRecord:
    return RecommendationLifecycleRecord(
        row_id=row["id"],
        recommendation_key=row["recommendation_key"],
        title=row["title"],
        recommendation_type=row["recommendation_type"],
        source=row["source"],
        state=row["state"],
        generated_at=row["generated_at"],
        shown_at=row["shown_at"],
        clicked_at=row["clicked_at"],
        dismissed_at=row["dismissed_at"],
        completed_at=row["completed_at"],
        expired_at=row["expired_at"],
    )


def _allowed_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    if current in TERMINAL_STATES:
        return False
    if target == SHOWN and current == GENERATED:
        return True
    if target == CLICKED and current in (GENERATED, SHOWN):
        return True
    if target == DISMISSED and current in (GENERATED, SHOWN, CLICKED):
        return True
    if target == COMPLETED and current in (SHOWN, CLICKED):
        return True
    if target == EXPIRED and current in (GENERATED, SHOWN):
        return True
    return False


def _timestamp_column(state: str) -> str:
    return {
        SHOWN: "shown_at",
        CLICKED: "clicked_at",
        DISMISSED: "dismissed_at",
        COMPLETED: "completed_at",
        EXPIRED: "expired_at",
    }[state]


def load_lifecycle_by_key(db: Any, user_id: str) -> dict[str, RecommendationLifecycleRecord]:
    rows = db.execute(
        """
        SELECT id, recommendation_key, title, recommendation_type, source,
               state, generated_at, shown_at, clicked_at,
               dismissed_at, completed_at, expired_at
        FROM recommendation_lifecycle
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchall()
    return {row["recommendation_key"]: _row_to_record(row) for row in rows}


def sync_generated_recommendations(
    db: Any,
    user_id: str,
    recommendations: list[Any],
    *,
    source: str = "dashboard",
    now: str | None = None,
) -> dict[str, RecommendationLifecycleRecord]:
    """Upsert freshly generated recommendations and return the updated index."""
    now = now or _utcnow()
    existing = load_lifecycle_by_key(db, user_id)
    active_keys: set[str] = set()

    for rec in recommendations or []:
        snap = recommendation_snapshot(rec, source=source)
        key = snap["recommendation_key"]
        active_keys.add(key)
        row = existing.get(key)
        if row is None:
            db.execute(
                """
                INSERT INTO recommendation_lifecycle (
                    user_id, recommendation_key, title, recommendation_type, source,
                    state, generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    key,
                    snap["title"],
                    snap["recommendation_type"],
                    snap["source"],
                    GENERATED,
                    now,
                ),
            )
            existing[key] = RecommendationLifecycleRecord(
                recommendation_key=key,
                title=snap["title"],
                recommendation_type=snap["recommendation_type"],
                source=snap["source"],
                state=GENERATED,
                generated_at=now,
            )
        else:
            db.execute(
                """
                UPDATE recommendation_lifecycle
                SET title=?, recommendation_type=?, source=?
                WHERE user_id=? AND recommendation_key=?
                """,
                (
                    snap["title"],
                    snap["recommendation_type"],
                    snap["source"],
                    user_id,
                    key,
                ),
            )

    expire_stale_recommendations(
        db,
        user_id,
        active_keys=active_keys,
        existing=existing,
        now=now,
    )
    db.commit()
    return load_lifecycle_by_key(db, user_id)


def expire_stale_recommendations(
    db: Any,
    user_id: str,
    *,
    active_keys: set[str],
    existing: dict[str, RecommendationLifecycleRecord] | None = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
    now: str | None = None,
) -> int:
    """Mark recommendations expired when they are no longer generated or past TTL."""
    now = now or _utcnow()
    now_dt = _parse_ts(now) or datetime.utcnow()
    existing = existing if existing is not None else load_lifecycle_by_key(db, user_id)
    expired_count = 0

    for key, row in existing.items():
        if row.is_terminal:
            continue
        generated_dt = _parse_ts(row.generated_at)
        past_ttl = (
            generated_dt is not None
            and generated_dt + timedelta(days=ttl_days) <= now_dt
        )
        if key not in active_keys or past_ttl:
            transition_recommendation(
                db,
                user_id,
                key,
                EXPIRED,
                existing=existing,
                now=now,
                commit=False,
            )
            expired_count += 1

    return expired_count


def transition_recommendation(
    db: Any,
    user_id: str,
    recommendation_key: str,
    target_state: str,
    *,
    existing: dict[str, RecommendationLifecycleRecord] | None = None,
    now: str | None = None,
    commit: bool = True,
) -> RecommendationLifecycleRecord | None:
    target_state = str(target_state or "").strip().lower()
    if target_state not in ALL_STATES:
        raise ValueError(f"invalid lifecycle state: {target_state}")

    existing = existing if existing is not None else load_lifecycle_by_key(db, user_id)
    row = existing.get(recommendation_key)
    if row is None:
        return None
    if not _allowed_transition(row.state, target_state):
        return row

    now = now or _utcnow()
    updates = ["state=?"]
    params: list[Any] = [target_state]
    if target_state != GENERATED:
        column = _timestamp_column(target_state)
        updates.append(f"{column}=COALESCE({column}, ?)")
        params.append(now)

    params.extend([user_id, recommendation_key])
    db.execute(
        f"""
        UPDATE recommendation_lifecycle
        SET {", ".join(updates)}
        WHERE user_id=? AND recommendation_key=?
        """,
        tuple(params),
    )
    if commit:
        db.commit()
    refreshed = load_lifecycle_by_key(db, user_id)
    return refreshed.get(recommendation_key)


def mark_recommendations_shown(
    db: Any,
    user_id: str,
    recommendation_keys: list[str],
    *,
    existing: dict[str, RecommendationLifecycleRecord] | None = None,
    now: str | None = None,
) -> None:
    existing = existing if existing is not None else load_lifecycle_by_key(db, user_id)
    now = now or _utcnow()
    for key in recommendation_keys:
        transition_recommendation(
            db,
            user_id,
            key,
            SHOWN,
            existing=existing,
            now=now,
            commit=False,
        )
    db.commit()


def filter_visible_recommendations(
    recommendations: list[Any],
    lifecycle_by_key: dict[str, RecommendationLifecycleRecord],
    *,
    source: str = "dashboard",
) -> list[Any]:
    visible: list[Any] = []
    for rec in recommendations or []:
        key = recommendation_key_for(rec, source=source)
        row = lifecycle_by_key.get(key)
        if row is None or row.is_visible:
            visible.append(rec)
    return visible


def attach_lifecycle_ids(
    recommendations: list[Any],
    *,
    source: str = "dashboard",
) -> list[Any]:
    """Ensure each recommendation exposes a stable id for tracking hooks."""
    for rec in recommendations or []:
        key = recommendation_key_for(rec, source=source)
        if hasattr(rec, "id"):
            if not getattr(rec, "id", None):
                rec.id = key
        else:
            try:
                rec.id = key
            except Exception:
                pass
    return recommendations
