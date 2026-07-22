"""Freshness + Change Intelligence coordinator (Milestone 9).

Runs after a successful Account Snapshot persist. Never raises into sync /
Home / Worker callers. Does not enqueue verification, mutate AuthTruth,
or rank Attention.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from mighty.change_intelligence import ChangeVerdict, diff_snapshots
from mighty.change_store import AccountChangeEvent, persist_change_event
from mighty.freshness_policy import (
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNAVAILABLE,
    FreshnessDecision,
    combine_freshness_and_change,
    classify_data_freshness,
)
from mighty.account_state import DATA_COMPLETE

logger = logging.getLogger(__name__)


@dataclass
class SnapshotRefreshObservation:
    provider: str
    verdict: ChangeVerdict | None = None
    event: AccountChangeEvent | None = None
    freshness: FreshnessDecision | None = None
    product_state: str = ""
    error: str | None = None
    refresh_latency_seconds: float | None = None
    first_data: bool = False


@dataclass
class FreshnessSweepCounters:
    accounts: int = 0
    fresh: int = 0
    stale: int = 0
    unavailable: int = 0
    refreshes: int = 0
    meaningful: int = 0
    quiet_refreshes: int = 0
    newly_discovered: int = 0
    duplicates_suppressed: int = 0
    errors: int = 0
    refresh_latency_samples: list[float] = field(default_factory=list)
    first_data_latency_samples: list[float] = field(default_factory=list)


def _latency_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    delta = (b - a).total_seconds()
    return delta if delta >= 0 else None


def observe_snapshot_refresh(
    db: Any,
    *,
    prev: Any | None,
    new: Any,
    now: datetime | None = None,
    commit: bool = True,
) -> SnapshotRefreshObservation:
    """Diff + persist change intelligence for a newly written snapshot.

    Failure-isolated: returns ``error`` string instead of raising.
    """
    provider = str(getattr(new, "provider", "") or "").strip().lower()
    obs = SnapshotRefreshObservation(provider=provider)
    try:
        now = now or datetime.now(timezone.utc)
        verdict = diff_snapshots(prev, new, provider=provider)
        obs.verdict = verdict
        obs.first_data = prev is None

        event = persist_change_event(
            db,
            user_id=str(getattr(new, "user_id", "") or ""),
            provider=provider,
            snapshot_id=str(getattr(new, "snapshot_id", "") or ""),
            prev_snapshot_id=(
                str(getattr(prev, "snapshot_id", "") or "") if prev else None
            ),
            verdict=verdict,
            created_at=str(getattr(new, "created_at", "") or "") or None,
            commit=commit,
        )
        obs.event = event

        freshness = classify_data_freshness(
            last_data_refresh=str(
                getattr(new, "verified_at", None)
                or getattr(new, "created_at", None)
                or ""
            )
            or None,
            data_status=DATA_COMPLETE,
            provider=provider,
            now=now,
        )
        obs.freshness = freshness
        obs.product_state = combine_freshness_and_change(
            freshness=freshness.freshness,
            change_outcome=event.outcome,
        )

        meta = getattr(new, "metadata", None) or {}
        started = None
        if isinstance(meta, dict):
            started = meta.get("refresh_started_at") or meta.get("started_at")
        obs.refresh_latency_seconds = _latency_seconds(
            started, getattr(new, "created_at", None)
        )

        logger.info(
            "freshness_change.observe provider=%s outcome=%s meaningful=%s "
            "suppressed=%s dupes=%s product_state=%s",
            provider,
            event.outcome,
            event.meaningful_count,
            event.suppressed,
            event.duplicates_suppressed,
            obs.product_state,
        )
        return obs
    except Exception as exc:
        logger.exception(
            "freshness_change.observe_failed provider=%s err=%s", provider, exc
        )
        obs.error = str(exc)
        return obs


def safe_observe_snapshot_refresh(
    db: Any,
    *,
    prev: Any | None,
    new: Any,
    **kwargs: Any,
) -> SnapshotRefreshObservation | None:
    """Wrapper that never raises; returns None only if ``new`` is missing."""
    if new is None:
        return None
    try:
        return observe_snapshot_refresh(db, prev=prev, new=new, **kwargs)
    except Exception as exc:
        logger.exception("freshness_change.safe_observe_failed err=%s", exc)
        return SnapshotRefreshObservation(
            provider=str(getattr(new, "provider", "") or ""),
            error=str(exc),
        )


def compute_account_freshness_counters(
    accounts: list[Any],
    *,
    now: datetime | None = None,
) -> FreshnessSweepCounters:
    """Aggregate freshness classes across AccountState-like objects."""
    counters = FreshnessSweepCounters()
    now = now or datetime.now(timezone.utc)
    for account in accounts:
        counters.accounts += 1
        decision = classify_data_freshness(
            last_data_refresh=getattr(account, "last_data_refresh", None),
            data_status=str(getattr(account, "data_status", "") or ""),
            connection_state=str(getattr(account, "connection_state", "") or ""),
            provider=str(getattr(account, "provider", "") or ""),
            now=now,
        )
        if decision.freshness == FRESHNESS_FRESH:
            counters.fresh += 1
        elif decision.freshness == FRESHNESS_STALE:
            counters.stale += 1
        else:
            counters.unavailable += 1
    return counters
