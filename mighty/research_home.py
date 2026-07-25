"""
mighty.research_home
────────────────────
Guarded staging/research entry for moderated Home V2 user testing.

Creates an ephemeral demo-only session (no persistent customer records),
projects deterministic fictional Home V2 states, and stubs outbound /
mutating actions. Never available in production.
"""

from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any, Literal, Mapping
from urllib.parse import quote

from mighty.account_readiness import READY, SIGNED_OUT, AccountReadiness
from mighty.account_status import AccountStatus
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
    REASON_LOGIN,
    REASON_OPPORTUNITY,
)
from mighty.attention_state import ATTENTION_STATE_SCHEMA_VERSION, AttentionState
from mighty.attention_view import build_attention_view
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy

ResearchState = Literal["healthy", "attention", "opportunity"]

VALID_STATES: frozenset[str] = frozenset({"healthy", "attention", "opportunity"})
DEFAULT_STATE: ResearchState = "healthy"

# Session keys — ephemeral cookie session only; never written to users table.
SESSION_FLAG = "research_home"
SESSION_STATE_KEY = "research_home_state"
SESSION_STARTED_AT = "research_home_started_at"

# Synthetic identity — not a customer row. No credentials, tokens, or real email.
RESEARCH_USER_ID = "research-preview-session"
RESEARCH_DISPLAY_NAME = "Jordan"
RESEARCH_SIDEBAR_LABEL = "Research Participant"
STUB_PATH_PREFIX = "/research/stub"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in _TRUTHY


def _env_name() -> str:
    return (
        (os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "").strip().lower()
        or (os.environ.get("RAILWAY_ENVIRONMENT") or "").strip().lower()
        or (os.environ.get("MIGHTY_ENV") or "").strip().lower()
    )


def is_production_environment() -> bool:
    """True when any production signal is present."""
    if _env_name() == "production":
        return True
    if (os.environ.get("MIGHTY_ENV") or "").strip().lower() == "production":
        return True
    return False


def is_staging_or_research_environment() -> bool:
    """True when the runtime is explicitly labeled staging/research or opted in."""
    if _env_truthy("RESEARCH_HOME_ENABLED"):
        return True
    return _env_name() in ("staging", "research")


def research_home_allowed() -> bool:
    """Gate for /research/home — DEMO_MODE + non-production + staging/research."""
    if not _env_truthy("DEMO_MODE"):
        return False
    if is_production_environment():
        return False
    return is_staging_or_research_environment()


def normalize_state(raw: str | None) -> ResearchState | None:
    """Return a valid research state, or None if the value is missing/invalid."""
    if raw is None or raw == "":
        return DEFAULT_STATE
    value = str(raw).strip().lower()
    if value in VALID_STATES:
        return value  # type: ignore[return-value]
    return None


def is_research_session(session: Mapping[str, Any] | None) -> bool:
    if not session:
        return False
    return bool(session.get(SESSION_FLAG))


def is_active_research_session(session: Mapping[str, Any] | None) -> bool:
    """Research cookie session that is still allowed by environment guards."""
    return is_research_session(session) and research_home_allowed()


def research_state(session: Mapping[str, Any] | None) -> ResearchState:
    if not session:
        return DEFAULT_STATE
    value = session.get(SESSION_STATE_KEY) or DEFAULT_STATE
    if value in VALID_STATES:
        return value  # type: ignore[return-value]
    return DEFAULT_STATE


def begin_research_session(session: Any, *, state: ResearchState = DEFAULT_STATE) -> None:
    """Install an ephemeral research session. Does not touch the users table."""
    session.clear()
    session[SESSION_FLAG] = True
    session[SESSION_STATE_KEY] = state
    session[SESSION_STARTED_AT] = datetime.now(timezone.utc).isoformat()
    session["user_id"] = RESEARCH_USER_ID
    session["demo_mode"] = True
    session.permanent = False


def synthetic_user_row() -> Mapping[str, Any]:
    """Session-only user mapping — never persisted as a customer record."""
    return {
        "id": RESEARCH_USER_ID,
        "email": RESEARCH_SIDEBAR_LABEL,
        "preferred_name": RESEARCH_DISPLAY_NAME,
        "api_key": "mk_research_preview_not_a_real_key",
        "password_hash": "",
        "created_at": session_created_marker(),
        "extension_version": "research-preview",
        "extension_last_seen_at": datetime.now(timezone.utc).isoformat(),
        "intent_summary": None,
        "type_affinity": None,
    }


