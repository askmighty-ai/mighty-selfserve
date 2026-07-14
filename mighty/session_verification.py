"""Background session verification lifecycle — separate from provider_session_state.

Freshness of Current Access is a read-model concern. When session evidence is
stale, product surfaces and the extension may request a background verification.
That request is recorded here; it must not rewrite provider_session_state by
itself. Only explicit session evidence from the verifier may update PSS.

Enqueue ownership lives here via ensure_provider_session_verification_if_stale —
callers must not duplicate staleness / throttle / timeout rules.

Production scheduling entry point: ``mighty.provider_access_manager``
(``request_provider_access_check`` / ``ensure_stale_provider_access_checks``).
Prefer those wrappers over calling this module directly from new product code.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from mighty.provider_access_probe import PROBE_PROVIDERS
from mighty.provider_session_state import ProviderSessionState, get_provider_session_states

VerificationLifecycle = Literal[
    "requested",
    "running",
    "session_verified",
    "extracting",
    "completed",
    "failed",
    "timed_out",
]

# Semantic terminal outcomes. Every verification cycle must end in exactly one.
# Lifecycle storage stays completed/failed/timed_out for compatibility; this is
# the canonical reason the cycle became terminal.
VerificationTerminalReason = Literal[
    "authenticated",
    "signed_out",
    "timeout",
    "navigation_failed",
    "cancelled",
    "unknown",
]

# In-flight access-cycle stages (not yet terminal).
ACTIVE_VERIFICATION_LIFECYCLES = frozenset(
    {"requested", "running", "session_verified", "extracting"}
)
# Claimable by the extension poller.
CLAIMABLE_VERIFICATION_LIFECYCLES = frozenset({"requested", "running"})
# Non-terminal mid-cycle stages after session evidence is known.
MID_CYCLE_VERIFICATION_LIFECYCLES = frozenset({"session_verified", "extracting"})
TERMINAL_VERIFICATION_LIFECYCLES = frozenset({"completed", "failed", "timed_out"})
VERIFICATION_TERMINAL_REASONS = frozenset(
    {
        "authenticated",
        "signed_out",
        "timeout",
        "navigation_failed",
        "cancelled",
        "unknown",
    }
)

# Live-session freshness for Current Access / verification scheduling.
# Retain this short window for session-evidence display; ready-result
# revalidation below gates how often a full access cycle is re-enqueued.
CURRENT_SESSION_FRESHNESS_SECONDS = 120
# Do not enqueue another verification for the same provider within this window.
VERIFICATION_THROTTLE_SECONDS = 60
# Hard ceiling for probe-phase (requested/running). After this the lifecycle
# MUST become terminal — verification is a finite operation.
VERIFICATION_MAX_DURATION_SECONDS = 20
# Alias kept for callers/tests that still reference the older name.
VERIFICATION_TIMEOUT_SECONDS = VERIFICATION_MAX_DURATION_SECONDS
# Allow session_verified → extracting → complete enough time for private-data pull.
# Still finite: expire_timed_out_verifications always terminals overdue mid-cycles.
VERIFICATION_EXTRACTION_TIMEOUT_SECONDS = 90
# After a confirmed ready extraction, wait this long before requesting another
# routine revalidation cycle (prevents ~120s session-freshness churn).
READY_REVALIDATION_INTERVAL_SECONDS = 15 * 60
# Preserve customer-facing ready through inconclusive/timeout rechecks, and
# while a later routine cycle runs, until this grace window elapses.
READY_RESULT_GRACE_SECONDS = 30 * 60

# Amex operational entry for automatic session verification.
AMEX_SESSION_VERIFICATION_ENTRY_URL = "https://global.americanexpress.com/overview"

SESSION_VERIFICATION_ENTRY_URLS: dict[str, str] = {
    "amex": AMEX_SESSION_VERIFICATION_ENTRY_URL,
}


def log_access_cycle_event(
    event: str,
    *,
    provider: str,
    verification_id: str | None,
    access_cycle_id: str | None = None,
    **extra: Any,
) -> None:
    """Concise structured log for one access cycle. Never logs secrets/bodies."""
    cycle_id = access_cycle_id or verification_id or ""
    parts = [
        f"[access_cycle] event={event}",
        f"provider={provider}",
        f"verification_id={verification_id or ''}",
        f"access_cycle_id={cycle_id}",
    ]
    for key, value in extra.items():
        if value is None:
            continue
        # Never log payloads / tokens / cookie-like values.
        if key in {"cookies", "token", "body", "password", "authorization"}:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


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
    terminal_reason: str | None = None
    terminal_source: str | None = None


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


def verification_duration_ms(
    *,
    requested_at: str | None,
    started_at: str | None = None,
    completed_at: str | None = None,
    now: datetime | None = None,
) -> int | None:
    """Duration from cycle enqueue (requested_at) to completion (or now).

    Uses requested_at as the anchor so queued + running time are included —
    matching the timeout deadline. started_at is retained for diagnostics only.
    """
    del started_at  # Exposed separately; duration is cycle-wide from enqueue.
    start = _parse_iso(requested_at)
    end = _parse_iso(completed_at) or (now or utc_now())
    if start is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def lifecycle_for_terminal_reason(
    terminal_reason: VerificationTerminalReason,
) -> VerificationLifecycle:
    """Map semantic terminal outcome → stored lifecycle."""
    if terminal_reason == "timeout":
        return "timed_out"
    if terminal_reason in {"authenticated", "signed_out"}:
        return "completed"
    return "failed"


def normalize_terminal_reason(
    value: str | None,
    *,
    default: VerificationTerminalReason = "unknown",
) -> VerificationTerminalReason:
    reason = (value or "").strip().lower()
    if reason in VERIFICATION_TERMINAL_REASONS:
        return reason  # type: ignore[return-value]
    return default


def terminal_reason_from_error_message(
    error_message: str | None,
    *,
    default: VerificationTerminalReason = "unknown",
) -> VerificationTerminalReason:
    """Best-effort map of failure strings → terminal outcome."""
    text = (error_message or "").strip().lower()
    if not text:
        return default
    if "timed out" in text or text == "timeout" or "verification timed out" in text:
        return "timeout"
    if (
        "navigation" in text
        or "tab_creation_blocked" in text
        or text == "probe_navigation_error"
        or "no_entry_url" in text
    ):
        return "navigation_failed"
    if "cancelled" in text or "canceled" in text or "tab closed" in text:
        return "cancelled"
    if "signed_out" in text or "login_required" in text or "needs_login" in text:
        return "signed_out"
    return default


_VERIFICATION_SELECT_COLUMNS = (
    "verification_id, user_id, provider, lifecycle, entry_url, "
    "error_message, requested_at, started_at, completed_at, "
    "terminal_reason, terminal_source"
)

_VERIFICATION_DIAGNOSTIC_COLUMNS = (
    ("terminal_reason", "TEXT"),
    ("terminal_source", "TEXT"),
)


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
            completed_at     TEXT,
            terminal_reason  TEXT,
            terminal_source  TEXT
        )
        """
    )
    existing = {
        row[1] for row in db.execute("PRAGMA table_info(provider_session_verification)").fetchall()
    }
    for col, coltype in _VERIFICATION_DIAGNOSTIC_COLUMNS:
        if col not in existing:
            try:
                db.execute(
                    f"ALTER TABLE provider_session_verification ADD COLUMN {col} {coltype}"
                )
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
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
        terminal_reason=row.get("terminal_reason"),
        terminal_source=row.get("terminal_source"),
    )


