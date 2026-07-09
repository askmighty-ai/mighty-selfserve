"""Login Truth — whether Mighty knows the user is logged into each provider site."""

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

LoginVerdict = Literal["YES", "NO", "UNKNOWN"]
ObservationKind = Literal["private", "login"]

PRIVATE_DATA_WINDOW_HOURS = 24

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
        return "saw Membership Rewards balance"
    if provider == "amex" and probe_rule == "membership_rewards_balance":
        return "saw Membership Rewards balance"
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


def compute_login_truth_rows(
    db: Any,
    user_id: str,
    *,
    decrypt_account_fn: Callable[[str, str], dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
    now: datetime | None = None,
) -> list[LoginTruthRow]:
    """Build login-truth rows for each configured probe provider."""
    ensure_probe_tables(db)
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
               matched_private_data_rules, evidence_snippet
        FROM provider_access_probe_runs
        WHERE user_id=?
        ORDER BY probed_at DESC
        """,
        (user_id,),
    ).fetchall():
        provider = row["provider"]
        if provider in probe_by_provider:
            probe_by_provider[provider].append(dict(row))

    rows: list[LoginTruthRow] = []
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
        observations = gather_provider_observations(
            provider,
            account_row=account_row,
            ad_data=ad_data,
            field_observations=fo_by_source.get(source_key, {}),
            probe_rows=probe_by_provider.get(provider, []),
        )
        result = resolve_login_truth(observations, now=now)
        rows.append(
            LoginTruthRow(
                provider=provider,
                login_known=result.login_known,
                evidence=result.evidence,
                last_observed_at=result.last_observed_at,
                source=result.source,
            )
        )
    return rows
