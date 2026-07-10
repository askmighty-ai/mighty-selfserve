"""Login Truth — diagnostic model for current account access vs cached data.

Current Access comes from canonical provider_session_state (explicit session
evidence only). Cached private data never implies the user is connected now.

Cached Data answers: does Mighty have stored private account data, and is it fresh?
That is independent of whether the user is signed in right now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from mighty.adapters.amex_extraction import AMEX_MR_KEY
from mighty.provider_access_probe import (
    AUTH_LOGIN_PAGE,
    AUTH_MFA_REQUIRED,
    AUTH_SESSION_EXPIRED,
    PROBE_PROVIDERS,
    PROVIDER_PROBE_CONFIG,
    ensure_probe_tables,
)
from mighty.provider_session_state import (
    ProviderSessionState,
    derive_session_evidence_from_probe,
    ensure_provider_session_state_tables,
    get_provider_session_states,
)
from mighty.session_verification import (
    SessionVerification,
    ensure_session_verification_tables,
    get_session_verifications,
    is_verification_active,
    request_session_verification,
    verification_entry_url,
)

LoginVerdict = Literal["YES", "NO", "UNKNOWN"]
ObservationKind = Literal["private", "login"]
CurrentAccess = Literal["connected_now", "signed_out", "checking", "unknown", "error"]
CachedDataState = Literal["fresh", "stale", "none"]
AccessState = Literal[
    "accessible",
    "needs_first_connection",
    "needs_reauthentication",
    "unknown",
    "unexpected_problem",
]
NextActionType = Literal[
    "none",
    "connect_account",
    "reauthenticate",
    "wait_for_observation",
    "report_problem",
    "verifying",
]

PRIVATE_DATA_WINDOW_HOURS = 24
CACHED_DATA_FRESH_HOURS = PRIVATE_DATA_WINDOW_HOURS
# Live-session freshness for Current Access. Stale connected evidence must not
# render as connected_now; request background re-verification instead.
CURRENT_SESSION_FRESHNESS_SECONDS = 120

LOGIN_AUTH_STATES = frozenset({
    AUTH_LOGIN_PAGE,
    AUTH_SESSION_EXPIRED,
    AUTH_MFA_REQUIRED,
})

# Extracted field keys that indicate private account data but may not match probe rule names.
EXTRACTED_PRIVATE_FIELD_KEYS: dict[str, frozenset[str]] = {
    "amex": frozenset({AMEX_MR_KEY, "membership_rewards_balance", "card_ending", "statement_balance"}),
    "delta": frozenset({"skymiles_number", "miles_balance", "medallion_status", "skymiles", "ecredits"}),
    "hilton": frozenset({"honors_number", "points_balance", "status", "upcoming_stay"}),
    "united": frozenset({"mileageplus_number", "miles_balance", "status", "wallet"}),
    "marriott": frozenset({"bonvoy_number", "points_balance", "status", "upcoming_stay"}),
}


@dataclass(frozen=True)
class TruthObservation:
    observed_at: datetime
    kind: ObservationKind
    evidence: str
    source: str


@dataclass(frozen=True)
class LoginTruthRow:
    provider: str
    login_known: LoginVerdict
    evidence: str
    last_observed_at: str | None
    source: str


@dataclass(frozen=True)
class LoginTruthDisplayRow:
    provider: str
    status_label: str
    evidence: str
    last_confirmed_at: str | None
    source_label: str
    source_internal: str | None = None
    login_known: LoginVerdict = "UNKNOWN"


@dataclass(frozen=True)
class AccessStateRow:
    provider: str
    login_known: LoginVerdict
    access_state: AccessState
    next_action_type: NextActionType
    next_action_text: str
    evidence: str
    last_observed_at: str | None
    source: str


@dataclass(frozen=True)
class AccessStateDisplayRow:
    provider: str
    access_state: AccessState
    access_label: str
    evidence: str
    last_confirmed_at: str | None
    next_action_text: str
    source_label: str
    source_internal: str | None = None
    login_known: LoginVerdict = "UNKNOWN"


@dataclass(frozen=True)
class CurrentAccountAccess:
    """Canonical read-model: current access vs cached private data."""

    provider: str
    current_access: CurrentAccess
    cached_data_state: CachedDataState
    last_verified: str | None
    last_private_data: str | None
    evidence: str
    source: str
    next_action_type: NextActionType
    next_action_text: str
    verification_lifecycle: str | None = None


@dataclass(frozen=True)
class CurrentAccountAccessDisplayRow:
    provider: str
    current_access: CurrentAccess
    current_access_label: str
    cached_data_state: CachedDataState
    cached_data_label: str
    last_verified: str | None
    next_action_text: str
    evidence: str
    source_label: str
    source_internal: str | None = None
    verification_lifecycle: str | None = None


STATUS_LABELS: dict[LoginVerdict, str] = {
    "YES": "Logged in",
    "NO": "Not logged in",
    "UNKNOWN": "Unknown",
}

STATUS_SORT_ORDER: dict[LoginVerdict, int] = {
    "YES": 0,
    "NO": 1,
    "UNKNOWN": 2,
}

CURRENT_ACCESS_LABELS: dict[CurrentAccess, str] = {
    "connected_now": "Connected now",
    "signed_out": "Signed out",
    "checking": "Checking",
    "unknown": "Unknown",
    "error": "Error",
}

CURRENT_ACCESS_SORT_ORDER: dict[CurrentAccess, int] = {
    "connected_now": 0,
    "checking": 1,
    "signed_out": 2,
    "unknown": 3,
    "error": 4,
}

CACHED_DATA_LABELS: dict[CachedDataState, str] = {
    "fresh": "Fresh",
    "stale": "Stale",
    "none": "None",
}

ACCESS_STATE_LABELS: dict[AccessState, str] = {
    "accessible": "Accessible",
    "needs_first_connection": "Not connected yet",
    "needs_reauthentication": "Sign in needed",
    "unknown": "Unknown",
    "unexpected_problem": "Needs investigation",
}

ACCESS_STATE_SORT_ORDER: dict[AccessState, int] = {
    "accessible": 0,
    "needs_reauthentication": 1,
    "needs_first_connection": 2,
    "unknown": 3,
    "unexpected_problem": 4,
}

NEXT_ACTION_BY_CURRENT_ACCESS: dict[CurrentAccess, tuple[NextActionType, str]] = {
    "connected_now": (
        "none",
        "Nothing. Mighty can monitor this account automatically.",
    ),
    "signed_out": (
        "reauthenticate",
        "Sign into this account again.",
    ),
    "checking": (
        "verifying",
        "Mighty is verifying this account now.",
    ),
    "unknown": (
        "connect_account",
        "Sign into this account once. Mighty will detect it automatically.",
    ),
    "error": (
        "report_problem",
        "Mighty could not verify this account automatically.",
    ),
}

NEXT_ACTION_UNKNOWN_INCONCLUSIVE = (
    "wait_for_observation",
    "Mighty could not verify this account automatically.",
)

NEXT_ACTION_BY_STATE: dict[AccessState, tuple[NextActionType, str]] = {
    "accessible": (
        "none",
        "Nothing. Mighty can monitor this account automatically.",
    ),
    "needs_reauthentication": (
        "reauthenticate",
        "Sign into this account again.",
    ),
    "needs_first_connection": (
        "connect_account",
        "Sign into this account once. Mighty will detect it automatically.",
    ),
    "unknown": (
        "wait_for_observation",
        "Visit this account while signed in so Mighty can check it.",
    ),
    "unexpected_problem": (
        "report_problem",
        "Mighty saw conflicting signals. This may be a bug.",
    ),
}

SOURCE_DISPLAY_LABELS: dict[str, tuple[str, str | None]] = {
    "account_data.items": ("Extracted account data", "account_data.items"),
    "field_observations": ("Field observation history", "field_observations"),
    "provider_access_probe": ("Account access probe", "provider_access_probe"),
    "account_data.sync_status": ("Sync status signal", "account_data.sync_status"),
    "account_data.connection_status": ("Connection status", "account_data.connection_status"),
    "provider_session_state": ("Provider session state", "provider_session_state"),
    "extension_amex_connected": ("Amex extension connected", "extension_amex_connected"),
    "extension_amex_needs_login": ("Amex extension needs login", "extension_amex_needs_login"),
    "extension_amex_extract": ("Amex extension extract", "extension_amex_extract"),
    "extension_amex_login_cleared": ("Amex login cleared", "extension_amex_login_cleared"),
    "—": ("—", None),
}

EvidenceCategory = Literal["session", "cached_data", "legacy"]

LEGACY_EVIDENCE_SOURCES = frozenset({
    "account_data.sync_status",
    "account_data.connection_status",
})

LEGACY_EVIDENCE_TYPES = frozenset({
    "connection_status",
    "sync_status",
})


@dataclass(frozen=True)
class SessionEvidenceTimelineEvent:
    """One timestamped evidence row for the Session Evidence Timeline admin page."""

    observed_at: datetime
    provider: str
    category: EvidenceCategory
    evidence_type: str
    result: str
    summary: str
    source: str
    confidence: str | None = None


@dataclass(frozen=True)
class IgnoredEvidenceItem:
    """A timeline signal that did not determine Current Access."""

    label: str
    reason: str


@dataclass(frozen=True)
class CurrentWinnerExplanation:
    """Transparent explanation of why the current session winner won."""

    state_label: str
    reason_headline: str
    evidence_type: str | None
    observed_at: str | None
    confidence: str | None
    ignored: list[IgnoredEvidenceItem]


@dataclass(frozen=True)
class ProviderSessionEvidenceSection:
    """Current session winner plus timeline events for one provider."""

    provider: str
    current: ProviderSessionState | None
    events: list[SessionEvidenceTimelineEvent]
    winner_explanation: CurrentWinnerExplanation | None = None

SESSION_STATE_TO_CURRENT_ACCESS: dict[str, CurrentAccess] = {
    "connected": "connected_now",
    "signed_out": "signed_out",
    "unknown": "unknown",
    "error": "error",
}


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
    now = now or datetime.now(timezone.utc)
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


def needs_session_verification(
    session_state: ProviderSessionState | None,
    provider: str,
    *,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
) -> bool:
    """True when Current Access should request background re-verification.

    Stale connected/signed_out/error evidence for a provider with an automatic
    verification entry URL triggers a check. Missing evidence does not — there
    is nothing to refresh until the user connects once.
    """
    if verification_entry_url(provider) is None:
        return False
    if session_state is None or session_state.state == "unknown":
        return False
    if session_state.state not in {"connected", "signed_out", "error"}:
        return False
    return not is_session_evidence_fresh(
        session_state, now=now, freshness_seconds=freshness_seconds
    )


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


def _private_field_keys(provider: str) -> frozenset[str]:
    keys: set[str] = set(EXTRACTED_PRIVATE_FIELD_KEYS.get(provider, ()))
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if cfg:
        keys.update(rule.label for rule in cfg.private_data_rules)
    return frozenset(keys)


def _private_evidence(provider: str, field_key: str, *, probe_rule: str | None = None) -> str:
    if provider == "amex" and field_key in {AMEX_MR_KEY, "membership_rewards_balance", "points_balance"}:
        return "Observed Membership Rewards balance"
    if provider == "amex" and probe_rule == "membership_rewards_balance":
        return "Observed Membership Rewards balance"
    if provider == "delta" and field_key in {"skymiles_number", "miles_balance", "medallion_status", "skymiles"}:
        label = {
            "skymiles_number": "SkyMiles number",
            "miles_balance": "miles balance",
            "medallion_status": "Medallion status",
            "skymiles": "SkyMiles data",
        }.get(field_key, field_key.replace("_", " "))
        return f"saw {label}"
    if provider == "hilton" and field_key in {"honors_number", "points_balance", "status"}:
        label = {
            "honors_number": "Hilton Honors number",
            "points_balance": "points balance",
            "status": "Honors status",
        }.get(field_key, field_key.replace("_", " "))
        return f"saw {label}"
    if probe_rule:
        return f"saw {probe_rule.replace('_', ' ')}"
    return f"saw {field_key.replace('_', ' ')}"


def _login_evidence(*, auth_state: str | None = None, sync_status: str | None = None) -> str:
    if sync_status == "login_required":
        return "sync_status: login_required"
    if auth_state == AUTH_LOGIN_PAGE:
        return "login page detected"
    if auth_state == AUTH_SESSION_EXPIRED:
        return "session expired / logout page"
    if auth_state == AUTH_MFA_REQUIRED:
        return "MFA / login wall detected"
    return "login required signal"


def _is_login_probe_row(row: dict[str, Any]) -> bool:
    auth_state = row.get("auth_state") or ""
    if auth_state in LOGIN_AUTH_STATES:
        return True
    if row.get("failure_reason") == "login_required":
        return True
    return False


def _items_from_account_data(ad_data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(ad_data.get("items") or ad_data.get("ai_items") or [])


def gather_provider_observations(
    provider: str,
    *,
    account_row: dict[str, Any] | None,
    ad_data: dict[str, Any] | None,
    field_observations: dict[str, dict[str, Any]],
    probe_rows: list[dict[str, Any]],
) -> list[TruthObservation]:
    """Collect timestamped private-data and login signals for one provider."""
    observations: list[TruthObservation] = []
    private_keys = _private_field_keys(provider)

    if account_row and ad_data:
        synced_at = _parse_iso(account_row.get("synced_at"))
        for item in _items_from_account_data(ad_data):
            key = item.get("key") or ""
            if key not in private_keys:
                continue
            value = item.get("value")
            if value is None or str(value).strip() == "":
                continue
            when = synced_at
            fo = field_observations.get(key)
            if fo:
                fo_at = _parse_iso(fo.get("last_seen"))
                if fo_at and (when is None or fo_at > when):
                    when = fo_at
            if when is None:
                continue
            observations.append(
                TruthObservation(
                    observed_at=when,
                    kind="private",
                    evidence=_private_evidence(provider, key),
                    source="account_data.items",
                )
            )

        for key, fo in field_observations.items():
            if key not in private_keys:
                continue
            when = _parse_iso(fo.get("last_seen"))
            if when is None:
                continue
            if any(o.kind == "private" and o.evidence == _private_evidence(provider, key) for o in observations):
                continue
            observations.append(
                TruthObservation(
                    observed_at=when,
                    kind="private",
                    evidence=_private_evidence(provider, key),
                    source="field_observations",
                )
            )

        sync_status = account_row.get("sync_status") or ad_data.get("sync_status")
        if sync_status == "login_required":
            when = _parse_iso(account_row.get("synced_at"))
            if when:
                observations.append(
                    TruthObservation(
                        observed_at=when,
                        kind="login",
                        evidence=_login_evidence(sync_status=sync_status),
                        source="account_data.sync_status",
                    )
                )

    for row in probe_rows:
        probed_at = _parse_iso(row.get("probed_at") or row.get("timestamp"))
        if probed_at is None:
            continue
        if row.get("private_data_detected"):
            rules = row.get("matched_private_data_rules") or []
            if rules:
                evidence = _private_evidence(provider, rules[0], probe_rule=rules[0])
            elif row.get("evidence_snippet"):
                evidence = str(row["evidence_snippet"])[:120]
            else:
                evidence = "private account data detected"
            observations.append(
                TruthObservation(
                    observed_at=probed_at,
                    kind="private",
                    evidence=evidence,
                    source="provider_access_probe",
                )
            )
        elif _is_login_probe_row(row):
            observations.append(
                TruthObservation(
                    observed_at=probed_at,
                    kind="login",
                    evidence=_login_evidence(auth_state=row.get("auth_state")),
                    source="provider_access_probe",
                )
            )

    return observations


def resolve_login_truth(
    observations: list[TruthObservation],
    *,
    now: datetime | None = None,
    window_hours: int = PRIVATE_DATA_WINDOW_HOURS,
) -> LoginTruthRow:
    """Apply YES / NO / UNKNOWN precedence. Private data within the window beats login signals."""
    provider = "unknown"
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    recent = [o for o in observations if o.observed_at >= cutoff]

    private_recent = [o for o in recent if o.kind == "private"]
    if private_recent:
        best = max(private_recent, key=lambda o: o.observed_at)
        return LoginTruthRow(
            provider=provider,
            login_known="YES",
            evidence=best.evidence,
            last_observed_at=best.observed_at.isoformat(),
            source=best.source,
        )

    if not recent:
        return LoginTruthRow(
            provider=provider,
            login_known="UNKNOWN",
            evidence="—",
            last_observed_at=None,
            source="—",
        )

    most_recent = max(recent, key=lambda o: o.observed_at)
    if most_recent.kind == "login":
        return LoginTruthRow(
            provider=provider,
            login_known="NO",
            evidence=most_recent.evidence,
            last_observed_at=most_recent.observed_at.isoformat(),
            source=most_recent.source,
        )

    return LoginTruthRow(
        provider=provider,
        login_known="UNKNOWN",
        evidence="—",
        last_observed_at=None,
        source="—",
    )


def resolve_access_state(
    observations: list[TruthObservation],
    login_row: LoginTruthRow,
    *,
    now: datetime | None = None,
    window_hours: int = PRIVATE_DATA_WINDOW_HOURS,
) -> AccessStateRow:
    """Map Login Truth plus observation history to a user-facing access state."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    private_recent = [
        o for o in observations if o.kind == "private" and o.observed_at >= cutoff
    ]
    private_ever = [o for o in observations if o.kind == "private"]

    if private_recent and login_row.login_known != "YES":
        access_state: AccessState = "unexpected_problem"
    elif login_row.login_known == "YES":
        access_state = "accessible"
    elif login_row.login_known == "NO":
        access_state = "needs_reauthentication"
    elif not private_ever:
        access_state = "needs_first_connection"
    else:
        access_state = "unknown"

    next_action_type, next_action_text = NEXT_ACTION_BY_STATE[access_state]
    return AccessStateRow(
        provider=login_row.provider,
        login_known=login_row.login_known,
        access_state=access_state,
        next_action_type=next_action_type,
        next_action_text=next_action_text,
        evidence=login_row.evidence,
        last_observed_at=login_row.last_observed_at,
        source=login_row.source,
    )


