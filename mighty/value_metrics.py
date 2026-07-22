"""Value Intelligence metrics (Milestone 10)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.value_intelligence import ValueSweepCounters

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValueMetricSnapshot:
    providers: int
    generated: int
    suppressed: int
    expired: int
    duplicates_suppressed: int
    active: int
    value_at_risk_total: float
    errors: int
    computed_at: str


def compute_value_metrics(
    counters: ValueSweepCounters,
    *,
    now: datetime | None = None,
) -> ValueMetricSnapshot:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    snap = ValueMetricSnapshot(
        providers=counters.providers,
        generated=counters.generated,
        suppressed=counters.suppressed,
        expired=counters.expired,
        duplicates_suppressed=counters.duplicates_suppressed,
        active=counters.active,
        value_at_risk_total=float(counters.value_at_risk_total),
        errors=counters.errors,
        computed_at=now.replace(microsecond=0).isoformat(),
    )
    logger.info(
        "value.metrics providers=%s generated=%s suppressed=%s expired=%s "
        "dupes=%s active=%s var_total=%.2f",
        snap.providers,
        snap.generated,
        snap.suppressed,
        snap.expired,
        snap.duplicates_suppressed,
        snap.active,
        snap.value_at_risk_total,
    )
    return snap


def persist_value_metric_snapshot(
    db: Any, snapshot: ValueMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS value_metric_snapshot (
            scope TEXT PRIMARY KEY,
            providers INTEGER NOT NULL,
            generated INTEGER NOT NULL,
            suppressed INTEGER NOT NULL,
            expired INTEGER NOT NULL,
            duplicates_suppressed INTEGER NOT NULL,
            active INTEGER NOT NULL,
            value_at_risk_total REAL NOT NULL,
            errors INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO value_metric_snapshot (
            scope, providers, generated, suppressed, expired,
            duplicates_suppressed, active, value_at_risk_total, errors, computed_at
        ) VALUES ('global', ?,?,?,?,?,?,?,?,?)
        ON CONFLICT(scope) DO UPDATE SET
            providers=excluded.providers,
            generated=excluded.generated,
            suppressed=excluded.suppressed,
            expired=excluded.expired,
            duplicates_suppressed=excluded.duplicates_suppressed,
            active=excluded.active,
            value_at_risk_total=excluded.value_at_risk_total,
            errors=excluded.errors,
            computed_at=excluded.computed_at
        """,
        (
            snapshot.providers,
            snapshot.generated,
            snapshot.suppressed,
            snapshot.expired,
            snapshot.duplicates_suppressed,
            snapshot.active,
            snapshot.value_at_risk_total,
            snapshot.errors,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
