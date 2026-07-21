"""AttentionDelivery — primary-only channel fan-out + receipts (Milestone 4).

Targets AttentionState.primary only. Never raises to Home/Worker/sync callers.

See docs/ATTENTION_DELIVERY.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from mighty.attention import AttentionItem, AttentionUrgency
from mighty.attention_engine import read_attention
from mighty.attention_view import build_attention_view

logger = logging.getLogger(__name__)

# Urgencies that may attempt push (RFC §7 delivery SLA intent).
_PUSH_URGENCIES = frozenset(
    {
        AttentionUrgency.BLOCKER,
        AttentionUrgency.TIME_SENSITIVE,
    }
)

# Milestone 5 — retry / SLA constants.
MAX_DELIVERY_ATTEMPTS = 3
DELIVERY_RETRY_BACKOFF_SECONDS = 60
BLOCKER_DELIVERY_SLA_SECONDS = 60

PushSender = Callable[[str, str, str, str | None], bool]


@dataclass(frozen=True)
class DeliveryAttempt:
    user_id: str
    attention_id: str
    channel: str
    status: str  # delivered | failed | skipped
    detail: str | None = None
    attempt_count: int = 0


def ensure_attention_delivery_tables(db: Any, *, commit: bool = True) -> None:
    """Create attention delivery receipt table."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS attention_delivery_receipt (
            user_id      TEXT NOT NULL,
            attention_id TEXT NOT NULL,
            channel      TEXT NOT NULL,
            status       TEXT NOT NULL,
            attempted_at TEXT NOT NULL,
            detail       TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 1,
            first_attempted_at TEXT,
            PRIMARY KEY (user_id, attention_id, channel)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_attention_delivery_user "
        "ON attention_delivery_receipt(user_id)"
    )
    for ddl in (
        "ALTER TABLE attention_delivery_receipt ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE attention_delivery_receipt ADD COLUMN first_attempted_at TEXT",
    ):
        try:
            db.execute(ddl)
        except Exception as exc:  # noqa: BLE001 — sqlite duplicate column
            msg = str(exc).lower()
            if "duplicate column" not in msg and "already exists" not in msg:
                raise
    if commit:
        db.commit()


def get_delivery_receipt(
    db: Any,
    user_id: str,
    attention_id: str,
    channel: str = "push",
) -> dict[str, Any] | None:
    ensure_attention_delivery_tables(db, commit=False)
    row = db.execute(
        """
        SELECT user_id, attention_id, channel, status, attempted_at, detail,
               attempt_count, first_attempted_at
        FROM attention_delivery_receipt
        WHERE user_id = ? AND attention_id = ? AND channel = ?
        """,
        (str(user_id).strip(), str(attention_id).strip(), str(channel).strip()),
    ).fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return dict(row)
    return {
        "user_id": row[0],
        "attention_id": row[1],
        "channel": row[2],
        "status": row[3],
        "attempted_at": row[4],
        "detail": row[5],
        "attempt_count": row[6] if len(row) > 6 else 1,
        "first_attempted_at": row[7] if len(row) > 7 else row[4],
    }


def record_delivery_receipt(
    db: Any,
    *,
    user_id: str,
    attention_id: str,
    channel: str,
    status: str,
    now: datetime,
    detail: str | None = None,
    attempt_count: int = 1,
    first_attempted_at: str | None = None,
    commit: bool = True,
) -> None:
    ensure_attention_delivery_tables(db, commit=False)
    stamp = _ensure_aware(now).replace(microsecond=0).isoformat()
    first = first_attempted_at or stamp
    db.execute(
        """
        INSERT INTO attention_delivery_receipt (
            user_id, attention_id, channel, status, attempted_at, detail,
            attempt_count, first_attempted_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, attention_id, channel) DO UPDATE SET
            status=excluded.status,
            attempted_at=excluded.attempted_at,
            detail=excluded.detail,
            attempt_count=excluded.attempt_count,
            first_attempted_at=COALESCE(
                attention_delivery_receipt.first_attempted_at,
                excluded.first_attempted_at
            )
        """,
        (
            str(user_id).strip(),
            str(attention_id).strip(),
            str(channel).strip(),
            str(status).strip(),
            stamp,
            detail,
            int(attempt_count),
            first,
        ),
    )
    if commit:
        db.commit()


def deliver_attention_primary(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    state: Any | None = None,
    send_push: PushSender | None = None,
) -> DeliveryAttempt | None:
    """Attempt push delivery for AttentionState.primary when eligible.

    Returns None when there is nothing to deliver. Never raises.
    """
    try:
        return _deliver_attention_primary(
            db,
            user_id,
            now=now,
            state=state,
            send_push=send_push,
        )
    except Exception:
        logger.exception(
            "attention_delivery_failed user_id=%s", str(user_id or "").strip()
        )
        return DeliveryAttempt(
            user_id=str(user_id or "").strip(),
            attention_id="",
            channel="push",
            status="failed",
            detail="exception",
        )


