"""Discovery metrics (Milestone 7) — heartbeat/offline snapshots, not GET hot path."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoveryMetricSnapshot:
    discovered: int
    auto_enrolled: int
    ambiguous: int
    already_enrolled: int
    dismissed: int
    ignored: int
    computed_at: str


def compute_discovery_metrics(db: Any, *, now: datetime) -> DiscoveryMetricSnapshot:
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    stamp = now.replace(microsecond=0).isoformat()
    try:
        from mighty.discovery_store import ensure_discovery_tables

        ensure_discovery_tables(db, commit=False)
    except Exception:
        return DiscoveryMetricSnapshot(0, 0, 0, 0, 0, 0, stamp)

    def _count(disposition: str) -> int:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM account_discovery WHERE disposition = ?",
            (disposition,),
        ).fetchone()
        try:
            return int(row["c"])
        except Exception:
            return int(row[0]) if row else 0

    try:
        snap = DiscoveryMetricSnapshot(
            discovered=_count("discovered") + _count("eligible"),
            auto_enrolled=_count("enrolled"),
            ambiguous=_count("ambiguous"),
            already_enrolled=_count("already_enrolled"),
            dismissed=_count("dismissed"),
            ignored=_count("ignored"),
            computed_at=stamp,
        )
    except Exception:
        logger.exception("discovery_metrics_compute_failed")
        return DiscoveryMetricSnapshot(0, 0, 0, 0, 0, 0, stamp)

    logger.info(
        "discovery.metrics discovered=%s enrolled=%s ambiguous=%s "
        "already_enrolled=%s dismissed=%s ignored=%s",
        snap.discovered,
        snap.auto_enrolled,
        snap.ambiguous,
        snap.already_enrolled,
        snap.dismissed,
        snap.ignored,
    )
    return snap


def persist_discovery_metric_snapshot(
    db: Any, snapshot: DiscoveryMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_metric_snapshot (
            scope TEXT PRIMARY KEY,
            discovered INTEGER NOT NULL,
            auto_enrolled INTEGER NOT NULL,
            ambiguous INTEGER NOT NULL,
            already_enrolled INTEGER NOT NULL,
            dismissed INTEGER NOT NULL,
            ignored INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO discovery_metric_snapshot (
            scope, discovered, auto_enrolled, ambiguous, already_enrolled,
            dismissed, ignored, computed_at
        ) VALUES ('global', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            discovered = excluded.discovered,
            auto_enrolled = excluded.auto_enrolled,
            ambiguous = excluded.ambiguous,
            already_enrolled = excluded.already_enrolled,
            dismissed = excluded.dismissed,
            ignored = excluded.ignored,
            computed_at = excluded.computed_at
        """,
        (
            snapshot.discovered,
            snapshot.auto_enrolled,
            snapshot.ambiguous,
            snapshot.already_enrolled,
            snapshot.dismissed,
            snapshot.ignored,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
