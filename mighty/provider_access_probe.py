"""Provider Access Probe — diagnostic layer for account reliability Phase 1.

Determines whether Mighty can open a provider account, detect login state, and
see at least one piece of private account-specific data. Does not modify account
state, extraction, or user-facing UI.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

# ── Auth states (structured probe diagnostics) ────────────────────────────────

AUTH_UNKNOWN = "unknown"
AUTH_MARKETING = "marketing"
AUTH_LOGIN_PAGE = "login_page"
AUTH_LOGIN_SUBMITTED = "login_submitted"
AUTH_MFA_REQUIRED = "mfa_required"
AUTH_AUTHENTICATED_NO_PRIVATE_DATA = "authenticated_no_private_data"
AUTH_PRIVATE_DATA_VISIBLE = "private_data_visible"
AUTH_BOT_BLOCKED = "bot_blocked"
AUTH_SESSION_EXPIRED = "session_expired"
AUTH_ERROR = "error"

AUTH_STATES = frozenset({
    AUTH_UNKNOWN,
    AUTH_MARKETING,
    AUTH_LOGIN_PAGE,
    AUTH_LOGIN_SUBMITTED,
    AUTH_MFA_REQUIRED,
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_PRIVATE_DATA_VISIBLE,
    AUTH_BOT_BLOCKED,
    AUTH_SESSION_EXPIRED,
    AUTH_ERROR,
})

AUTH_TO_PROBE_STATUS: dict[str, str] = {
    AUTH_PRIVATE_DATA_VISIBLE: PROBE_SIGNED_IN_DATA,
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA: PROBE_SIGNED_IN_NO_DATA,
    AUTH_MARKETING: PROBE_NEEDS_SIGN_IN,
    AUTH_LOGIN_PAGE: PROBE_NEEDS_SIGN_IN,
    AUTH_LOGIN_SUBMITTED: PROBE_NEEDS_SIGN_IN,
    AUTH_MFA_REQUIRED: PROBE_NEEDS_SIGN_IN,
    AUTH_UNKNOWN: PROBE_NEEDS_SIGN_IN,
    AUTH_BOT_BLOCKED: PROBE_BLOCKED,
    AUTH_SESSION_EXPIRED: PROBE_NEEDS_SIGN_IN,
    AUTH_ERROR: PROBE_ERROR,
}

# ── Blank / unloaded page diagnostics ───────────────────────────────────────────

FAILURE_BLANK_OR_UNLOADED = "blank_or_unloaded_page"
BLANK_PAGE_MIN_TEXT_LENGTH = 20

PAGE_DIAGNOSTIC_KEYS: tuple[str, ...] = (
    "ready_state",
    "body_exists",
    "body_text_length",
    "visible_text_preview",
    "page_title",
    "iframe_count",
    "input_count",
    "button_count",
    "password_input_count",
    "final_url",
    "classifier_started_at",
    "dom_wait_ms",
    "content_script_error",
)

# Deep inspect — manual probe diagnostics (Phase 1, Amex first).
DEEP_INSPECT_PROVIDERS: frozenset[str] = frozenset({"amex"})

DEEP_INSPECT_KEYS: tuple[str, ...] = (
    "outer_html_length",
    "outer_html_preview",
    "iframe_count",
    "iframes",
    "shadow_root_count",
    "script_count",
    "script_srcs",
    "stylesheet_hrefs",
    "navigation_timing",
    "cookie_names",
    "local_storage_keys",
    "session_storage_keys",
    "js_errors",
    "content_script_injection_succeeded",
    "final_url",
    "page_title",
    "ready_state",
    "visible_text_preview",
)

SPA_ROOT_KEYS: frozenset[str] = frozenset({
    "key", "exists", "child_element_count", "inner_html_length", "text_length",
})

RESOURCE_SUMMARY_KEYS: frozenset[str] = frozenset({
    "name", "duration_ms", "initiator_type", "response_status",
})

IFRAME_METADATA_KEYS: frozenset[str] = frozenset({"index", "src", "id", "name", "sandbox"})

SENSITIVE_QUERY_PARAM_RE = re.compile(
    r"^(token|auth|session|key|secret|password|csrf|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|api[_-]?key|code|signature|sig)$",
    re.IGNORECASE,
)
AUTH_NETWORK_KEYWORD_RE = re.compile(
    r"session|login|auth|token|csrf|sso|identity",
    re.IGNORECASE,
)

NETWORK_TRACE_ENTRY_KEYS: frozenset[str] = frozenset({
    "url",
    "method",
    "resource_type",
    "initiator_type",
    "status_code",
    "status_text",
    "start_time_ms",
    "duration_ms",
    "redirect_count",
    "redirect_urls",
    "same_origin",
    "credentials",
    "mode",
    "with_credentials",
    "request_header_names",
    "response_header_names",
    "cors_error",
    "network_error",
    "blocked",
    "auth_keyword_match",
    "highlighted",
})

FORBIDDEN_NETWORK_TRACE_KEYS: frozenset[str] = frozenset({
    "request_body",
    "response_body",
    "response_headers",
    "request_headers",
    "authorization",
    "cookie",
    "set-cookie",
    "body",
    "headers",
})

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

# Manual probe runner — one provider at a time (Phase 1A.5).
MANUAL_PROBE_PROVIDERS: tuple[str, ...] = ("amex", "delta")

PROBE_LIFECYCLE_IDLE = "idle"
PROBE_LIFECYCLE_RUNNING = "running"
PROBE_LIFECYCLE_DONE = "done"
PROBE_LIFECYCLE_ERROR = "error"

PROBE_LIFECYCLES = frozenset({
    PROBE_LIFECYCLE_IDLE,
    PROBE_LIFECYCLE_RUNNING,
    PROBE_LIFECYCLE_DONE,
    PROBE_LIFECYCLE_ERROR,
})


class ConcurrentProbeError(Exception):
    """Raised when a manual probe is already running for this user."""


def is_automatic_probe_disabled() -> bool:
    """True in development/admin-test mode — extension must not auto-run probes."""
    if os.environ.get("DISABLE_AUTOMATIC_PROVIDER_PROBES", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("MIGHTY_ADMIN_TEST", "").lower() in ("1", "true", "yes"):
        return True
    return os.environ.get("FLASK_ENV", "").lower() == "development"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Provider probe rules ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class PrivateDataRule:
    """Pattern that indicates account-specific private data was seen."""

    label: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class ProbeRule:
    """Text (and optional URL) rule for auth-state classification."""

    label: str
    text_pattern: re.Pattern[str]
    url_pattern: re.Pattern[str] | None = None


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# Shared blocking / MFA / session-expired text rules (all providers).
SHARED_BLOCKING_RULES: tuple[ProbeRule, ...] = (
    ProbeRule("captcha", _rx(r"captcha|recaptcha|hcaptcha")),
    ProbeRule("bot_detection", _rx(r"bot detection|verify you are human|unusual activity")),
    ProbeRule("access_denied", _rx(r"access denied|request blocked")),
)
SHARED_MFA_RULES: tuple[ProbeRule, ...] = (
    ProbeRule("verification_code", _rx(r"verification code|one-?time passcode|security code")),
    ProbeRule("two_factor", _rx(r"two-?factor|multi-?factor|2fa")),
    ProbeRule("identity_challenge", _rx(r"authenticate your identity|confirm (?:it'?s|your identity)")),
)
SHARED_SESSION_EXPIRED_RULES: tuple[ProbeRule, ...] = (
    ProbeRule("session_expired", _rx(r"session (?:has )?expired|session timed out")),
    ProbeRule("sign_in_again", _rx(r"please sign in again|sign in again to continue")),
    ProbeRule("session_ended", _rx(r"your session has ended|session is no longer valid")),
)


@dataclass(frozen=True)
class ProviderProbeConfig:
    source: str
    entry_url: str
    account_path_res: tuple[re.Pattern[str], ...]
    login_path_res: tuple[re.Pattern[str], ...]
    marketing_path_res: tuple[re.Pattern[str], ...]
    signed_in_signals: tuple[str, ...]
    private_data_rules: tuple[PrivateDataRule, ...]
    login_rules: tuple[ProbeRule, ...] = ()
    blocking_rules: tuple[ProbeRule, ...] = ()
    mfa_rules: tuple[ProbeRule, ...] = ()
    session_expired_rules: tuple[ProbeRule, ...] = ()


PROVIDER_PROBE_CONFIG: dict[str, ProviderProbeConfig] = {
    "amex": ProviderProbeConfig(
        source="amex",
        entry_url="https://global.americanexpress.com/overview",
        account_path_res=(
            _rx(r"/overview(?:/|$|\?)"),
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
        login_rules=(
            ProbeRule("login_url", _rx(r"(?!)"), _rx(r"/en-us/account/log-?in|/(?:login|sign-?in|logon)(?:/|$|\?)")),
            ProbeRule("sign_in_heading", _rx(r"sign in to your account")),
            ProbeRule("login_form_fields", _rx(r"user id.{0,80}password|password.{0,80}user id")),
        ),
        blocking_rules=SHARED_BLOCKING_RULES,
        mfa_rules=SHARED_MFA_RULES,
        session_expired_rules=SHARED_SESSION_EXPIRED_RULES,
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
        login_rules=(
            ProbeRule("login_url", _rx(r"(?!)"), _rx(r"/(?:sign-?in|log-?in|skymiles/login)(?:/|$|\?)")),
            ProbeRule("skymiles_login_heading", _rx(r"skymiles number or username")),
            ProbeRule("login_form_fields", _rx(r"(?:sign in|log in).{0,80}password|password.{0,80}(?:username|skymiles)")),
        ),
        blocking_rules=SHARED_BLOCKING_RULES,
        mfa_rules=SHARED_MFA_RULES,
        session_expired_rules=SHARED_SESSION_EXPIRED_RULES,
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


def match_probe_rules(
    rules: tuple[ProbeRule, ...],
    *,
    text: str = "",
    url: str = "",
) -> list[str]:
    """Return labels for rules matched against page text and/or URL path."""
    matched: list[str] = []
    path = _url_path(url)
    for rule in rules:
        if rule.url_pattern is not None and path and rule.url_pattern.search(path):
            matched.append(rule.label)
            continue
        if text and rule.text_pattern.search(text):
            matched.append(rule.label)
    return matched


def detect_form_signals(text: str, payload: dict[str, Any] | None = None) -> dict[str, bool]:
    """Infer login/MFA form presence from DOM text and optional extension hints."""
    payload = payload or {}
    lower = text.lower() if text else ""

    username_hints = (
        "user id", "username", "skymiles number or username", "skymiles number",
        "email address", "account number",
    )
    password_hints = ("password", "show password", "forgot password", "enter password")
    mfa_hints = (
        "verification code", "one-time passcode", "security code", "two-factor",
        "multi-factor", "authenticate your identity", "enter the code",
    )

    username_field = any(h in lower for h in username_hints)
    password_field = any(h in lower for h in password_hints)
    login_form = username_field and password_field
    mfa_signal = any(h in lower for h in mfa_hints)

    return {
        "login_form_present": bool(payload.get("login_form_present", login_form)),
        "password_field_present": bool(payload.get("password_field_present", password_field)),
        "username_field_present": bool(payload.get("username_field_present", username_field)),
        "mfa_signal_present": bool(payload.get("mfa_signal_present", mfa_signal)),
    }


def detect_blocking_signals(
    provider: str,
    *,
    text: str = "",
    url: str = "",
    payload: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    payload = payload or {}
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    rules = cfg.blocking_rules if cfg else SHARED_BLOCKING_RULES
    matched = match_probe_rules(rules, text=text, url=url)
    present = bool(matched) or bool(payload.get("bot_block_signal_present")) or bool(payload.get("blocked"))
    return present, matched


def detect_session_expired_signals(
    provider: str,
    *,
    text: str = "",
    payload: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    payload = payload or {}
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    rules = cfg.session_expired_rules if cfg else SHARED_SESSION_EXPIRED_RULES
    matched = match_probe_rules(rules, text=text)
    present = bool(matched) or bool(payload.get("session_expired_signal_present"))
    return present, matched


def detect_mfa_signals(
    provider: str,
    *,
    text: str = "",
    payload: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    payload = payload or {}
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    rules = cfg.mfa_rules if cfg else SHARED_MFA_RULES
    matched = match_probe_rules(rules, text=text)
    form = detect_form_signals(text, payload)
    present = bool(matched) or form["mfa_signal_present"]
    return present, matched


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
    url: str = "",
    dom_text: str = "",
    api_text: str = "",
    embedded_text: str = "",
    network_text: str = "",
) -> tuple[bool, str | None, str | None, list[str]]:
    """Return (found, evidence_type, snippet, matched_rule_labels)."""
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        return False, None, None, []

    if url and is_marketing_url(provider, url) and not is_account_url(provider, url):
        return False, None, None, []

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
                return True, evidence_type, snippet, [rule.label]
    return False, None, None, []


def extract_page_diagnostics(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize page diagnostics from extension probe payload."""
    payload = payload or {}
    diag: dict[str, Any] = {}
    nested = payload.get("page_diagnostics")
    if isinstance(nested, dict):
        diag.update(nested)
    for key in PAGE_DIAGNOSTIC_KEYS:
        if key in payload and payload.get(key) is not None:
            diag[key] = payload.get(key)
    if not diag.get("final_url"):
        diag["final_url"] = payload.get("final_url") or payload.get("url_visited")
    if not diag.get("page_title") and payload.get("page_title"):
        diag["page_title"] = payload.get("page_title")
    if diag.get("body_text_length") is None and payload.get("dom_text") is not None:
        diag["body_text_length"] = len(str(payload.get("dom_text") or ""))
    return diag


