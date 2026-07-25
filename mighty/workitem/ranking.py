"""Deterministic WorkItem ranking (Home OS domain model §6).

Produces a total order over effective Work Items. Same items + same as_of ⇒
same order. Never uses randomness, visit recency, marketing, or input order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from mighty.workitem.model import WorkItem


class RankingError(ValueError):
    """Raised when ranking inputs are invalid."""


# Sentinel so missing expiry sorts after dated items in the same band.
_DEADLINE_LAST = datetime.max.replace(tzinfo=timezone.utc)


def rank_work_items(
    items: Sequence[WorkItem],
    *,
    as_of: datetime,
) -> tuple[WorkItem, ...]:
    """Return effective items in deterministic total order (best first).

    ``as_of`` is required for expiry evaluation. Input sequence order never
    affects the result. Does not mutate ``items``.
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    effective = [item for item in items if _is_rankable(item, as_of=as_of)]
    # Precompute dependency depth so A-blocks-B elevates A without inventing edges.
    blocked_by = _blocked_by_map(effective)
    ordered = sorted(
        effective,
        key=lambda item: _sort_key(item, blocked_by=blocked_by),
    )
    return tuple(ordered)


def _is_rankable(item: WorkItem, *, as_of: datetime) -> bool:
    """Items already filtered for effectiveness still need intrinsic expiry check."""
    if item.expires_at is not None and as_of >= item.expires_at:
        return False
    return True


def _blocked_by_map(items: Sequence[WorkItem]) -> dict[str, frozenset[str]]:
    """Map item id → set of ids that block it (from declared ``blocks`` edges)."""
    ids = {item.id for item in items}
    blocked: dict[str, set[str]] = {item.id: set() for item in items}
    for item in items:
        for blocked_id in item.blocks:
            if blocked_id in ids and blocked_id != item.id:
                blocked[blocked_id].add(item.id)
    return {k: frozenset(v) for k, v in blocked.items()}


def _dependency_rank(item_id: str, blocked_by: dict[str, frozenset[str]]) -> int:
    """Items that block others sort earlier: more dependents ⇒ lower (better) rank.

    Within the same class/time/effort band only — callers place this after
    priority and time keys. Uses a stable count of direct dependents that
    declare being blocked by this item (inverse of blocked_by).
    """
    dependents = sum(1 for deps in blocked_by.values() if item_id in deps)
    # Negate via sort: higher dependents should come first → use negative count.
    return -dependents


def _sort_key(
    item: WorkItem,
    *,
    blocked_by: dict[str, frozenset[str]],
) -> tuple:
    """Ranking keys in contract order (domain model §6)."""
    # 1. Urgency: class/priority then within-class severity band.
    priority_key = item.priority_rank
    urgency_key = item.urgency_rank

    # 2. Time sensitivity: earlier expires_at wins; no expiry sorts last in band.
    deadline = item.expires_at if item.expires_at is not None else _DEADLINE_LAST

    # 3. User effort (within-band assist): lower effort_weight wins.
    effort_key = item.effort_weight

    # 4. Dependency: items that block others rank above the blocked items.
    #    Also push items that are blocked later via blocked_by count.
    dep_key = _dependency_rank(item.id, blocked_by)
    blocked_penalty = len(blocked_by.get(item.id, ()))

    # 5. Confidence: higher confidence ranks first within band → negate.
    confidence_key = -item.confidence

    # Final tie-break: provider ascending (missing as ""), then id ascending.
    provider_key = item.provider or ""
    id_key = item.id

    return (
        priority_key,
        urgency_key,
        deadline,
        effort_key,
        dep_key,
        blocked_penalty,
        confidence_key,
        provider_key,
        id_key,
    )
