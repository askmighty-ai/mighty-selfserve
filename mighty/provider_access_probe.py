"""Provider Access Probe — diagnostic layer for account reliability Phase 1.

Determines whether Mighty can open a provider account, detect login state, and
see at least one piece of private account-specific data. Does not modify account
state, extraction, or user-facing UI.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ── Probe statuses ────────────────────────────────────────────────────────────

PROBE_NOT_STARTED = "not_started"
PROBE_NEEDS_SIGN_IN = "needs_sign_in"
PROBE_SIGNED_IN_NO_DATA = "signed_in_no_data_seen"
PROBE_SIGNED_IN_DATA = "signed_in_data_seen"
PROBE_BLOCKED = "blocked"
PROBE_ERROR = "error"

PROBE_STATUSES = frozenset({
    PROBE_NOT_STARTED,
    PROBE_NEEDS_SIGN_IN,
    PROBE_SIGNED_IN_NO_DATA,
    PROBE_SIGNED_IN_DATA,
    PROBE_BLOCKED,
    PROBE_ERROR,
})

# ── Evidence types ────────────────────────────────────────────────────────────

EVIDENCE_DOM_TEXT = "dom_text"
EVIDENCE_API_RESPONSE = "api_response"
EVIDENCE_EMBEDDED_STATE = "embedded_state"
EVIDENCE_NETWORK_JSON = "network_json"

EVIDENCE_TYPES = frozenset({
    EVIDENCE_DOM_TEXT,
    EVIDENCE_API_RESPONSE,
    EVIDENCE_EMBEDDED_STATE,
    EVIDENCE_NETWORK_JSON,
})

# Providers with probe configuration (extension runner implements amex + delta first).
PROBE_PROVIDERS = frozenset({"amex", "delta", "hilton", "united", "marriott"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Provider probe rules ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class PrivateDataRule:
    """Pattern that indicates account-specific private data was seen."""

    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ProviderProbeConfig:
    source: str
    entry_url: str
    account_path_res: tuple[re.Pattern[str], ...]
    login_path_res: tuple[re.Pattern[str], ...]
    marketing_path_res: tuple[re.Pattern[str], ...]
    signed_in_signals: tuple[str, ...]
    private_data_rules: tuple[PrivateDataRule, ...]


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


PROVIDER_PROBE_CONFIG: dict[str, ProviderProbeConfig] = {
    "amex": ProviderProbeConfig(
        source="amex",
        entry_url="https://www.americanexpress.com/en-us/account/",
        account_path_res=(
            _rx(r"/en-us/account(?:/|$|\?)"),
            _rx(r"/en-us/rewards(?:/|$|\?)"),
            _rx(r"/account/(?:home|summary|dashboard)"),
        ),
        login_path_res=(
            _rx(r"/en-us/account/log-?in"),
            _rx(r"/(login|sign-?in|logon)(/|$|\?)"),
        ),
        marketing_path_res=(
            _rx(r"/en-us/(?:credit-cards|business|prepaid|gift-cards)(?:/|$|\?)"),
            _rx(r"/en-us/(?:benefits|offers|travel)(?:/|$|\?)"),
        ),
        signed_in_signals=(
            "membership rewards",
            "account home",
            "recent activity",
            "card ending",
            "payment due",
            "available credit",
            "manage account",
            "account services",
            "statement balance",
        ),
        private_data_rules=(
            PrivateDataRule("membership_rewards_balance", _rx(r"membership rewards[^0-9\n]{0,120}([\d][\d,]*)")),
            PrivateDataRule("points_balance", _rx(r"(?:points|rewards)\s*(?:balance|:)?\s*([\d][\d,]*)")),
            PrivateDataRule("card_name", _rx(r"(?:card(?:\s+name)?|product)\s*:\s*([^\n]{4,60})")),
            PrivateDataRule("card_ending", _rx(r"card\s+ending\s+(?:in\s+)?[\d*]{4,}")),
            PrivateDataRule("statement_balance", _rx(r"statement\s+balance[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)")),
        ),
    ),
    "delta": ProviderProbeConfig(
        source="delta",
        entry_url="https://www.delta.com/myprofile/",
        account_path_res=(
            _rx(r"/myprofile"),
            _rx(r"/myskymiles"),
            _rx(r"/my-trips"),
            _rx(r"/wallet"),
            _rx(r"/profile"),
        ),
        login_path_res=(
            _rx(r"/(sign-?in|log-?in|skymiles/login)(/|$|\?)"),
        ),
        marketing_path_res=(
            _rx(r"/us/en/(?:flights|destinations|vacations|deals)(?:/|$|\?)"),
            _rx(r"/content/www/en_US/(?:travel|destinations)(?:/|$|\?)"),
        ),
        signed_in_signals=(
            "my skymiles",
            "skymiles number",
            "medallion",
            "miles available",
            "available miles",
            "my wallet",
            "my trips",
            "welcome back",
            "member since",
            "ecredit",
        ),
        private_data_rules=(
            PrivateDataRule("skymiles_number", _rx(r"skymiles\s*(?:#|number|no\.?)?\s*:?\s*(\d{9,10})")),
            PrivateDataRule("miles_balance", _rx(r"(?:available\s+miles|miles\s+(?:balance|available))[^0-9]{0,20}([\d][\d,]*)")),
            PrivateDataRule("medallion_status", _rx(r"(?:medallion|elite)\s+(?:status|member)\s*:?\s*([^\n]{3,40})")),
            PrivateDataRule("ecredits", _rx(r"(?:e-?credit|ecredit)s?[^$\d]{0,30}\$?([\d][\d,]*(?:\.\d{2})?)")),
            PrivateDataRule("upcoming_trip", _rx(r"(?:upcoming|next)\s+(?:trip|flight)[^\n]{0,80}")),
        ),
    ),
    "hilton": ProviderProbeConfig(
        source="hilton",
        entry_url="https://www.hilton.com/en/hilton-honors/guest/my-account/",
        account_path_res=(_rx(r"/hilton-honors/guest/my-account"), _rx(r"/hilton-honors/guest/profile")),
        login_path_res=(_rx(r"/hilton-honors/login"),),
        marketing_path_res=(_rx(r"/en/(?:hotels|destinations)(?:/|$|\?)"),),
        signed_in_signals=("hilton honors", "my account", "points balance", "member number"),
        private_data_rules=(
            PrivateDataRule("honors_number", _rx(r"honors\s*(?:#|number)?\s*:?\s*(\d{6,12})")),
            PrivateDataRule("points_balance", _rx(r"points[^0-9]{0,30}([\d][\d,]*)")),
            PrivateDataRule("status", _rx(r"(?:honors|member)\s+status\s*:?\s*([^\n]{3,30})")),
            PrivateDataRule("upcoming_stay", _rx(r"(?:upcoming|next)\s+stay[^\n]{0,80}")),
        ),
    ),
    "united": ProviderProbeConfig(
        source="united",
        entry_url="https://www.united.com/en/us/myunited",
        account_path_res=(_rx(r"/myunited"), _rx(r"/mileageplus"), _rx(r"/my-trips")),
        login_path_res=(_rx(r"/en/us/login"),),
        marketing_path_res=(_rx(r"/en/us/(?:flights|destinations|deals)(?:/|$|\?)"),),
        signed_in_signals=("mileageplus", "my united", "available miles", "premier status", "my trips"),
        private_data_rules=(
            PrivateDataRule("mileageplus_number", _rx(r"mileageplus\s*(?:#|number)?\s*:?\s*([A-Z0-9]{8,})")),
            PrivateDataRule("miles_balance", _rx(r"(?:available\s+miles|miles\s+(?:balance|available))[^0-9]{0,20}([\d][\d,]*)")),
            PrivateDataRule("status", _rx(r"(?:premier|status)\s*:?\s*([^\n]{3,30})")),
            PrivateDataRule("wallet", _rx(r"(?:travel\s+)?bank|wallet[^$\d]{0,30}\$?([\d][\d,]*)")),
            PrivateDataRule("upcoming_trip", _rx(r"(?:upcoming|next)\s+(?:trip|flight)[^\n]{0,80}")),
        ),
    ),
    "marriott": ProviderProbeConfig(
        source="marriott",
        entry_url="https://www.marriott.com/loyalty/myAccount/default.mi",
        account_path_res=(_rx(r"/loyalty/myaccount"), _rx(r"/loyalty/myAccount")),
        login_path_res=(_rx(r"/(sign-in|log-in|login)(\.mi|/|$|\?)"),),
        marketing_path_res=(_rx(r"/(hotels|destinations)(?:/|$|\?)"),),
        signed_in_signals=("bonvoy", "my account", "member number", "points balance"),
        private_data_rules=(
            PrivateDataRule("bonvoy_number", _rx(r"bonvoy\s*(?:#|number)?\s*:?\s*(\d{6,12})")),
            PrivateDataRule("points_balance", _rx(r"points[^0-9]{0,30}([\d][\d,]*)")),
            PrivateDataRule("status", _rx(r"(?:elite|member)\s+status\s*:?\s*([^\n]{3,30})")),
            PrivateDataRule("upcoming_stay", _rx(r"(?:upcoming|next)\s+(?:stay|reservation)[^\n]{0,80}")),
        ),
    ),
}


# ── Classification ────────────────────────────────────────────────────────────

def _url_path(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).path or ""
    except Exception:
        return ""


def is_login_url(provider: str, url: str) -> bool:
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False
    path = _url_path(url)
    return any(r.search(path) for r in cfg.login_path_res)


def is_marketing_url(provider: str, url: str) -> bool:
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False
    path = _url_path(url)
    if any(r.search(path) for r in cfg.account_path_res):
        return False
    return any(r.search(path) for r in cfg.marketing_path_res)


def is_account_url(provider: str, url: str) -> bool:
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False
    path = _url_path(url)
    return any(r.search(path) for r in cfg.account_path_res)


def detect_signed_in_from_text(provider: str, url: str, text: str) -> bool:
    """True when authenticated account content is likely present (not marketing/login)."""
    if not text or len(text.strip()) < 80:
        return False
    if is_login_url(provider, url):
        return False
    if is_marketing_url(provider, url):
        return False

    lower = text.lower()
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False

    # Login form heuristics (Amex-style)
    login_hits = sum(
        1 for s in ("sign in to your account", "user id", "show password", "forgot password", "password")
        if s in lower
    )
    if login_hits >= 2 and not any(sig in lower for sig in cfg.signed_in_signals[:3]):
        return False

    if not is_account_url(provider, url):
        # Off-account URL: require stronger private-ish signals
        strong = sum(1 for sig in cfg.signed_in_signals if sig in lower)
        return strong >= 2

    return any(sig in lower for sig in cfg.signed_in_signals)


def detect_private_data(
    provider: str,
    *,
    dom_text: str = "",
    api_text: str = "",
    embedded_text: str = "",
    network_text: str = "",
) -> tuple[bool, str | None, str | None]:
    """Return (found, evidence_type, snippet)."""
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False, None, None

    sources: tuple[tuple[str, str], ...] = (
        (EVIDENCE_DOM_TEXT, dom_text),
        (EVIDENCE_API_RESPONSE, api_text),
        (EVIDENCE_EMBEDDED_STATE, embedded_text),
        (EVIDENCE_NETWORK_JSON, network_text),
    )
    for evidence_type, blob in sources:
        if not blob or len(blob.strip()) < 10:
            continue
        for rule in cfg.private_data_rules:
            m = rule.pattern.search(blob)
            if m:
                snippet = m.group(0).strip()[:240]
                return True, evidence_type, snippet
    return False, None, None


def classify_probe_result(
    *,
    provider: str,
    signed_in_detected: bool,
    private_data_detected: bool,
    blocked: bool = False,
    error: str | None = None,
    url_visited: str = "",
    dom_text: str = "",
) -> str:
    """Map probe observations to a canonical status."""
    if error:
        return PROBE_ERROR
    if blocked:
        return PROBE_BLOCKED
    if not signed_in_detected:
        return PROBE_NEEDS_SIGN_IN
    if private_data_detected:
        return PROBE_SIGNED_IN_DATA
    # Re-check: marketing-only pages must not count as signed in
    if dom_text and url_visited:
        if is_marketing_url(provider, url_visited) and not is_account_url(provider, url_visited):
            return PROBE_NEEDS_SIGN_IN
        if not detect_signed_in_from_text(provider, url_visited, dom_text):
            return PROBE_NEEDS_SIGN_IN
    return PROBE_SIGNED_IN_NO_DATA


def evaluate_probe_payload(provider: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate extension payload and compute authoritative status + evidence."""
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        raise ValueError(f"unsupported probe provider: {provider!r}")

    url = str(payload.get("url_visited") or cfg.entry_url)
    dom_text = str(payload.get("dom_text") or payload.get("page_text") or "")
    api_text = str(payload.get("api_text") or "")
    embedded_text = str(payload.get("embedded_text") or "")
    network_text = str(payload.get("network_text") or "")

    blocked = bool(payload.get("blocked"))
    error = payload.get("error")
    if error:
        error = str(error)

    signed_in = bool(payload.get("signed_in_detected"))
    if dom_text:
        signed_in = detect_signed_in_from_text(provider, url, dom_text)

    private_found, evidence_type, snippet = detect_private_data(
        provider,
        dom_text=dom_text,
        api_text=api_text,
        embedded_text=embedded_text,
        network_text=network_text,
    )
    if payload.get("private_data_detected") and payload.get("evidence_snippet"):
        private_found = True
        evidence_type = str(payload.get("evidence_type") or EVIDENCE_DOM_TEXT)
        snippet = str(payload.get("evidence_snippet"))[:240]

    status = classify_probe_result(
        provider=provider,
        signed_in_detected=signed_in,
        private_data_detected=private_found,
        blocked=blocked,
        error=error,
        url_visited=url,
        dom_text=dom_text,
    )

    failure_reason = payload.get("failure_reason")
    if status == PROBE_NEEDS_SIGN_IN and not failure_reason:
        failure_reason = "login_required"
    elif status == PROBE_SIGNED_IN_NO_DATA and not failure_reason:
        failure_reason = "signed_in_no_private_evidence"
    elif status == PROBE_BLOCKED and not failure_reason:
        failure_reason = "access_blocked"
    elif status == PROBE_ERROR and not failure_reason:
        failure_reason = error or "probe_error"

    return {
        "provider": provider,
        "status": status,
        "url_visited": url,
        "signed_in_detected": signed_in,
        "private_data_detected": private_found,
        "evidence_type": evidence_type,
        "evidence_snippet": snippet,
        "failure_reason": failure_reason,
        "probed_at": payload.get("timestamp") or utc_now_iso(),
    }