def _cookie_name_only(raw: str) -> str:
    """Return cookie name without value."""
    name = str(raw).strip()
    if "=" in name:
        name = name.split("=", 1)[0].strip()
    return name


def sanitize_probe_url(raw_url: str) -> str:
    """Strip sensitive query param values from probe URLs."""
    url = str(raw_url or "").strip()
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
        sanitized_pairs = []
        for name, value in parse_qsl(parts.query, keep_blank_values=True):
            if SENSITIVE_QUERY_PARAM_RE.match(name):
                sanitized_pairs.append((name, "[REDACTED]"))
            else:
                sanitized_pairs.append((name, value))
        query = urlencode(sanitized_pairs)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    except Exception:
        return url.split("?", 1)[0]


def _sanitize_network_trace_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in entry.items():
        if key in FORBIDDEN_NETWORK_TRACE_KEYS:
            continue
        if key not in NETWORK_TRACE_ENTRY_KEYS:
            continue
        if key == "url":
            out[key] = sanitize_probe_url(str(value))
        elif key == "redirect_urls" and isinstance(value, list):
            out[key] = [sanitize_probe_url(str(u)) for u in value[:10]]
        elif key in ("request_header_names", "response_header_names") and isinstance(value, list):
            out[key] = [str(h).split(":", 1)[0].strip() for h in value[:50] if str(h).strip()]
        elif key in ("status_code", "start_time_ms", "duration_ms", "redirect_count"):
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                pass
        elif key in ("same_origin", "with_credentials", "cors_error", "network_error", "blocked",
                     "auth_keyword_match", "highlighted"):
            out[key] = bool(value)
        elif value is not None:
            out[key] = str(value)
    if out.get("url") and "auth_keyword_match" not in out:
        out["auth_keyword_match"] = bool(AUTH_NETWORK_KEYWORD_RE.search(out["url"]))
    return out