def session_created_marker() -> str:
    return "research-preview"


def count_research_customer_rows(db) -> int:
    """How many users rows look like research identities (should stay 0)."""
    try:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE id=? OR email=?",
            (RESEARCH_USER_ID, RESEARCH_SIDEBAR_LABEL),
        ).fetchone()
        if row is None:
            return 0
        return int(row["n"] if isinstance(row, Mapping) else row[0])
    except Exception:
        return 0


def _escape(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs) -> AccountReadiness:
    labels = {
        READY: ("Connected", user_copy.READINESS_COPY_READY, "ready", "up_to_date"),
        SIGNED_OUT: (
            "Sign in required",
            user_copy.READINESS_COPY_SIGNED_OUT,
            "needs_sign_in",
            "needs_login",
        ),
    }
    label, copy, presentation, canonical = labels[state]
    defaults = dict(
        provider=provider,
        state=state,
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state="connected" if state == READY else "signed_out",
        access_cycle_id=None,
        session_evidence_at=None,
        extraction_at=None,
        extraction_ok=state == READY,
        extraction_correlated=state == READY,
        verification_id=None,
        cached_data_label=None,
        last_confirmed_ready_at=(
            datetime.now(timezone.utc).isoformat() if state == READY else None
        ),
        last_confirmed_access_cycle_id="cycle-research-1" if state == READY else None,
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


def _status(provider: str, display_name: str, readiness_state: str) -> AccountStatus:
    view = build_customer_account_access_view(
        provider=provider,
        display_name=display_name,
        readiness=_readiness(provider, readiness_state),
        discovered_from=DISCOVERED_MANUAL,
    )
    canonical = view.canonical_status or "unverified"
    presentation_key = {
        "up_to_date": "ready",
        "needs_login": "needs_sign_in",
        "checking": "checking",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
        "unverified": "unknown",
    }.get(canonical, "unknown")
    return AccountStatus(
        source=view.provider,
        display_name=view.display_name,
        status=canonical,
        presentation_key=presentation_key,
        presentation_label=view.status_label,
        last_successful_sync_at=view.last_confirmed_at,
        current_attempt_at=None,
        last_error=None,
        user_action_label=view.user_action_text,
        user_action_url=view.user_action_url,
        customer_access=view,
    )


def _attention_blocker() -> AttentionItem:
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id="att_research_auth_blocker_amex",
        user_id=RESEARCH_USER_ID,
        fingerprint="research:auth:amex",
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider="amex",
        reason=AttentionReason(code=REASON_LOGIN),
        cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
        source_kind=AttentionSourceKind.AUTH,
        source_ref="research:amex",
        observed_at=datetime.now(timezone.utc).isoformat(),
        becomes_stale_at=None,
        interruption_expected=True,
    )


def _attention_opportunity() -> AttentionItem:
    return AttentionItem(
        schema_version=ATTENTION_ITEM_SCHEMA_VERSION,
        attention_id="att_research_opportunity_marriott",
        user_id=RESEARCH_USER_ID,
        fingerprint="research:opportunity:marriott",
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        provider="marriott",
        reason=AttentionReason(code=REASON_OPPORTUNITY),
        cta_key=AttentionCtaKey.OPEN_PROVIDER_SURFACE,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref="research:marriott:cert",
        observed_at=datetime.now(timezone.utc).isoformat(),
        becomes_stale_at=None,
        interruption_expected=False,
    )


def stub_url(action: str) -> str:
    return f"{STUB_PATH_PREFIX}/{quote(action, safe='')}"