def resolve_cached_data_state(
    observations: list[TruthObservation],
    *,
    now: datetime | None = None,
    fresh_hours: int = CACHED_DATA_FRESH_HOURS,
) -> tuple[CachedDataState, str | None]:
    """Derive cached-data freshness from private observations only."""
    now = now or datetime.now(timezone.utc)
    private = [o for o in observations if o.kind == "private"]
    if not private:
        return "none", None
    newest = max(private, key=lambda o: o.observed_at)
    last_private_data = newest.observed_at.isoformat()
    cutoff = now - timedelta(hours=fresh_hours)
    if newest.observed_at >= cutoff:
        return "fresh", last_private_data
    return "stale", last_private_data


def resolve_current_account_access(
    provider: str,
    observations: list[TruthObservation],
    *,
    session_state: ProviderSessionState | None = None,
    verification: SessionVerification | None = None,
    now: datetime | None = None,
    freshness_seconds: int = CURRENT_SESSION_FRESHNESS_SECONDS,
    fresh_hours: int = CACHED_DATA_FRESH_HOURS,
) -> CurrentAccountAccess:
    """Current access from fresh session evidence; cached data stays independent.

    Stored provider_session_state may still say connected while displayed
    current access becomes checking/unknown once evidence is outside the
    live-session freshness window. Verification lifecycle is read-only here.
    """
    now = now or datetime.now(timezone.utc)
    cached_data_state, last_private_data = resolve_cached_data_state(
        observations,
        now=now,
        fresh_hours=fresh_hours,
    )

    verification_lifecycle = verification.lifecycle if verification else None
    verifying = is_verification_active(verification)
    fresh = is_session_evidence_fresh(
        session_state, now=now, freshness_seconds=freshness_seconds
    )

    if session_state is None or session_state.state == "unknown":
        evidence = "—"
        source = "—"
        last_verified = None
        if verifying:
            current_access: CurrentAccess = "checking"
        elif verification is not None and verification.lifecycle == "failed":
            current_access = "error"
        else:
            current_access = "unknown"
    else:
        evidence = session_state.evidence_summary or "—"
        source = session_state.source or "provider_session_state"
        last_verified = session_state.observed_at
        if fresh:
            current_access = SESSION_STATE_TO_CURRENT_ACCESS.get(
                session_state.state, "unknown"
            )
        elif verifying:
            current_access = "checking"
        elif verification is not None and verification.lifecycle == "failed":
            current_access = "error"
        else:
            # Stale historical evidence must not render as connected_now.
            current_access = "unknown"

    if current_access == "unknown" and (
        (session_state is not None and session_state.state != "unknown" and not fresh)
        or (
            verification is not None
            and verification.lifecycle in {"completed", "timed_out"}
            and not fresh
        )
    ):
        next_action_type, next_action_text = NEXT_ACTION_UNKNOWN_INCONCLUSIVE
    else:
        next_action_type, next_action_text = NEXT_ACTION_BY_CURRENT_ACCESS[current_access]

    return CurrentAccountAccess(
        provider=provider,
        current_access=current_access,
        cached_data_state=cached_data_state,
        last_verified=last_verified,
        last_private_data=last_private_data,
        evidence=evidence,
        source=source,
        next_action_type=next_action_type,
        next_action_text=next_action_text,
        verification_lifecycle=verification_lifecycle,
    )


