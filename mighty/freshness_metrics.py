"""Freshness and Change Intelligence metrics (Milestone 9)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Sequence

from mighty.freshness_change import (
    FreshnessSweepCounters,
    SnapshotRefreshObservation,
)
from mighty.freshness_policy import (
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreshnessMetricSnapshot:
    accounts: int
    fresh: int
    stale: int
    unavailable: int
    freshness_rate: float
    stale_rate: float
    refreshes: int
    meaningful_changes: int
    meaningful_change_rate: float
    duplicates_suppressed: int
    quiet_refreshes: int
    newly_discovered: int
    avg_refresh_latency_seconds: float | None
    avg_first_data_latency_seconds: float | None
    computed_at: str


def _rate(num: int, den: int) -> float:
    if den <= 0:
        return 1.0 if num == 0 else 0.0
    return num / den


def _avg(samples: Sequence[float]) -> float | None:
    if not samples:
        return None
    return float(mean(samples))


def apply_refresh_observation(
    counters: FreshnessSweepCounters,
    obs: SnapshotRefreshObservation,
) -> None:
    if obs.error:
        counters.errors += 1
        return
    counters.refreshes += 1
    event = obs.event
    if event is None:
        return
    if event.outcome == STATE_MATERIALLY_CHANGED:
        counters.meaningful += 1
    elif event.outcome == STATE_NEWLY_DISCOVERED:
        counters.newly_discovered += 1
        counters.meaningful += 1
    elif event.outcome == STATE_REFRESHED_NO_MEANINGFUL:
        counters.quiet_refreshes += 1
    counters.duplicates_suppressed += int(event.duplicates_suppressed or 0)
    if obs.refresh_latency_seconds is not None:
        counters.refresh_latency_samples.append(obs.refresh_latency_seconds)
    if obs.first_data and obs.refresh_latency_seconds is not None:
        counters.first_data_latency_samples.append(obs.refresh_latency_seconds)


def compute_freshness_metrics(
    counters: FreshnessSweepCounters,
    *,
    now: datetime | None = None,
) -> FreshnessMetricSnapshot:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.replace(microsecond=0).isoformat()
    usable = counters.fresh + counters.stale
    snap = FreshnessMetricSnapshot(
        accounts=counters.accounts,
        fresh=counters.fresh,
        stale=counters.stale,
        unavailable=counters.unavailable,
        freshness_rate=_rate(counters.fresh, usable if usable else counters.accounts),
        stale_rate=_rate(counters.stale, usable if usable else counters.accounts),
        refreshes=counters.refreshes,
        meaningful_changes=counters.meaningful,
        meaningful_change_rate=_rate(counters.meaningful, counters.refreshes),
        duplicates_suppressed=counters.duplicates_suppressed,
        quiet_refreshes=counters.quiet_refreshes,
        newly_discovered=counters.newly_discovered,
        avg_refresh_latency_seconds=_avg(counters.refresh_latency_samples),
        avg_first_data_latency_seconds=_avg(counters.first_data_latency_samples),
        computed_at=stamp,
    )
    logger.info(
        "freshness.metrics accounts=%s fresh=%.3f stale=%.3f refreshes=%s "
        "meaningful_rate=%.3f dupes=%s",
        snap.accounts,
        snap.freshness_rate,
        snap.stale_rate,
        snap.refreshes,
        snap.meaningful_change_rate,
        snap.duplicates_suppressed,
    )
    return snap


def persist_freshness_metric_snapshot(
    db: Any, snapshot: FreshnessMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS freshness_metric_snapshot (
            scope TEXT PRIMARY KEY,
            accounts INTEGER NOT NULL,
            fresh INTEGER NOT NULL,
            stale INTEGER NOT NULL,
            unavailable INTEGER NOT NULL,
            freshness_rate REAL NOT NULL,
            stale_rate REAL NOT NULL,
            refreshes INTEGER NOT NULL,
            meaningful_changes INTEGER NOT NULL,
            meaningful_change_rate REAL NOT NULL,
            duplicates_suppressed INTEGER NOT NULL,
            quiet_refreshes INTEGER NOT NULL,
            newly_discovered INTEGER NOT NULL,
            avg_refresh_latency_seconds REAL,
            avg_first_data_latency_seconds REAL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO freshness_metric_snapshot (
            scope, accounts, fresh, stale, unavailable, freshness_rate, stale_rate,
            refreshes, meaningful_changes, meaningful_change_rate,
            duplicates_suppressed, quiet_refreshes, newly_discovered,
            avg_refresh_latency_seconds, avg_first_data_latency_seconds, computed_at
        ) VALUES (
            'global', ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        ON CONFLICT(scope) DO UPDATE SET
            accounts=excluded.accounts,
            fresh=excluded.fresh,
            stale=excluded.stale,
            unavailable=excluded.unavailable,
            freshness_rate=excluded.freshness_rate,
            stale_rate=excluded.stale_rate,
            refreshes=excluded.refreshes,
            meaningful_changes=excluded.meaningful_changes,
            meaningful_change_rate=excluded.meaningful_change_rate,
            duplicates_suppressed=excluded.duplicates_suppressed,
            quiet_refreshes=excluded.quiet_refreshes,
            newly_discovered=excluded.newly_discovered,
            avg_refresh_latency_seconds=excluded.avg_refresh_latency_seconds,
            avg_first_data_latency_seconds=excluded.avg_first_data_latency_seconds,
            computed_at=excluded.computed_at
        """,
        (
            snapshot.accounts,
            snapshot.fresh,
            snapshot.stale,
            snapshot.unavailable,
            snapshot.freshness_rate,
            snapshot.stale_rate,
            snapshot.refreshes,
            snapshot.meaningful_changes,
            snapshot.meaningful_change_rate,
            snapshot.duplicates_suppressed,
            snapshot.quiet_refreshes,
            snapshot.newly_discovered,
            snapshot.avg_refresh_latency_seconds,
            snapshot.avg_first_data_latency_seconds,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
