"""Canonical provider session state — live access, separate from cached account data.

Cached private fields (e.g. Membership Rewards balance) must not by themselves mark a
provider as currently connected. Explicit session evidence wins, and newer evidence
replaces older evidence.

Production writers: do **not** call ``upsert_provider_session_state`` from new code.
Route active verification and definitive session evidence through
``mighty.provider_access_manager`` (the Provider Access Manager boundary).
The ``record_*`` helpers below are compatibility wrappers that delegate to PAM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from mighty.provider_access_probe import (
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_ERROR,
    AUTH_LOGIN_PAGE,
    AUTH_MFA_REQUIRED,
    AUTH_PRIVATE_DATA_VISIBLE,
    AUTH_SESSION_EXPIRED,
    PROBE_PROVIDERS,
)

SessionState = Literal["connected", "signed_out", "unknown", "error"]
SessionConfidence = Literal["high", "medium", "low"]

SIGNED_OUT_AUTH_STATES = frozenset({
    AUTH_LOGIN_PAGE,
    AUTH_SESSION_EXPIRED,
    AUTH_MFA_REQUIRED,
})

CONNECTED_AUTH_STATES = frozenset({
    AUTH_PRIVATE_DATA_VISIBLE,
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
})

SESSION_API_MARKERS = ("ReadUserSession.v1", "UpdateUserSession.v1")

CONFIDENCE_RANK: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
}

# Higher wins when observed_at and confidence are equal.
# Legacy connection_status / sync_status are lowest — timeline context only.
EVIDENCE_PRIORITY: dict[str, int] = {
    "session_api": 100,
    "session_verified": 90,
    "session_verified_extract": 90,
    "login_required": 80,  # extension needs-login / explicit login_required
    "login_page": 70,
    "session_expired": 70,
    "mfa_required": 70,
    "authenticated_private_data": 60,
    "authenticated_page": 60,
    "probe_error": 20,
    "connection_status": 10,
}


@dataclass(frozen=True)
class ProviderSessionState:
    provider: str
    state: SessionState
    evidence_type: str
    evidence_summary: str
    observed_at: str | None
    source: str
    confidence: SessionConfidence


@dataclass(frozen=True)
class SessionEvidence:
    provider: str
    state: SessionState
    evidence_type: str
    evidence_summary: str
    observed_at: datetime
    source: str
    confidence: SessionConfidence


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def evidence_priority(*, evidence_type: str, source: str) -> int:
    """Rank explicit session evidence above legacy account_data signals."""
    if source in {
        "account_data.sync_status",
        "account_data.connection_status",
    }:
        return 5
    if source.startswith("extension_amex"):
        return max(EVIDENCE_PRIORITY.get(evidence_type, 50), 85)
    return EVIDENCE_PRIORITY.get(evidence_type, 40)


def _confidence_rank(value: str | None) -> int:
    return CONFIDENCE_RANK.get(value or "", 0)


def should_replace_session_evidence(
    existing: ProviderSessionState | None,
    incoming: SessionEvidence,
) -> bool:
    """True when incoming evidence should replace the stored row.

    Deterministic order: observed_at, then confidence, then evidence priority.
    Equal on all axes keeps the existing row (no thrash).
    """
    if existing is None or not existing.observed_at:
        return True
    existing_at = _parse_iso(existing.observed_at)
    if existing_at is None:
        return True
    if incoming.observed_at > existing_at:
        return True
    if incoming.observed_at < existing_at:
        return False

    incoming_conf = _confidence_rank(incoming.confidence)
    existing_conf = _confidence_rank(existing.confidence)
    if incoming_conf > existing_conf:
        return True
    if incoming_conf < existing_conf:
        return False

    incoming_pri = evidence_priority(
        evidence_type=incoming.evidence_type, source=incoming.source
    )
    existing_pri = evidence_priority(
        evidence_type=existing.evidence_type, source=existing.source
    )
    return incoming_pri > existing_pri


def ensure_provider_session_state_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_session_state (
            user_id           TEXT NOT NULL,
            provider          TEXT NOT NULL,
            state             TEXT NOT NULL,
            evidence_type     TEXT NOT NULL,
            evidence_summary  TEXT NOT NULL,
            observed_at       TEXT,
            source            TEXT NOT NULL,
            confidence        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            PRIMARY KEY (user_id, provider)
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_pss_user "
        "ON provider_session_state(user_id)"
    )
    db.commit()


def _status_code(entry: dict[str, Any]) -> int | None:
    raw = entry.get("status_code")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _session_api_statuses(deep_inspect: dict[str, Any] | None) -> list[tuple[str, int]]:
    """Return (api_name, status_code) pairs from auth network / request traces."""
    if not deep_inspect:
        return []
    trace = deep_inspect.get("auth_network_trace") or {}
    buckets: list[Any] = []
    for key in (
        "highlighted_requests",
        "auth_session_requests",
        "status_401_requests",
        "status_403_requests",
        "requests",
    ):
        buckets.extend(trace.get(key) or [])

    found: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for entry in buckets:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        code = _status_code(entry)
        if code is None:
            continue
        for marker in SESSION_API_MARKERS:
            if marker in url:
                key = (marker, code)
                if key not in seen:
                    seen.add(key)
                    found.append(key)
                break
    return found


def derive_session_evidence_from_probe(result: dict[str, Any]) -> SessionEvidence | None:
    """Map a probe result to explicit session evidence, or None if inconclusive."""
    provider = str(result.get("provider") or "")
    if not provider:
        return None

    observed_at = _parse_iso(result.get("probed_at") or result.get("timestamp")) or datetime.now(
        timezone.utc
    )
    deep_inspect = result.get("deep_inspect")
    if not isinstance(deep_inspect, dict):
        deep_inspect = None

    api_statuses = _session_api_statuses(deep_inspect)
    if api_statuses:
        success = [(name, code) for name, code in api_statuses if code == 200]
        denied = [(name, code) for name, code in api_statuses if code in {401, 403}]
        if success:
            name, code = success[0]
            return SessionEvidence(
                provider=provider,
                state="connected",
                evidence_type="session_api",
                evidence_summary=f"{name} returned {code}",
                observed_at=observed_at,
                source="provider_access_probe",
                confidence="high",
            )
        if denied:
            name, code = denied[0]
            return SessionEvidence(
                provider=provider,
                state="signed_out",
                evidence_type="session_api",
                evidence_summary=f"{name} returned {code}",
                observed_at=observed_at,
                source="provider_access_probe",
                confidence="high",
            )

    auth_state = result.get("auth_state") or ""
    failure_reason = result.get("failure_reason") or ""

    if auth_state in SIGNED_OUT_AUTH_STATES or failure_reason == "login_required":
        if auth_state == AUTH_LOGIN_PAGE or failure_reason == "login_required":
            summary = "login page detected"
            evidence_type = "login_page"
        elif auth_state == AUTH_SESSION_EXPIRED:
            summary = "session expired / logout page"
            evidence_type = "session_expired"
        elif auth_state == AUTH_MFA_REQUIRED:
            summary = "MFA / login wall detected"
            evidence_type = "mfa_required"
        else:
            summary = "login required signal"
            evidence_type = "login_required"
        return SessionEvidence(
            provider=provider,
            state="signed_out",
            evidence_type=evidence_type,
            evidence_summary=summary,
            observed_at=observed_at,
            source="provider_access_probe",
            confidence="high",
        )

    if auth_state in CONNECTED_AUTH_STATES:
        if auth_state == AUTH_PRIVATE_DATA_VISIBLE:
            summary = "authenticated page with private account data"
            evidence_type = "authenticated_private_data"
        else:
            summary = "authenticated overview / account page observed"
            evidence_type = "authenticated_page"
        return SessionEvidence(
            provider=provider,
            state="connected",
            evidence_type=evidence_type,
            evidence_summary=summary,
            observed_at=observed_at,
            source="provider_access_probe",
            confidence="medium",
        )

    if auth_state == AUTH_ERROR:
        return SessionEvidence(
            provider=provider,
            state="error",
            evidence_type="probe_error",
            evidence_summary=str(result.get("evidence_snippet") or "probe error"),
            observed_at=observed_at,
            source="provider_access_probe",
            confidence="low",
        )

    return None


def record_amex_extension_connected(
    db: Any,
    user_id: str,
    *,
    observed_at: datetime | str | None = None,
    evidence_type: str = "session_verified",
    evidence_summary: str = "Amex extension reported verified authenticated session",
    source: str = "extension_amex_connected",
) -> ProviderSessionState:
    """Compatibility wrapper — routes through Provider Access Manager.

    Prefer ``mighty.provider_access_manager.record_amex_extension_connected``.
    """
    from mighty.provider_access_manager import (
        record_amex_extension_connected as _pam_record,
    )

    return _pam_record(
        db,
        user_id,
        observed_at=observed_at,
        evidence_type=evidence_type,
        evidence_summary=evidence_summary,
        source=source,
    )


def record_amex_extension_needs_login(
    db: Any,
    user_id: str,
    *,
    observed_at: datetime | str | None = None,
) -> ProviderSessionState:
    """Compatibility wrapper — routes through Provider Access Manager.

    Prefer ``mighty.provider_access_manager.record_amex_extension_needs_login``.
    """
    from mighty.provider_access_manager import (
        record_amex_extension_needs_login as _pam_record,
    )

    return _pam_record(db, user_id, observed_at=observed_at)


def record_extension_login_required(
    db: Any,
    user_id: str,
    provider: str,
    *,
    observed_at: datetime | str | None = None,
    source: str = "extension_sync_failure",
) -> ProviderSessionState | None:
    """Compatibility wrapper — routes through Provider Access Manager.

    Prefer ``mighty.provider_access_manager.record_extension_login_required``.
    Only probe providers participate in provider_session_state. Legacy sync_status
    is still written separately for compatibility.
    """
    from mighty.provider_access_manager import (
        record_extension_login_required as _pam_record,
    )

    return _pam_record(
        db, user_id, provider, observed_at=observed_at, source=source
    )


def record_extension_session_connected(
    db: Any,
    user_id: str,
    provider: str,
    *,
    observed_at: datetime | str | None = None,
    evidence_type: str = "session_verified",
    evidence_summary: str | None = None,
    source: str = "extension_login_cleared",
) -> ProviderSessionState | None:
    """Compatibility wrapper — routes through Provider Access Manager.

    Prefer ``mighty.provider_access_manager.record_extension_session_connected``.
    """
    from mighty.provider_access_manager import (
        record_extension_session_connected as _pam_record,
    )

    return _pam_record(
        db,
        user_id,
        provider,
        observed_at=observed_at,
        evidence_type=evidence_type,
        evidence_summary=evidence_summary,
        source=source,
    )


def upsert_provider_session_state(
    db: Any,
    user_id: str,
    evidence: SessionEvidence,
) -> ProviderSessionState:
    """Persist session evidence using observed_at, confidence, then evidence priority.

    **Do not call from new production code.** Use Provider Access Manager
    (``record_provider_access_evidence`` / evidence helpers). Approved callers:
    ``mighty.provider_access_manager`` and this module's storage implementation.
    """
    ensure_provider_session_state_tables(db)
    existing = get_provider_session_state(db, user_id, evidence.provider)
    if not should_replace_session_evidence(existing, evidence):
        assert existing is not None
        return existing

    now = utc_now_iso()
    observed_at = evidence.observed_at.isoformat()
    db.execute(
        """
        INSERT INTO provider_session_state (
            user_id, provider, state, evidence_type, evidence_summary,
            observed_at, source, confidence, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, provider) DO UPDATE SET
            state=excluded.state,
            evidence_type=excluded.evidence_type,
            evidence_summary=excluded.evidence_summary,
            observed_at=excluded.observed_at,
            source=excluded.source,
            confidence=excluded.confidence,
            updated_at=excluded.updated_at
        """,
        (
            user_id,
            evidence.provider,
            evidence.state,
            evidence.evidence_type,
            evidence.evidence_summary,
            observed_at,
            evidence.source,
            evidence.confidence,
            now,
        ),
    )
    db.commit()
    return ProviderSessionState(
        provider=evidence.provider,
        state=evidence.state,
        evidence_type=evidence.evidence_type,
        evidence_summary=evidence.evidence_summary,
        observed_at=observed_at,
        source=evidence.source,
        confidence=evidence.confidence,
    )


def record_session_evidence_from_probe(
    db: Any,
    user_id: str,
    result: dict[str, Any],
) -> ProviderSessionState | None:
    """Compatibility wrapper — routes through Provider Access Manager."""
    from mighty.provider_access_manager import (
        record_session_evidence_from_probe as _pam_record,
    )

    return _pam_record(db, user_id, result)


def get_provider_session_state(
    db: Any,
    user_id: str,
    provider: str,
) -> ProviderSessionState | None:
    ensure_provider_session_state_tables(db)
    row = db.execute(
        """
        SELECT provider, state, evidence_type, evidence_summary,
               observed_at, source, confidence
        FROM provider_session_state
        WHERE user_id=? AND provider=?
        """,
        (user_id, provider),
    ).fetchone()
    if not row:
        return None
    return ProviderSessionState(
        provider=row["provider"],
        state=row["state"],
        evidence_type=row["evidence_type"],
        evidence_summary=row["evidence_summary"],
        observed_at=row["observed_at"],
        source=row["source"],
        confidence=row["confidence"],
    )


def get_provider_session_states(
    db: Any,
    user_id: str,
    *,
    providers: tuple[str, ...] | list[str] | None = None,
) -> dict[str, ProviderSessionState]:
    ensure_provider_session_state_tables(db)
    provider_list = list(providers or sorted(PROBE_PROVIDERS))
    rows = db.execute(
        """
        SELECT provider, state, evidence_type, evidence_summary,
               observed_at, source, confidence
        FROM provider_session_state
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchall()
    by_provider = {
        row["provider"]: ProviderSessionState(
            provider=row["provider"],
            state=row["state"],
            evidence_type=row["evidence_type"],
            evidence_summary=row["evidence_summary"],
            observed_at=row["observed_at"],
            source=row["source"],
            confidence=row["confidence"],
        )
        for row in rows
        if row["provider"] in provider_list
    }
    return by_provider


def project_session_state_from_probe_rows(
    db: Any,
    user_id: str,
    probe_rows: list[dict[str, Any]],
) -> None:
    """Apply probe history into session state (oldest first so newest wins)."""
    ordered = sorted(
        probe_rows,
        key=lambda row: _parse_iso(row.get("probed_at") or row.get("timestamp"))
        or datetime.min.replace(tzinfo=timezone.utc),
    )
    for row in ordered:
        record_session_evidence_from_probe(db, user_id, row)