def _parse_probe_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        import json

        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _load_provider_observation_context(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
) -> tuple[
    list[str],
    dict[str, list[TruthObservation]],
    dict[str, ProviderSessionState],
]:
    """Load private/login observations and canonical session state per provider.

    Read-only: never projects probes or legacy sync_status into provider_session_state.
    Current access comes only from the stored provider_session_state row.
    """
    ensure_probe_tables(db)
    ensure_provider_session_state_tables(db)
    provider_list = list(providers or sorted(PROBE_PROVIDERS))

    account_rows = {
        row["source"]: dict(row)
        for row in db.execute(
            "SELECT source, data_enc, synced_at, sync_status, sync_failure_reason, "
            "connection_status "
            "FROM account_data WHERE user_id=?",
            (user_id,),
        ).fetchall()
    }

    fo_by_source: dict[str, dict[str, dict[str, Any]]] = {}
    for row in db.execute(
        "SELECT source, field_key, last_seen FROM field_observations WHERE user_id=?",
        (user_id,),
    ).fetchall():
        fo_by_source.setdefault(row["source"], {})[row["field_key"]] = dict(row)

    probe_by_provider: dict[str, list[dict[str, Any]]] = {p: [] for p in provider_list}
    for row in db.execute(
        """
        SELECT provider, probed_at, auth_state, private_data_detected, failure_reason,
               matched_private_data_rules, evidence_snippet, deep_inspect_json
        FROM provider_access_probe_runs
        WHERE user_id=?
        ORDER BY probed_at DESC
        """,
        (user_id,),
    ).fetchall():
        provider = row["provider"]
        if provider not in probe_by_provider:
            continue
        probe = dict(row)
        deep = _parse_probe_json(probe.get("deep_inspect_json"))
        if isinstance(deep, dict):
            probe["deep_inspect"] = deep
        probe_by_provider[provider].append(probe)

    session_by_provider = get_provider_session_states(
        db, user_id, providers=provider_list
    )

    observations_by_provider: dict[str, list[TruthObservation]] = {}
    for provider in provider_list:
        source = PROVIDER_PROBE_CONFIG.get(provider, None)
        source_key = source.source if source else provider
        account_row = account_rows.get(source_key)
        ad_data = None
        if account_row and account_row.get("data_enc"):
            try:
                ad_data = decrypt_account_fn(user_id, account_row["data_enc"])
            except Exception:
                ad_data = {}
        observations_by_provider[provider] = gather_provider_observations(
            provider,
            account_row=account_row,
            ad_data=ad_data,
            field_observations=fo_by_source.get(source_key, {}),
            probe_rows=probe_by_provider.get(provider, []),
        )
    return provider_list, observations_by_provider, session_by_provider


