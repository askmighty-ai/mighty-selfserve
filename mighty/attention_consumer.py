"""Safe Attention Platform consumer helpers for Home/Worker (Milestone 3).

Composes engine read → AttentionView, shadow/compare recording, and cutover
gating. Never raises to callers — Attention failures must not break surfaces.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from mighty.attention_compare import LegacyAttentionSignal
from mighty.attention_cutover import (
    attention_cutover_enabled,
    attention_cutover_exposes_payload,
    attention_cutover_mode,
)
from mighty.attention_engine import read_attention
from mighty.attention_shadow import record_attention_shadow, shadow_now
from mighty.attention_view import AttentionSurface, AttentionView, build_attention_view

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttentionConsumerResult:
    mode: str
    view: AttentionView | None
    used_attention: bool
    platform_failed: bool


def load_attention_view_safe(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    *,
    now: datetime | None = None,
    provider_display_names: Mapping[str, str] | None = None,
    provider_open_urls: Mapping[str, str] | None = None,
) -> tuple[AttentionView | None, bool]:
    """Return (view, platform_failed). Never raises."""
    try:
        clock = now or shadow_now()
        state = read_attention(db, user_id, now=clock)
        view = build_attention_view(
            state,
            surface=surface,
            provider_display_names=provider_display_names,
            provider_open_urls=provider_open_urls,
        )
        return view, False
    except Exception:
        logger.exception(
            "attention_consumer_failed user_id=%s surface=%s",
            user_id,
            surface,
        )
        return None, True


def consume_attention_for_surface(
    db: Any,
    user_id: str,
    surface: AttentionSurface,
    *,
    legacy: LegacyAttentionSignal | None = None,
    now: datetime | None = None,
    provider_display_names: Mapping[str, str] | None = None,
    provider_open_urls: Mapping[str, str] | None = None,
    record_shadow: bool = True,
) -> AttentionConsumerResult:
    """Shadow/compare + optional cutover view for one surface.

    When cutover is ``on`` and the platform succeeds, ``used_attention`` is True
    and ``view`` is the SSoT for attention presentation. On failure, view is
    None and ``used_attention`` is False so the caller can fall back safely.
    """
    mode = attention_cutover_mode(surface)
    clock = now or shadow_now()
    shadow_surface = "home" if surface == "home" else "worker"
    snapshot = None
    failed = False

    if record_shadow:
        try:
            snapshot = record_attention_shadow(
                db,
                user_id,
                shadow_surface,
                now=clock,
                legacy=legacy,
            )
            failed = snapshot is None
        except Exception:
            failed = True
            logger.exception(
                "attention_consumer_shadow_failed user_id=%s surface=%s",
                user_id,
                surface,
            )

    view: AttentionView | None = None
    if snapshot is not None:
        try:
            view = build_attention_view(
                snapshot.state,
                surface=surface,
                provider_display_names=provider_display_names,
                provider_open_urls=provider_open_urls,
            )
        except Exception:
            failed = True
            view = None
            logger.exception(
                "attention_view_build_failed user_id=%s surface=%s",
                user_id,
                surface,
            )
    elif mode == "on":
        # Shadow disabled or failed — still attempt a direct read for cutover.
        view, failed = load_attention_view_safe(
            db,
            user_id,
            surface,
            now=clock,
            provider_display_names=provider_display_names,
            provider_open_urls=provider_open_urls,
        )

    use = bool(mode == "on" and view is not None and not failed)
    return AttentionConsumerResult(
        mode=mode,
        view=view if (use or attention_cutover_exposes_payload(surface)) else None,
        used_attention=use,
        platform_failed=failed,
    )


def attention_api_payload(result: AttentionConsumerResult) -> dict[str, Any] | None:
    """Serialize consumer result for Worker JSON when not fully off."""
    if result.mode == "off":
        return None
    payload: dict[str, Any] = {
        "cutover": result.mode,
        "used_attention": result.used_attention,
        "platform_failed": result.platform_failed,
    }
    if result.view is not None:
        payload["view"] = result.view.to_dict()
    return payload
