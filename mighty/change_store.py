"""Durable account change reports derived from snapshot diffs (Milestone 9).

Not a parallel history of every mutation — stores change *events* with
dedupe by field fingerprint so the same delta is not re-reported.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from mighty.change_intelligence import ChangeVerdict, FieldDelta
from mighty.freshness_policy import (
    STATE_MATERIALLY_CHANGED,
    STATE_NEWLY_DISCOVERED,
    STATE_REFRESHED_NO_MEANINGFUL,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_change_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class AccountChangeEvent:
    change_id: str
    user_id: str
    provider: str
    snapshot_id: str
    prev_snapshot_id: str | None
    outcome: str
    summary: str
    fields: tuple[dict[str, Any], ...]
    change_fingerprint: str
    created_at: str
    suppressed: bool
    meaningful_count: int
    duplicates_suppressed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "user_id": self.user_id,
            "provider": self.provider,
            "snapshot_id": self.snapshot_id,
            "prev_snapshot_id": self.prev_snapshot_id,
            "outcome": self.outcome,
            "summary": self.summary,
            "fields": list(self.fields),
            "change_fingerprint": self.change_fingerprint,
            "created_at": self.created_at,
            "suppressed": self.suppressed,
            "meaningful_count": self.meaningful_count,
            "duplicates_suppressed": self.duplicates_suppressed,
        }


def ensure_change_tables(db: Any, *, commit: bool = True) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_changes (
            change_id           TEXT PRIMARY KEY,
            user_id             TEXT NOT NULL,
            provider            TEXT NOT NULL,
            snapshot_id         TEXT NOT NULL,
            prev_snapshot_id    TEXT,
            outcome             TEXT NOT NULL,
            summary             TEXT NOT NULL DEFAULT '',
            fields_json         TEXT NOT NULL,
            change_fingerprint  TEXT NOT NULL DEFAULT '',
            created_at          TEXT NOT NULL,
            suppressed          INTEGER NOT NULL DEFAULT 0,
            meaningful_count    INTEGER NOT NULL DEFAULT 0,
            duplicates_suppressed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_changes_user_provider "
        "ON account_changes(user_id, provider, created_at DESC)"
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_change_fingerprints (
            user_id     TEXT NOT NULL,
            provider    TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            change_id   TEXT NOT NULL,
            field_key   TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            is_current  INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (user_id, provider, fingerprint)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_account_change_fp_current "
        "ON account_change_fingerprints(user_id, provider, is_current)"
    )
    if commit:
        db.commit()


def _current_fingerprints(
    db: Any, user_id: str, provider: str
) -> set[str]:
    rows = db.execute(
        """
        SELECT fingerprint FROM account_change_fingerprints
        WHERE user_id=? AND provider=? AND is_current=1
        """,
        (user_id, provider),
    ).fetchall()
    return {str(r["fingerprint"]) for r in rows}


def _mark_field_key_not_current(
    db: Any, user_id: str, provider: str, field_key: str
) -> None:
    db.execute(
        """
        UPDATE account_change_fingerprints
        SET is_current=0
        WHERE user_id=? AND provider=? AND field_key=? AND is_current=1
        """,
        (user_id, provider, field_key),
    )


def persist_change_event(
    db: Any,
    *,
    user_id: str,
    provider: str,
    snapshot_id: str,
    prev_snapshot_id: str | None,
    verdict: ChangeVerdict,
    created_at: str | None = None,
    commit: bool = True,
) -> AccountChangeEvent:
    """Persist a change event; suppress duplicate meaningful fingerprints."""
    ensure_change_tables(db, commit=False)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    stamp = created_at or utc_now_iso()

    meaningful = [d for d in verdict.deltas if d.meaningful]
    known = _current_fingerprints(db, uid, prov)
    novel: list[FieldDelta] = []
    dupes = 0
    for delta in meaningful:
        if delta.fingerprint in known:
            dupes += 1
        else:
            novel.append(delta)

    # First snapshot / quiet refresh still recorded (not suppressed) for history.
    outcome = verdict.outcome
    suppressed = False
    summary = verdict.summary
    fields_to_store: Sequence[FieldDelta] = verdict.deltas

    if outcome == STATE_MATERIALLY_CHANGED and not novel and meaningful:
        suppressed = True
        summary = ""
        outcome = STATE_REFRESHED_NO_MEANINGFUL
        fields_to_store = ()
    elif outcome == STATE_MATERIALLY_CHANGED and novel:
        # Only surface novel meaningful deltas in the durable summary fields.
        fields_to_store = tuple(novel) + tuple(
            d for d in verdict.deltas if not d.meaningful
        )
        from mighty.change_intelligence import summarize_meaningful_deltas

        summary = summarize_meaningful_deltas(
            prov, STATE_MATERIALLY_CHANGED, novel
        )

    change_id = new_change_id()
    fields_payload = [d.to_dict() for d in fields_to_store]
    db.execute(
        """
        INSERT INTO account_changes (
            change_id, user_id, provider, snapshot_id, prev_snapshot_id,
            outcome, summary, fields_json, change_fingerprint, created_at,
            suppressed, meaningful_count, duplicates_suppressed
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            change_id,
            uid,
            prov,
            snapshot_id,
            prev_snapshot_id,
            outcome,
            summary,
            json.dumps(fields_payload, default=str),
            verdict.change_fingerprint,
            stamp,
            1 if suppressed else 0,
            len(novel) if outcome == STATE_MATERIALLY_CHANGED else verdict.meaningful_count,
            dupes,
        ),
    )

    if outcome in {STATE_MATERIALLY_CHANGED, STATE_NEWLY_DISCOVERED}:
        for delta in novel if outcome == STATE_MATERIALLY_CHANGED else meaningful:
            _mark_field_key_not_current(db, uid, prov, delta.field_key)
            db.execute(
                """
                INSERT INTO account_change_fingerprints (
                    user_id, provider, fingerprint, change_id, field_key,
                    created_at, is_current
                ) VALUES (?,?,?,?,?,?,1)
                ON CONFLICT(user_id, provider, fingerprint) DO UPDATE SET
                    change_id=excluded.change_id,
                    field_key=excluded.field_key,
                    created_at=excluded.created_at,
                    is_current=1
                """,
                (
                    uid,
                    prov,
                    delta.fingerprint,
                    change_id,
                    delta.field_key,
                    stamp,
                ),
            )

    if commit:
        db.commit()

    return AccountChangeEvent(
        change_id=change_id,
        user_id=uid,
        provider=prov,
        snapshot_id=snapshot_id,
        prev_snapshot_id=prev_snapshot_id,
        outcome=outcome,
        summary=summary,
        fields=tuple(fields_payload),
        change_fingerprint=verdict.change_fingerprint,
        created_at=stamp,
        suppressed=suppressed,
        meaningful_count=len(novel)
        if outcome == STATE_MATERIALLY_CHANGED
        else (
            verdict.meaningful_count
            if outcome == STATE_NEWLY_DISCOVERED
            else 0
        ),
        duplicates_suppressed=dupes,
    )


