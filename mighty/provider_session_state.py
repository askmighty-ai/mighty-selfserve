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

import re
from dataclasses import dataclass, field
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
    is_account_url,
    is_login_url,
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

# Amex chrome/nav assets — never definitive connected or signed_out evidence.
_AMEX_STATIC_ASSET_RE = re.compile(
    r"(?:/|^)(?:header|footer|globalnav|site-?chrome|axp-webassets|nav-?chrome)"
    r"|/_next/static/|/static/|/assets/|\.(?:css|js|woff2?|png|jpg|svg)(?:\?|$)",
    re.IGNORECASE,
)

VerificationFinalDecision = Literal["connected", "signed_out", "inconclusive"]

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


@dataclass(frozen=True)
class VerificationDecision:
    """Sanitized Amex active-verification decision record for production logs."""

    provider: str
    verification_id: str | None
    access_cycle_id: str | None
    final_decision: VerificationFinalDecision
    decision_reason: str
    final_url: str | None
    login_url_detected: bool
    login_form_detected: bool
    authenticated_page_detected: bool
    session_api_200_detected: bool
    session_api_401_or_403_detected: bool
    passive_needs_login_seen: bool
    evidence_timestamps: dict[str, Any] = field(default_factory=dict)

    def to_log_fields(self) -> dict[str, Any]:
        """Flat fields safe for access-cycle logs (no secrets/bodies)."""
        ts = self.evidence_timestamps or {}
        return {
            "final_decision": self.final_decision,
            "decision_reason": self.decision_reason,
            "final_url": self.final_url or "",
            "login_url_detected": self.login_url_detected,
            "login_form_detected": self.login_form_detected,
            "authenticated_page_detected": self.authenticated_page_detected,
            "session_api_200_detected": self.session_api_200_detected,
            "session_api_401_or_403_detected": self.session_api_401_or_403_detected,
            "passive_needs_login_seen": self.passive_needs_login_seen,
            "evidence_ts_session_api_200": ts.get("session_api_200"),
            "evidence_ts_session_api_401_or_403": ts.get("session_api_401_or_403"),
            "evidence_ts_login_url": ts.get("login_url"),
            "evidence_ts_login_form": ts.get("login_form"),
            "evidence_ts_authenticated_page": ts.get("authenticated_page"),
        }


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
    return [(name, code) for name, code, _ts in _session_api_events(deep_inspect)]


def _session_api_events(
    deep_inspect: dict[str, Any] | None,
) -> list[tuple[str, int, float | None]]:
    """Return (api_name, status_code, start_time_ms) from auth network traces."""
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

    found: list[tuple[str, int, float | None]] = []
    seen: set[tuple[str, int, float | None]] = set()
    for entry in buckets:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        code = _status_code(entry)
        if code is None:
            continue
        raw_ts = entry.get("start_time_ms")
        try:
            ts: float | None = float(raw_ts) if raw_ts is not None else None
        except (TypeError, ValueError):
            ts = None
        for marker in SESSION_API_MARKERS:
            if marker in url:
                key = (marker, code, ts)
                if key not in seen:
                    seen.add(key)
                    found.append(key)
                break
    return found


def _network_request_urls(deep_inspect: dict[str, Any] | None) -> list[str]:
    if not deep_inspect:
        return []
    trace = deep_inspect.get("auth_network_trace") or {}
    urls: list[str] = []
    for key in (
        "requests",
        "highlighted_requests",
        "auth_session_requests",
        "status_401_requests",
        "status_403_requests",
        "redirect_requests",
    ):
        for entry in trace.get(key) or []:
            if isinstance(entry, dict) and entry.get("url"):
                urls.append(str(entry["url"]))
    return urls


def _is_amex_static_asset_url(url: str) -> bool:
    return bool(_AMEX_STATIC_ASSET_RE.search(url or ""))