def verification_entry_url(provider: str) -> str | None:
    return SESSION_VERIFICATION_ENTRY_URLS.get(provider)


def is_verification_active(verification: SessionVerification | None) -> bool:
    return verification is not None and verification.lifecycle in ACTIVE_VERIFICATION_LIFECYCLES


def session_evidence_age_seconds(
    session_state: ProviderSessionState | None,
    *,
    now: datetime | None = None,
) -> float | None:
    """Seconds since session evidence was observed, or None if unknown."""
    if session_state is None or not session_state.observed_at:
        return None
    observed = _parse_iso(session_state.observed_at)
    if observed is None:
        return None
    now = now or utc_now()
    return (now - observed).total_seconds()


def is_session_evidence_fresh(
    session_state: ProviderSessionState | None,
    *,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
) -> bool:
    """True when explicit session evidence is within the live-session window."""
    age = session_evidence_age_seconds(session_state, now=now)
    if age is None:
        return False
    return age <= freshness_seconds


def is_ready_result_within_revalidation_interval(
    last_ready_at: str | None,
    *,
    now: datetime | None = None,
    interval_seconds: int = READY_REVALIDATION_INTERVAL_SECONDS,
) -> bool:
    """True when a confirmed ready extraction is still inside the revalidation interval."""
    when = _parse_iso(last_ready_at)
    if when is None:
        return False
    now = now or utc_now()
    return (now - when).total_seconds() <= interval_seconds