def _row_to_event(row: Any) -> AccountChangeEvent:
    fields = json.loads(row["fields_json"] or "[]")
    return AccountChangeEvent(
        change_id=row["change_id"],
        user_id=row["user_id"],
        provider=row["provider"],
        snapshot_id=row["snapshot_id"],
        prev_snapshot_id=row["prev_snapshot_id"],
        outcome=row["outcome"],
        summary=row["summary"] or "",
        fields=tuple(fields if isinstance(fields, list) else []),
        change_fingerprint=row["change_fingerprint"] or "",
        created_at=row["created_at"],
        suppressed=bool(row["suppressed"]),
        meaningful_count=int(row["meaningful_count"] or 0),
        duplicates_suppressed=int(row["duplicates_suppressed"] or 0),
    )


def list_account_changes(
    db: Any,
    user_id: str,
    provider: str | None = None,
    *,
    limit: int = 50,
    include_suppressed: bool = False,
    meaningful_only: bool = False,
) -> list[AccountChangeEvent]:
    ensure_change_tables(db, commit=False)
    clauses = ["user_id=?"]
    params: list[Any] = [user_id]
    if provider:
        clauses.append("provider=?")
        params.append(str(provider).strip().lower())
    if not include_suppressed:
        clauses.append("suppressed=0")
    if meaningful_only:
        clauses.append(
            f"outcome IN ('{STATE_MATERIALLY_CHANGED}','{STATE_NEWLY_DISCOVERED}')"
        )
        clauses.append("summary != ''")
    where = " AND ".join(clauses)
    rows = db.execute(
        f"""
        SELECT * FROM account_changes
        WHERE {where}
        ORDER BY created_at DESC, change_id DESC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [_row_to_event(r) for r in rows]


def latest_change_summary(
    db: Any, user_id: str, provider: str
) -> str | None:
    events = list_account_changes(
        db, user_id, provider, limit=1, meaningful_only=True
    )
    if not events:
        return None
    summary = (events[0].summary or "").strip()
    return summary or None


def change_alerts_from_store(
    db: Any, user_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    """Presentation-ready alerts for Home/Account bridges (no re-ranking)."""
    events = list_account_changes(
        db, user_id, limit=limit, meaningful_only=True
    )
    alerts: list[dict[str, Any]] = []
    for event in events:
        if not event.summary:
            continue
        alerts.append(
            {
                "type": event.outcome,
                "urgency": "info",
                "source": event.provider,
                "label": event.provider,
                "message": event.summary,
                "detail": f"{event.meaningful_count} meaningful field(s)",
                "changed_at": event.created_at,
                "change_id": event.change_id,
            }
        )
    return alerts
