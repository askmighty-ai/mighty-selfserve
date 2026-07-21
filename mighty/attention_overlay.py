"""Attention overlays — filter + compose (PR 2D).

Pure read-path stage:

    candidates + overlays + now → AttentionState

Applies snooze / dismiss / in_flight visibility, then ranks via
``select_attention``. Produces ``SilenceVerdict.SUPPRESSED`` when required.

No persistence, HTTP commands, delivery, Home, or supervisor writes.

See docs/ATTENTION_OVERLAY.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from mighty.attention import AttentionClass, AttentionItem
from mighty.attention_state import (
    ATTENTION_STATE_SCHEMA_VERSION,
    AttentionState,
    SilenceVerdict,
    select_attention,
)

# RFC §4.4 — AttentionSupervisor default; Store/Supervisor writers use this.
IN_FLIGHT_TIMEOUT_SECONDS = 30 * 60

# Ranks 1–4 only (trust / agent / auth_blocker / system). Used for suppressed.
_CLASS_RANK_1_TO_4: frozenset[AttentionClass] = frozenset(
    {
        AttentionClass.TRUST,
        AttentionClass.AGENT_AUTHORIZATION,
        AttentionClass.AUTH_BLOCKER,
        AttentionClass.SYSTEM,
    }
)


class OverlayStatus(str, Enum):
    """Store-owned interaction status for one attention_id (RFC §4.1)."""

    CLEAR = "clear"
    SNOOZED = "snoozed"
    IN_FLIGHT = "in_flight"
    DURABLE_DISMISSED = "durable_dismissed"


_VALID_OVERLAY_STATUS = frozenset(item.value for item in OverlayStatus)


class AttentionOverlayValidationError(ValueError):
    """Raised when an AttentionOverlay payload violates the contract."""


@dataclass(frozen=True)
class AttentionOverlay:
    """Interaction overlay for one AttentionItem — never creates candidates."""

    attention_id: str
    status: OverlayStatus
    until: str | None
    started_at: str | None
    updated_at: str

    def __post_init__(self) -> None:
        attention_id = str(self.attention_id or "").strip()
        if not attention_id:
            raise AttentionOverlayValidationError(
                "attention_id must be a non-empty string"
            )
        if attention_id != self.attention_id:
            object.__setattr__(self, "attention_id", attention_id)
        if not isinstance(self.status, OverlayStatus):
            raise AttentionOverlayValidationError(
                "status must be an OverlayStatus"
            )
        updated_at = str(self.updated_at or "").strip()
        if not updated_at:
            raise AttentionOverlayValidationError(
                "updated_at must be a non-empty ISO-8601 string"
            )
        if updated_at != self.updated_at:
            object.__setattr__(self, "updated_at", updated_at)
        if self.status is OverlayStatus.SNOOZED and not self.until:
            raise AttentionOverlayValidationError(
                "until is required when status is snoozed"
            )
        if self.status is OverlayStatus.IN_FLIGHT and not self.started_at:
            raise AttentionOverlayValidationError(
                "started_at is required when status is in_flight"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "attention_id": self.attention_id,
            "status": self.status.value,
            "until": self.until,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionOverlay:
        if not isinstance(payload, Mapping):
            raise AttentionOverlayValidationError(
                "AttentionOverlay payload must be a mapping"
            )
        status_raw = payload.get("status")
        if isinstance(status_raw, OverlayStatus):
            status = status_raw
        else:
            text = str(status_raw or "").strip().lower()
            if text not in _VALID_OVERLAY_STATUS:
                raise AttentionOverlayValidationError(
                    f"status must be one of {sorted(_VALID_OVERLAY_STATUS)}, "
                    f"got {status_raw!r}"
                )
            status = OverlayStatus(text)
        return cls(
            attention_id=str(payload.get("attention_id") or ""),
            status=status,
            until=_optional_str(payload.get("until")),
            started_at=_optional_str(payload.get("started_at")),
            updated_at=str(payload.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class OverlayFilterResult:
    """Visible candidates after overlay application, plus snooze facts."""

    visible: tuple[AttentionItem, ...]
    snoozed_rank_1_to_4: bool


def apply_overlays(
    items: Sequence[AttentionItem],
    overlays: Sequence[AttentionOverlay] | Mapping[str, AttentionOverlay],
    *,
    now: datetime,
) -> OverlayFilterResult:
    """Filter candidates by overlays. Does not rank or mutate inputs."""
    now = _ensure_aware(now)
    by_id = _index_overlays(overlays)
    visible: list[AttentionItem] = []
    snoozed_rank_1_to_4 = False

    # Stable pass: preserve relative input order; ranking reorders later.
    for item in items:
        overlay = by_id.get(item.attention_id)
        disposition = _disposition(item, overlay, now=now)
        if disposition == "hidden_snoozed":
            if item.attention_class in _CLASS_RANK_1_TO_4:
                snoozed_rank_1_to_4 = True
            continue
        if disposition == "hidden_dismissed":
            continue
        visible.append(item)

    return OverlayFilterResult(
        visible=tuple(visible),
        snoozed_rank_1_to_4=snoozed_rank_1_to_4,
    )


def compose_attention(
    items: Sequence[AttentionItem],
    overlays: Sequence[AttentionOverlay] | Mapping[str, AttentionOverlay],
    *,
    now: datetime,
) -> AttentionState:
    """Apply overlays, rank, and promote suppressed silence when required."""
    now = _ensure_aware(now)
    filtered = apply_overlays(items, overlays, now=now)
    base = select_attention(filtered.visible, now=now)

    if not filtered.snoozed_rank_1_to_4:
        return base

    # Suppressed only when no visible effective ranks 1–5 remain.
    if base.silence is None:
        return base

    remaining: tuple[AttentionItem, ...]
    if base.primary is None:
        remaining = base.remaining
    else:
        remaining = (base.primary, *base.remaining)

    return AttentionState(
        schema_version=ATTENTION_STATE_SCHEMA_VERSION,
        primary=None,
        remaining=remaining,
        silence=SilenceVerdict.SUPPRESSED,
    )


def _disposition(
    item: AttentionItem,
    overlay: AttentionOverlay | None,
    *,
    now: datetime,
) -> str:
    """Return visible | hidden_snoozed | hidden_dismissed."""
    if overlay is None or overlay.status is OverlayStatus.CLEAR:
        return "visible"

    if overlay.status is OverlayStatus.SNOOZED:
        until = _parse_iso(overlay.until)
        if until is not None and now < until:
            return "hidden_snoozed"
        return "visible"

    if overlay.status is OverlayStatus.IN_FLIGHT:
        # Always visible to the ranker; in-progress copy is View-layer.
        # Persisting clear after timeout is AttentionSupervisor (later).
        return "visible"

    if overlay.status is OverlayStatus.DURABLE_DISMISSED:
        # Durable dismiss is opportunity-only (RFC §5.2). Ignore elsewhere.
        if item.attention_class is AttentionClass.OPPORTUNITY:
            return "hidden_dismissed"
        return "visible"

    return "visible"


def _index_overlays(
    overlays: Sequence[AttentionOverlay] | Mapping[str, AttentionOverlay],
) -> dict[str, AttentionOverlay]:
    if isinstance(overlays, Mapping):
        return {str(key): value for key, value in overlays.items()}
    indexed: dict[str, AttentionOverlay] = {}
    for overlay in overlays:
        indexed[overlay.attention_id] = overlay
    return indexed


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


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
