"""Attention Engine — thin read-path composer (Milestone 2).

Composes existing stages only. Contains no ranking, silence, overlay, or
producer business rules.

See docs/ATTENTION_ENGINE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.attention import AttentionItem
from mighty.attention_compiler import compile_attention_candidates
from mighty.attention_loaders import (
    load_account_states_for_attention,
    load_auth_truths,
    load_authorize_rows,
    load_worker_signal,
)
from mighty.attention_overlay import AttentionOverlay, compose_attention
from mighty.attention_state import AttentionState
from mighty.attention_store import (
    ensure_attention_overlay_tables,
    list_attention_overlays,
)


@dataclass(frozen=True)
class AttentionReadSnapshot:
    """Diagnostic snapshot of one engine read (immutable)."""

    state: AttentionState
    candidates: tuple[AttentionItem, ...]
    overlays: tuple[AttentionOverlay, ...]
    user_id: str
    generated_at: str


def read_attention(
    db: Any,
    user_id: str,
    *,
    now: datetime,
) -> AttentionState:
    """Load → compile → overlays → AttentionState.

    ``now`` must be supplied by the caller for stale/snooze evaluation.
    """
    return read_attention_snapshot(db, user_id, now=now).state


def read_attention_snapshot(
    db: Any,
    user_id: str,
    *,
    now: datetime,
) -> AttentionReadSnapshot:
    """Full immutable read snapshot for tests and shadow recording."""
    now = _ensure_aware(now)
    generated_at = now.replace(microsecond=0).isoformat()
    uid = str(user_id or "").strip()

    account_states = load_account_states_for_attention(db, uid)
    auth_truths = load_auth_truths(
        db,
        uid,
        now=now,
        projected_at=generated_at,
        accounts=account_states,
    )
    authorize_rows = load_authorize_rows(db, uid)
    worker_signal = load_worker_signal(
        db,
        uid,
        now=now,
        enrolled_account_count=len(account_states),
    )
    candidates = compile_attention_candidates(
        auth_truths=auth_truths,
        authorize_rows=authorize_rows,
        worker_signal=worker_signal,
        account_states=account_states,
    )
    ensure_attention_overlay_tables(db, commit=False)
    overlays = tuple(list_attention_overlays(db, uid))
    state = compose_attention(candidates, overlays, now=now)
    return AttentionReadSnapshot(
        state=state,
        candidates=candidates,
        overlays=overlays,
        user_id=uid,
        generated_at=generated_at,
    )


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
