"""AttentionState — deterministic ranking over AttentionItems (PR 2C).

Pure product-policy stage:

    Sequence[AttentionItem] → AttentionState

Selects the primary candidate and silence verdict from effective items using
RFC §7. No overlays, Store, persistence, delivery, Home, or provider policy.

See docs/ATTENTION_STATE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from mighty.attention import AttentionClass, AttentionItem

ATTENTION_STATE_SCHEMA_VERSION = 1

# RFC §7 rank table — lower number wins.
_CLASS_RANK: dict[AttentionClass, int] = {
    AttentionClass.TRUST: 1,
    AttentionClass.AGENT_AUTHORIZATION: 2,
    AttentionClass.AUTH_BLOCKER: 3,
    AttentionClass.SYSTEM: 4,
    AttentionClass.VALUE_AT_RISK: 5,
    AttentionClass.OPPORTUNITY: 6,
    AttentionClass.ACCESS_DEGRADED: 7,
    AttentionClass.DATA_GAP: 8,
}

_RANKS_BLOCKER_BAND = frozenset({1, 2, 3, 4, 5})
_RANKS_AWAITING_DATA = frozenset({7, 8})

# Sentinel so None becomes_stale_at sorts last within rank 5.
_DEADLINE_LAST = datetime.max.replace(tzinfo=timezone.utc)


class SilenceVerdict(str, Enum):
    """Quiet-product silence modes (RFC §7).

    ``AttentionState.silence`` is ``None`` when effective ranks 1–5 are visible
    (product is not silent). ``suppressed`` requires overlays and is not
    produced by ``select_attention``.
    """

    ALL_CLEAR = "all_clear"
    SUPPRESSED = "suppressed"
    AWAITING_DATA = "awaiting_data"


_VALID_SILENCE = frozenset(item.value for item in SilenceVerdict)


class AttentionStateValidationError(ValueError):
    """Raised when an AttentionState payload violates the frozen contract."""


@dataclass(frozen=True)
class AttentionState:
    """Immutable ranked attention snapshot (pure ranker output).

    Does not embed AuthTruth, AccountState, copy, overlays, or delivery state.
    """

    schema_version: int
    primary: AttentionItem | None
    remaining: tuple[AttentionItem, ...]
    silence: SilenceVerdict | None

    def __post_init__(self) -> None:
        if self.schema_version != ATTENTION_STATE_SCHEMA_VERSION:
            raise AttentionStateValidationError(
                f"schema_version must be {ATTENTION_STATE_SCHEMA_VERSION}, "
                f"got {self.schema_version!r}"
            )
        if self.primary is not None and not isinstance(self.primary, AttentionItem):
            raise AttentionStateValidationError("primary must be an AttentionItem or None")
        if not isinstance(self.remaining, tuple):
            object.__setattr__(self, "remaining", tuple(self.remaining))
        for item in self.remaining:
            if not isinstance(item, AttentionItem):
                raise AttentionStateValidationError(
                    "remaining must contain only AttentionItem values"
                )
        if self.silence is not None and not isinstance(self.silence, SilenceVerdict):
            raise AttentionStateValidationError(
                "silence must be a SilenceVerdict or None"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "primary": self.primary.to_dict() if self.primary is not None else None,
            "remaining": [item.to_dict() for item in self.remaining],
            "silence": self.silence.value if self.silence is not None else None,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AttentionState:
        if not isinstance(payload, Mapping):
            raise AttentionStateValidationError("AttentionState payload must be a mapping")
        schema_version = _parse_schema_version(payload.get("schema_version"))
        primary_raw = payload.get("primary")
        primary = (
            None
            if primary_raw is None
            else AttentionItem.from_dict(primary_raw)
        )
        remaining_raw = payload.get("remaining")
        if remaining_raw is None:
            remaining: tuple[AttentionItem, ...] = ()
        elif not isinstance(remaining_raw, (list, tuple)):
            raise AttentionStateValidationError("remaining must be a list or tuple")
        else:
            remaining = tuple(AttentionItem.from_dict(item) for item in remaining_raw)
        silence = _parse_silence(payload.get("silence"))
        return cls(
            schema_version=schema_version,
            primary=primary,
            remaining=remaining,
            silence=silence,
        )


def select_attention(
    items: Sequence[AttentionItem],
    *,
    now: datetime,
) -> AttentionState:
    """Rank effective AttentionItems into a deterministic AttentionState.

    ``now`` must be supplied explicitly for stale-item evaluation. Input order
    never affects output. Does not mutate ``items``.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    effective = [item for item in items if _is_effective(item, now=now)]
    ordered = sorted(effective, key=lambda item: _sort_key(item))

    if not ordered:
        return AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=None,
            remaining=(),
            silence=SilenceVerdict.ALL_CLEAR,
        )

    primary = ordered[0]
    remaining = tuple(ordered[1:])
    silence = _silence_for(ordered)
    return AttentionState(
        schema_version=ATTENTION_STATE_SCHEMA_VERSION,
        primary=primary,
        remaining=remaining,
        silence=silence,
    )


def _is_effective(item: AttentionItem, *, now: datetime) -> bool:
    deadline = _parse_iso(item.becomes_stale_at)
    if deadline is None:
        return True
    return now < deadline


def _rank_of(item: AttentionItem) -> int:
    return _CLASS_RANK[item.attention_class]


def _sort_key(item: AttentionItem) -> tuple[int, datetime, str, str]:
    rank = _rank_of(item)
    if rank == 5:
        deadline = _parse_iso(item.becomes_stale_at) or _DEADLINE_LAST
    else:
        # Non-rank-5: deadline does not participate; use a constant.
        deadline = _DEADLINE_LAST
    provider_key = item.provider or ""
    return (rank, deadline, provider_key, item.attention_id)


def _silence_for(ordered: Sequence[AttentionItem]) -> SilenceVerdict | None:
    ranks = {_rank_of(item) for item in ordered}
    if ranks & _RANKS_BLOCKER_BAND:
        return None
    if ranks & _RANKS_AWAITING_DATA:
        return SilenceVerdict.AWAITING_DATA
    return SilenceVerdict.ALL_CLEAR


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


def _parse_schema_version(value: Any) -> int:
    if value is None:
        raise AttentionStateValidationError("schema_version is required")
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise AttentionStateValidationError(
            f"schema_version must be an int, got {value!r}"
        ) from exc
    if version != ATTENTION_STATE_SCHEMA_VERSION:
        raise AttentionStateValidationError(
            f"schema_version must be {ATTENTION_STATE_SCHEMA_VERSION}, got {version!r}"
        )
    return version


def _parse_silence(value: Any) -> SilenceVerdict | None:
    if value is None:
        return None
    if isinstance(value, SilenceVerdict):
        return value
    text = str(value).strip().lower()
    if text not in _VALID_SILENCE:
        raise AttentionStateValidationError(
            f"silence must be one of {sorted(_VALID_SILENCE)} or null, got {value!r}"
        )
    return SilenceVerdict(text)
