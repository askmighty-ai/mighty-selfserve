"""Recovery production metrics (Milestone 6).

Computed on Recovery Supervisor heartbeat — not GET hot paths.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoveryMetricSnapshot:
    autonomous_recovery_coverage: float
    unexpected_interruption_rate: float
    cases_active: int
    cases_escalated: int
    cases_succeeded: int
    computed_at: str


def compute_recovery_metrics(db: Any, *, now: datetime) -> RecoveryMetricSnapshot:
    """Compute recovery coverage and unexpected interruption rates."""
    now = _ensure_aware(now)
    stamp = now.replace(microsecond=0).isoformat()
    try:
        from mighty.recovery_store import ensure_recovery_tables

        ensure_recovery_tables(db, commit=False)
    except Exception:
        return RecoveryMetricSnapshot(0.0, 0.0, 0, 0, 0, stamp)

    try:
        active = _count(
            db,
            "SELECT COUNT(*) AS c FROM recovery_case "
            "WHERE status IN ('open', 'running', 'waiting')",
        )
        escalated = _count(
            db, "SELECT COUNT(*) AS c FROM recovery_case WHERE status = 'escalated'"
        )
        succeeded = _count(
            db, "SELECT COUNT(*) AS c FROM recovery_case WHERE status = 'succeeded'"
        )
        terminal = escalated + succeeded
        # Coverage: succeeded / (succeeded + escalated) among terminals.
        coverage = (succeeded / terminal) if terminal else 1.0
        # Unexpected interruption: escalations whose reason is not human_only.
        unexpected = _count(
            db,
            """
            SELECT COUNT(*) AS c FROM recovery_case
            WHERE status = 'escalated'
              AND (
                escalation_reason IS NULL
                OR escalation_reason NOT LIKE 'human_only:%'
              )
            """,
        )
        unexpected_rate = (unexpected / escalated) if escalated else 0.0
    except Exception:
        logger.exception("recovery_metrics_compute_failed")
        return RecoveryMetricSnapshot(0.0, 0.0, 0, 0, 0, stamp)

    return RecoveryMetricSnapshot(
        autonomous_recovery_coverage=coverage,
        unexpected_interruption_rate=unexpected_rate,
        cases_active=active,
        cases_escalated=escalated,
        cases_succeeded=succeeded,
        computed_at=stamp,
    )


def persist_recovery_metric_snapshot(
    db: Any, snapshot: RecoveryMetricSnapshot, *, commit: bool = True
) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_metric_snapshot (
            scope TEXT PRIMARY KEY,
            autonomous_recovery_coverage REAL NOT NULL,
            unexpected_interruption_rate REAL NOT NULL,
            cases_active INTEGER NOT NULL,
            cases_escalated INTEGER NOT NULL,
            cases_succeeded INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        INSERT INTO recovery_metric_snapshot (
            scope, autonomous_recovery_coverage, unexpected_interruption_rate,
            cases_active, cases_escalated, cases_succeeded, computed_at
        ) VALUES ('global', ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET
            autonomous_recovery_coverage = excluded.autonomous_recovery_coverage,
            unexpected_interruption_rate = excluded.unexpected_interruption_rate,
            cases_active = excluded.cases_active,
            cases_escalated = excluded.cases_escalated,
            cases_succeeded = excluded.cases_succeeded,
            computed_at = excluded.computed_at
        """,
        (
            snapshot.autonomous_recovery_coverage,
            snapshot.unexpected_interruption_rate,
            snapshot.cases_active,
            snapshot.cases_escalated,
            snapshot.cases_succeeded,
            snapshot.computed_at,
        ),
    )
    if commit:
        db.commit()
    logger.info(
        "recovery.metrics coverage=%.3f unexpected_interrupt=%.3f "
        "active=%s escalated=%s succeeded=%s",
        snapshot.autonomous_recovery_coverage,
        snapshot.unexpected_interruption_rate,
        snapshot.cases_active,
        snapshot.cases_escalated,
        snapshot.cases_succeeded,
    )


def _count(db: Any, sql: str) -> int:
    row = db.execute(sql).fetchone()
    if row is None:
        return 0
    try:
        return int(row["c"])
    except Exception:
        return int(row[0])


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
