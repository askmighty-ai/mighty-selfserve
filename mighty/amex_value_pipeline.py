"""Amex value-pipeline stage instrumentation (Session 2 Learning Blocker).

Stages mirror the Founder-mandated path:

  extension observes Amex → response captured → payload accepted →
  account associated → extraction job → extraction terminal →
  normalized data persisted → dashboard projection

Used for diagnostics only — not customer philosophy copy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mighty.admin_local_time import to_utc_iso_z

PIPELINE_STAGES: tuple[str, ...] = (
    "extension_observes_amex",
    "response_captured",
    "payload_accepted",
    "account_associated",
    "extraction_job_created",
    "extraction_terminal",
    "normalized_data_persisted",
    "dashboard_projection",
)

STAGE_LABELS: dict[str, str] = {
    "extension_observes_amex": "1. Extension observes Amex",
    "response_captured": "2. Relevant response captured",
    "payload_accepted": "3. Payload accepted by backend",
    "account_associated": "4. Account associated correctly",
    "extraction_job_created": "5. Extraction job created",
    "extraction_terminal": "6. Extraction completed or failed",
    "normalized_data_persisted": "7. Normalized data persisted",
    "dashboard_projection": "8. Dashboard projection rendered",
}


def ensure_pipeline_table(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS amex_value_pipeline_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 1,
            detail TEXT,
            source TEXT,
            access_cycle_id TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_amex_pipeline_user_created "
        "ON amex_value_pipeline_events(user_id, created_at)"
    )
    try:
        db.commit()
    except Exception:
        pass


def _utc_now() -> str:
    return to_utc_iso_z(datetime.now(timezone.utc))


def record_pipeline_event(
    db: Any,
    user_id: str,
    stage: str,
    *,
    ok: bool = True,
    detail: str | None = None,
    source: str = "unknown",
    access_cycle_id: str | None = None,
) -> dict[str, Any]:
    ensure_pipeline_table(db)
    stage = (stage or "").strip()[:80]
    if stage not in PIPELINE_STAGES and stage != "note":
        stage = stage[:80] or "note"
    created = _utc_now()
    db.execute(
        "INSERT INTO amex_value_pipeline_events"
        "(user_id, stage, ok, detail, source, access_cycle_id, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            user_id,
            stage,
            1 if ok else 0,
            (detail or "")[:500],
            (source or "unknown")[:40],
            (access_cycle_id or "")[:80] or None,
            created,
        ),
    )
    db.commit()
    return {
        "stage": stage,
        "ok": ok,
        "detail": detail,
        "source": source,
        "access_cycle_id": access_cycle_id,
        "created_at": created,
    }


def summarize_pipeline(db: Any, user_id: str, *, limit: int = 40) -> dict[str, Any]:
    """Latest event per known stage + first failing stage for beta diagnostics."""
    ensure_pipeline_table(db)
    rows = db.execute(
        """
        SELECT stage, ok, detail, source, access_cycle_id, created_at
        FROM amex_value_pipeline_events
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, max(1, min(int(limit), 200))),
    ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        stage = str(row["stage"] or "")
        if stage in latest:
            continue
        latest[stage] = {
            "stage": stage,
            "label": STAGE_LABELS.get(stage, stage),
            "ok": bool(row["ok"]),
            "detail": row["detail"],
            "source": row["source"],
            "access_cycle_id": row["access_cycle_id"],
            "created_at": row["created_at"],
        }
    ordered = []
    first_fail: str | None = None
    for stage in PIPELINE_STAGES:
        entry = latest.get(stage)
        if entry is None:
            ordered.append(
                {
                    "stage": stage,
                    "label": STAGE_LABELS[stage],
                    "ok": None,
                    "detail": None,
                    "source": None,
                    "access_cycle_id": None,
                    "created_at": None,
                }
            )
            if first_fail is None:
                first_fail = stage
            continue
        ordered.append(entry)
        if first_fail is None and entry["ok"] is False:
            first_fail = stage
    return {
        "stages": ordered,
        "first_failing_stage": first_fail,
        "events_sampled": len(rows or []),
    }