def get_last_confirmed_ready_at(db: Any, user_id: str, provider: str) -> str | None:
    """Return synced_at for a completed extraction, if any (scheduling only)."""
    try:
        row = db.execute(
            """
            SELECT synced_at, extraction_status
            FROM account_data
            WHERE user_id = ? AND source = ?
            """,
            (user_id, provider),
        ).fetchone()
    except Exception as exc:
        # Unit-test DBs may omit account_data; missing table ≠ ready result.
        if "account_data" not in str(exc):
            raise
        return None
    if not row:
        return None
    if (row["extraction_status"] or "") != "complete":
        return None
    synced_at = row["synced_at"]
    return str(synced_at) if synced_at else None


def session_state_needs_verification(
    session_state: ProviderSessionState | None,
    provider: str,
    *,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    last_ready_at: str | None = None,
    revalidation_interval_seconds: int = READY_REVALIDATION_INTERVAL_SECONDS,
) -> bool:
    """True when stale connected/signed_out/error evidence should be re-checked.

    When a confirmed ready result is still within the revalidation interval,
    do not request another routine cycle solely because live-session evidence
    aged past CURRENT_SESSION_FRESHNESS_SECONDS.
    """
    if verification_entry_url(provider) is None:
        return False
    if session_state is None or session_state.state == "unknown":
        return False
    if session_state.state not in {"connected", "signed_out", "error"}:
        return False
    # Definitive signed_out / error still revalidate on the short freshness
    # window — only suppress churn for connected + recent ready.
    if (
        session_state.state == "connected"
        and is_ready_result_within_revalidation_interval(
            last_ready_at,
            now=now,
            interval_seconds=revalidation_interval_seconds,
        )
    ):
        return False
    return not is_session_evidence_fresh(
        session_state, now=now, freshness_seconds=freshness_seconds
    )


def expire_timed_out_verifications(
    db: Any,
    user_id: str,
    *,
    now: datetime | None = None,
    timeout_seconds: int = VERIFICATION_MAX_DURATION_SECONDS,
    extraction_timeout_seconds: int = VERIFICATION_EXTRACTION_TIMEOUT_SECONDS,
) -> int:
    """Mark overdue active jobs as timed_out. Returns count updated.

    Does not mutate provider_session_state — timeouts never imply signed_out.
    requested/running use VERIFICATION_MAX_DURATION_SECONDS; session_verified/
    extracting use the extraction window so private-data pull can finish.
    Every overdue active row becomes terminal (timed_out / timeout).
    """
    ensure_session_verification_tables(db)
    now = now or utc_now()
    now_iso = now.isoformat()
    probe_cutoff = (now - timedelta(seconds=timeout_seconds)).isoformat()
    extract_cutoff = (now - timedelta(seconds=extraction_timeout_seconds)).isoformat()

    # Collect rows about to expire so we can log diagnostics with duration_ms.
    due_rows = db.execute(
        """
        SELECT verification_id, provider, lifecycle, requested_at, started_at
        FROM provider_session_verification
        WHERE user_id = ?
          AND (
            (lifecycle IN ('requested', 'running') AND requested_at < ?)
            OR (lifecycle IN ('session_verified', 'extracting') AND requested_at < ?)
          )
        """,
        (user_id, probe_cutoff, extract_cutoff),
    ).fetchall()

    cur_probe = db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = 'timed_out',
            completed_at = ?,
            error_message = COALESCE(error_message, 'verification timed out'),
            terminal_reason = 'timeout',
            terminal_source = COALESCE(terminal_source, 'expire_watchdog')
        WHERE user_id = ?
          AND lifecycle IN ('requested', 'running')
          AND requested_at < ?
        """,
        (now_iso, user_id, probe_cutoff),
    )
    cur_extract = db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = 'timed_out',
            completed_at = ?,
            error_message = COALESCE(error_message, 'extraction timed out'),
            terminal_reason = 'timeout',
            terminal_source = COALESCE(terminal_source, 'expire_watchdog')
        WHERE user_id = ?
          AND lifecycle IN ('session_verified', 'extracting')
          AND requested_at < ?
        """,
        (now_iso, user_id, extract_cutoff),
    )
    db.commit()
    updated = int(cur_probe.rowcount or 0) + int(cur_extract.rowcount or 0)
    for row in due_rows or []:
        row_dict = dict(row)
        duration = verification_duration_ms(
            requested_at=row_dict.get("requested_at"),
            started_at=row_dict.get("started_at"),
            completed_at=now_iso,
            now=now,
        )
        log_access_cycle_event(
            "verification_terminal",
            provider=str(row_dict.get("provider") or ""),
            verification_id=str(row_dict.get("verification_id") or ""),
            access_cycle_id=str(row_dict.get("verification_id") or ""),
            lifecycle="timed_out",
            terminal_reason="timeout",
            terminal_source="expire_watchdog",
            duration_ms=duration,
            prior_lifecycle=row_dict.get("lifecycle"),
        )
    return updated


