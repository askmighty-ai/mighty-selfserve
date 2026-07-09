"""Canonical provider session state — live access, separate from cached account data.

Cached private fields (e.g. Membership Rewards balance) must not by themselves mark a
provider as currently connected. Explicit session evidence wins, and newer evidence
replaces older evidence.
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


def upsert_provider_session_state(
    db: Any,
    user_id: str,
    evidence: SessionEvidence,
) -> ProviderSessionState:
    """Persist session evidence when it is newer than the stored observation."""
    ensure_provider_session_state_tables(db)
    existing = get_provider_session_state(db, user_id, evidence.provider)
    if existing and existing.observed_at:
        existing_at = _parse_iso(existing.observed_at)
        if existing_at and existing_at > evidence.observed_at:
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
    evidence = derive_session_evidence_from_probe(result)
    if evidence is None:
        return None
    return upsert_provider_session_state(db, user_id, evidence)


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
