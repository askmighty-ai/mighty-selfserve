"""Attention shadow recorder — Milestone 2 (no customer cutover).

Records Attention Engine output beside Home/Worker without changing product
policy or UI. Surfaces continue to use existing Home / account-status paths.

See docs/ATTENTION_ENGINE.md.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from mighty.attention_engine import AttentionReadSnapshot, read_attention_snapshot

logger = logging.getLogger(__name__)

AttentionSurface = Literal["home", "worker"]


def ensure_attention_shadow_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attention_shadow (
            user_id              TEXT NOT NULL,
            surface              TEXT NOT NULL,
            generated_at         TEXT NOT NULL,
            primary_attention_id TEXT,
            silence              TEXT,
            candidate_count      INTEGER NOT NULL DEFAULT 0,
            state_json           TEXT NOT NULL,
            PRIMARY KEY (user_id, surface)
        )
        """
    )
    if commit:
        db.commit()


def record_attention_shadow(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    *,
    now: datetime,
    commit: bool = True,
) -> AttentionReadSnapshot | None:
    """Run the Attention Engine and persist a shadow snapshot.

    Failures are swallowed and logged — shadow must never break Home/Worker.
    Returns the snapshot on success, else ``None``.
    """
    try:
        snapshot = read_attention_snapshot(db, user_id, now=now)
        persist_attention_shadow(db, surface, snapshot, commit=commit)
        return snapshot
    except Exception:
        logger.exception(
            "attention_shadow_failed user_id=%s surface=%s",
            user_id,
            surface,
        )
        return None


def persist_attention_shadow(
    db: Any,
    surface: AttentionSurface,
    snapshot: AttentionReadSnapshot,
    *,
    commit: bool = True,
) -> None:
    ensure_attention_shadow_tables(db, commit=False)
    state = snapshot.state
    primary_id = state.primary.attention_id if state.primary is not None else None
    silence = state.silence.value if state.silence is not None else None
    payload = json.dumps(state.to_dict(), separators=(",", ":"), sort_keys=True)
    db.execute(
        """
        INSERT INTO attention_shadow (
            user_id, surface, generated_at, primary_attention_id,
            silence, candidate_count, state_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, surface) DO UPDATE SET
            generated_at=excluded.generated_at,
            primary_attention_id=excluded.primary_attention_id,
            silence=excluded.silence,
            candidate_count=excluded.candidate_count,
            state_json=excluded.state_json
        """,
        (
            snapshot.user_id,
            surface,
            snapshot.generated_at,
            primary_id,
            silence,
            len(snapshot.candidates),
            payload,
        ),
    )
    if commit:
        db.commit()


def load_attention_shadow(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
) -> dict[str, Any] | None:
    ensure_attention_shadow_tables(db, commit=False)
    row = db.execute(
        """
        SELECT user_id, surface, generated_at, primary_attention_id,
               silence, candidate_count, state_json
        FROM attention_shadow
        WHERE user_id = ? AND surface = ?
        """,
        (str(user_id), surface),
    ).fetchone()
    if not row:
        return None
    mapping = dict(row) if not isinstance(row, dict) else row
    try:
        state = json.loads(mapping["state_json"])
    except Exception:
        state = None
    return {
        "user_id": mapping["user_id"],
        "surface": mapping["surface"],
        "generated_at": mapping["generated_at"],
        "primary_attention_id": mapping.get("primary_attention_id"),
        "silence": mapping.get("silence"),
        "candidate_count": mapping.get("candidate_count"),
        "state": state,
    }


def shadow_now() -> datetime:
    """Wall clock for shadow calls only — engine still receives it explicitly."""
    return datetime.now(timezone.utc)