def _load_provider_observations(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
) -> list[tuple[str, list[TruthObservation]]]:
    """Load raw observations for each configured probe provider."""
    provider_list, observations_by_provider, _session = _load_provider_observation_context(
        db,
        user_id,
        decrypt_account_fn=decrypt_account_fn,
        providers=providers,
    )
    return [(provider, observations_by_provider[provider]) for provider in provider_list]


def _load_provider_truth_bundles(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
) -> list[tuple[LoginTruthRow, list[TruthObservation]]]:
    """Load login-truth rows and raw observations for each configured probe provider."""
    bundles: list[tuple[LoginTruthRow, list[TruthObservation]]] = []
    for provider, observations in _load_provider_observations(
        db,
        user_id,
        decrypt_account_fn=decrypt_account_fn,
        providers=providers,
    ):
        result = resolve_login_truth(observations, now=now)
        login_row = LoginTruthRow(
            provider=provider,
            login_known=result.login_known,
            evidence=result.evidence,
            last_observed_at=result.last_observed_at,
            source=result.source,
        )
        bundles.append((login_row, observations))
    return bundles


def compute_login_truth_rows(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
) -> list[LoginTruthRow]:
    """Build login-truth rows for each configured probe provider."""
    return [
        login_row
        for login_row, _observations in _load_provider_truth_bundles(
            db,
            user_id,
            decrypt_account_fn=decrypt_account_fn,
            providers=providers,
            now=now,
        )
    ]


