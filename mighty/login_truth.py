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
    SessionEvidence,
    ensure_provider_session_state_tables,
    get_provider_session_states,
    project_session_state_from_probe_rows,
    upsert_provider_session_state,
)

LoginVerdict = Literal["YES", "NO", "UNKNOWN"]
ObservationKind = Literal["private", "login"]
CurrentAccess = Literal["connected_now", "signed_out", "unknown"]
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
]

PRIVATE_DATA_WINDOW_HOURS = 24
CACHED_DATA_FRESH_HOURS = PRIVATE_DATA_WINDOW_HOURS

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
    "unknown": "Unknown",
}

CURRENT_ACCESS_SORT_ORDER: dict[CurrentAccess, int] = {
    "connected_now": 0,
    "signed_out": 1,
    "unknown": 2,
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
    "unknown": (
        "connect_account",
        "Sign into this account once. Mighty will detect it automatically.",
    ),
}

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
    "provider_session_state": ("Provider session state", "provider_session_state"),
    "—": ("—", None),
}

SESSION_STATE_TO_CURRENT_ACCESS: dict[str, CurrentAccess] = {
    "connected": "connected_now",
    "signed_out": "signed_out",
    "unknown": "unknown",
    "error": "unknown",
}


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
    now: datetime | None = None,
    fresh_hours: int = CACHED_DATA_FRESH_HOURS,
) -> CurrentAccountAccess:
    """Current access from session state; cached data from private observations only."""
    now = now or datetime.now(timezone.utc)
    cached_data_state, last_private_data = resolve_cached_data_state(
        observations,
        now=now,
        fresh_hours=fresh_hours,
    )

    if session_state is None or session_state.state == "unknown":
        current_access: CurrentAccess = "unknown"
        evidence = "—"
        source = "—"
        last_verified = None
    else:
        current_access = SESSION_STATE_TO_CURRENT_ACCESS.get(
            session_state.state, "unknown"
        )
        evidence = session_state.evidence_summary or "—"
        source = session_state.source or "provider_session_state"
        last_verified = session_state.observed_at

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
    """Load private/login observations and canonical session state per provider."""
    ensure_probe_tables(db)
    ensure_provider_session_state_tables(db)
    provider_list = list(providers or sorted(PROBE_PROVIDERS))

    account_rows = {
        row["source"]: dict(row)
        for row in db.execute(
            "SELECT source, data_enc, synced_at, sync_status, sync_failure_reason "
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

    # Project probe history into canonical session state (newest explicit evidence wins).
    for provider in provider_list:
        project_session_state_from_probe_rows(
            db, user_id, probe_by_provider.get(provider, [])
        )
        source = PROVIDER_PROBE_CONFIG.get(provider, None)
        source_key = source.source if source else provider
        account_row = account_rows.get(source_key)
        if not account_row:
            continue
        sync_status = account_row.get("sync_status")
        if sync_status != "login_required":
            continue
        when = _parse_iso(account_row.get("synced_at"))
        if when is None:
            continue
        upsert_provider_session_state(
            db,
            user_id,
            SessionEvidence(
                provider=provider,
                state="signed_out",
                evidence_type="login_required",
                evidence_summary="sync_status: login_required",
                observed_at=when,
                source="account_data.sync_status",
                confidence="medium",
            ),
        )

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
) -> list[CurrentAccountAccess]:
    """Build Current Access / Cached Data rows for each configured probe provider."""
    provider_list, observations_by_provider, session_by_provider = (
        _load_provider_observation_context(
            db,
            user_id,
            decrypt_account_fn=decrypt_account_fn,
            providers=providers,
        )
    )
    return [
        resolve_current_account_access(
            provider,
            observations_by_provider[provider],
            session_state=session_by_provider.get(provider),
            now=now,
        )
        for provider in provider_list
    ]


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
        "unknown": sum(1 for row in rows if row.current_access == "unknown"),
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
    )