# ── Storage ───────────────────────────────────────────────────────────────────

def ensure_probe_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_access_probe_runs (
            run_id                  TEXT PRIMARY KEY,
            user_id                 TEXT NOT NULL,
            provider                TEXT NOT NULL,
            status                  TEXT NOT NULL,
            url_visited             TEXT,
            signed_in_detected      INTEGER NOT NULL DEFAULT 0,
            private_data_detected   INTEGER NOT NULL DEFAULT 0,
            evidence_type           TEXT,
            evidence_snippet        TEXT,
            failure_reason          TEXT,
            probed_at               TEXT NOT NULL,
            created_at              TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_papr_user_provider "
        "ON provider_access_probe_runs(user_id, provider, probed_at DESC)"
    )
    db.commit()


def record_probe_run(db: Any, user_id: str, result: dict[str, Any]) -> str:
    ensure_probe_tables(db)
    run_id = str(uuid.uuid4())
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO provider_access_probe_runs (
            run_id, user_id, provider, status, url_visited,
            signed_in_detected, private_data_detected,
            evidence_type, evidence_snippet, failure_reason,
            probed_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            user_id,
            result["provider"],
            result["status"],
            result.get("url_visited"),
            1 if result.get("signed_in_detected") else 0,
            1 if result.get("private_data_detected") else 0,
            result.get("evidence_type"),
            result.get("evidence_snippet"),
            result.get("failure_reason"),
            result.get("probed_at") or now,
            now,
        ),
    )
    db.commit()
    return run_id