def _only_amex_static_network(deep_inspect: dict[str, Any] | None) -> bool:
    urls = _network_request_urls(deep_inspect)
    if not urls:
        return False
    return all(_is_amex_static_asset_url(u) for u in urls)


def _probe_observed_at(result: dict[str, Any]) -> datetime:
    return _parse_iso(result.get("probed_at") or result.get("timestamp")) or datetime.now(
        timezone.utc
    )


def verification_decision_to_evidence(
    decision: VerificationDecision,
    result: dict[str, Any],
) -> SessionEvidence | None:
    """Map a verification decision to PSS evidence, or None when inconclusive."""
    if decision.final_decision == "inconclusive":
        return None
    observed_at = _probe_observed_at(result)
    if decision.final_decision == "connected":
        if decision.session_api_200_detected:
            evidence_type = "session_api"
            summary = decision.decision_reason
            confidence: SessionConfidence = "high"
        elif result.get("auth_state") == AUTH_PRIVATE_DATA_VISIBLE:
            evidence_type = "authenticated_private_data"
            summary = "authenticated page with private account data"
            confidence = "medium"
        else:
            evidence_type = "authenticated_page"
            summary = "authenticated overview / account page observed"
            confidence = "medium"
        return SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type=evidence_type,
            evidence_summary=summary,
            observed_at=observed_at,
            source="provider_access_probe",
            confidence=confidence,
        )

    # signed_out
    if decision.session_api_401_or_403_detected:
        evidence_type = "session_api"
    elif decision.login_url_detected:
        evidence_type = "login_page"
    elif decision.login_form_detected:
        evidence_type = "login_page"
    else:
        evidence_type = "login_required"
    return SessionEvidence(
        provider="amex",
        state="signed_out",
        evidence_type=evidence_type,
        evidence_summary=decision.decision_reason,
        observed_at=observed_at,
        source="provider_access_probe",
        confidence="high",
    )