def build_research_home_html(
    state: ResearchState,
    *,
    escape=None,
    today_label: str | None = None,
) -> str:
    """Render Home V2 for a deterministic fictional research state."""
    esc = escape or _escape
    today = today_label or datetime.now().strftime("%A, %B %-d")
    first_name = RESEARCH_DISPLAY_NAME

    if state == "attention":
        accounts = [_status("amex", "American Express", SIGNED_OUT)]
        result = resolve_home_state(accounts=accounts)
        attention = build_attention_view(
            AttentionState(
                schema_version=ATTENTION_STATE_SCHEMA_VERSION,
                primary=_attention_blocker(),
                remaining=(),
                silence=None,
            ),
            surface="home",
            provider_open_urls={"amex": stub_url("provider-signin")},
        )
        return render_home_page(
            result,
            first_name=first_name,
            today_label=today,
            last_checked="3 minutes ago",
            escape=esc,
            attention=attention,
            use_attention=True,
            recent_wins=[],
            gmail_connected=True,
            chrome_active=True,
        )

    if state == "opportunity":
        accounts = [_status("marriott", "Marriott Bonvoy", READY)]
        result = resolve_home_state(accounts=accounts)
        attention = build_attention_view(
            AttentionState(
                schema_version=ATTENTION_STATE_SCHEMA_VERSION,
                primary=_attention_opportunity(),
                remaining=(),
                silence=None,
            ),
            surface="home",
            provider_open_urls={"marriott": stub_url("provider-surface")},
        )
        return render_home_page(
            result,
            first_name=first_name,
            today_label=today,
            last_checked="8 minutes ago",
            escape=esc,
            attention=attention,
            use_attention=True,
            recent_wins=[
                {
                    "message": "Free night certificate is ready to use",
                    "source": "marriott",
                }
            ],
            gmail_connected=True,
            chrome_active=True,
        )

    # healthy (default)
    accounts = [
        _status("amex", "American Express", READY),
        _status("marriott", "Marriott Bonvoy", READY),
        _status("delta", "Delta SkyMiles", READY),
    ]
    result = resolve_home_state(accounts=accounts)
    return render_home_page(
        result,
        first_name=first_name,
        today_label=today,
        last_checked="2 minutes ago",
        escape=esc,
        attention=None,
        use_attention=False,
        recent_wins=[
            {"message": "Membership Rewards balance updated", "source": "amex"},
            {"message": "SkyMiles balance updated", "source": "delta"},
        ],
        gmail_connected=True,
        chrome_active=True,
    )


def render_research_indicator() -> str:
    """Small, unobtrusive marker — staging research sessions only."""
    return (
        '<div class="research-preview-indicator" role="status" '
        'data-research-preview="1" aria-label="Research preview">'
        '<span class="research-preview-indicator__dot" aria-hidden="true"></span>'
        "Research preview"
        "</div>"
        "<style>"
        ".research-preview-indicator{"
        "display:inline-flex;align-items:center;gap:6px;"
        "margin:8px 24px 0;padding:4px 10px;"
        "font-size:11px;font-weight:500;letter-spacing:0.02em;"
        "color:#78716c;background:rgba(0,0,0,0.03);"
        "border:0.5px solid rgba(0,0,0,0.06);border-radius:999px;"
        "width:fit-content;user-select:none;"
        "}"
        ".research-preview-indicator__dot{"
        "width:6px;height:6px;border-radius:50%;background:#a8a29e;flex-shrink:0;"
        "}"
        "@media(max-width:768px){.research-preview-indicator{margin:8px 16px 0}}"
        "</style>"
    )


# Paths research sessions may reach without stubbing.
_RESEARCH_ALLOWED_PREFIXES = (
    "/dashboard",
    "/dashboard/legacy",
    "/home",
    "/research/",
    "/logout",
    "/static/",
    "/logo",
    "/favicon",
    "/health",
    "/api/csrf-token",
)

# Mutating / outbound surfaces that must never run in a research session.
_RESEARCH_BLOCKED_PREFIXES = (
    "/api/data/",
    "/api/email/",
    "/api/providers/",
    "/api/2fa/",
    "/api/push/",
    "/api/authorize",
    "/api/record",
    "/api/settings/",
    "/oauth",
    "/email-scan",
    "/credentials",
    "/onboarding",
    "/extension-setup",
    "/sync",
    "/gmail",
    "/google",
)


def research_request_allowed(path: str, method: str) -> bool:
    """Return False when a research session must not reach this path."""
    p = path or "/"
    m = (method or "GET").upper()

    if p.startswith(STUB_PATH_PREFIX):
        return True
    if p in (
        "/dashboard",
        "/dashboard/legacy",
        "/home",
        "/logout",
        "/health",
        "/api/csrf-token",
    ):
        return True
    if p.startswith("/static/") or p.startswith("/logo"):
        return True
    if p.startswith("/research/"):
        return True

    for prefix in _RESEARCH_BLOCKED_PREFIXES:
        if p == prefix or p.startswith(prefix.rstrip("/") + "/") or p.startswith(prefix):
            return False

    # Block any non-GET that isn't an allowlisted research/logout path.
    if m not in ("GET", "HEAD", "OPTIONS"):
        if p.startswith("/logout"):
            return True
        return False

    for prefix in _RESEARCH_ALLOWED_PREFIXES:
        if p == prefix.rstrip("/") or p.startswith(prefix):
            return True
    # Default: block unknown surfaces so enrollment / settings can't mutate.
    return False