def get_latest_probe_per_provider(db: Any, user_id: str) -> dict[str, dict[str, Any]]:
    ensure_probe_tables(db)
    rows = db.execute(
        """
        SELECT r.*
        FROM provider_access_probe_runs r
        INNER JOIN (
            SELECT provider, MAX(probed_at) AS max_probed
            FROM provider_access_probe_runs
            WHERE user_id = ?
            GROUP BY provider
        ) latest ON r.provider = latest.provider AND r.probed_at = latest.max_probed
        WHERE r.user_id = ?
        ORDER BY r.provider
        """,
        (user_id, user_id),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        d = dict(row)
        d["signed_in_detected"] = bool(d.get("signed_in_detected"))
        d["private_data_detected"] = bool(d.get("private_data_detected"))
        out[d["provider"]] = d
    return out


def probe_summary_for_providers(providers: tuple[str, ...] | list[str] | None = None) -> list[dict[str, Any]]:
    """Build summary rows for admin display, filling not_started for missing providers."""
    sources = list(providers or sorted(PROBE_PROVIDERS))
    return [{"provider": p, "status": PROBE_NOT_STARTED} for p in sources]


def merge_probe_summaries(
    latest: dict[str, dict[str, Any]],
    providers: tuple[str, ...] | list[str] | None = None,
) -> list[dict[str, Any]]:
    sources = list(providers or sorted(PROBE_PROVIDERS))
    rows = []
    for p in sources:
        if p in latest:
            rows.append(latest[p])
        else:
            rows.append({"provider": p, "status": PROBE_NOT_STARTED})
    return rows


def row_to_json(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": row.get("provider"),
        "status": row.get("status"),
        "url_visited": row.get("url_visited"),
        "signed_in_detected": row.get("signed_in_detected"),
        "private_data_detected": row.get("private_data_detected"),
        "evidence_type": row.get("evidence_type"),
        "evidence_snippet": row.get("evidence_snippet"),
        "timestamp": row.get("probed_at") or row.get("timestamp"),
        "failure_reason": row.get("failure_reason"),
        "run_id": row.get("run_id"),
    }