def compute_access_state_rows(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
) -> list[AccessStateRow]:
    """Build legacy access-state rows for each configured probe provider."""
    rows: list[AccessStateRow] = []
    for login_row, observations in _load_provider_truth_bundles(
        db,
        user_id,
        decrypt_account_fn=decrypt_account_fn,
        providers=providers,
        now=now,
    ):
        rows.append(resolve_access_state(observations, login_row, now=now))
    return rows


def compute_current_account_access_rows(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
    request_verification: bool = False,
) -> list[CurrentAccountAccess]:
    """Build Current Access / Cached Data rows for each configured probe provider.

    When request_verification is True, stale session evidence enqueues a
    background verification job. That writes only the verification lifecycle
    table — never provider_session_state.
    """
    now = now or datetime.now(timezone.utc)
    provider_list, observations_by_provider, session_by_provider = (
        _load_provider_observation_context(
            db,
            user_id,
            decrypt_account_fn=decrypt_account_fn,
            providers=providers,
        )
    )
    ensure_session_verification_tables(db)
    verifications = get_session_verifications(
        db, user_id, providers=provider_list, now=now
    )

    if request_verification:
        for provider in provider_list:
            session_state = session_by_provider.get(provider)
            if not needs_session_verification(session_state, provider, now=now):
                continue
            if is_verification_active(verifications.get(provider)):
                continue
            created = request_session_verification(db, user_id, provider, now=now)
            if created is not None:
                verifications[provider] = created

    return [
        resolve_current_account_access(
            provider,
            observations_by_provider[provider],
            session_state=session_by_provider.get(provider),
            verification=verifications.get(provider),
            now=now,
        )
        for provider in provider_list
    ]


def classify_evidence_category(
    *,
    evidence_type: str,
    source: str,
) -> EvidenceCategory:
    """Map an evidence row to canonical session, cached data, or legacy."""
    if source in LEGACY_EVIDENCE_SOURCES or evidence_type in LEGACY_EVIDENCE_TYPES:
        return "legacy"
    if evidence_type == "cached_private_data":
        return "cached_data"
    return "session"


