"""Natural Session metrics (Milestone 8)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.natural_session import NaturalSessionSweepResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NaturalSessionMetricSnapshot:
    detections: int
    enqueued: int
    skipped_fresh: int
    deferred_recovery: int
    unsupported: int
    passive_coverage_rate: float
    computed_at: str


def compute_natural_session_metrics(
    sweep: NaturalSessionSweepResult, *, now: datetime
) -> NaturalSessionMetricSnapshot:
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    stamp = now.replace(microsecond=0).isoformat()
    capable = sweep.detections - sweep.unsupported
    # Coverage among capable providers: skipped_fresh or enqueued / capable
    covered = sweep.skipped_fresh + sweep.enqueued
    rate = (covered / capable) if capable > 0 else 1.0
    snap = NaturalSessionMetricSnapshot(
        detections=sweep.detections,
        enqueued=sweep.enqueued,
        skipped_fresh=sweep.skipped_fresh,
        deferred_recovery=sweep.deferred_recovery,
        unsupported=sweep.unsupported,
        passive_coverage_rate=rate,
        computed_at=stamp,
    )
    logger.info(
        "natural_session.metrics detections=%s enqueued=%s skipped=%s "
        "deferred_recovery=%s coverage=%.3f",
        snap.detections,
        snap.enqueued,
        snap.skipped_fresh,
        snap.deferred_recovery,
        snap.passive_coverage_rate,
    )
    return snap


def persist_natural_session_metric_snapshot(
    db: Any, snapshot: NaturalSessionMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS natural_session_metric_snapshot (
            scope TEXT PRIMARY KEY,
            detections INTEGER NOT NULL,
            enqueued INTEGER NOT NULL,
            skipped_fresh INTEGER NOT NULL,
            deferred_recovery INTEGER NOT NULL,
            unsupported INTEGER NOT NULL,
            passive_coverage_rate REAL NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO natural_session_metric_snapshot (
            scope, detections, enqueued, skipped_fresh, deferred_recovery,
            unsupported, passive_coverage_rate, computed_at
        ) VALUES ('global', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            detections = excluded.detections,
            enqueued = excluded.enqueued,
            skipped_fresh = excluded.skipped_fresh,
            deferred_recovery = excluded.deferred_recovery,
            unsupported = excluded.unsupported,
            passive_coverage_rate = excluded.passive_coverage_rate,
            computed_at = excluded.computed_at
        """,
        (
            snapshot.detections,
            snapshot.enqueued,
            snapshot.skipped_fresh,
            snapshot.deferred_recovery,
            snapshot.unsupported,
            snapshot.passive_coverage_rate,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