def stub_response_body(action: str) -> str:
    """Safe stub page for outbound / mutating research CTAs."""
    safe = _escape(action.replace("-", " "))
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Research preview — Mighty</title>"
        "<style>"
        "body{font-family:Inter,system-ui,sans-serif;background:#f5f2ec;"
        "color:#1c1917;margin:0;padding:48px 24px;}"
        ".card{max-width:420px;margin:0 auto;background:#fff;border-radius:14px;"
        "border:0.5px solid rgba(0,0,0,0.08);padding:28px 24px;"
        "box-shadow:0 1px 2px rgba(0,0,0,0.04)}"
        "h1{font-size:18px;margin:0 0 8px;letter-spacing:-0.02em}"
        "p{font-size:14px;line-height:1.5;color:#57534e;margin:0 0 18px}"
        "a{color:#1c1917;font-weight:600;font-size:14px}"
        "</style></head><body>"
        "<div class='card' data-research-stub='1'>"
        "<h1>Unavailable in research preview</h1>"
        f"<p>This action (<strong>{safe}</strong>) is disabled so moderated "
        "Home testing never reaches real accounts, email, or providers.</p>"
        "<a href='/dashboard'>Back to Home</a>"
        "</div></body></html>"
    )


def fill_dashboard_html(
    dashboard_html: str,
    *,
    hero_html: str,
    indicator_html: str,
    sidebar_desktop: str,
    sidebar_mobile: str,
    csrf: str,
) -> str:
    """Fill DASHBOARD_HTML placeholders for a research Home-only view."""
    replacements = {
        "{_SIDEBAR_DESKTOP_}": sidebar_desktop,
        "{_SIDEBAR_MOBILE_}": sidebar_mobile,
        "{email}": _escape(RESEARCH_SIDEBAR_LABEL),
        "{feed_html}": "",
        "{pending_count}": "0",
        "{pending_display}": "none",
        "{expiring_count}": "0",
        "{expiring_plural}": "s",
        "{expiring_display}": "none",
        "{agent_status_indicator}": "",
        "{agent_cta_button}": "",
        "{feed_col_hidden}": "display:none",
        "{welcome_state}": "",
        "{demo_mode_banner}": indicator_html,
        "{onboarding_banner}": "",
        "{reauth_banner}": "",
        "{new_accounts_banner}": "",
        "{account_data_html}": "",
        "{recommendations_section_html}": "",
        "{hero_section_html}": hero_html,
        "{topbar_search_html}": "",
        "{insights_html}": "",
        "{available_rail_html}": "",
        "{action_center_html}": "",
        "{recently_found_html}": "",
        "{relevant_now_html}": "",
        "{wallet_insights_html}": "",
        "{value_center_html}": "",
        "{top_benefits_html}": "",
        "{progress_section_html}": "",
        "{opportunities_html}": "",
        "{onboarding_modal}": "",
        "{dash_modals}": "",
        "{csrf_token}": csrf,
        "{global_sync_label}": "Research preview",
        "{latest_sync_baseline}": '""',
        "{awaiting_sync_poll}": "false",
        "{dash_global_last_updated_title}": "Fictional research data",
        "{dash_role_extension_desc}": "",
        "{dash_activity_watching}": "Watching",
        "{dash_ext_setup_link}": "Extension setup",
        "{dash_needs_login_badge}": "",
        "{dash_last_updated_prefix}": "",
        "{dash_mobile_worker_toast}": "",
        "{dash_ext_install_toast}": "",
        "{dash_status_label_updating}": "",
    }
    out = dashboard_html
    for key, value in replacements.items():
        out = out.replace(key, value)
    # Best-effort: blank any remaining template holes so the page still loads.
    import re

    out = re.sub(r"\{[a-zA-Z0-9_]+\}", "", out)
    return out