def filter_timeline_events(
    events: list[SessionEvidenceTimelineEvent],
    *,
    include_cached_data: bool = False,
    include_legacy: bool = False,
) -> list[SessionEvidenceTimelineEvent]:
    """Default view: canonical session only. Optional cached / legacy rows."""
    visible: list[SessionEvidenceTimelineEvent] = []
    for event in events:
        if event.category == "legacy" and not include_legacy:
            continue
        if event.category == "cached_data" and not include_cached_data:
            continue
        visible.append(event)
    return visible


def _event_dedupe_key(event: SessionEvidenceTimelineEvent) -> tuple[Any, ...]:
    return (
        event.provider,
        event.category,
        event.evidence_type,
        event.result,
        event.summary,
        event.source,
        event.observed_at.isoformat(),
    )


def _session_event_from_state(
    state: ProviderSessionState,
) -> SessionEvidenceTimelineEvent | None:
    when = _parse_iso(state.observed_at)
    if when is None:
        return None
    evidence_type = state.evidence_type or "provider_session_state"
    source = state.source or "provider_session_state"
    return SessionEvidenceTimelineEvent(
        observed_at=when,
        provider=state.provider,
        category=classify_evidence_category(
            evidence_type=evidence_type, source=source
        ),
        evidence_type=evidence_type,
        result=state.state,
        summary=state.evidence_summary or "—",
        source=source,
        confidence=state.confidence,
    )


def _session_events_from_probes(
    provider: str,
    probe_rows: list[dict[str, Any]],
) -> list[SessionEvidenceTimelineEvent]:
    events: list[SessionEvidenceTimelineEvent] = []
    for row in probe_rows:
        payload = dict(row)
        payload["provider"] = provider
        evidence = derive_session_evidence_from_probe(payload)
        if evidence is None:
            continue
        category = classify_evidence_category(
            evidence_type=evidence.evidence_type, source=evidence.source
        )
        events.append(
            SessionEvidenceTimelineEvent(
                observed_at=evidence.observed_at,
                provider=provider,
                category=category,
                evidence_type=evidence.evidence_type,
                result=evidence.state,
                summary=evidence.evidence_summary,
                source=evidence.source,
                confidence=evidence.confidence,
            )
        )
    return events


def _connection_status_event(
    provider: str,
    account_row: dict[str, Any] | None,
    ad_data: dict[str, Any] | None,
) -> SessionEvidenceTimelineEvent | None:
    if not account_row:
        return None
    status = (account_row.get("connection_status") or "").strip()
    if not status and ad_data:
        status = str(ad_data.get("connection_status") or "").strip()
    if status not in {"connected", "needs_login"}:
        return None
    when = _parse_iso(account_row.get("synced_at"))
    if when is None:
        return None
    result = "connected" if status == "connected" else "signed_out"
    return SessionEvidenceTimelineEvent(
        observed_at=when,
        provider=provider,
        category="legacy",
        evidence_type="connection_status",
        result=result,
        summary=f"connection_status={status}",
        source="account_data.connection_status",
        confidence="medium",
    )


def _sync_status_legacy_event(
    provider: str,
    account_row: dict[str, Any] | None,
    ad_data: dict[str, Any] | None,
) -> SessionEvidenceTimelineEvent | None:
    """Legacy sync_status signal — never determines Current Access."""
    if not account_row:
        return None
    status = (account_row.get("sync_status") or "").strip()
    if not status and ad_data:
        status = str(ad_data.get("sync_status") or "").strip()
    if status != "login_required":
        return None
    when = _parse_iso(account_row.get("synced_at"))
    if when is None:
        return None
    return SessionEvidenceTimelineEvent(
        observed_at=when,
        provider=provider,
        category="legacy",
        evidence_type="sync_status",
        result="signed_out",
        summary=f"sync_status={status}",
        source="account_data.sync_status",
        confidence="medium",
    )


def _cached_data_events(
    provider: str,
    observations: list[TruthObservation],
) -> list[SessionEvidenceTimelineEvent]:
    events: list[SessionEvidenceTimelineEvent] = []
    for obs in observations:
        if obs.kind != "private":
            continue
        events.append(
            SessionEvidenceTimelineEvent(
                observed_at=obs.observed_at,
                provider=provider,
                category="cached_data",
                evidence_type="cached_private_data",
                result="cached_data",
                summary=obs.evidence,
                source=obs.source,
                confidence=None,
            )
        )
    return events


def _ignored_label_for_event(event: SessionEvidenceTimelineEvent) -> str:
    if event.category == "legacy":
        return event.summary
    if event.category == "cached_data":
        return event.summary
    return f"{event.evidence_type}={event.result}"


def _ignored_reason_for_event(event: SessionEvidenceTimelineEvent) -> str:
    if event.category == "legacy":
        return "Legacy compatibility signal"
    if event.category == "cached_data":
        return "Cached data never proves current login"
    return "Not the winning session evidence"


