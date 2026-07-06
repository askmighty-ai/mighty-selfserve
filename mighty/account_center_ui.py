"""
mighty.account_center_ui
────────────────────────
Consumer-facing Account Connection Center — presentation only.

Reads AccountState objects exclusively. No pipeline, lifecycle, or extraction
concepts appear in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mighty.account_presentation import resolve_account_presentation
from mighty.account_state import (
    ACCESS_API,
    ACCESS_BROWSER_SESSION,
    ACCESS_MANUAL,
    ACCESS_MIGHTY_LOGIN,
    ACTION_CONNECT,
    ACTION_LOGIN,
    ACTION_NONE,
    ACTION_OPEN_PROVIDER,
    ACTION_REVIEW,
    ACTION_WAIT,
    CONFIDENCE_LOW,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    CONN_NOT_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
    SESSION_EXPIRED,
    SESSION_EXPIRING,
    SESSION_HEALTHY,
    SESSION_UNKNOWN,
    AccountState,
)

from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_CONNECTED,
    ACCOUNT_STATE_LABELS,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_LOGIN,
    ACCOUNT_STATE_NO_DATA,
    CTA_CHECKING,
    CTA_REFRESH,
    CTA_SIGN_IN,
    CTA_VIEW,
)

# ── Status tones (card accent colors) ───────────────────────────────────────
TONE_CONNECTED = "connected"
TONE_ATTENTION = "attention"
TONE_LOGIN = "login"
TONE_NEVER = "never"

TONE_COLORS: dict[str, str] = {
    TONE_CONNECTED: "#16a34a",
    TONE_ATTENTION: "#ca8a04",
    TONE_LOGIN: "#dc2626",
    TONE_NEVER: "#9ca3af",
}

TONE_BG: dict[str, str] = {
    TONE_CONNECTED: "#f0fdf4",
    TONE_ATTENTION: "#fefce8",
    TONE_LOGIN: "#fef2f2",
    TONE_NEVER: "#f9fafb",
}

# ── Primary actions (future wiring via data-action-kind) ─────────────────────
PRIMARY_CONNECT = "connect"
PRIMARY_LOGIN = "login"
PRIMARY_RECONNECT = "login"
PRIMARY_REFRESH = "refresh"
PRIMARY_VIEW = "view"
PRIMARY_VIEW_BENEFITS = "view"
PRIMARY_CHECKING = "checking"

LINKABLE_ACTION_KINDS = frozenset({PRIMARY_LOGIN, PRIMARY_RECONNECT, PRIMARY_CONNECT})

LINKABLE_ACTION_KINDS = frozenset({PRIMARY_LOGIN, PRIMARY_RECONNECT, PRIMARY_CONNECT})

ACCESS_LABELS: dict[str, str] = {
    ACCESS_BROWSER_SESSION: "Extension",
    ACCESS_MIGHTY_LOGIN: "Cloud",
    ACCESS_API: "API",
    ACCESS_MANUAL: "Manual",
}

SESSION_LABELS: dict[str, str] = {
    SESSION_HEALTHY: "Healthy",
    SESSION_EXPIRING: "Expiring soon",
    SESSION_EXPIRED: "Expired",
    SESSION_UNKNOWN: "Unknown",
}

DATA_STATUS_LABELS: dict[str, str] = {
    DATA_NONE: "No data",
    DATA_PARTIAL: "Partial",
    DATA_COMPLETE: "Complete",
}

CONNECTION_STATUS_LABELS: dict[str, str] = {
    CONN_CONNECTED: "Connected",
    CONN_CONNECTING: "Connecting",
    CONN_NEEDS_LOGIN: "Needs login",
    CONN_NOT_CONNECTED: "Not connected",
}


@dataclass
class AccountCenterSummary:
    total: int
    connected: int
    needs_login: int
    needs_attention: int
    not_connected: int


@dataclass
class AccountCenterCardView:
    """Presentation model for one provider card."""

    provider: str
    display_name: str
    icon: str
    color: str
    status_tone: str
    status_label: str
    data_freshness: str
    session_label: str
    access_label: str
    observation_count: int
    last_refresh_label: str
    primary_action: str
    primary_action_kind: str
    primary_action_href: str | None
    primary_action_external: bool
    primary_action_disabled: bool
    status_line: str


def status_tone(state: AccountState) -> str:
    presentation = resolve_account_presentation(state)
    key = presentation.key
    if key == ACCOUNT_STATE_NEEDS_LOGIN:
        return TONE_LOGIN
    if key == ACCOUNT_STATE_CHECKING:
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_NO_DATA:
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_NEEDS_ATTENTION:
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_CONNECTED:
        return TONE_CONNECTED
    if state.connection_state == CONN_NOT_CONNECTED:
        return TONE_NEVER
    return TONE_ATTENTION


def status_label(state: AccountState) -> str:
    return resolve_account_presentation(state).label


def data_freshness_label(
    state: AccountState,
    fmt_relative: Callable[[str], str],
) -> str:
    status = DATA_STATUS_LABELS.get(state.data_status, state.data_status.title())
    if not state.last_data_refresh:
        return status
    rel = fmt_relative(state.last_data_refresh)
    if state.data_status in {DATA_COMPLETE, DATA_PARTIAL}:
        return f"Fresh ({rel})"
    return f"{status} ({rel})"


def last_refresh_label(
    state: AccountState,
    fmt_relative: Callable[[str], str],
) -> str:
    if not state.last_data_refresh:
        return "Never"
    return fmt_relative(state.last_data_refresh)


def primary_action(state: AccountState) -> tuple[str, str, bool]:
    """Return (button label, action kind, disabled) for the card CTA."""
    presentation = resolve_account_presentation(state)
    if presentation.cta_disabled:
        return presentation.cta_label, PRIMARY_CHECKING, True

    label = presentation.cta_label
    if presentation.key == ACCOUNT_STATE_NEEDS_LOGIN:
        return label, PRIMARY_LOGIN, False
    if presentation.key == ACCOUNT_STATE_CHECKING:
        return label, PRIMARY_CHECKING, True
    if presentation.key == ACCOUNT_STATE_NO_DATA:
        return label, PRIMARY_REFRESH, False
    if presentation.key == ACCOUNT_STATE_NEEDS_ATTENTION:
        if label == CTA_SIGN_IN:
            return label, PRIMARY_LOGIN, False
        return label, PRIMARY_REFRESH, False
    if label == CTA_VIEW:
        return label, PRIMARY_VIEW, False
    if label == CTA_REFRESH:
        return label, PRIMARY_REFRESH, False
    if label == CTA_CHECKING:
        return label, PRIMARY_CHECKING, True
    return label, PRIMARY_CONNECT, False


def resolve_primary_action_href(
    kind: str,
    provider: str,
    *,
    provider_login_url: str | None = None,
) -> tuple[str | None, bool]:
    """Return (href, open_in_new_tab) for linkable CTAs; (None, False) for placeholders."""
    if kind not in LINKABLE_ACTION_KINDS:
        return None, False
    if provider_login_url:
        return provider_login_url, True
    return f"/credentials?connect={provider}", False


def resolve_primary_action_href(
    kind: str,
    provider: str,
    *,
    provider_login_url: str | None = None,
) -> tuple[str | None, bool]:
    """Return (href, open_in_new_tab) for linkable CTAs; (None, False) for placeholders."""
    if kind not in LINKABLE_ACTION_KINDS:
        return None, False
    if provider_login_url:
        return provider_login_url, True
    if kind == PRIMARY_CONNECT:
        return f"/credentials?connect={provider}", False
    return f"/credentials?connect={provider}", False


def build_card_view(
    state: AccountState,
    *,
    icon: str = "🔗",
    color: str = "#f3f4f6",
    fmt_relative: Callable[[str], str],
    provider_login_url: str | None = None,
) -> AccountCenterCardView:
    label, kind, disabled = primary_action(state)
    label, kind = primary_action(state)
    href, external = resolve_primary_action_href(
        kind, state.provider, provider_login_url=provider_login_url,
    )
    return AccountCenterCardView(
        provider=state.provider,
        display_name=state.display_name,
        icon=icon,
        color=color,
        status_tone=status_tone(state),
        status_label=status_label(state),
        data_freshness=data_freshness_label(state, fmt_relative),
        session_label=SESSION_LABELS.get(state.session_health, state.session_health.title()),
        access_label=ACCESS_LABELS.get(state.access_method, state.access_method.replace("_", " ").title()),
        observation_count=len(state.observations_available),
        last_refresh_label=last_refresh_label(state, fmt_relative),
        primary_action=label,
        primary_action_kind=kind,
        primary_action_href=href,
        primary_action_external=external,
        primary_action_disabled=disabled,
        status_line=state.status_line,
    )


def build_summary(cards: list[AccountCenterCardView]) -> AccountCenterSummary:
    counts = {TONE_CONNECTED: 0, TONE_LOGIN: 0, TONE_ATTENTION: 0, TONE_NEVER: 0}
    for card in cards:
        counts[card.status_tone] = counts.get(card.status_tone, 0) + 1
    return AccountCenterSummary(
        total=len(cards),
        connected=counts[TONE_CONNECTED],
        needs_login=counts[TONE_LOGIN],
        needs_attention=counts[TONE_ATTENTION],
        not_connected=counts[TONE_NEVER],
    )


def summary_headline(summary: AccountCenterSummary) -> str:
    parts = [f"{summary.total} account{'s' if summary.total != 1 else ''}"]
    if summary.connected:
        parts.append(f"{summary.connected} {ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CONNECTED].lower()}")
    if summary.needs_login:
        verb = "needs" if summary.needs_login == 1 else "need"
        parts.append(
            f"{summary.needs_login} {verb} "
            f"{ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_LOGIN].lower()}"
        )
    if summary.needs_attention:
        verb = "needs" if summary.needs_attention == 1 else "need"
        parts.append(
            f"{summary.needs_attention} {verb} "
            f"{ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION].lower()}"
        )
    if summary.not_connected:
        parts.append(f"{summary.not_connected} not connected")
    return " · ".join(parts)


def sort_cards(cards: list[AccountCenterCardView]) -> list[AccountCenterCardView]:
    tone_order = {TONE_LOGIN: 0, TONE_ATTENTION: 1, TONE_NEVER: 2, TONE_CONNECTED: 3}

    def _key(card: AccountCenterCardView) -> tuple[int, str]:
        return (tone_order.get(card.status_tone, 9), card.display_name.lower())

    return sorted(cards, key=_key)


def render_card_cta(card: AccountCenterCardView, escape: Callable[[Any], str]) -> str:
    disabled_attr = ' disabled aria-disabled="true"' if card.primary_action_disabled else ""
    if card.primary_action_href and not card.primary_action_disabled:
    if card.primary_action_href:
        external = (
            ' target="_blank" rel="noopener noreferrer"'
            if card.primary_action_external
            else ""
        )
        return (
            f'<a href="{escape(card.primary_action_href)}" class="acc-card-cta"'
            f'{external} data-provider="{escape(card.provider)}" '
            f'data-action="{escape(card.primary_action_kind)}">'
            f"{escape(card.primary_action)}</a>"
        )
    return (
        f'<button type="button" class="acc-card-cta"'
        f'{disabled_attr} '
        f'data-provider="{escape(card.provider)}" '
        f'data-action="{escape(card.primary_action_kind)}">'
        f"{escape(card.primary_action)}</button>"
    )


def render_card(card: AccountCenterCardView, escape: Callable[[Any], str]) -> str:
    tone = card.status_tone
    accent = TONE_COLORS[tone]
    bg = TONE_BG[tone]
    return (
        f'<article class="acc-card acc-card--{escape(tone)}" '
        f'data-provider="{escape(card.provider)}" '
        f'data-action-kind="{escape(card.primary_action_kind)}">'
        f'<div class="acc-card-accent" style="background:{escape(accent)}"></div>'
        f'<div class="acc-card-body">'
        f'<header class="acc-card-header">'
        f'<div class="acc-card-icon" style="background:{escape(card.color)}">'
        f'{escape(card.icon)}</div>'
        f'<div class="acc-card-title-block">'
        f'<h2 class="acc-card-title">{escape(card.display_name)}</h2>'
        f'<span class="acc-card-status" style="color:{escape(accent)};'
        f'background:{escape(bg)}">{escape(card.status_label)}</span>'
        f"</div></header>"
        f'<dl class="acc-card-meta">'
        f'<div class="acc-meta-row">'
        f'<dt>Data</dt><dd>{escape(card.data_freshness)}</dd></div>'
        f'<div class="acc-meta-row">'
        f'<dt>Session</dt><dd>{escape(card.session_label)}</dd></div>'
        f'<div class="acc-meta-row">'
        f'<dt>Access</dt><dd>{escape(card.access_label)}</dd></div>'
        f'<div class="acc-meta-row">'
        f'<dt>Observations</dt><dd>{card.observation_count}</dd></div>'
        f"</dl>"
        f'<p class="acc-card-subline">{escape(card.status_line)}</p>'
        f'<footer class="acc-card-footer">'
        f'<span class="acc-card-refreshed">Last refresh · {escape(card.last_refresh_label)}</span>'
        f"{render_card_cta(card, escape)}"
        f"</footer></div></article>"
    )


def render_empty_state(escape: Callable[[Any], str]) -> str:
    return (
        f'<div class="acc-empty">'
        f'<p class="acc-empty-title">No connected accounts yet</p>'
        f'<p class="acc-empty-body">When you connect providers, their status will appear here.</p>'
        f'<a href="/credentials" class="acc-empty-link">Go to Accounts</a>'
        f"</div>"
    )


def render_page_body(
    cards: list[AccountCenterCardView],
    summary: AccountCenterSummary,
    escape: Callable[[Any], str],
) -> str:
    if not cards:
        grid = render_empty_state(escape)
    else:
        grid = "".join(render_card(c, escape) for c in cards)
    headline = escape(summary_headline(summary))
    return (
        f'<header class="acc-hero">'
        f'<h1>Connections</h1>'
        f'<p class="acc-hero-sub">The current state of each connected account.</p>'
        f'<p class="acc-hero-summary">{headline}</p>'
        f"</header>"
        f'<div class="acc-grid">{grid}</div>'
    )


ACCOUNT_CENTER_CSS = """
/* BASE_CSS pins .app-shell to 100vh + overflow:hidden; scroll main content instead. */
.main-content{height:100vh;overflow-y:auto;-webkit-overflow-scrolling:touch}
.page{max-width:960px;margin:0 auto;padding:32px 24px 64px}
.page-header-text h1{font-size:26px;font-weight:700;color:#1c1917;letter-spacing:-0.02em}
.acc-hero{margin-bottom:40px}
.acc-hero h1{font-size:32px;font-weight:700;color:#1c1917;letter-spacing:-0.03em;margin-bottom:8px}
.acc-hero-sub{font-size:15px;color:#78716c;line-height:1.5;margin-bottom:12px}
.acc-hero-summary{font-size:13px;font-weight:500;color:#6366f1;letter-spacing:0.01em}
.acc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:20px}
.acc-card{position:relative;background:#fff;border-radius:16px;border:1px solid rgba(0,0,0,0.06);overflow:hidden;display:flex;flex-direction:column;transition:box-shadow 0.15s,transform 0.15s}
.acc-card:hover{box-shadow:0 8px 30px rgba(0,0,0,0.06);transform:translateY(-1px)}
.acc-card-accent{height:3px;width:100%}
.acc-card-body{padding:20px 20px 18px;display:flex;flex-direction:column;flex:1;gap:16px}
.acc-card-header{display:flex;align-items:flex-start;gap:14px}
.acc-card-icon{width:44px;height:44px;border-radius:12px;display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0}
.acc-card-title-block{flex:1;min-width:0}
.acc-card-title{font-size:16px;font-weight:600;color:#1c1917;margin:0 0 6px;line-height:1.25}
.acc-card-status{display:inline-block;font-size:11px;font-weight:600;padding:3px 10px;border-radius:99px;letter-spacing:0.02em}
.acc-card-meta{display:grid;grid-template-columns:1fr 1fr;gap:10px 16px;margin:0}
.acc-meta-row{display:flex;flex-direction:column;gap:2px}
.acc-meta-row dt{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.06em;color:#a8a29e;margin:0}
.acc-meta-row dd{font-size:13px;font-weight:500;color:#44403c;margin:0}
.acc-card-subline{font-size:12px;color:#78716c;line-height:1.45;margin:0}
.acc-card-footer{margin-top:auto;display:flex;align-items:center;justify-content:space-between;gap:12px;padding-top:4px;border-top:1px solid rgba(0,0,0,0.05)}
.acc-card-refreshed{font-size:11px;color:#a8a29e;white-space:nowrap}
.acc-card-cta{padding:8px 16px;border-radius:10px;font-size:13px;font-weight:600;border:none;background:#6366f1;color:#fff;cursor:pointer;transition:background 0.12s;white-space:nowrap;font-family:inherit;display:inline-block;text-decoration:none;text-align:center}
.acc-card-cta:hover{background:#4f46e5;color:#fff;text-decoration:none}
.acc-card-cta:disabled{opacity:0.55;cursor:not-allowed}
.acc-empty{text-align:center;padding:80px 24px;background:#fff;border-radius:16px;border:1px dashed rgba(0,0,0,0.08)}
.acc-empty-title{font-size:18px;font-weight:600;color:#1c1917;margin-bottom:8px}
.acc-empty-body{font-size:14px;color:#78716c;margin-bottom:20px}
.acc-empty-link{display:inline-block;padding:10px 20px;background:#6366f1;color:#fff;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none}
.acc-empty-link:hover{background:#4f46e5;text-decoration:none;color:#fff}
@media(max-width:640px){
  .page{padding:20px 16px 48px}
  .acc-hero h1{font-size:26px}
  .acc-grid{grid-template-columns:1fr}
  .acc-card-footer{flex-direction:column;align-items:stretch}
  .acc-card-cta{width:100%;text-align:center}
}
"""