def run_attention_delivery_sweep(
    db: Any,
    *,
    now: datetime,
    user_ids: list[str] | None = None,
    send_push: PushSender | None = None,
) -> int:
    """Attempt primary delivery for the given users. Returns attempt count."""
    try:
        if user_ids is None:
            user_ids = _list_enrolled_user_ids(db)
    except Exception:
        logger.exception("attention_delivery_sweep_init_failed")
        return 0

    attempts = 0
    for raw in user_ids:
        uid = str(raw or "").strip()
        if not uid:
            continue
        result = deliver_attention_primary(
            db, uid, now=now, send_push=send_push
        )
        if result is not None and result.status in {"delivered", "failed"}:
            attempts += 1
    return attempts


def _deliver_attention_primary(
    db: Any,
    user_id: str,
    *,
    now: datetime,
    state: Any | None,
    send_push: PushSender | None,
) -> DeliveryAttempt | None:
    uid = str(user_id or "").strip()
    if not uid:
        return None
    now = _ensure_aware(now)
    if state is None:
        state = read_attention(db, uid, now=now)
    primary = getattr(state, "primary", None)
    if primary is None:
        return None
    if not isinstance(primary, AttentionItem):
        return None
    if primary.urgency not in _PUSH_URGENCIES:
        return DeliveryAttempt(
            user_id=uid,
            attention_id=primary.attention_id,
            channel="push",
            status="skipped",
            detail="urgency_not_pushable",
        )

    existing = get_delivery_receipt(db, uid, primary.attention_id, "push")
    prior_attempts = int((existing or {}).get("attempt_count") or 0)
    first_attempted_at = (existing or {}).get("first_attempted_at") or (
        (existing or {}).get("attempted_at")
    )

    if existing and existing.get("status") == "delivered":
        return DeliveryAttempt(
            user_id=uid,
            attention_id=primary.attention_id,
            channel="push",
            status="skipped",
            detail="already_delivered",
            attempt_count=prior_attempts or 1,
        )

    if existing and existing.get("status") == "failed":
        if prior_attempts >= MAX_DELIVERY_ATTEMPTS:
            return DeliveryAttempt(
                user_id=uid,
                attention_id=primary.attention_id,
                channel="push",
                status="skipped",
                detail="max_retries",
                attempt_count=prior_attempts,
            )
        last = _parse_iso(existing.get("attempted_at"))
        if last is not None:
            age = (now - last).total_seconds()
            if age < DELIVERY_RETRY_BACKOFF_SECONDS:
                return DeliveryAttempt(
                    user_id=uid,
                    attention_id=primary.attention_id,
                    channel="push",
                    status="skipped",
                    detail="retry_backoff",
                    attempt_count=prior_attempts,
                )

    next_attempt = prior_attempts + 1 if prior_attempts else 1

    if send_push is None:
        record_delivery_receipt(
            db,
            user_id=uid,
            attention_id=primary.attention_id,
            channel="push",
            status="skipped",
            now=now,
            detail="no_push_sender",
            attempt_count=next_attempt,
            first_attempted_at=first_attempted_at,
        )
        return DeliveryAttempt(
            user_id=uid,
            attention_id=primary.attention_id,
            channel="push",
            status="skipped",
            detail="no_push_sender",
            attempt_count=next_attempt,
        )

    view = build_attention_view(state, surface="push")
    presentation = view.primary
    if presentation is None:
        return DeliveryAttempt(
            user_id=uid,
            attention_id=primary.attention_id,
            channel="push",
            status="skipped",
            detail="no_presentation",
            attempt_count=prior_attempts,
        )

    title = presentation.title
    body = presentation.body
    url = presentation.cta_url or "/"
    ok = False
    detail = None
    try:
        ok = bool(send_push(uid, title, body, url))
    except Exception as exc:
        detail = str(exc)[:200]
        ok = False

    status = "delivered" if ok else "failed"
    record_delivery_receipt(
        db,
        user_id=uid,
        attention_id=primary.attention_id,
        channel="push",
        status=status,
        now=now,
        detail=detail,
        attempt_count=next_attempt,
        first_attempted_at=first_attempted_at,
    )
    if (
        not ok
        and primary.urgency is AttentionUrgency.BLOCKER
        and first_attempted_at
    ):
        first_dt = _parse_iso(str(first_attempted_at))
        if first_dt is not None:
            elapsed = (now - first_dt).total_seconds()
            if elapsed >= BLOCKER_DELIVERY_SLA_SECONDS:
                logger.info(
                    "attention.sla_breached user_id=%s attention_id=%s "
                    "channel=push elapsed_s=%.0f",
                    uid,
                    primary.attention_id,
                    elapsed,
                )
    logger.info(
        "attention.%s user_id=%s attention_id=%s channel=push attempt=%s",
        "delivered" if ok else "delivery_failed",
        uid,
        primary.attention_id,
        next_attempt,
    )
    return DeliveryAttempt(
        user_id=uid,
        attention_id=primary.attention_id,
        channel="push",
        status=status,
        detail=detail,
        attempt_count=next_attempt,
    )


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _list_enrolled_user_ids(db: Any) -> list[str]:
    rows = db.execute(
        "SELECT DISTINCT user_id FROM account_state ORDER BY user_id ASC"
    ).fetchall()
    result: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            uid = row.get("user_id")
        else:
            try:
                uid = row["user_id"]
            except Exception:
                uid = row[0]
        text = str(uid or "").strip()
        if text:
            result.append(text)
    return result


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