def build_current_winner_explanation(
    current: ProviderSessionState | None,
    all_events: list[SessionEvidenceTimelineEvent],
) -> CurrentWinnerExplanation:
    """Explain why Current Access won, and which signals were ignored."""
    state_labels = {
        "connected": "Connected",
        "signed_out": "Signed out",
        "unknown": "Unknown",
        "error": "Error",
    }
    ignored: list[IgnoredEvidenceItem] = []
    seen_labels: set[str] = set()

    winner_key: tuple[Any, ...] | None = None
    if current is not None and current.observed_at:
        winner_when = _parse_iso(current.observed_at)
        if winner_when is not None:
            winner_key = (
                current.evidence_type or "",
                current.state,
                (current.evidence_summary or "").strip(),
                current.source or "",
                winner_when.isoformat(),
            )

    for event in all_events:
        if event.category == "session":
            event_key = (
                event.evidence_type,
                event.result,
                (event.summary or "").strip(),
                event.source,
                event.observed_at.isoformat(),
            )
            if winner_key is not None and event_key == winner_key:
                continue
            # Canonical session rows that are not the winner stay in the timeline;
            # only legacy + cached are listed as ignored for Current Access.
            continue
        label = _ignored_label_for_event(event)
        if label in seen_labels:
            continue
        seen_labels.add(label)
        ignored.append(
            IgnoredEvidenceItem(
                label=label,
                reason=_ignored_reason_for_event(event),
            )
        )

    if current is None or current.state == "unknown":
        return CurrentWinnerExplanation(
            state_label="Unknown",
            reason_headline="No explicit session evidence yet",
            evidence_type=None,
            observed_at=None,
            confidence=None,
            ignored=ignored,
        )

    confidence = (current.confidence or "").upper() or None
    if confidence:
        reason_headline = f"Latest {confidence} confidence evidence:"
    else:
        reason_headline = "Latest session evidence:"

    return CurrentWinnerExplanation(
        state_label=state_labels.get(current.state, current.state.replace("_", " ").title()),
        reason_headline=reason_headline,
        evidence_type=current.evidence_type,
        observed_at=_fmt_iso_for_winner(current.observed_at),
        confidence=current.confidence,
        ignored=ignored,
    )


def gather_session_evidence_timeline(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    provider: str | None = None,
    include_cached_data: bool = False,
    include_legacy: bool = False,
) -> list[ProviderSessionEvidenceSection]:
    """Build per-provider session evidence timelines (newest first).

    Default view shows canonical session evidence only. Cached private data and
    legacy compatibility signals (connection_status / sync_status) are optional
    and never determine Current Access.
    """
    ensure_probe_tables(db)
    ensure_provider_session_state_tables(db)

    if provider:
        if provider not in PROBE_PROVIDERS:
            return []
        provider_list = [provider]
    else:
        provider_list = list(providers) if providers is not None else sorted(PROBE_PROVIDERS)
        provider_list = [p for p in provider_list if p in PROBE_PROVIDERS]

    if not provider_list:
        return []

    provider_list, observations_by_provider, session_by_provider = (
        _load_provider_observation_context(
            db,
            user_id,
            decrypt_account_fn=decrypt_account_fn,
            providers=provider_list,
        )
    )

    account_rows = {
        row["source"]: dict(row)
        for row in db.execute(
            "SELECT source, data_enc, synced_at, sync_status, connection_status "
            "FROM account_data WHERE user_id=?",
            (user_id,),
        ).fetchall()
    }

    probe_by_provider: dict[str, list[dict[str, Any]]] = {p: [] for p in provider_list}
    for row in db.execute(
        """
        SELECT provider, probed_at, auth_state, private_data_detected, failure_reason,
               matched_private_data_rules, evidence_snippet, deep_inspect_json, status
        FROM provider_access_probe_runs
        WHERE user_id=?
        ORDER BY probed_at DESC
        """,
        (user_id,),
    ).fetchall():
        pname = row["provider"]
        if pname not in probe_by_provider:
            continue
        probe = dict(row)
        deep = _parse_probe_json(probe.get("deep_inspect_json"))
        if isinstance(deep, dict):
            probe["deep_inspect"] = deep
        probe_by_provider[pname].append(probe)

    sections: list[ProviderSessionEvidenceSection] = []
    for pname in provider_list:
        all_events: list[SessionEvidenceTimelineEvent] = []
        seen: set[tuple[Any, ...]] = set()

        def _add(event: SessionEvidenceTimelineEvent | None) -> None:
            if event is None:
                return
            key = _event_dedupe_key(event)
            if key in seen:
                return
            seen.add(key)
            all_events.append(event)

        current = session_by_provider.get(pname)
        if current is not None:
            _add(_session_event_from_state(current))

        for event in _session_events_from_probes(pname, probe_by_provider.get(pname, [])):
            _add(event)

        source_cfg = PROVIDER_PROBE_CONFIG.get(pname)
        source_key = source_cfg.source if source_cfg else pname
        account_row = account_rows.get(source_key)
        ad_data = None
        if account_row and account_row.get("data_enc"):
            try:
                ad_data = decrypt_account_fn(user_id, account_row["data_enc"])
            except Exception:
                ad_data = {}
        _add(_sync_status_legacy_event(pname, account_row, ad_data))
        _add(_connection_status_event(pname, account_row, ad_data))

        # Always collect cached rows so winner explanation can list them as ignored.
        for event in _cached_data_events(
            pname, observations_by_provider.get(pname, [])
        ):
            _add(event)

        all_events.sort(key=lambda e: e.observed_at, reverse=True)
        visible = filter_timeline_events(
            all_events,
            include_cached_data=include_cached_data,
            include_legacy=include_legacy,
        )
        explanation = build_current_winner_explanation(current, all_events)
        sections.append(
            ProviderSessionEvidenceSection(
                provider=pname,
                current=current,
                events=visible,
                winner_explanation=explanation,
            )
        )
    return sections


