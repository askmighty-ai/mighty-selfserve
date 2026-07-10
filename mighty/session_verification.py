"""Background session verification lifecycle — separate from provider_session_state.

Freshness of Current Access is a read-model concern. When session evidence is
stale, surfaces may request a background verification through the extension.
That request is recorded here; it must not rewrite provider_session_state by
itself. Only explicit session evidence from the verifier may update PSS.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from mighty.provider_access_probe import PROBE_PROVIDERS

VerificationLifecycle = Literal[
    "requested",
    "running",
    "completed",
    "failed",
    "timed_out",
]

ACTIVE_VERIFICATION_LIFECYCLES = frozenset({"requested", "running"})
TERMINAL_VERIFICATION_LIFECYCLES = frozenset({"completed", "failed", "timed_out"})

# Do not enqueue another verification for the same provider within this window.
VERIFICATION_THROTTLE_SECONDS = 60
# Mark requested/running jobs as timed_out after this period.
VERIFICATION_TIMEOUT_SECONDS = 25

# Amex operational entry for automatic session verification.
AMEX_SESSION_VERIFICATION_ENTRY_URL = "https://global.americanexpress.com/overview"

SESSION_VERIFICATION_ENTRY_URLS: dict[str, str] = {
    "amex": AMEX_SESSION_VERIFICATION_ENTRY_URL,
}


@dataclass(frozen=True)
class SessionVerification:
    verification_id: str
    provider: str
    lifecycle: VerificationLifecycle
    requested_at: str | None
    started_at: str | None = None
    completed_at: str | None = None
    error_message: str | None = None
    entry_url: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def ensure_session_verification_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_session_verification (
            verification_id  TEXT PRIMARY KEY,
            user_id          TEXT NOT NULL,
            provider         TEXT NOT NULL,
            lifecycle        TEXT NOT NULL,
            entry_url        TEXT,
            error_message    TEXT,
            requested_at     TEXT NOT NULL,
            started_at       TEXT,
            completed_at     TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_psv_user_provider_requested "
        "ON provider_session_verification(user_id, provider, requested_at DESC)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_psv_user_lifecycle "
        "ON provider_session_verification(user_id, lifecycle, requested_at DESC)"
    )
    db.commit()


def _row_to_verification(row: dict[str, Any] | None) -> SessionVerification | None:
    if not row:
        return None
    return SessionVerification(
        verification_id=row["verification_id"],
        provider=row["provider"],
        lifecycle=row["lifecycle"],  # type: ignore[arg-type]
        requested_at=row.get("requested_at"),
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        error_message=row.get("error_message"),
        entry_url=row.get("entry_url"),
    )


def verification_entry_url(provider: str) -> str | None:
    return SESSION_VERIFICATION_ENTRY_URLS.get(provider)


def is_verification_active(verification: SessionVerification | None) -> bool:
    return verification is not None and verification.lifecycle in ACTIVE_VERIFICATION_LIFECYCLES


def expire_timed_out_verifications(
    db: Any,
    user_id: str,
    *,
    now: datetime | None = None,
    timeout_seconds: int = VERIFICATION_TIMEOUT_SECONDS,
) -> int:
    """Mark overdue requested/running jobs as timed_out. Returns count updated."""
    ensure_session_verification_tables(db)
    now = now or utc_now()
    cutoff = (now - timedelta(seconds=timeout_seconds)).isoformat()
    cur = db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = 'timed_out',
            completed_at = ?,
            error_message = COALESCE(error_message, 'verification timed out')
        WHERE user_id = ?
          AND lifecycle IN ('requested', 'running')
          AND requested_at < ?
        """,
        (now.isoformat(), user_id, cutoff),
    )
    db.commit()
    return int(cur.rowcount or 0)