def get_latest_session_verification(
    db: Any,
    user_id: str,
    provider: str,
) -> SessionVerification | None:
    ensure_session_verification_tables(db)
    row = db.execute(
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
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
    *,
    ensure_stale: bool = False,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Oldest active verification for the extension to execute.

    When ensure_stale is True, enqueue verification for stale providers first
    (extension lifecycle trigger). Does not write provider_session_state.
    """
    ensure_session_verification_tables(db)
    now = now or utc_now()
    if ensure_stale:
        ensure_stale_session_verifications_for_user(db, user_id, now=now)
    else:
        expire_timed_out_verifications(db, user_id, now=now)
    row = db.execute(
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
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
        WHERE user_id = ? AND provider = ?
          AND lifecycle IN ('requested', 'running', 'session_verified', 'extracting')
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
    has no verification entry URL (unsupported). Prefer
    ensure_provider_session_verification_if_stale for product callers.
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
    log_access_cycle_event(
        "access_cycle_created",
        provider=provider,
        verification_id=verification_id,
        access_cycle_id=verification_id,
    )
    return SessionVerification(
        verification_id=verification_id,
        provider=provider,
        lifecycle="requested",
        requested_at=requested_at,
        entry_url=entry_url,
    )


def ensure_provider_session_verification_if_stale(
    db: Any,
    user_id: str,
    provider: str,
    *,
    session_state: ProviderSessionState | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    last_ready_at: str | None = None,
    revalidation_interval_seconds: int = READY_REVALIDATION_INTERVAL_SECONDS,
) -> SessionVerification | None:
    """Enqueue verification when session evidence is stale; otherwise no-op.

    Owns: staleness check, ready-result revalidation interval, active-job reuse,
    60s throttle, VERIFICATION_MAX_DURATION_SECONDS timeout, duplicate prevention.
    Never writes provider_session_state. Does not enqueue while an active job
    already exists (request_session_verification reuses it).
    """
    provider = provider.strip().lower()
    ensure_session_verification_tables(db)
    now = now or utc_now()
    expire_timed_out_verifications(db, user_id, now=now)

    if session_state is None:
        states = get_provider_session_states(db, user_id, providers=[provider])
        session_state = states.get(provider)

    if last_ready_at is None:
        last_ready_at = get_last_confirmed_ready_at(db, user_id, provider)

    if not session_state_needs_verification(
        session_state,
        provider,
        now=now,
        freshness_seconds=freshness_seconds,
        last_ready_at=last_ready_at,
        revalidation_interval_seconds=revalidation_interval_seconds,
    ):
        return None

    return request_session_verification(db, user_id, provider, now=now)


def ensure_stale_session_verifications_for_user(
    db: Any,
    user_id: str,
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
) -> dict[str, SessionVerification]:
    """Ensure verification jobs for all stale providers with an entry URL.

    Returns the active/created verification keyed by provider (empty when none).
    """
    ensure_session_verification_tables(db)
    now = now or utc_now()
    expire_timed_out_verifications(db, user_id, now=now)

    if providers is None:
        provider_list = [
            p for p in sorted(PROBE_PROVIDERS) if verification_entry_url(p) is not None
        ]
    else:
        provider_list = [p for p in providers if verification_entry_url(p) is not None]

    if not provider_list:
        return {}

    states = get_provider_session_states(db, user_id, providers=provider_list)
    result: dict[str, SessionVerification] = {}
    for provider in provider_list:
        created = ensure_provider_session_verification_if_stale(
            db,
            user_id,
            provider,
            session_state=states.get(provider),
            now=now,
            freshness_seconds=freshness_seconds,
        )
        if created is not None:
            result[provider] = created
    return result


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
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
        FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    verification = _row_to_verification(dict(row) if row else None)
    if verification is not None and verification.lifecycle == "running":
        log_access_cycle_event(
            "extension_claimed",
            provider=verification.provider,
            verification_id=verification.verification_id,
            access_cycle_id=verification.verification_id,
        )
    return verification


def advance_session_verification(
    db: Any,
    user_id: str,
    verification_id: str,
    *,
    lifecycle: VerificationLifecycle,
    error_message: str | None = None,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Advance an in-flight access cycle to a non-terminal mid-cycle stage."""
    if lifecycle not in MID_CYCLE_VERIFICATION_LIFECYCLES:
        raise ValueError(f"invalid mid-cycle verification lifecycle: {lifecycle!r}")
    ensure_session_verification_tables(db)
    now = now or utc_now()
    if lifecycle == "session_verified":
        allowed = ("requested", "running", "session_verified")
    else:
        allowed = ("requested", "running", "session_verified", "extracting")
    placeholders = ", ".join("?" for _ in allowed)
    db.execute(
        f"""
        UPDATE provider_session_verification
        SET lifecycle = ?, error_message = COALESCE(?, error_message)
        WHERE verification_id = ? AND user_id = ?
          AND lifecycle IN ({placeholders})
        """,
        (lifecycle, error_message, verification_id, user_id, *allowed),
    )
    db.commit()
    row = db.execute(
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
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
    lifecycle: VerificationLifecycle | None = None,
    error_message: str | None = None,
    terminal_reason: VerificationTerminalReason | str | None = None,
    terminal_source: str | None = None,
    now: datetime | None = None,
) -> SessionVerification | None:
    """Transition an active verification to a terminal lifecycle.

    Prefer ``terminal_reason`` — lifecycle is derived when omitted. Always sets
    completed_at, terminal_reason, and terminal_source. No-ops if already
    terminal (returns the existing row).
    """
    ensure_session_verification_tables(db)
    now = now or utc_now()
    now_iso = now.isoformat()

    existing = db.execute(
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
        FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    if existing is None:
        return None
    existing_dict = dict(existing)
    if existing_dict.get("lifecycle") in TERMINAL_VERIFICATION_LIFECYCLES:
        return _row_to_verification(existing_dict)

    if terminal_reason:
        reason = normalize_terminal_reason(terminal_reason)
    elif error_message:
        reason = terminal_reason_from_error_message(error_message)
    elif lifecycle == "timed_out":
        reason = "timeout"
    elif lifecycle == "failed":
        reason = "unknown"
    elif lifecycle == "completed":
        reason = "authenticated"
    else:
        reason = "unknown"
    final_lifecycle = lifecycle if lifecycle in TERMINAL_VERIFICATION_LIFECYCLES else None
    if final_lifecycle is None:
        final_lifecycle = lifecycle_for_terminal_reason(reason)
    source = (terminal_source or "complete_session_verification").strip() or (
        "complete_session_verification"
    )
    duration = verification_duration_ms(
        requested_at=existing_dict.get("requested_at"),
        started_at=existing_dict.get("started_at"),
        completed_at=now_iso,
        now=now,
    )

    db.execute(
        """
        UPDATE provider_session_verification
        SET lifecycle = ?,
            error_message = ?,
            completed_at = ?,
            terminal_reason = ?,
            terminal_source = ?
        WHERE verification_id = ? AND user_id = ?
          AND lifecycle IN (
              'requested', 'running', 'session_verified', 'extracting'
          )
        """,
        (
            final_lifecycle,
            error_message,
            now_iso,
            reason,
            source,
            verification_id,
            user_id,
        ),
    )
    db.commit()
    log_access_cycle_event(
        "verification_terminal",
        provider=str(existing_dict.get("provider") or ""),
        verification_id=verification_id,
        access_cycle_id=verification_id,
        lifecycle=final_lifecycle,
        terminal_reason=reason,
        terminal_source=source,
        duration_ms=duration,
        prior_lifecycle=existing_dict.get("lifecycle"),
    )
    row = db.execute(
        f"""
        SELECT {_VERIFICATION_SELECT_COLUMNS}
        FROM provider_session_verification
        WHERE verification_id = ? AND user_id = ?
        """,
        (verification_id, user_id),
    ).fetchone()
    return _row_to_verification(dict(row) if row else None)


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
            "terminal_reason": None,
            "terminal_source": None,
            "duration_ms": None,
            "verification_started_at": None,
            "verification_completed_at": None,
        }
    duration = verification_duration_ms(
        requested_at=verification.requested_at,
        started_at=verification.started_at,
        completed_at=verification.completed_at,
    )
    started_at = verification.started_at or verification.requested_at
    return {
        "verification_id": verification.verification_id,
        "provider": verification.provider,
        "lifecycle": verification.lifecycle,
        "entry_url": verification.entry_url,
        "error_message": verification.error_message,
        "requested_at": verification.requested_at,
        "started_at": verification.started_at,
        "completed_at": verification.completed_at,
        "terminal_reason": verification.terminal_reason,
        "terminal_source": verification.terminal_source,
        "duration_ms": duration,
        "verification_started_at": started_at,
        "verification_completed_at": verification.completed_at,
    }
