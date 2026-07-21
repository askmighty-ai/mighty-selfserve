"""Attention HTTP command helpers — thin adapters over AttentionStore (M4).

No ranking or producer policy. Side effects (Access Manager) are optional and
invoked only after a successful Store write.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mighty.attention import AttentionCtaKey, AttentionItem
from mighty.attention_engine import read_attention_snapshot
from mighty.attention_store import (
    AttentionStoreCommandError,
    clear_attention_overlay,
    dismiss_attention,
    snooze_attention,
    start_attention_cta,
)
from mighty.attention_view import AttentionSurface, build_attention_view

logger = logging.getLogger(__name__)

DEFAULT_SNOOZE = timedelta(minutes=30)

VerificationRequester = Callable[[Any, str, str], Any]


def find_attention_item(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    now: datetime,
) -> AttentionItem | None:
    """Locate a candidate AttentionItem by id for the user."""
    snap = read_attention_snapshot(db, user_id, now=now)
    aid = str(attention_id or "").strip()
    for item in snap.candidates:
        if item.attention_id == aid:
            return item
    return None


def command_snooze(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    now: datetime,
    duration: timedelta | None = None,
) -> dict[str, Any]:
    item = _require_item(db, user_id, attention_id, now=now)
    overlay = snooze_attention(
        db, item, now=now, duration=duration or DEFAULT_SNOOZE
    )
    return {"ok": True, "command": "snooze", "overlay": overlay.to_dict()}


def command_dismiss(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    now: datetime,
) -> dict[str, Any]:
    item = _require_item(db, user_id, attention_id, now=now)
    overlay = dismiss_attention(db, item, now=now)
    return {"ok": True, "command": "dismiss", "overlay": overlay.to_dict()}


def command_cta(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    now: datetime,
    request_verification: VerificationRequester | None = None,
) -> dict[str, Any]:
    item = _require_item(db, user_id, attention_id, now=now)
    overlay = start_attention_cta(db, item, now=now)
    side: dict[str, Any] = {"verification_requested": False}
    if (
        item.cta_key is AttentionCtaKey.START_PROVIDER_LOGIN
        and item.provider
        and request_verification is not None
    ):
        try:
            request_verification(db, user_id, item.provider)
            side["verification_requested"] = True
        except Exception:
            logger.exception(
                "attention_cta_side_effect_failed user_id=%s attention_id=%s",
                user_id,
                attention_id,
            )
            side["verification_error"] = True
    return {
        "ok": True,
        "command": "cta",
        "overlay": overlay.to_dict(),
        "side_effects": side,
    }


def command_clear(
    db: Any,
    user_id: str,
    attention_id: str,
) -> dict[str, Any]:
    clear_attention_overlay(db, user_id, attention_id)
    return {"ok": True, "command": "clear"}


def build_view_payload(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    *,
    now: datetime,
) -> dict[str, Any]:
    snap = read_attention_snapshot(db, user_id, now=now)
    view = build_attention_view(snap.state, surface=surface)
    return {
        "ok": True,
        "generated_at": snap.generated_at,
        "view": view.to_dict(),
        "state": snap.state.to_dict(),
    }


def _require_item(
    db: Any,
    user_id: str,
    attention_id: str,
    *,
    now: datetime,
) -> AttentionItem:
    item = find_attention_item(db, user_id, attention_id, now=now)
    if item is None:
        raise AttentionStoreCommandError(
            f"attention item not found: {attention_id}"
        )
    if item.user_id != str(user_id).strip():
        raise AttentionStoreCommandError("attention item user mismatch")
    return item


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