def get_latest_session_verification(
    db: Any,
    user_id: str,
    provider: str,
) -> SessionVerification | None:
    ensure_session_verification_tables(db)
    row = db.execute(
        """
        SELECT verification_id, user_id, provider, lifecycle, entry_url,
               error_message, requested_at, started_at, completed_at
        FROM provider_session_verification
        WHERE user_id = ? AND provider = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    return _row_to_verification(dict(row) if row else None)


def get_session_verifications(
    db: Any,
    user_id: str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
) -> dict[str, SessionVerification]:
    """Latest verification per provider, after applying timeouts."""
    ensure_session_verification_tables(db)
    expire_timed_out_verifications(db, user_id, now=now)
    provider_list = list(providers) if providers is not None else sorted(PROBE_PROVIDERS)
    result: dict[str, SessionVerification] = {}
    for provider in provider_list:
        latest = get_latest_session_verification(db, user_id, provider)
        if latest is not None:
            result[provider] = latest
    return result


def get_pending_session_verification(
    db: Any,
    user_id: str,
) -> SessionVerification | None:
    """Oldest active verification for the extension to execute."""
    ensure_session_verification_tables(db)
    expire_timed_out_verifications(db, user_id)
    row = db.execute(
        """
        SELECT verification_id, user_id, provider, lifecycle, entry_url,
               error_message, requested_at, started_at, completed_at
        FROM provider_session_verification
        WHERE user_id = ? AND lifecycle IN ('requested', 'running')
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return _row_to_verification(dict(row) if row else None)


def _has_active_verification(db: Any, user_id: str, provider: str) -> bool:
    row = db.execute(
        """
        SELECT 1 FROM provider_session_verification
        WHERE user_id = ? AND provider = ? AND lifecycle IN ('requested', 'running')
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    return row is not None


def _seconds_since_latest_request(
    db: Any,
    user_id: str,
    provider: str,
    *,
    now: datetime,
) -> float | None:
    row = db.execute(
        """
        SELECT requested_at FROM provider_session_verification
        WHERE user_id = ? AND provider = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id, provider),
    ).fetchone()
    if not row:
        return None
    requested_at = _parse_iso(row["requested_at"])
    if requested_at is None:
        return None
    return (now - requested_at).total_seconds()


def request_session_verification(
    db: Any,
    user_id: str,
    provider: str,
    *,
    now: datetime | None = None,
    throttle_seconds: int = VERIFICATION_THROTTLE_SECONDS,
) -> SessionVerification | None:
    """Enqueue a background verification if not already active / throttled.

    Returns the active or newly created verification, or None when the provider
    has no verification entry URL (unsupported).
    """
    provider = provider.strip().lower()
    entry_url = verification_entry_url(provider)
    if entry_url is None:
        return None

    ensure_session_verification_tables(db)
    now = now or utc_now()
    expire_timed_out_verifications(db, user_id, now=now)

    if _has_active_verification(db, user_id, provider):
        return get_latest_session_verification(db, user_id, provider)

    age = _seconds_since_latest_request(db, user_id, provider, now=now)
    if age is not None and age < throttle_seconds:
        return get_latest_session_verification(db, user_id, provider)

    verification_id = str(uuid.uuid4())
    requested_at = now.isoformat()
    db.execute(
        """
        INSERT INTO provider_session_verification (
            verification_id, user_id, provider, lifecycle, entry_url, requested_at
        ) VALUES (?, ?, ?, 'requested', ?, ?)
        """,
        (verification_id, user_id, provider, entry_url, requested_at),
    )
    db.commit()
    return SessionVerification(
        verification_id=verification_id,
        provider=provider,
        lifecycle="requested",
        requested_at=requested_at,
        entry_url=entry_url,
    )


def mark_session_verification_running(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    now: datetime | None = None,
) -> SessionVerification | None:
    ensure_session_verification_tables(db)
    now = now or utc_now()
    db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = 'running', started_at = COALESCE(started_at, ?)
        WHERE verification_id = ? AND user_id = ?
          AND lifecycle IN ('requested', 'running')
        """,
        (now.isoformat(), verification_id, user_id),
    )
    db.commit()
    row = db.execute(
        """
        SELECT verification_id, user_id, provider, lifecycle, entry_url,
               error_message, requested_at, started_at, completed_at
        FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    return _row_to_verification(dict(row) if row else None)


def complete_session_verification(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    lifecycle: VerificationLifecycle = "completed",
    error_message: str | None = None,
    now: datetime | None = None,
) -> None:
    if lifecycle not in TERMINAL_VERIFICATION_LIFECYCLES:
        raise ValueError(f"invalid verification completion lifecycle: {lifecycle!r}")
    ensure_session_verification_tables(db)
    now = now or utc_now()
    db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = ?, error_message = ?, completed_at = ?
        WHERE verification_id = ? AND user_id = ?
          AND lifecycle IN ('requested', 'running')
        """,
        (lifecycle, error_message, now.isoformat(), verification_id, user_id),
    )
    db.commit()


def session_verification_to_json(verification: SessionVerification | None) -> dict[str, Any]:
    if verification is None:
        return {
            "verification_id": None,
            "provider": None,
            "lifecycle": "idle",
            "entry_url": None,
            "error_message": None,
            "requested_at": None,
            "started_at": None,
            "completed_at": None,
        }
    return {
        "verification_id": verification.verification_id,
        "provider": verification.provider,
        "lifecycle": verification.lifecycle,
        "entry_url": verification.entry_url,
        "error_message": verification.error_message,
        "requested_at": verification.requested_at,
        "started_at": verification.started_at,
        "completed_at": verification.completed_at,
    }