def sanitize_auth_network_trace(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize auth network trace; never persist secrets or header values."""
    raw = raw or {}
    out: dict[str, Any] = {}

    if raw.get("observation_ms") is not None:
        try:
            out["observation_ms"] = int(raw["observation_ms"])
        except (TypeError, ValueError):
            pass
    if raw.get("request_count") is not None:
        try:
            out["request_count"] = int(raw["request_count"])
        except (TypeError, ValueError):
            pass

    status_counts = raw.get("status_counts")
    if isinstance(status_counts, dict):
        out["status_counts"] = {str(k): int(v) for k, v in status_counts.items() if v is not None}

    for key in (
        "highlighted_requests",
        "auth_session_requests",
        "status_401_requests",
        "status_403_requests",
        "redirect_requests",
        "cors_or_network_failures",
        "requests",
    ):
        items = raw.get(key)
        if isinstance(items, list):
            out[key] = [_sanitize_network_trace_entry(i) for i in items[:100]]

    out["diagnostic_summary"] = compute_auth_network_diagnostic(out, cookie_names=None)
    return out


def compute_auth_network_diagnostic(trace: dict[str, Any], cookie_names: list[str] | None = None) -> str:
    """Evidence-based summary from captured network trace metadata."""
    if not trace:
        return "no authentication network trace captured"

    highlighted = trace.get("highlighted_requests") or trace.get("status_401_requests") or []
    read_session = [
        r for r in highlighted
        if isinstance(r, dict) and "ReadUserSession.v1" in str(r.get("url") or "")
    ]
    update_session = [
        r for r in highlighted
        if isinstance(r, dict) and "UpdateUserSession.v1" in str(r.get("url") or "")
    ]
    status_401 = trace.get("status_401_requests") or [
        r for r in (trace.get("requests") or [])
        if isinstance(r, dict) and r.get("status_code") == 401
    ]
    status_403 = trace.get("status_403_requests") or []
    cors_failures = trace.get("cors_or_network_failures") or []

    parts: list[str] = []

    if read_session:
        parts.append(f"ReadUserSession.v1 returned {read_session[0].get('status_code', '?')}")
    if update_session:
        parts.append(f"UpdateUserSession.v1 returned {update_session[0].get('status_code', '?')}")

    if not parts and status_401:
        parts.append(f"{len(status_401)} request(s) returned 401")

    if status_403:
        parts.append(f"{len(status_403)} request(s) returned 403")

    if cors_failures:
        parts.append(f"{len(cors_failures)} CORS/network failure(s) observed")

    if cookie_names:
        parts.append("cookies present at document level")
    elif cookie_names is not None:
        parts.append("no document cookies observed")

    session_calls = read_session + update_session
    if session_calls:
        cred_flags = [
            r.get("with_credentials") for r in session_calls if isinstance(r, dict)
        ]
        cred_modes = [
            r.get("credentials") for r in session_calls if isinstance(r, dict) and r.get("credentials")
        ]
        if any(c is True for c in cred_flags) or any(c == "include" for c in cred_modes):
            parts.append("session requests attempted with credentials/include")
        else:
            parts.append("session request credential/cookie attachment not confirmed as include")

    if not parts:
        return "network trace captured; no auth/session 401/403 or highlighted failures in summary"

    return "; ".join(parts)


def _sanitize_spa_root(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {"key": str(entry.get("key") or "")}
    out["exists"] = bool(entry.get("exists"))
    if out["exists"]:
        for key in ("child_element_count", "inner_html_length", "text_length"):
            if entry.get(key) is not None:
                try:
                    out[key] = int(entry[key])
                except (TypeError, ValueError):
                    pass
    return out


def _sanitize_resource_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in RESOURCE_SUMMARY_KEYS:
        if key in entry and entry.get(key) is not None:
            if key == "duration_ms":
                try:
                    out[key] = int(entry[key])
                except (TypeError, ValueError):
                    pass
            elif key == "response_status":
                try:
                    out[key] = int(entry[key])
                except (TypeError, ValueError):
                    pass
            else:
                out[key] = str(entry[key])
    return out


def _sanitize_iframe_entry(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in IFRAME_METADATA_KEYS:
        if key in entry and entry.get(key) is not None:
            out[key] = entry[key]
    return out


def sanitize_deep_inspect(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deep inspect payload; never persist cookie/storage values."""
    raw = raw or {}
    out: dict[str, Any] = {}

    if raw.get("outer_html_length") is not None:
        try:
            out["outer_html_length"] = int(raw["outer_html_length"])
        except (TypeError, ValueError):
            pass
    if raw.get("outer_html_preview") is not None:
        out["outer_html_preview"] = str(raw["outer_html_preview"])[:2000]

    if raw.get("iframe_count") is not None:
        try:
            out["iframe_count"] = int(raw["iframe_count"])
        except (TypeError, ValueError):
            pass

    iframes = raw.get("iframes")
    if isinstance(iframes, list):
        out["iframes"] = [_sanitize_iframe_entry(f) for f in iframes]

    if raw.get("shadow_root_count") is not None:
        try:
            out["shadow_root_count"] = int(raw["shadow_root_count"])
        except (TypeError, ValueError):
            pass
    if raw.get("script_count") is not None:
        try:
            out["script_count"] = int(raw["script_count"])
        except (TypeError, ValueError):
            pass

    script_srcs = raw.get("script_srcs")
    if isinstance(script_srcs, list):
        out["script_srcs"] = [str(s) for s in script_srcs[:20]]

    stylesheet_hrefs = raw.get("stylesheet_hrefs")
    if isinstance(stylesheet_hrefs, list):
        out["stylesheet_hrefs"] = [str(h) for h in stylesheet_hrefs]

    nav = raw.get("navigation_timing")
    if isinstance(nav, dict):
        out["navigation_timing"] = {
            k: nav[k] for k in (
                "dom_content_loaded_ms",
                "load_event_ms",
                "response_start_ms",
                "duration_ms",
            )
            if k in nav and nav[k] is not None
        }

    cookie_names = raw.get("cookie_names")
    if isinstance(cookie_names, list):
        out["cookie_names"] = [_cookie_name_only(c) for c in cookie_names if str(c).strip()]

    for key in ("local_storage_keys", "session_storage_keys"):
        keys = raw.get(key)
        if isinstance(keys, list):
            out[key] = [str(k) for k in keys]

    js_errors = raw.get("js_errors")
    if isinstance(js_errors, list):
        sanitized_errors: list[Any] = []
        for err in js_errors[:50]:
            if isinstance(err, dict):
                sanitized_errors.append({
                    k: err[k]
                    for k in ("message", "source", "line", "col")
                    if k in err and err[k] is not None
                })
            elif err is not None:
                sanitized_errors.append({"message": str(err)[:500]})
        out["js_errors"] = sanitized_errors

    if raw.get("content_script_injection_succeeded") is not None:
        out["content_script_injection_succeeded"] = bool(raw["content_script_injection_succeeded"])

    for key in ("final_url", "page_title", "ready_state", "visible_text_preview"):
        if raw.get(key) is not None:
            val = str(raw[key])
            out[key] = val[:500] if key == "visible_text_preview" else val

    spa_roots = raw.get("spa_roots")
    if isinstance(spa_roots, list):
        out["spa_roots"] = [_sanitize_spa_root(r) for r in spa_roots]

    mutation = raw.get("mutation_timeline")
    if isinstance(mutation, dict):
        mt: dict[str, Any] = {}
        for key in (
            "total_count", "first_mutation_ms", "last_mutation_ms",
            "observe_duration_ms", "mutation_activity",
        ):
            if mutation.get(key) is not None:
                if key == "mutation_activity":
                    mt[key] = str(mutation[key])
                else:
                    try:
                        mt[key] = int(mutation[key])
                    except (TypeError, ValueError):
                        pass
        out["mutation_timeline"] = mt

    console_diag = raw.get("console_diagnostics")
    if isinstance(console_diag, list):
        out["console_diagnostics"] = [
            {
                "level": str(item.get("level") or ""),
                "message": str(item.get("message") or "")[:500],
            }
            for item in console_diag[:50]
            if isinstance(item, dict) and item.get("message")
        ]

    resources = raw.get("resource_diagnostics")
    if isinstance(resources, dict):
        rd: dict[str, Any] = {}
        for key in ("js_count", "css_count", "fetch_xhr_count"):
            if resources.get(key) is not None:
                try:
                    rd[key] = int(resources[key])
                except (TypeError, ValueError):
                    pass
        for key in ("failed_loads", "slow_loads"):
            items = resources.get(key)
            if isinstance(items, list):
                rd[key] = [_sanitize_resource_entry(i) for i in items[:20]]
        out["resource_diagnostics"] = rd

    frameworks = raw.get("framework_detection")
    if isinstance(frameworks, list):
        out["framework_detection"] = [str(f) for f in frameworks]

    observation = raw.get("observation_window")
    if isinstance(observation, dict):
        ow: dict[str, Any] = {}
        for key in (
            "observation_ms", "start_dom_size", "end_dom_size",
            "start_visible_text_length", "end_visible_text_length",
            "dom_size_delta", "visible_text_length_delta",
        ):
            if observation.get(key) is not None:
                try:
                    ow[key] = int(observation[key])
                except (TypeError, ValueError):
                    pass
        for key in ("start_visible_text_preview", "end_visible_text_preview"):
            if observation.get(key) is not None:
                ow[key] = str(observation[key])[:500]
        out["observation_window"] = ow

    auth_trace = raw.get("auth_network_trace")
    if isinstance(auth_trace, dict):
        sanitized_trace = sanitize_auth_network_trace(auth_trace)
        cookie_names_for_diag = out.get("cookie_names")
        if cookie_names_for_diag is None and raw.get("cookie_names"):
            cookie_names_for_diag = [_cookie_name_only(c) for c in raw["cookie_names"]]
        sanitized_trace["diagnostic_summary"] = compute_auth_network_diagnostic(
            sanitized_trace,
            cookie_names=cookie_names_for_diag,
        )
        out["auth_network_trace"] = sanitized_trace

    return out


def extract_deep_inspect(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize deep inspect diagnostics from extension probe payload."""
    payload = payload or {}
    nested = payload.get("deep_inspect")
    if isinstance(nested, dict):
        return sanitize_deep_inspect(nested)
    return {}


def _sanitize_operational_redirect_transition(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in OPERATIONAL_REDIRECT_TRANSITION_KEYS:
        if key not in entry or entry.get(key) is None:
            continue
        if key == "url":
            out[key] = sanitize_probe_url(str(entry[key]))
        elif key == "observed_at_ms":
            try:
                out[key] = int(entry[key])
            except (TypeError, ValueError):
                pass
        elif key == "transition_qualifiers":
            qualifiers = entry.get(key)
            if isinstance(qualifiers, list):
                out[key] = [str(q) for q in qualifiers[:10]]
        else:
            out[key] = str(entry[key])
    return out


def _sanitize_operational_redirect_history(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in OPERATIONAL_REDIRECT_HISTORY_KEYS:
        if key not in entry or entry.get(key) is None:
            continue
        if key == "href":
            out[key] = sanitize_probe_url(str(entry[key]))
        elif key == "observed_at_ms":
            try:
                out[key] = int(entry[key])
            except (TypeError, ValueError):
                pass
        else:
            out[key] = str(entry[key])
    return out


def _sanitize_cookie_names_by_domain(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for domain, names in raw.items():
        if not isinstance(names, list):
            continue
        cleaned: list[str] = []
        for name in names[:100]:
            cookie_name = _cookie_name_only(str(name))
            if cookie_name and "=" not in cookie_name:
                cleaned.append(cookie_name)
        out[str(domain)] = cleaned
    return out


def sanitize_operational_redirect_diagnostic(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize Amex manual probe redirect diagnostic; names/metadata only."""
    raw = raw or {}
    out: dict[str, Any] = {}

    for key in (
        "requested_entry_url",
        "first_observed_tab_url",
        "final_url",
    ):
        if raw.get(key):
            out[key] = sanitize_probe_url(str(raw[key]))

    if raw.get("final_url_is_login") is not None:
        out["final_url_is_login"] = bool(raw["final_url_is_login"])

    transitions = raw.get("url_transitions")
    if isinstance(transitions, list):
        sanitized = [_sanitize_operational_redirect_transition(e) for e in transitions[:100]]
        sanitized.sort(key=lambda e: e.get("observed_at_ms") or 0)
        out["url_transitions"] = [e for e in sanitized if e]

    for key in ("cookie_names_before", "cookie_names_after"):
        domain_names = _sanitize_cookie_names_by_domain(raw.get(key))
        if domain_names:
            out[key] = domain_names

    first_nav = raw.get("first_navigation_response")
    if isinstance(first_nav, dict):
        nav_out: dict[str, Any] = {}
        for nav_key in OPERATIONAL_REDIRECT_FIRST_NAV_KEYS:
            if nav_key not in first_nav or first_nav.get(nav_key) is None:
                continue
            if nav_key in ("url", "redirect_url"):
                nav_out[nav_key] = sanitize_probe_url(str(first_nav[nav_key]))
            elif nav_key == "available":
                nav_out[nav_key] = bool(first_nav[nav_key])
            elif nav_key in ("status_code", "redirect_count"):
                try:
                    nav_out[nav_key] = int(first_nav[nav_key])
                except (TypeError, ValueError):
                    pass
            else:
                nav_out[nav_key] = str(first_nav[nav_key])
        if nav_out:
            out["first_navigation_response"] = nav_out

    history = raw.get("client_history_events")
    if isinstance(history, list):
        out["client_history_events"] = [
            e for e in (_sanitize_operational_redirect_history(h) for h in history[:50]) if e
        ]

    if raw.get("error"):
        out["error"] = str(raw["error"])[:500]

    for forbidden in FORBIDDEN_OPERATIONAL_REDIRECT_KEYS:
        out.pop(forbidden, None)

    return out


def extract_operational_redirect_diagnostic(payload: dict[str, Any] | None) -> dict[str, Any]:
    payload = payload or {}
    nested = payload.get("operational_redirect_diagnostic")
    if not isinstance(nested, dict):
        return {}
    return sanitize_operational_redirect_diagnostic(nested)


def is_blank_or_unloaded_page(
    dom_text: str = "",
    *,
    payload: dict[str, Any] | None = None,
) -> bool:
    """True when the probe saw an empty or not-yet-loaded page."""
    diag = extract_page_diagnostics(payload)
    if diag.get("body_exists") is False:
        return True
    text_len = diag.get("body_text_length")
    if text_len is not None:
        try:
            if int(text_len) < BLANK_PAGE_MIN_TEXT_LENGTH:
                return True
        except (TypeError, ValueError):
            pass
    stripped = (dom_text or "").strip()
    if len(stripped) < BLANK_PAGE_MIN_TEXT_LENGTH:
        ready_state = str(diag.get("ready_state") or "").lower()
        if not stripped or ready_state in ("loading", "uninitialized"):
            return True
        if not diag.get("input_count") and not diag.get("button_count"):
            return True
    return False


def classify_auth_state(
    *,
    provider: str,
    url: str,
    dom_text: str = "",
    error: str | None = None,
    blocked: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify structured auth state and collect matched rules + form signals."""
    payload = payload or {}
    cfg = PROVIDER_PROBE_CONFIG.get(provider)

    form = detect_form_signals(dom_text, payload)
    bot_block, matched_blocking = detect_blocking_signals(
        provider, text=dom_text, url=url, payload=payload,
    )
    session_expired, matched_session = detect_session_expired_signals(
        provider, text=dom_text, payload=payload,
    )
    mfa_required, matched_mfa = detect_mfa_signals(
        provider, text=dom_text, payload=payload,
    )
    matched_login = match_probe_rules(cfg.login_rules, text=dom_text, url=url) if cfg else []

    private_found, evidence_type, snippet, matched_private = detect_private_data(
        provider,
        url=url,
        dom_text=dom_text,
        api_text=str(payload.get("api_text") or ""),
        embedded_text=str(payload.get("embedded_text") or ""),
        network_text=str(payload.get("network_text") or ""),
    )
    if payload.get("private_data_detected") and payload.get("evidence_snippet"):
        private_found = True
        evidence_type = str(payload.get("evidence_type") or EVIDENCE_DOM_TEXT)
        snippet = str(payload.get("evidence_snippet"))[:240]
        if not matched_private:
            matched_private = ["extension_reported"]

    signed_in = detect_signed_in_from_text(provider, url, dom_text) if dom_text else False
    if private_found and not is_marketing_url(provider, url):
        signed_in = True

    auth_state = AUTH_UNKNOWN
    failure_reason = payload.get("failure_reason")

    if error:
        auth_state = AUTH_ERROR
        failure_reason = failure_reason or str(error)
    elif bot_block or blocked:
        auth_state = AUTH_BOT_BLOCKED
        failure_reason = failure_reason or "access_blocked"
    elif is_blank_or_unloaded_page(dom_text, payload=payload):
        auth_state = AUTH_UNKNOWN
        signed_in = False
        private_found = False
        failure_reason = FAILURE_BLANK_OR_UNLOADED
    elif session_expired:
        auth_state = AUTH_SESSION_EXPIRED
        failure_reason = failure_reason or "session_expired"
    elif mfa_required:
        auth_state = AUTH_MFA_REQUIRED
        failure_reason = failure_reason or "mfa_required"
    elif is_marketing_url(provider, url) and not is_account_url(provider, url):
        auth_state = AUTH_MARKETING
        signed_in = False
        private_found = False
        failure_reason = failure_reason or "marketing_page_only"
    elif is_login_url(provider, url) or matched_login or form["login_form_present"]:
        auth_state = AUTH_LOGIN_PAGE
        signed_in = False
        private_found = False
        failure_reason = failure_reason or "login_required"
    elif payload.get("login_submitted") or _rx(r"signing you in|authenticating").search(dom_text or ""):
        auth_state = AUTH_LOGIN_SUBMITTED
        signed_in = False
        private_found = False
        failure_reason = failure_reason or "login_submitted"
    elif private_found:
        auth_state = AUTH_PRIVATE_DATA_VISIBLE
        failure_reason = None
    elif signed_in:
        auth_state = AUTH_AUTHENTICATED_NO_PRIVATE_DATA
        failure_reason = failure_reason or "signed_in_no_private_evidence"
    else:
        auth_state = AUTH_UNKNOWN
        signed_in = False
        failure_reason = failure_reason or "unknown_auth_state"

    return {
        "auth_state": auth_state,
        "signed_in_detected": signed_in,
        "private_data_detected": private_found,
        "evidence_type": evidence_type,
        "evidence_snippet": snippet,
        "failure_reason": failure_reason,
        "login_form_present": form["login_form_present"],
        "password_field_present": form["password_field_present"],
        "username_field_present": form["username_field_present"],
        "mfa_signal_present": mfa_required,
        "bot_block_signal_present": bot_block,
        "session_expired_signal_present": session_expired,
        "matched_login_rules": matched_login,
        "matched_private_data_rules": matched_private,
        "matched_blocking_rules": matched_blocking,
    }


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
    """Validate extension payload and compute authoritative status + auth diagnostics."""
    cfg = PROVIDER_PROBE_CONFIG.get(provider)
    if not cfg:
        raise ValueError(f"unsupported probe provider: {provider!r}")

    entry_url = str(payload.get("entry_url") or cfg.entry_url)
    final_url = str(payload.get("final_url") or payload.get("url_visited") or entry_url)
    dom_text = str(payload.get("dom_text") or payload.get("page_text") or "")
    page_title = str(payload.get("page_title") or "").strip() or None

    blocked = bool(payload.get("blocked"))
    error = payload.get("error")
    if error:
        error = str(error)

    auth = classify_auth_state(
        provider=provider,
        url=final_url,
        dom_text=dom_text,
        error=error,
        blocked=blocked,
        payload=payload,
    )

    page_diagnostics = extract_page_diagnostics(payload)
    deep_inspect = extract_deep_inspect(payload)
    if provider == "amex":
        redirect_diag = extract_operational_redirect_diagnostic(payload)
        if redirect_diag:
            deep_inspect = {**deep_inspect, "operational_redirect_diagnostic": redirect_diag}
    if not page_title and page_diagnostics.get("page_title"):
        page_title = str(page_diagnostics["page_title"]).strip() or None

    status = AUTH_TO_PROBE_STATUS.get(auth["auth_state"], PROBE_NEEDS_SIGN_IN)
    probed_at = payload.get("timestamp") or utc_now_iso()

    return {
        "provider": provider,
        "entry_url": entry_url,
        "final_url": final_url,
        "url_visited": final_url,
        "page_title": page_title,
        "auth_state": auth["auth_state"],
        "status": status,
        "signed_in_detected": auth["signed_in_detected"],
        "private_data_detected": auth["private_data_detected"],
        "evidence_type": auth["evidence_type"],
        "evidence_snippet": auth["evidence_snippet"],
        "login_form_present": auth["login_form_present"],
        "password_field_present": auth["password_field_present"],
        "username_field_present": auth["username_field_present"],
        "mfa_signal_present": auth["mfa_signal_present"],
        "bot_block_signal_present": auth["bot_block_signal_present"],
        "session_expired_signal_present": auth["session_expired_signal_present"],
        "matched_login_rules": auth["matched_login_rules"],
        "matched_private_data_rules": auth["matched_private_data_rules"],
        "matched_blocking_rules": auth["matched_blocking_rules"],
        "failure_reason": auth["failure_reason"],
        "page_diagnostics": page_diagnostics,
        "deep_inspect": deep_inspect,
        "probed_at": probed_at,
        "timestamp": probed_at,
    }


# ── Storage ───────────────────────────────────────────────────────────────────

_PROBE_RUN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entry_url", "TEXT"),
    ("final_url", "TEXT"),
    ("page_title", "TEXT"),
    ("auth_state", "TEXT"),
    ("login_form_present", "INTEGER NOT NULL DEFAULT 0"),
    ("password_field_present", "INTEGER NOT NULL DEFAULT 0"),
    ("username_field_present", "INTEGER NOT NULL DEFAULT 0"),
    ("mfa_signal_present", "INTEGER NOT NULL DEFAULT 0"),
    ("bot_block_signal_present", "INTEGER NOT NULL DEFAULT 0"),
    ("session_expired_signal_present", "INTEGER NOT NULL DEFAULT 0"),
    ("matched_login_rules", "TEXT"),
    ("matched_private_data_rules", "TEXT"),
    ("matched_blocking_rules", "TEXT"),
    ("page_diagnostics_json", "TEXT"),
    ("deep_inspect_json", "TEXT"),
)


def _migrate_probe_run_columns(db: Any) -> None:
    existing = {row[1] for row in db.execute("PRAGMA table_info(provider_access_probe_runs)").fetchall()}
    for col, coltype in _PROBE_RUN_COLUMNS:
        if col not in existing:
            try:
                db.execute(f"ALTER TABLE provider_access_probe_runs ADD COLUMN {col} {coltype}")
            except Exception as exc:
                if "duplicate column" not in str(exc).lower():
                    raise


def _json_list(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(list(value))


def _json_diagnostics(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value)
    return None


def _parse_json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _parse_json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    try:
        parsed = json.loads(value)
        return [str(v) for v in parsed] if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


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
            created_at              TEXT NOT NULL,
            entry_url               TEXT,
            final_url               TEXT,
            page_title              TEXT,
            auth_state              TEXT,
            login_form_present      INTEGER NOT NULL DEFAULT 0,
            password_field_present  INTEGER NOT NULL DEFAULT 0,
            username_field_present  INTEGER NOT NULL DEFAULT 0,
            mfa_signal_present      INTEGER NOT NULL DEFAULT 0,
            bot_block_signal_present INTEGER NOT NULL DEFAULT 0,
            session_expired_signal_present INTEGER NOT NULL DEFAULT 0,
            matched_login_rules     TEXT,
            matched_private_data_rules TEXT,
            matched_blocking_rules  TEXT
        )
        """
    )
    _migrate_probe_run_columns(db)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_papr_user_provider "
        "ON provider_access_probe_runs(user_id, provider, probed_at DESC)"
    )
    db.commit()


def record_probe_run(db: Any, user_id: str, result: dict[str, Any]) -> str:
    ensure_probe_tables(db)
    run_id = str(uuid.uuid4())
    now = utc_now_iso()
    final_url = result.get("final_url") or result.get("url_visited")
    db.execute(
        """
        INSERT INTO provider_access_probe_runs (
            run_id, user_id, provider, status, url_visited,
            signed_in_detected, private_data_detected,
            evidence_type, evidence_snippet, failure_reason,
            probed_at, created_at,
            entry_url, final_url, page_title, auth_state,
            login_form_present, password_field_present, username_field_present,
            mfa_signal_present, bot_block_signal_present, session_expired_signal_present,
            matched_login_rules, matched_private_data_rules, matched_blocking_rules,
            page_diagnostics_json, deep_inspect_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            user_id,
            result["provider"],
            result["status"],
            final_url,
            1 if result.get("signed_in_detected") else 0,
            1 if result.get("private_data_detected") else 0,
            result.get("evidence_type"),
            result.get("evidence_snippet"),
            result.get("failure_reason"),
            result.get("probed_at") or now,
            now,
            result.get("entry_url"),
            final_url,
            result.get("page_title"),
            result.get("auth_state"),
            1 if result.get("login_form_present") else 0,
            1 if result.get("password_field_present") else 0,
            1 if result.get("username_field_present") else 0,
            1 if result.get("mfa_signal_present") else 0,
            1 if result.get("bot_block_signal_present") else 0,
            1 if result.get("session_expired_signal_present") else 0,
            _json_list(result.get("matched_login_rules")),
            _json_list(result.get("matched_private_data_rules")),
            _json_list(result.get("matched_blocking_rules")),
            _json_diagnostics(result.get("page_diagnostics")),
            _json_diagnostics(result.get("deep_inspect")),
        ),
    )
    db.commit()
    return run_id


def _normalize_probe_row(row: dict[str, Any]) -> dict[str, Any]:
    d = dict(row)
    for flag in (
        "signed_in_detected",
        "private_data_detected",
        "login_form_present",
        "password_field_present",
        "username_field_present",
        "mfa_signal_present",
        "bot_block_signal_present",
        "session_expired_signal_present",
    ):
        if flag in d:
            d[flag] = bool(d.get(flag))
    d["matched_login_rules"] = _parse_json_list(d.get("matched_login_rules"))
    d["matched_private_data_rules"] = _parse_json_list(d.get("matched_private_data_rules"))
    d["matched_blocking_rules"] = _parse_json_list(d.get("matched_blocking_rules"))
    d["page_diagnostics"] = _parse_json_dict(d.get("page_diagnostics_json"))
    d["deep_inspect"] = _parse_json_dict(d.get("deep_inspect_json"))
    if not d.get("final_url"):
        d["final_url"] = d.get("url_visited")
    return d


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
        d = _normalize_probe_row(dict(row))
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
        "auth_state": row.get("auth_state"),
        "entry_url": row.get("entry_url"),
        "final_url": row.get("final_url") or row.get("url_visited"),
        "url_visited": row.get("final_url") or row.get("url_visited"),
        "page_title": row.get("page_title"),
        "signed_in_detected": row.get("signed_in_detected"),
        "private_data_detected": row.get("private_data_detected"),
        "evidence_type": row.get("evidence_type"),
        "evidence_snippet": row.get("evidence_snippet"),
        "login_form_present": row.get("login_form_present"),
        "password_field_present": row.get("password_field_present"),
        "username_field_present": row.get("username_field_present"),
        "mfa_signal_present": row.get("mfa_signal_present"),
        "bot_block_signal_present": row.get("bot_block_signal_present"),
        "session_expired_signal_present": row.get("session_expired_signal_present"),
        "matched_login_rules": row.get("matched_login_rules") or [],
        "matched_private_data_rules": row.get("matched_private_data_rules") or [],
        "matched_blocking_rules": row.get("matched_blocking_rules") or [],
        "timestamp": row.get("probed_at") or row.get("timestamp"),
        "failure_reason": row.get("failure_reason"),
        "page_diagnostics": row.get("page_diagnostics") or _parse_json_dict(row.get("page_diagnostics_json")),
        "deep_inspect": row.get("deep_inspect") or _parse_json_dict(row.get("deep_inspect_json")),
        "run_id": row.get("run_id"),
    }


# ── Manual probe runner (Phase 1A.5) ──────────────────────────────────────────


def ensure_manual_probe_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_access_probe_manual_runs (
            manual_run_id   TEXT PRIMARY KEY,
            user_id         TEXT NOT NULL,
            provider        TEXT NOT NULL,
            lifecycle       TEXT NOT NULL,
            error_message   TEXT,
            probe_run_id    TEXT,
            requested_at    TEXT NOT NULL,
            started_at      TEXT,
            completed_at    TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_papmr_user_lifecycle "
        "ON provider_access_probe_manual_runs(user_id, lifecycle, requested_at DESC)"
    )
    db.commit()


def _normalize_manual_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "manual_run_id": None,
            "provider": None,
            "lifecycle": PROBE_LIFECYCLE_IDLE,
            "error_message": None,
            "probe_run_id": None,
            "requested_at": None,
            "started_at": None,
            "completed_at": None,
        }
    return dict(row)


def get_manual_probe_state(db: Any, user_id: str) -> dict[str, Any]:
    """Latest manual probe lifecycle for admin display."""
    ensure_manual_probe_tables(db)
    row = db.execute(
        """
        SELECT manual_run_id, user_id, provider, lifecycle, error_message,
               probe_run_id, requested_at, started_at, completed_at
        FROM provider_access_probe_manual_runs
        WHERE user_id = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    return _normalize_manual_row(dict(row) if row else None)


def get_pending_manual_probe(db: Any, user_id: str) -> dict[str, Any] | None:
    """Return the active running manual probe for the extension to execute."""
    ensure_manual_probe_tables(db)
    row = db.execute(
        """
        SELECT manual_run_id, user_id, provider, lifecycle, error_message,
               probe_run_id, requested_at, started_at, completed_at
        FROM provider_access_probe_manual_runs
        WHERE user_id = ? AND lifecycle = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return dict(row) if row else None


def _has_running_manual_probe(db: Any, user_id: str) -> bool:
    ensure_manual_probe_tables(db)
    row = db.execute(
        """
        SELECT 1 FROM provider_access_probe_manual_runs
        WHERE user_id = ? AND lifecycle = ?
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return row is not None


def start_manual_probe(db: Any, user_id: str, provider: str) -> dict[str, Any]:
    """Queue a single-provider manual probe. Rejects concurrent runs."""
    provider = provider.strip().lower()
    if provider not in MANUAL_PROBE_PROVIDERS:
        raise ValueError(
            f"unsupported manual probe provider: {provider!r} "
            f"(allowed: {', '.join(MANUAL_PROBE_PROVIDERS)})"
        )
    ensure_manual_probe_tables(db)
    ensure_bootstrap_trace_tables(db)
    ensure_live_session_comparison_tables(db)
    if _has_running_manual_probe(db, user_id):
        raise ConcurrentProbeError("a manual probe is already running")
    if _has_running_bootstrap_trace(db, user_id):
        raise ConcurrentProbeError("a bootstrap trace is already running")
    if _has_running_live_session_comparison(db, user_id):
        raise ConcurrentProbeError("a live session comparison is already running")

    manual_run_id = str(uuid.uuid4())
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO provider_access_probe_manual_runs (
            manual_run_id, user_id, provider, lifecycle,
            requested_at, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (manual_run_id, user_id, provider, PROBE_LIFECYCLE_RUNNING, now, now),
    )
    db.commit()
    return {
        "manual_run_id": manual_run_id,
        "provider": provider,
        "lifecycle": PROBE_LIFECYCLE_RUNNING,
        "requested_at": now,
    }


def complete_manual_probe(
    db: Any,
    user_id: str,
    manual_run_id: str,
    *,
    lifecycle: str,
    probe_run_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """Mark a manual probe run finished (done or error)."""
    if lifecycle not in (PROBE_LIFECYCLE_DONE, PROBE_LIFECYCLE_ERROR):
        raise ValueError(f"invalid manual probe completion lifecycle: {lifecycle!r}")
    ensure_manual_probe_tables(db)
    now = utc_now_iso()
    db.execute(
        """
        UPDATE provider_access_probe_manual_runs
        SET lifecycle = ?, probe_run_id = ?, error_message = ?, completed_at = ?
        WHERE manual_run_id = ? AND user_id = ? AND lifecycle = ?
        """,
        (
            lifecycle,
            probe_run_id,
            error_message,
            now,
            manual_run_id,
            user_id,
            PROBE_LIFECYCLE_RUNNING,
        ),
    )
    db.commit()


def manual_probe_state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "manual_run_id": state.get("manual_run_id"),
        "provider": state.get("provider"),
        "lifecycle": state.get("lifecycle") or PROBE_LIFECYCLE_IDLE,
        "error_message": state.get("error_message"),
        "probe_run_id": state.get("probe_run_id"),
        "requested_at": state.get("requested_at"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
    }


# ── Amex bootstrap trace (Phase 1 diagnostic) ─────────────────────────────────

AMEX_BOOTSTRAP_ENTRY_URLS: tuple[str, ...] = (
    "https://www.americanexpress.com/en-us/account/",
    "https://www.americanexpress.com/en-us/account/login",
    "https://global.americanexpress.com/login",
)

AMEX_LIVE_SESSION_COMPARISON_ENTRY_URLS: tuple[str, ...] = (
    *AMEX_BOOTSTRAP_ENTRY_URLS,
    "https://global.americanexpress.com/overview",
)

BOOTSTRAP_KEYWORD_RE = re.compile(
    r"bootstrap|session|login|auth|token|csrf|sso|identity|ReadUserSession|UpdateUserSession",
    re.IGNORECASE,
)

BOOTSTRAP_NAV_EVENT_KEYS: frozenset[str] = frozenset({
    "observed_at_ms", "url", "status", "transition_type", "source",
})

BOOTSTRAP_HISTORY_EVENT_KEYS: frozenset[str] = frozenset({
    "observed_at_ms", "type", "href", "path", "hash", "state_type",
})

OPERATIONAL_REDIRECT_TRANSITION_KEYS: frozenset[str] = frozenset({
    "observed_at_ms", "url", "status", "transition_type", "transition_qualifiers", "source", "type",
})

OPERATIONAL_REDIRECT_HISTORY_KEYS: frozenset[str] = frozenset({
    "observed_at_ms", "type", "href", "path",
})

OPERATIONAL_REDIRECT_FIRST_NAV_KEYS: frozenset[str] = frozenset({
    "available", "reason", "source", "url", "status_code", "redirect_url", "redirect_count", "type",
})

FORBIDDEN_OPERATIONAL_REDIRECT_KEYS: frozenset[str] = frozenset({
    "cookie", "set-cookie", "request_body", "response_body", "body", "authorization", "token",
    "value", "cookie_value", "cookie_values",
})


def _sanitize_nav_event(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in BOOTSTRAP_NAV_EVENT_KEYS:
        if key in entry and entry.get(key) is not None:
            if key == "url":
                out[key] = sanitize_probe_url(str(entry[key]))
            elif key == "observed_at_ms":
                try:
                    out[key] = int(entry[key])
                except (TypeError, ValueError):
                    pass
            else:
                out[key] = str(entry[key])
    return out


def _sanitize_history_event(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in BOOTSTRAP_HISTORY_EVENT_KEYS:
        if key in entry and entry.get(key) is not None:
            if key == "href":
                out[key] = sanitize_probe_url(str(entry[key]))
            elif key == "observed_at_ms":
                try:
                    out[key] = int(entry[key])
                except (TypeError, ValueError):
                    pass
            else:
                out[key] = str(entry[key])
    return out


def sanitize_bootstrap_trace(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize bootstrap trace payload; never persist secrets."""
    raw = raw or {}
    out: dict[str, Any] = {}

    if raw.get("entry_url"):
        out["entry_url"] = sanitize_probe_url(str(raw["entry_url"]))
    if raw.get("observation_ms") is not None:
        try:
            out["observation_ms"] = int(raw["observation_ms"])
        except (TypeError, ValueError):
            pass

    nav = raw.get("navigation_timeline")
    if isinstance(nav, dict):
        events = nav.get("events")
        sanitized_events = [_sanitize_nav_event(e) for e in events[:100]] if isinstance(events, list) else []
        sanitized_events.sort(key=lambda e: e.get("observed_at_ms") or 0)
        out["navigation_timeline"] = {
            "initial_url": sanitize_probe_url(str(nav.get("initial_url") or raw.get("entry_url") or "")),
            "final_url": sanitize_probe_url(str(nav.get("final_url") or "")),
            "events": sanitized_events,
        }

    loc = raw.get("location_history")
    if isinstance(loc, dict):
        out["location_history"] = {
            "href_timeline": [
                _sanitize_history_event(e)
                for e in (loc.get("href_timeline") or [])[:100]
                if isinstance(e, dict)
            ],
            "history_events": [
                _sanitize_history_event(e)
                for e in (loc.get("history_events") or [])[:100]
                if isinstance(e, dict)
            ],
            "hash_changes": [
                _sanitize_history_event(e)
                for e in (loc.get("hash_changes") or [])[:50]
                if isinstance(e, dict)
            ],
        }

    requests = raw.get("bootstrap_requests")
    if isinstance(requests, list):
        sanitized_requests = [_sanitize_network_trace_entry(r) for r in requests[:100]]
        sanitized_requests.sort(key=lambda r: r.get("start_time_ms") or r.get("observed_at_ms") or 0)
        out["bootstrap_requests"] = sanitized_requests

    if raw.get("first_401_at_ms") is not None:
        try:
            out["first_401_at_ms"] = int(raw["first_401_at_ms"])
        except (TypeError, ValueError):
            pass
    if raw.get("first_401_url"):
        out["first_401_url"] = sanitize_probe_url(str(raw["first_401_url"]))

    for key in ("page_title", "visible_text_preview"):
        if raw.get(key) is not None:
            out[key] = str(raw[key])[:500]

    out["diagnostic_summary"] = compute_bootstrap_diagnostic(out)
    return out


def _ordered_bootstrap_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [r for r in requests if isinstance(r, dict) and r.get("url")]
    ordered.sort(key=lambda r: (r.get("start_time_ms") or r.get("observed_at_ms") or 0))
    return ordered


def compute_bootstrap_diagnostic(trace: dict[str, Any]) -> str:
    parts: list[str] = []
    nav = trace.get("navigation_timeline") or {}
    initial = nav.get("initial_url") or trace.get("entry_url") or ""
    final = nav.get("final_url") or ""
    if initial and final and initial != final:
        parts.append(f"navigation changed from entry URL to {final}")
    elif initial:
        parts.append(f"final URL matches entry path: {final or initial}")

    requests = _ordered_bootstrap_requests(trace.get("bootstrap_requests") or [])
    session_calls = [
        r for r in requests
        if isinstance(r, dict) and (
            "ReadUserSession" in str(r.get("url") or "")
            or "UpdateUserSession" in str(r.get("url") or "")
        )
    ]
    if session_calls:
        statuses = [str(r.get("status_code")) for r in session_calls if r.get("status_code") is not None]
        parts.append(f"session API sequence statuses: {', '.join(statuses) or 'unknown'}")

    first_401_url = trace.get("first_401_url")
    first_401_ms = trace.get("first_401_at_ms")
    if first_401_url:
        parts.append(f"first 401 at {first_401_ms}ms on {first_401_url}")
    elif any(r.get("status_code") == 401 for r in requests):
        parts.append("401 observed in bootstrap request sequence")

    history = trace.get("location_history") or {}
    push_events = history.get("history_events") or []
    if push_events:
        parts.append(f"{len(push_events)} pushState/replaceState event(s) observed")

    if not parts:
        return "bootstrap trace captured; no navigation or session API divergence detected yet"
    return "; ".join(parts)


def build_amex_bootstrap_trace(payload: dict[str, Any]) -> dict[str, Any]:
    entry_url = str(payload.get("entry_url") or "")
    if entry_url and entry_url not in AMEX_BOOTSTRAP_ENTRY_URLS:
        raise ValueError(f"unsupported bootstrap entry URL: {entry_url!r}")

    sanitized = sanitize_bootstrap_trace(payload)
    sanitized["entry_url"] = sanitize_probe_url(entry_url)
    sanitized["compared_at"] = payload.get("compared_at") or utc_now_iso()
    return sanitized


def ensure_bootstrap_trace_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_access_probe_bootstrap_traces (
            trace_run_id        TEXT PRIMARY KEY,
            user_id               TEXT NOT NULL,
            entry_url             TEXT NOT NULL,
            lifecycle             TEXT NOT NULL,
            payload_json          TEXT,
            diagnostic_summary    TEXT,
            error_message         TEXT,
            requested_at          TEXT NOT NULL,
            started_at            TEXT,
            completed_at          TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_papbt_user_entry "
        "ON provider_access_probe_bootstrap_traces(user_id, entry_url, requested_at DESC)"
    )
    db.commit()


def _has_running_bootstrap_trace(db: Any, user_id: str) -> bool:
    ensure_bootstrap_trace_tables(db)
    row = db.execute(
        """
        SELECT 1 FROM provider_access_probe_bootstrap_traces
        WHERE user_id = ? AND lifecycle = ?
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return row is not None


def get_latest_bootstrap_traces_by_entry(db: Any, user_id: str) -> dict[str, dict[str, Any]]:
    ensure_bootstrap_trace_tables(db)
    out: dict[str, dict[str, Any]] = {}
    for entry_url in AMEX_BOOTSTRAP_ENTRY_URLS:
        row = db.execute(
            """
            SELECT trace_run_id, user_id, entry_url, lifecycle, payload_json,
                   diagnostic_summary, error_message, requested_at, completed_at
            FROM provider_access_probe_bootstrap_traces
            WHERE user_id = ? AND entry_url = ?
            ORDER BY requested_at DESC
            LIMIT 1
            """,
            (user_id, entry_url),
        ).fetchone()
        if row:
            data = dict(row)
            data["trace"] = _parse_json_dict(data.get("payload_json"))
            out[entry_url] = data
    return out


def get_pending_bootstrap_trace(db: Any, user_id: str) -> dict[str, Any] | None:
    ensure_bootstrap_trace_tables(db)
    row = db.execute(
        """
        SELECT trace_run_id, user_id, entry_url, lifecycle, requested_at, started_at
        FROM provider_access_probe_bootstrap_traces
        WHERE user_id = ? AND lifecycle = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return dict(row) if row else None


def start_bootstrap_trace(db: Any, user_id: str, entry_url: str) -> dict[str, Any]:
    entry_url = entry_url.strip()
    if entry_url not in AMEX_BOOTSTRAP_ENTRY_URLS:
        raise ValueError(
            f"unsupported bootstrap entry URL: {entry_url!r} "
            f"(allowed: {', '.join(AMEX_BOOTSTRAP_ENTRY_URLS)})"
        )
    ensure_bootstrap_trace_tables(db)
    ensure_manual_probe_tables(db)
    ensure_live_session_comparison_tables(db)
    if _has_running_manual_probe(db, user_id):
        raise ConcurrentProbeError("a manual probe is already running")
    if _has_running_bootstrap_trace(db, user_id):
        raise ConcurrentProbeError("a bootstrap trace is already running")
    if _has_running_live_session_comparison(db, user_id):
        raise ConcurrentProbeError("a live session comparison is already running")

    trace_run_id = str(uuid.uuid4())
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO provider_access_probe_bootstrap_traces (
            trace_run_id, user_id, entry_url, lifecycle, requested_at, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (trace_run_id, user_id, entry_url, PROBE_LIFECYCLE_RUNNING, now, now),
    )
    db.commit()
    return {
        "trace_run_id": trace_run_id,
        "entry_url": entry_url,
        "provider": "amex",
        "lifecycle": PROBE_LIFECYCLE_RUNNING,
        "requested_at": now,
        "started_at": now,
    }


def complete_bootstrap_trace(
    db: Any,
    user_id: str,
    trace_run_id: str,
    *,
    lifecycle: str,
    trace: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    if lifecycle not in PROBE_LIFECYCLES:
        raise ValueError(f"invalid bootstrap trace lifecycle: {lifecycle!r}")
    ensure_bootstrap_trace_tables(db)
    now = utc_now_iso()
    db.execute(
        """
        UPDATE provider_access_probe_bootstrap_traces
        SET lifecycle = ?, payload_json = ?, diagnostic_summary = ?,
            error_message = ?, completed_at = ?
        WHERE trace_run_id = ? AND user_id = ?
        """,
        (
            lifecycle,
            _json_diagnostics(trace),
            (trace or {}).get("diagnostic_summary") if trace else error_message,
            error_message,
            now,
            trace_run_id,
            user_id,
        ),
    )
    db.commit()


def bootstrap_trace_state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace_run_id": state.get("trace_run_id"),
        "entry_url": state.get("entry_url"),
        "provider": state.get("provider") or "amex",
        "lifecycle": state.get("lifecycle") or PROBE_LIFECYCLE_IDLE,
        "diagnostic_summary": state.get("diagnostic_summary"),
        "error_message": state.get("error_message"),
        "requested_at": state.get("requested_at"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "trace": state.get("trace"),
    }


# ── Amex live session comparison (Phase 1 diagnostic) ─────────────────────────

LIVE_SESSION_AUTH_REQUEST_RE = re.compile(
    r"ReadUserSession\.v1|UpdateUserSession\.v1|session|login|auth|token|csrf|sso|identity",
    re.IGNORECASE,
)

LIVE_SESSION_SNAPSHOT_STRING_KEYS: frozenset[str] = frozenset({
    "source",
    "final_url",
    "page_title",
    "visible_text_preview",
    "referrer",
    "navigator_user_agent",
    "network_trace_limitation",
})

LIVE_SESSION_SNAPSHOT_LIST_KEYS: frozenset[str] = frozenset({
    "document_cookie_names",
    "local_storage_keys",
    "session_storage_keys",
    "auth_session_requests",
})


def _sanitize_user_agent_data(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    brands = raw.get("brands")
    if isinstance(brands, list):
        sanitized_brands = []
        for brand in brands[:10]:
            if not isinstance(brand, dict):
                continue
            sanitized_brands.append({
                "brand": str(brand.get("brand") or "")[:80],
                "version": str(brand.get("version") or "")[:40],
            })
        if sanitized_brands:
            out["brands"] = sanitized_brands
    for key in ("mobile", "platform"):
        if raw.get(key) is not None:
            out[key] = str(raw[key])[:80]
    return out or None


def _sanitize_service_worker(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("scope", "script_url"):
        if entry.get(key):
            out[key] = sanitize_probe_url(str(entry[key]))
    for key in ("state", "update_via_cache"):
        if entry.get(key) is not None:
            out[key] = str(entry[key])[:80]
    return out


def _sanitize_auth_session_request(entry: Any) -> dict[str, Any]:
    sanitized = _sanitize_network_trace_entry(entry)
    url = str(sanitized.get("url") or "")
    if url and not LIVE_SESSION_AUTH_REQUEST_RE.search(url):
        return {}
    return sanitized


def sanitize_live_session_snapshot(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize one tab side of an Amex live session comparison."""
    raw = raw or {}
    out: dict[str, Any] = {"found": bool(raw.get("found", True))}

    for key in LIVE_SESSION_SNAPSHOT_STRING_KEYS:
        if raw.get(key) is not None:
            val = str(raw[key])
            out[key] = val[:500] if key == "visible_text_preview" else val

    if raw.get("final_url"):
        out["final_url"] = sanitize_probe_url(str(raw["final_url"]))
    if raw.get("referrer"):
        out["referrer"] = sanitize_probe_url(str(raw["referrer"]))

    for key in LIVE_SESSION_SNAPSHOT_LIST_KEYS:
        items = raw.get(key)
        if not isinstance(items, list):
            continue
        if key == "auth_session_requests":
            out[key] = [
                req for req in (_sanitize_auth_session_request(i) for i in items[:100])
                if req
            ]
        elif key == "document_cookie_names":
            out[key] = [_cookie_name_only(c) for c in items]
        else:
            out[key] = [str(k) for k in items[:100]]

    chrome_cookies = raw.get("chrome_cookie_names")
    if isinstance(chrome_cookies, dict):
        out["chrome_cookie_names"] = {
            str(domain): [_cookie_name_only(n) for n in names[:100]]
            for domain, names in chrome_cookies.items()
            if isinstance(names, list)
        }

    sw_regs = raw.get("service_worker_registrations")
    if isinstance(sw_regs, list):
        out["service_worker_registrations"] = [
            _sanitize_service_worker(r) for r in sw_regs[:20] if isinstance(r, dict)
        ]

    ua_data = _sanitize_user_agent_data(raw.get("navigator_user_agent_data"))
    if ua_data:
        out["navigator_user_agent_data"] = ua_data

    if raw.get("network_trace_limitation"):
        out["network_trace_limitation"] = str(raw["network_trace_limitation"])

    return out


def _find_auth_request(requests: list[dict[str, Any]], needle: str) -> dict[str, Any] | None:
    for req in requests:
        if isinstance(req, dict) and needle in str(req.get("url") or ""):
            return req
    return None


def _session_api_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    requests = snapshot.get("auth_session_requests") or []
    read_user = _find_auth_request(requests, "ReadUserSession.v1")
    update_user = _find_auth_request(requests, "UpdateUserSession.v1")
    return {
        "read_user_session_status": read_user.get("status_code") if read_user else None,
        "update_user_session_status": update_user.get("status_code") if update_user else None,
        "read_user_session": read_user,
        "update_user_session": update_user,
    }


def _fmt_diff_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, list):
        if not value:
            return "[]"
        if all(isinstance(v, dict) for v in value):
            return f"[{len(value)} item(s)]"
        return ", ".join(str(v) for v in value[:20])
    if isinstance(value, dict):
        return ", ".join(f"{k}={v!r}" for k, v in list(value.items())[:10])
    return str(value)


def compute_live_session_field_diffs(
    logged_in: dict[str, Any],
    bootstrap: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return side-by-side diffs for fields that differ between the two tabs."""
    diffs: list[dict[str, Any]] = []

    if not logged_in.get("found"):
        limitation = str(logged_in.get("network_trace_limitation") or "")
        left_label = "snapshot_failed" if limitation == "snapshot_failed" else "not found"
        diffs.append({
            "field": "logged_in_tab",
            "logged_in_tab": left_label,
            "bootstrap_probe_tab": "available" if bootstrap.get("found") else "not found",
        })
        return diffs

    scalar_fields = (
        "final_url",
        "page_title",
        "visible_text_preview",
        "referrer",
        "navigator_user_agent",
    )
    for field in scalar_fields:
        left = logged_in.get(field)
        right = bootstrap.get(field)
        if left != right:
            diffs.append({
                "field": field,
                "logged_in_tab": left,
                "bootstrap_probe_tab": right,
            })

    left_ua = logged_in.get("navigator_user_agent_data") or {}
    right_ua = bootstrap.get("navigator_user_agent_data") or {}
    if left_ua != right_ua:
        diffs.append({
            "field": "navigator_user_agent_data",
            "logged_in_tab": left_ua or None,
            "bootstrap_probe_tab": right_ua or None,
        })

    for field in ("document_cookie_names", "local_storage_keys", "session_storage_keys"):
        left = sorted(set(logged_in.get(field) or []))
        right = sorted(set(bootstrap.get(field) or []))
        if left != right:
            diffs.append({
                "field": field,
                "logged_in_tab": left,
                "bootstrap_probe_tab": right,
            })

    left_sw = logged_in.get("service_worker_registrations") or []
    right_sw = bootstrap.get("service_worker_registrations") or []
    if left_sw != right_sw:
        diffs.append({
            "field": "service_worker_registrations",
            "logged_in_tab": left_sw,
            "bootstrap_probe_tab": right_sw,
        })

    left_fn = sorted(set((logged_in.get("chrome_cookie_names") or {}).get("functions.americanexpress.com") or []))
    right_fn = sorted(set((bootstrap.get("chrome_cookie_names") or {}).get("functions.americanexpress.com") or []))
    if left_fn != right_fn:
        diffs.append({
            "field": "chrome_cookie_names.functions.americanexpress.com",
            "logged_in_tab": left_fn,
            "bootstrap_probe_tab": right_fn,
        })

    left_apis = _session_api_summary(logged_in)
    right_apis = _session_api_summary(bootstrap)
    for api_key in ("read_user_session_status", "update_user_session_status"):
        if left_apis.get(api_key) != right_apis.get(api_key):
            diffs.append({
                "field": api_key,
                "logged_in_tab": left_apis.get(api_key),
                "bootstrap_probe_tab": right_apis.get(api_key),
            })

    for label, req_key in (
        ("ReadUserSession.v1", "read_user_session"),
        ("UpdateUserSession.v1", "update_user_session"),
    ):
        left_req = left_apis.get(req_key) or {}
        right_req = right_apis.get(req_key) or {}
        for meta_key in ("method", "with_credentials", "request_header_names", "response_header_names"):
            left_val = left_req.get(meta_key)
            right_val = right_req.get(meta_key)
            if left_val != right_val:
                diffs.append({
                    "field": f"{label}.{meta_key}",
                    "logged_in_tab": left_val,
                    "bootstrap_probe_tab": right_val,
                })

    if logged_in.get("network_trace_limitation"):
        diffs.append({
            "field": "logged_in_tab.network_trace_limitation",
            "logged_in_tab": logged_in.get("network_trace_limitation"),
            "bootstrap_probe_tab": bootstrap.get("network_trace_limitation"),
        })

    return diffs


def compute_live_session_comparison_differences(
    logged_in: dict[str, Any],
    bootstrap: dict[str, Any],
) -> list[str]:
    """Human-readable difference lines derived from field diffs."""
    lines: list[str] = []
    for diff in compute_live_session_field_diffs(logged_in, bootstrap):
        field = diff.get("field") or "unknown"
        left = _fmt_diff_value(diff.get("logged_in_tab"))
        right = _fmt_diff_value(diff.get("bootstrap_probe_tab"))
        lines.append(f"{field}: logged_in={left} bootstrap={right}")
    return lines


def compute_live_session_comparison_diagnostic(
    logged_in: dict[str, Any],
    bootstrap: dict[str, Any],
    differences: list[str] | None = None,
) -> str:
    differences = differences or compute_live_session_comparison_differences(logged_in, bootstrap)
    parts: list[str] = []

    if not logged_in.get("found"):
        limitation = str(logged_in.get("network_trace_limitation") or "")
        if limitation == "snapshot_failed":
            parts.append("logged-in tab snapshot failed")
        elif limitation == "no_logged_in_amex_tab":
            parts.append("no logged-in Amex tab available for comparison")
        else:
            parts.append("logged-in tab unavailable for comparison")
    else:
        left_apis = _session_api_summary(logged_in)
        right_apis = _session_api_summary(bootstrap)
        read_logged = left_apis.get("read_user_session_status")
        read_boot = right_apis.get("read_user_session_status")
        upd_logged = left_apis.get("update_user_session_status")
        upd_boot = right_apis.get("update_user_session_status")

        if read_boot in (400, 401):
            parts.append(f"bootstrap probe ReadUserSession.v1 returned {read_boot}")
        if upd_boot in (400, 401):
            parts.append(f"bootstrap probe UpdateUserSession.v1 returned {upd_boot}")
        if read_logged not in (None, 400, 401):
            parts.append(f"logged-in tab ReadUserSession.v1 returned {read_logged}")
        if upd_logged not in (None, 400, 401):
            parts.append(f"logged-in tab UpdateUserSession.v1 returned {upd_logged}")
        if read_logged not in (None, 400, 401) and read_boot in (400, 401):
            parts.append("session API succeeds on logged-in tab but fails on bootstrap probe")
        if upd_logged not in (None, 400, 401) and upd_boot in (400, 401):
            parts.append("UpdateUserSession succeeds on logged-in tab but fails on bootstrap probe")

    if differences:
        parts.append(f"{len(differences)} metadata difference(s) recorded")
    if not parts:
        return "live session comparison captured; no major auth/session divergence detected"
    return "; ".join(parts)


def build_amex_live_session_comparison(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize and compare logged-in tab vs bootstrap probe tab snapshots."""
    logged_in_raw = payload.get("logged_in_tab") or {}
    bootstrap_raw = payload.get("bootstrap_probe_tab") or {}

    logged_in = sanitize_live_session_snapshot(logged_in_raw)
    logged_in["source"] = "logged_in_tab"
    bootstrap = sanitize_live_session_snapshot(bootstrap_raw)
    bootstrap["source"] = "bootstrap_probe_tab"

    differences = compute_live_session_comparison_differences(logged_in, bootstrap)
    field_diffs = compute_live_session_field_diffs(logged_in, bootstrap)
    diagnostic = compute_live_session_comparison_diagnostic(logged_in, bootstrap, differences)

    entry_url = str(payload.get("entry_url") or "")
    if entry_url:
        entry_url = sanitize_probe_url(entry_url)

    return {
        "provider": "amex",
        "entry_url": entry_url,
        "logged_in_tab": logged_in,
        "bootstrap_probe_tab": bootstrap,
        "field_diffs": field_diffs,
        "differences": differences,
        "diagnostic_summary": diagnostic,
        "compared_at": payload.get("compared_at") or utc_now_iso(),
    }


def ensure_live_session_comparison_tables(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_access_probe_live_session_comparisons (
            comparison_run_id   TEXT PRIMARY KEY,
            user_id               TEXT NOT NULL,
            entry_url             TEXT NOT NULL,
            lifecycle             TEXT NOT NULL,
            payload_json          TEXT,
            diagnostic_summary    TEXT,
            error_message         TEXT,
            requested_at          TEXT NOT NULL,
            started_at            TEXT,
            completed_at          TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_paplsc_user_requested "
        "ON provider_access_probe_live_session_comparisons(user_id, requested_at DESC)"
    )
    db.commit()


def _has_running_live_session_comparison(db: Any, user_id: str) -> bool:
    ensure_live_session_comparison_tables(db)
    row = db.execute(
        """
        SELECT 1 FROM provider_access_probe_live_session_comparisons
        WHERE user_id = ? AND lifecycle = ?
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return row is not None


def get_latest_live_session_comparison(db: Any, user_id: str) -> dict[str, Any] | None:
    ensure_live_session_comparison_tables(db)
    row = db.execute(
        """
        SELECT comparison_run_id, user_id, entry_url, lifecycle, payload_json,
               diagnostic_summary, error_message, requested_at, started_at, completed_at
        FROM provider_access_probe_live_session_comparisons
        WHERE user_id = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["comparison"] = _parse_json_dict(data.get("payload_json"))
    return data


def get_pending_live_session_comparison(db: Any, user_id: str) -> dict[str, Any] | None:
    ensure_live_session_comparison_tables(db)
    row = db.execute(
        """
        SELECT comparison_run_id, user_id, entry_url, lifecycle, requested_at, started_at
        FROM provider_access_probe_live_session_comparisons
        WHERE user_id = ? AND lifecycle = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (user_id, PROBE_LIFECYCLE_RUNNING),
    ).fetchone()
    return dict(row) if row else None


def start_live_session_comparison(db: Any, user_id: str, entry_url: str) -> dict[str, Any]:
    """Queue an Amex live session comparison diagnostic."""
    entry_url = entry_url.strip()
    if entry_url not in AMEX_LIVE_SESSION_COMPARISON_ENTRY_URLS:
        raise ValueError(
            f"unsupported live session comparison entry URL: {entry_url!r} "
            f"(allowed: {', '.join(AMEX_LIVE_SESSION_COMPARISON_ENTRY_URLS)})"
        )
    ensure_live_session_comparison_tables(db)
    ensure_manual_probe_tables(db)
    ensure_bootstrap_trace_tables(db)
    if _has_running_manual_probe(db, user_id):
        raise ConcurrentProbeError("a manual probe is already running")
    if _has_running_bootstrap_trace(db, user_id):
        raise ConcurrentProbeError("a bootstrap trace is already running")
    if _has_running_live_session_comparison(db, user_id):
        raise ConcurrentProbeError("a live session comparison is already running")

    comparison_run_id = str(uuid.uuid4())
    now = utc_now_iso()
    db.execute(
        """
        INSERT INTO provider_access_probe_live_session_comparisons (
            comparison_run_id, user_id, entry_url, lifecycle, requested_at, started_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (comparison_run_id, user_id, entry_url, PROBE_LIFECYCLE_RUNNING, now, now),
    )
    db.commit()
    return {
        "comparison_run_id": comparison_run_id,
        "entry_url": entry_url,
        "provider": "amex",
        "lifecycle": PROBE_LIFECYCLE_RUNNING,
        "requested_at": now,
        "started_at": now,
    }


def complete_live_session_comparison(
    db: Any,
    user_id: str,
    comparison_run_id: str,
    *,
    lifecycle: str,
    comparison: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    if lifecycle not in PROBE_LIFECYCLES:
        raise ValueError(f"invalid live session comparison lifecycle: {lifecycle!r}")
    ensure_live_session_comparison_tables(db)
    now = utc_now_iso()
    db.execute(
        """
        UPDATE provider_access_probe_live_session_comparisons
        SET lifecycle = ?, payload_json = ?, diagnostic_summary = ?,
            error_message = ?, completed_at = ?
        WHERE comparison_run_id = ? AND user_id = ?
        """,
        (
            lifecycle,
            _json_diagnostics(comparison),
            (comparison or {}).get("diagnostic_summary") if comparison else error_message,
            error_message,
            now,
            comparison_run_id,
            user_id,
        ),
    )
    db.commit()


def live_session_comparison_state_to_json(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "comparison_run_id": state.get("comparison_run_id"),
        "entry_url": state.get("entry_url"),
        "provider": state.get("provider") or "amex",
        "lifecycle": state.get("lifecycle") or PROBE_LIFECYCLE_IDLE,
        "diagnostic_summary": state.get("diagnostic_summary"),
        "error_message": state.get("error_message"),
        "requested_at": state.get("requested_at"),
        "started_at": state.get("started_at"),
        "completed_at": state.get("completed_at"),
        "comparison": state.get("comparison"),
    }