def decide_amex_verification_session(
    result: dict[str, Any],
    *,
    verification_id: str | None = None,
    access_cycle_id: str | None = None,
    passive_needs_login_seen: bool = False,
) -> VerificationDecision:
    """Classify Amex active-verification auth from explicit cycle-scoped evidence.

    Precedence:
      CONNECTED — session API 200 or strong authenticated-page evidence (active cycle)
      SIGNED_OUT — login URL/form or session API 401/403 (active cycle)
      INCONCLUSIVE — timeout/network, static-only, mixed/unordered conflict, or
        passive needs-login not tied to this verification cycle

    Passive ``/amex/needs-login`` reports must not decide the active cycle unless
    correlated via verification_id/access_cycle_id (``passive_needs_login_seen``
    with matching ids already filtered by the caller).
    """
    vid = (verification_id or result.get("verification_id") or None) or None
    if isinstance(vid, str):
        vid = vid.strip() or None
    cycle_id = (access_cycle_id or result.get("access_cycle_id") or vid or None) or None
    if isinstance(cycle_id, str):
        cycle_id = cycle_id.strip() or None

    final_url = str(result.get("final_url") or result.get("url_visited") or "") or None
    deep_inspect = result.get("deep_inspect")
    if not isinstance(deep_inspect, dict):
        deep_inspect = None

    api_events = _session_api_events(deep_inspect)
    api_200 = [(m, c, t) for m, c, t in api_events if c == 200]
    api_denied = [(m, c, t) for m, c, t in api_events if c in {401, 403}]

    login_url_detected = bool(final_url and is_login_url("amex", final_url))
    login_form_detected = bool(result.get("login_form_present"))
    auth_state = str(result.get("auth_state") or "")
    failure_reason = str(result.get("failure_reason") or "")

    # Strong authenticated-page evidence from this probe — do not trust login-biased
    # auth_state alone when private/account signals are present.
    authenticated_page_detected = auth_state in CONNECTED_AUTH_STATES
    if result.get("private_data_detected") and not login_url_detected:
        authenticated_page_detected = True
    if (
        result.get("signed_in_detected")
        and not login_url_detected
        and auth_state not in SIGNED_OUT_AUTH_STATES
    ):
        authenticated_page_detected = True
    if final_url and is_account_url("amex", final_url) and auth_state in CONNECTED_AUTH_STATES:
        authenticated_page_detected = True

    session_api_200_detected = bool(api_200)
    session_api_401_or_403_detected = bool(api_denied)
    only_static = _only_amex_static_network(deep_inspect)

    # Passive needs-login is never definitive for this decision unless the caller
    # already correlated it to this verification (then still requires active-tab proof).
    passive = bool(passive_needs_login_seen or result.get("passive_needs_login_seen"))

    timestamps: dict[str, Any] = {
        "session_api_200": None,
        "session_api_401_or_403": None,
        "login_url": None,
        "login_form": None,
        "authenticated_page": None,
    }
    if api_200:
        ts_vals = [t for _m, _c, t in api_200 if t is not None]
        timestamps["session_api_200"] = max(ts_vals) if ts_vals else None
    if api_denied:
        ts_vals = [t for _m, _c, t in api_denied if t is not None]
        timestamps["session_api_401_or_403"] = max(ts_vals) if ts_vals else None

    def _make(
        final_decision: VerificationFinalDecision,
        decision_reason: str,
    ) -> VerificationDecision:
        return VerificationDecision(
            provider="amex",
            verification_id=vid,
            access_cycle_id=cycle_id or vid,
            final_decision=final_decision,
            decision_reason=decision_reason,
            final_url=final_url,
            login_url_detected=login_url_detected,
            login_form_detected=login_form_detected,
            authenticated_page_detected=authenticated_page_detected,
            session_api_200_detected=session_api_200_detected,
            session_api_401_or_403_detected=session_api_401_or_403_detected,
            passive_needs_login_seen=passive,
            evidence_timestamps=timestamps,
        )

    # Hard network / timeout failures never become signed_out.
    if result.get("status") == "error" or failure_reason in {
        "timeout",
        "network_issue",
        "probe_navigation_error",
        "probe_no_result",
        "blank_or_unloaded_page",
    } or "timeout" in failure_reason or "network" in failure_reason:
        if failure_reason in {
            "timeout",
            "network_issue",
            "probe_navigation_error",
            "probe_no_result",
            "blank_or_unloaded_page",
        } or result.get("status") == "error" or "timeout" in failure_reason or "network" in failure_reason:
            return _make("inconclusive", failure_reason or "probe_error")

    if only_static and not session_api_200_detected and not session_api_401_or_403_detected:
        return _make("inconclusive", "static_assets_only")

    # Passive-only signal without active-tab proof → ignore / inconclusive.
    if (
        passive
        and not session_api_200_detected
        and not session_api_401_or_403_detected
        and not login_url_detected
        and not authenticated_page_detected
        and not login_form_detected
    ):
        return _make("inconclusive", "passive_needs_login_uncorrelated")

    # Build definitive active-cycle events with comparable timestamps.
    # Missing timestamps → None (cannot order against timed events).
    connected_events: list[tuple[str, float | None]] = []
    signed_out_events: list[tuple[str, float | None]] = []

    if session_api_200_detected:
        connected_events.append(("session_api_200", timestamps["session_api_200"]))
    if authenticated_page_detected and not login_url_detected:
        # DOM snapshot is end-of-observation; leave ts unset unless provided.
        timestamps["authenticated_page"] = result.get("authenticated_page_observed_at_ms")
        connected_events.append(("authenticated_page", timestamps["authenticated_page"]))

    if session_api_401_or_403_detected:
        signed_out_events.append(
            ("session_api_401_or_403", timestamps["session_api_401_or_403"])
        )
    if login_url_detected:
        # Final URL is end-state evidence.
        timestamps["login_url"] = result.get("login_url_observed_at_ms")
        signed_out_events.append(("login_url", timestamps["login_url"]))

    # Login form is definitive signed_out when on an explicit login URL, or on a
    # non-account page with no connected evidence. Login chrome on an account /
    # overview URL must not alone force signed_out (common Amex false positive).
    if login_form_detected:
        timestamps["login_form"] = result.get("login_form_observed_at_ms")
        account_url = bool(final_url and is_account_url("amex", final_url))
        if login_url_detected:
            # Login URL already covers signed_out; only add form when it has its
            # own timestamp (avoid None timestamps poisoning ordering).
            if timestamps["login_form"] is not None:
                signed_out_events.append(("login_form", timestamps["login_form"]))
        elif connected_events:
            # Competing evidence — include for ordered conflict resolution.
            signed_out_events.append(("login_form", timestamps["login_form"]))
        elif not account_url:
            signed_out_events.append(("login_form", timestamps["login_form"]))
        # else: account-page login chrome only → ignore as definitive signed_out

    # auth_state login_page / login_required without form/url: only if no connected.
    if (
        not connected_events
        and not signed_out_events
        and (
            auth_state in SIGNED_OUT_AUTH_STATES
            or failure_reason == "login_required"
        )
    ):
        account_url = bool(final_url and is_account_url("amex", final_url))
        if login_url_detected:
            signed_out_events.append(("login_page", None))
        elif login_form_detected and not account_url:
            signed_out_events.append(("login_page", None))
        elif auth_state == AUTH_LOGIN_PAGE and not account_url:
            signed_out_events.append(("login_page", None))
        elif account_url and (login_form_detected or auth_state == AUTH_LOGIN_PAGE):
            return _make("inconclusive", "login_chrome_on_account_page")
        elif auth_state in {AUTH_SESSION_EXPIRED, AUTH_MFA_REQUIRED}:
            signed_out_events.append(("login_page", None))

    if connected_events and signed_out_events:
        c_times = [t for _k, t in connected_events]
        s_times = [t for _k, t in signed_out_events]
        if any(t is None for t in c_times) or any(t is None for t in s_times):
            return _make(
                "inconclusive",
                "conflicting_evidence_unordered",
            )
        c_max = max(t for t in c_times if t is not None)
        s_max = max(t for t in s_times if t is not None)
        if c_max > s_max:
            reason = (
                f"{api_200[0][0]} returned 200"
                if session_api_200_detected
                else "authenticated page observed"
            )
            return _make("connected", reason)
        if s_max > c_max:
            if session_api_401_or_403_detected and timestamps[
                "session_api_401_or_403"
            ] == s_max:
                name, code, _ = max(
                    api_denied,
                    key=lambda e: (e[2] is not None, e[2] or 0),
                )
                return _make("signed_out", f"{name} returned {code}")
            if login_url_detected:
                return _make("signed_out", "login page detected")
            return _make("signed_out", "login form detected")
        return _make("inconclusive", "conflicting_evidence_unordered")

    if connected_events:
        if session_api_200_detected:
            name, code, _ = api_200[0]
            return _make("connected", f"{name} returned {code}")
        return _make("connected", "authenticated page observed")

    if signed_out_events:
        if session_api_401_or_403_detected:
            name, code, _ = api_denied[0]
            return _make("signed_out", f"{name} returned {code}")
        if login_url_detected:
            return _make("signed_out", "login page detected")
        if login_form_detected:
            return _make("signed_out", "login form detected")
        return _make("signed_out", "login required signal")

    if passive:
        return _make("inconclusive", "passive_needs_login_ignored")

    return _make("inconclusive", failure_reason or "insufficient_evidence")


def derive_session_evidence_from_probe(result: dict[str, Any]) -> SessionEvidence | None:
    """Map a probe result to explicit session evidence, or None if inconclusive."""
    provider = str(result.get("provider") or "")
    if not provider:
        return None

    observed_at = _probe_observed_at(result)
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
