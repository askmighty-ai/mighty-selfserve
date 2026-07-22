"""Recovery Store — lifecycle state + attempt history (Milestone 6).

One owner for recovery lifecycle. Does not rank attention or invent auth
evidence. See docs/ATTENTION_AUTONOMOUS_RECOVERY.md.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from mighty.recovery_planner import (
    AttemptOutcome,
    RecoveryAttemptRecord,
    RecoveryCapability,
    RecoveryHistory,
)

ACTIVE_STATUSES: frozenset[str] = frozenset({"open", "running", "waiting"})
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "escalated", "cancelled"}
)


@dataclass(frozen=True)
class RecoveryCase:
    case_id: str
    user_id: str
    provider: str
    root_cause: str
    status: str
    escalation_reason: str | None
    next_attempt_at: str | None
    created_at: str
    updated_at: str


def ensure_recovery_tables(db: Any, *, commit: bool = True) -> None:
    """Create recovery lifecycle tables."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_case (
            case_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            root_cause TEXT NOT NULL,
            status TEXT NOT NULL,
            escalation_reason TEXT,
            next_attempt_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_recovery_case_active
        ON recovery_case(user_id, provider, root_cause)
        WHERE status IN ('open', 'running', 'waiting')
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_case_user
        ON recovery_case(user_id)
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_attempt (
            attempt_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            outcome TEXT NOT NULL,
            detail_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (case_id) REFERENCES recovery_case(case_id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_attempt_case
        ON recovery_attempt(case_id, created_at)
        """
    )
    if commit:
        db.commit()


def claim_or_get_active_case(
    db: Any,
    *,
    user_id: str,
    provider: str,
    root_cause: str,
    now: datetime,
) -> RecoveryCase:
    """Return the active case or create one. Enforces single active owner."""
    ensure_recovery_tables(db, commit=False)
    uid = str(user_id or "").strip()
    prov = str(provider or "").strip().lower()
    cause = str(root_cause or "").strip().lower()
    if not uid or not prov or not cause:
        raise ValueError("user_id, provider, and root_cause are required")

    existing = get_active_case(db, user_id=uid, provider=prov, root_cause=cause)
    if existing is not None:
        return existing

    now = _ensure_aware(now)
    stamp = _iso(now)
    case_id = f"rc_{uuid.uuid4().hex[:16]}"
    try:
        db.execute(
            """
            INSERT INTO recovery_case (
                case_id, user_id, provider, root_cause, status,
                escalation_reason, next_attempt_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'open', NULL, NULL, ?, ?)
            """,
            (case_id, uid, prov, cause, stamp, stamp),
        )
        db.commit()
    except Exception:
        # Unique active index race — re-read.
        db.rollback()
        existing = get_active_case(db, user_id=uid, provider=prov, root_cause=cause)
        if existing is not None:
            return existing
        raise
    return RecoveryCase(
        case_id=case_id,
        user_id=uid,
        provider=prov,
        root_cause=cause,
        status="open",
        escalation_reason=None,
        next_attempt_at=None,
        created_at=stamp,
        updated_at=stamp,
    )


def get_active_case(
    db: Any,
    *,
    user_id: str,
    provider: str,
    root_cause: str,
) -> RecoveryCase | None:
    ensure_recovery_tables(db, commit=False)
    row = db.execute(
        """
        SELECT case_id, user_id, provider, root_cause, status,
               escalation_reason, next_attempt_at, created_at, updated_at
        FROM recovery_case
        WHERE user_id = ? AND provider = ? AND root_cause = ?
          AND status IN ('open', 'running', 'waiting')
        LIMIT 1
        """,
        (
            str(user_id).strip(),
            str(provider).strip().lower(),
            str(root_cause).strip().lower(),
        ),
    ).fetchone()
    return _case_from_row(row) if row else None


def get_case(db: Any, case_id: str) -> RecoveryCase | None:
    ensure_recovery_tables(db, commit=False)
    row = db.execute(
        """
        SELECT case_id, user_id, provider, root_cause, status,
               escalation_reason, next_attempt_at, created_at, updated_at
        FROM recovery_case
        WHERE case_id = ?
        """,
        (str(case_id),),
    ).fetchone()
    return _case_from_row(row) if row else None


def list_active_cases_for_user(db: Any, user_id: str) -> list[RecoveryCase]:
    ensure_recovery_tables(db, commit=False)
    rows = db.execute(
        """
        SELECT case_id, user_id, provider, root_cause, status,
               escalation_reason, next_attempt_at, created_at, updated_at
        FROM recovery_case
        WHERE user_id = ? AND status IN ('open', 'running', 'waiting')
        ORDER BY created_at ASC, case_id ASC
        """,
        (str(user_id).strip(),),
    ).fetchall()
    return [_case_from_row(r) for r in rows if r]


def has_active_recovery_for_provider(
    db: Any, user_id: str, provider: str
) -> bool:
    """True when Recovery owns an active case for this provider (any root_cause)."""
    prov = str(provider or "").strip().lower()
    if not prov:
        return False
    return any(
        case.provider == prov for case in list_active_cases_for_user(db, user_id)
    )


def list_escalated_providers(db: Any, user_id: str) -> set[str]:
    """Providers with a latest terminal escalation (for Attention gate)."""
    ensure_recovery_tables(db, commit=False)
    uid = str(user_id).strip()
    rows = db.execute(
        """
        SELECT provider, status, updated_at
        FROM recovery_case
        WHERE user_id = ?
          AND status IN ('escalated', 'succeeded', 'cancelled')
        ORDER BY updated_at DESC, case_id DESC
        """,
        (uid,),
    ).fetchall()
    latest: dict[str, str] = {}
    for row in rows:
        mapping = _row_mapping(row)
        provider = str(mapping.get("provider") or "").strip().lower()
        if not provider or provider in latest:
            continue
        latest[provider] = str(mapping.get("status") or "")
    return {p for p, status in latest.items() if status == "escalated"}


def provider_allows_attention(db: Any, user_id: str, provider: str) -> bool:
    """True when Attention may emit for this provider.

    - Active recovery → False (suppress)
    - Latest terminal escalated → True
    - Latest terminal succeeded/cancelled → False (failure cleared path)
    - No case yet → False (supervisor will open; do not interrupt first)
    """
    ensure_recovery_tables(db, commit=False)
    uid = str(user_id).strip()
    prov = str(provider).strip().lower()
    active = db.execute(
        """
        SELECT 1 FROM recovery_case
        WHERE user_id = ? AND provider = ?
          AND status IN ('open', 'running', 'waiting')
        LIMIT 1
        """,
        (uid, prov),
    ).fetchone()
    if active:
        return False
    row = db.execute(
        """
        SELECT status FROM recovery_case
        WHERE user_id = ? AND provider = ?
          AND status IN ('escalated', 'succeeded', 'cancelled')
        ORDER BY updated_at DESC, case_id DESC
        LIMIT 1
        """,
        (uid, prov),
    ).fetchone()
    if not row:
        return False
    status = str(_row_mapping(row).get("status") or "")
    return status == "escalated"


def transition_case(
    db: Any,
    case_id: str,
    *,
    status: str,
    now: datetime,
    escalation_reason: str | None = None,
    next_attempt_at: str | None = None,
    clear_next_attempt: bool = False,
) -> RecoveryCase:
    ensure_recovery_tables(db, commit=False)
    now = _ensure_aware(now)
    stamp = _iso(now)
    case = get_case(db, case_id)
    if case is None:
        raise ValueError(f"unknown recovery case: {case_id}")

    next_at = None if clear_next_attempt else (
        next_attempt_at if next_attempt_at is not None else case.next_attempt_at
    )
    reason = (
        escalation_reason
        if escalation_reason is not None
        else case.escalation_reason
    )
    db.execute(
        """
        UPDATE recovery_case
        SET status = ?, escalation_reason = ?, next_attempt_at = ?, updated_at = ?
        WHERE case_id = ?
        """,
        (status, reason, next_at, stamp, case_id),
    )
    db.commit()
    updated = get_case(db, case_id)
    assert updated is not None
    return updated


def append_attempt(
    db: Any,
    case_id: str,
    *,
    capability: RecoveryCapability | str,
    outcome: AttemptOutcome | str,
    now: datetime,
    detail: dict[str, Any] | None = None,
) -> str:
    ensure_recovery_tables(db, commit=False)
    now = _ensure_aware(now)
    attempt_id = f"ra_{uuid.uuid4().hex[:16]}"
    cap = (
        capability.value
        if isinstance(capability, RecoveryCapability)
        else str(capability)
    )
    out = outcome.value if isinstance(outcome, AttemptOutcome) else str(outcome)
    db.execute(
        """
        INSERT INTO recovery_attempt (
            attempt_id, case_id, capability, outcome, detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            attempt_id,
            case_id,
            cap,
            out,
            json.dumps(detail or {}, sort_keys=True),
            _iso(now),
        ),
    )
    db.commit()
    return attempt_id


def load_history(db: Any, case_id: str) -> RecoveryHistory:
    ensure_recovery_tables(db, commit=False)
    rows = db.execute(
        """
        SELECT capability, outcome
        FROM recovery_attempt
        WHERE case_id = ?
        ORDER BY created_at ASC, attempt_id ASC
        """,
        (str(case_id),),
    ).fetchall()
    attempts: list[RecoveryAttemptRecord] = []
    for row in rows:
        mapping = _row_mapping(row)
        try:
            cap = RecoveryCapability(str(mapping.get("capability") or ""))
            outcome = AttemptOutcome(str(mapping.get("outcome") or ""))
        except ValueError:
            continue
        attempts.append(RecoveryAttemptRecord(capability=cap, outcome=outcome))
    return RecoveryHistory(attempts=tuple(attempts))


def list_recovery_user_ids(db: Any) -> list[str]:
    ensure_recovery_tables(db, commit=False)
    rows = db.execute(
        """
        SELECT DISTINCT user_id FROM recovery_case
        ORDER BY user_id ASC
        """
    ).fetchall()
    result: list[str] = []
    for row in rows:
        mapping = _row_mapping(row)
        uid = str(mapping.get("user_id") or "").strip()
        if uid:
            result.append(uid)
    return result


def _case_from_row(row: Any) -> RecoveryCase:
    mapping = _row_mapping(row)
    return RecoveryCase(
        case_id=str(mapping.get("case_id") or ""),
        user_id=str(mapping.get("user_id") or ""),
        provider=str(mapping.get("provider") or ""),
        root_cause=str(mapping.get("root_cause") or ""),
        status=str(mapping.get("status") or ""),
        escalation_reason=_optional_str(mapping.get("escalation_reason")),
        next_attempt_at=_optional_str(mapping.get("next_attempt_at")),
        created_at=str(mapping.get("created_at") or ""),
        updated_at=str(mapping.get("updated_at") or ""),
    )


def _row_mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        keys = getattr(row, "keys", None)
        if callable(keys):
            return {k: row[k] for k in keys()}
        return {}


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()