def format_session_evidence_result_label(result: str, *, category: EvidenceCategory) -> str:
    if category == "cached_data":
        return "Cached data"
    if category == "legacy":
        return f"Legacy · {result.replace('_', ' ').title()}"
    labels = {
        "connected": "Connected",
        "signed_out": "Signed out",
        "unknown": "Unknown",
        "error": "Error",
        "cached_data": "Cached data",
    }
    return labels.get(result, result.replace("_", " ").title())


def format_current_winner_line(section: ProviderSessionEvidenceSection) -> str:
    """Human-readable 'Current winner' line for a provider section."""
    explanation = section.winner_explanation
    if explanation is not None:
        if explanation.evidence_type is None:
            return (
                f"Current winner: {explanation.state_label.lower()} — "
                f"{explanation.reason_headline}"
            )
        when = explanation.observed_at or "—"
        return (
            f"Current winner: {explanation.state_label.lower()} because of "
            f"{explanation.evidence_type} at {when}"
        )
    current = section.current
    if current is None or current.state == "unknown":
        return "Current winner: unknown — no explicit session evidence yet"
    when = _fmt_iso_for_winner(current.observed_at)
    summary = current.evidence_summary or "—"
    return f"Current winner: {current.state} because of {summary} at {when}"


def _fmt_iso_for_winner(value: str | None) -> str:
    dt = _parse_iso(value)
    if dt is None:
        return value or "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def format_status_label(verdict: LoginVerdict) -> str:
    return STATUS_LABELS[verdict]


def friendly_source_label(internal: str) -> tuple[str, str | None]:
    return SOURCE_DISPLAY_LABELS.get(internal, (internal.replace("_", " ").title(), internal))


def sort_login_truth_rows(rows: list[LoginTruthRow]) -> list[LoginTruthRow]:
    return sorted(rows, key=lambda row: (STATUS_SORT_ORDER[row.login_known], row.provider))


def login_truth_summary(rows: list[LoginTruthRow]) -> dict[str, int]:
    return {
        "logged_in": sum(1 for row in rows if row.login_known == "YES"),
        "not_logged_in": sum(1 for row in rows if row.login_known == "NO"),
        "unknown": sum(1 for row in rows if row.login_known == "UNKNOWN"),
    }


def format_login_truth_display_row(row: LoginTruthRow) -> LoginTruthDisplayRow:
    source_label, source_internal = friendly_source_label(row.source)
    return LoginTruthDisplayRow(
        provider=row.provider,
        status_label=format_status_label(row.login_known),
        evidence=row.evidence,
        last_confirmed_at=row.last_observed_at,
        source_label=source_label,
        source_internal=source_internal,
        login_known=row.login_known,
    )


def format_access_state_label(access_state: AccessState) -> str:
    return ACCESS_STATE_LABELS[access_state]


def sort_access_state_rows(rows: list[AccessStateRow]) -> list[AccessStateRow]:
    return sorted(
        rows,
        key=lambda row: (ACCESS_STATE_SORT_ORDER[row.access_state], row.provider),
    )


def access_state_summary(rows: list[AccessStateRow]) -> dict[str, int]:
    return {
        "accessible": sum(1 for row in rows if row.access_state == "accessible"),
        "sign_in_needed": sum(1 for row in rows if row.access_state == "needs_reauthentication"),
        "not_connected_or_unknown": sum(
            1 for row in rows if row.access_state in {"needs_first_connection", "unknown"}
        ),
        "needs_investigation": sum(1 for row in rows if row.access_state == "unexpected_problem"),
    }


def format_access_state_display_row(row: AccessStateRow) -> AccessStateDisplayRow:
    source_label, source_internal = friendly_source_label(row.source)
    return AccessStateDisplayRow(
        provider=row.provider,
        access_state=row.access_state,
        access_label=format_access_state_label(row.access_state),
        evidence=row.evidence,
        last_confirmed_at=row.last_observed_at,
        next_action_text=row.next_action_text,
        source_label=source_label,
        source_internal=source_internal,
        login_known=row.login_known,
    )


def format_current_access_label(current_access: CurrentAccess) -> str:
    return CURRENT_ACCESS_LABELS[current_access]


def format_cached_data_label(cached_data_state: CachedDataState) -> str:
    return CACHED_DATA_LABELS[cached_data_state]


def sort_current_account_access_rows(
    rows: list[CurrentAccountAccess],
) -> list[CurrentAccountAccess]:
    return sorted(
        rows,
        key=lambda row: (CURRENT_ACCESS_SORT_ORDER[row.current_access], row.provider),
    )


def current_account_access_summary(rows: list[CurrentAccountAccess]) -> dict[str, int]:
    return {
        "connected_now": sum(1 for row in rows if row.current_access == "connected_now"),
        "signed_out": sum(1 for row in rows if row.current_access == "signed_out"),
        "checking": sum(1 for row in rows if row.current_access == "checking"),
        "unknown": sum(1 for row in rows if row.current_access == "unknown"),
        "error": sum(1 for row in rows if row.current_access == "error"),
    }


def format_current_account_access_display_row(
    row: CurrentAccountAccess,
) -> CurrentAccountAccessDisplayRow:
    source_label, source_internal = friendly_source_label(row.source)
    return CurrentAccountAccessDisplayRow(
        provider=row.provider,
        current_access=row.current_access,
        current_access_label=format_current_access_label(row.current_access),
        cached_data_state=row.cached_data_state,
        cached_data_label=format_cached_data_label(row.cached_data_state),
        last_verified=row.last_verified,
        next_action_text=row.next_action_text,
        evidence=row.evidence,
        source_label=source_label,
        source_internal=source_internal,
        verification_lifecycle=row.verification_lifecycle,
    )
