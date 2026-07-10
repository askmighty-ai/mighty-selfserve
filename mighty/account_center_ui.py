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

from mighty.account_presentation import (
    build_access_loop_summary,
    presentations_for_states,
    resolve_account_presentation,
)
from mighty.account_state import (
    ACCESS_API,
    ACCESS_BROWSER_SESSION,
    ACCESS_MANUAL,
    ACCESS_MIGHTY_LOGIN,
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
    ACCOUNT_STATE_LABELS,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    ACCOUNT_STATE_UNKNOWN,
    ACCOUNT_STATE_UPDATING,
    CTA_FIX,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
    DATA_NEVER_REFRESHED,
    DATA_REFRESHED_PREFIX,
    SESSION_NEVER_VERIFIED,
    SESSION_VERIFIED_PREFIX,
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
PRIMARY_FIX = "fix"
PRIMARY_VIEW = "view"
PRIMARY_CHECKING = "checking"

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
    CONN_NEEDS_LOGIN: "Needs sign in",
    CONN_NOT_CONNECTED: "Not connected",
}


@dataclass
class AccountCenterSummary:
    total: int
    ready: int
    needs_sign_in: int
    updating: int
    needs_attention: int


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
    session_verified_label: str
    data_refreshed_label: str
    primary_action: str
    primary_action_kind: str
    primary_action_href: str | None
    primary_action_external: bool
    primary_action_disabled: bool
    status_line: str


def status_tone(state: AccountState, *, presentation_key: str | None = None) -> str:
    key = presentation_key or resolve_account_presentation(state).key
    if key == ACCOUNT_STATE_NEEDS_SIGN_IN:
        return TONE_LOGIN
    if key in (ACCOUNT_STATE_UPDATING, ACCOUNT_STATE_CHECKING, "checking"):
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_UNKNOWN or key == "unknown":
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_NEEDS_ATTENTION:
        return TONE_ATTENTION
    if key == ACCOUNT_STATE_READY:
        return TONE_CONNECTED
    if state.connection_state == CONN_NOT_CONNECTED:
        return TONE_NEVER
    return TONE_ATTENTION


def status_label(
    state: AccountState,
    *,
    presentation_label: str | None = None,
) -> str:
    if presentation_label is not None:
        return presentation_label
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


def session_verified_label(
    state: AccountState,
    fmt_relative: Callable[[str], str],
) -> str:
    if not state.last_verified_at:
        return SESSION_NEVER_VERIFIED
    return f"{SESSION_VERIFIED_PREFIX} · {fmt_relative(state.last_verified_at)}"


def data_refreshed_label(
    state: AccountState,
    fmt_relative: Callable[[str], str],
) -> str:
    if not state.last_data_refresh:
        return DATA_NEVER_REFRESHED
    return f"{DATA_REFRESHED_PREFIX} · {fmt_relative(state.last_data_refresh)}"


def primary_action(
    state: AccountState,
    *,
    presentation=None,
) -> tuple[str, str, bool]:
    """Return (button label, action kind, disabled) for the card CTA."""
    presentation = presentation or resolve_account_presentation(state)
    if presentation.key == ACCOUNT_STATE_NEEDS_SIGN_IN:
        return presentation.cta_label, PRIMARY_LOGIN, False
    if presentation.key in (ACCOUNT_STATE_UPDATING, ACCOUNT_STATE_CHECKING, "checking"):
        return presentation.cta_label or ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING], PRIMARY_CHECKING, True
    if presentation.key in (ACCOUNT_STATE_UNKNOWN, "unknown"):
        return (
            presentation.cta_label or ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UNKNOWN],
            PRIMARY_CHECKING,
            True,
        )
    if presentation.key == ACCOUNT_STATE_READY:
        return presentation.cta_label, PRIMARY_VIEW, False
    if presentation.key == ACCOUNT_STATE_NEEDS_ATTENTION:
        return CTA_FIX, PRIMARY_FIX, False
    return presentation.cta_label, PRIMARY_CONNECT, False


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


def build_card_view(
    state: AccountState,
    *,
    icon: str = "🔗",
    color: str = "#f3f4f6",
    fmt_relative: Callable[[str], str],
    provider_login_url: str | None = None,
    session_access=None,
) -> AccountCenterCardView:
    """Build card view. Login/access presentation comes only from session_access."""
    from mighty.account_presentation import AccountPresentation
    from mighty.session_access import (
        SESSION_STATUS_LABELS,
        resolve_session_access_presentation,
    )

    presentation = None
    session_state = None
    if session_access is not None:
        sess = resolve_session_access_presentation(
            session_access, display_name=state.display_name,
        )
        session_state = sess.session_state
        # All four canonical states — never fall back to legacy login presentation.
        presentation = AccountPresentation(
            key=sess.presentation_key,
            label=SESSION_STATUS_LABELS[sess.session_state],
            cta_label=sess.cta_label or "",
            cta_disabled=sess.session_state in ("checking", "unknown"),
            extension_hint=sess.extension_hint,
        )
    else:
        # No Current Access row (non-probe): treat login as unknown — never legacy needs_login.
        session_state = "unknown"
        presentation = AccountPresentation(
            key=ACCOUNT_STATE_UNKNOWN,
            label=SESSION_STATUS_LABELS["unknown"],
            cta_label="",
            cta_disabled=True,
            extension_hint="Mighty could not verify this account automatically.",
        )

    label, kind, disabled = primary_action(state, presentation=presentation)
    # Only signed_out may emit a login CTA.
    if session_state != "signed_out" and kind == PRIMARY_LOGIN:
        label = SESSION_STATUS_LABELS.get(session_state or "unknown", "Unable to verify")
        kind = PRIMARY_CHECKING
        disabled = True
    href, external = resolve_primary_action_href(
        kind, state.provider, provider_login_url=provider_login_url,
    )
    return AccountCenterCardView(
        provider=state.provider,
        display_name=state.display_name,
        icon=icon,
        color=color,
        status_tone=status_tone(state, presentation_key=presentation.key),
        status_label=presentation.label,
        data_freshness=data_freshness_label(state, fmt_relative),
        session_label=SESSION_LABELS.get(state.session_health, state.session_health.title()),
        access_label=ACCESS_LABELS.get(state.access_method, state.access_method.replace("_", " ").title()),
        observation_count=len(state.observations_available),
        session_verified_label=session_verified_label(state, fmt_relative),
        data_refreshed_label=data_refreshed_label(state, fmt_relative),
        primary_action=label,
        primary_action_kind=kind,
        primary_action_href=href,
        primary_action_external=external,
        primary_action_disabled=disabled,
        status_line=state.status_line,
    )


def build_summary(cards: list[AccountCenterCardView]) -> AccountCenterSummary:
    counts = {
        ACCOUNT_STATE_READY: 0,
        ACCOUNT_STATE_NEEDS_SIGN_IN: 0,
        ACCOUNT_STATE_UPDATING: 0,
        ACCOUNT_STATE_NEEDS_ATTENTION: 0,
    }
    tone_to_key = {
        TONE_CONNECTED: ACCOUNT_STATE_READY,
        TONE_LOGIN: ACCOUNT_STATE_NEEDS_SIGN_IN,
        TONE_ATTENTION: ACCOUNT_STATE_NEEDS_ATTENTION,
        TONE_NEVER: ACCOUNT_STATE_NEEDS_SIGN_IN,
    }
    for card in cards:
        key = tone_to_key.get(card.status_tone, ACCOUNT_STATE_NEEDS_ATTENTION)
        if card.status_label == ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING]:
            key = ACCOUNT_STATE_UPDATING
        counts[key] = counts.get(key, 0) + 1
    return AccountCenterSummary(
        total=len(cards),
        ready=counts[ACCOUNT_STATE_READY],
        needs_sign_in=counts[ACCOUNT_STATE_NEEDS_SIGN_IN],
        updating=counts[ACCOUNT_STATE_UPDATING],
        needs_attention=counts[ACCOUNT_STATE_NEEDS_ATTENTION],
    )


def summary_headline(summary: AccountCenterSummary) -> str:
    presentations = []
    for _ in range(summary.needs_sign_in):
        from mighty.account_presentation import AccountPresentation

        presentations.append(
            AccountPresentation(
                key=ACCOUNT_STATE_NEEDS_SIGN_IN,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN],
                cta_label=CTA_SIGN_IN,
                cta_disabled=False,
            )
        )
    for _ in range(summary.updating):
        from mighty.account_presentation import AccountPresentation

        presentations.append(
            AccountPresentation(
                key=ACCOUNT_STATE_UPDATING,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING],
                cta_label=CTA_UPDATING,
                cta_disabled=True,
            )
        )
    for _ in range(summary.ready):
        from mighty.account_presentation import AccountPresentation

        presentations.append(
            AccountPresentation(
                key=ACCOUNT_STATE_READY,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY],
                cta_label=CTA_VIEW,
                cta_disabled=False,
            )
        )
    for _ in range(summary.needs_attention):
        from mighty.account_presentation import AccountPresentation

        presentations.append(
            AccountPresentation(
                key=ACCOUNT_STATE_NEEDS_ATTENTION,
                label=ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
                cta_label=CTA_FIX,
                cta_disabled=False,
            )
        )
    loop = build_access_loop_summary(presentations)
    if loop.is_updating:
        if loop.detail_lines:
            return f"{loop.headline} · {' · '.join(loop.detail_lines)}"
        return loop.headline
    if loop.detail_lines:
        return " · ".join(loop.detail_lines)
    return loop.headline


def summary_headline_from_states(
    states: list[AccountState],
    *,
    sync_running: bool = False,
    updating_source: str | None = None,
) -> str:
    presentations = presentations_for_states(
        states, sync_running=sync_running, updating_source=updating_source,
    )
    loop = build_access_loop_summary(presentations)
    if loop.detail_lines and loop.headline != " · ".join(loop.detail_lines):
        return f"{loop.headline} · {' · '.join(loop.detail_lines)}"
    return loop.headline


def sort_cards(cards: list[AccountCenterCardView]) -> list[AccountCenterCardView]:
    tone_order = {TONE_LOGIN: 0, TONE_ATTENTION: 1, TONE_NEVER: 2, TONE_CONNECTED: 3}

    def _key(card: AccountCenterCardView) -> tuple[int, str]:
        return (tone_order.get(card.status_tone, 9), card.display_name.lower())

    return sorted(cards, key=_key)


def render_card_cta(card: AccountCenterCardView, escape: Callable[[Any], str]) -> str:
    disabled_attr = ' disabled aria-disabled="true"' if card.primary_action_disabled else ""

    if card.primary_action_href and not card.primary_action_disabled:
        external = (
            ' target="_blank" rel="noopener noreferrer"'
            if card.primary_action_external
            else ""
        )
        return (
            f'<a href="{escape(card.primary_action_href)}" class="acc-card-cta"'
            f'{external} data-provider="{escape(card.provider)}" '
            f'data-action="{escape(card.primary_action_kind)}">'
            f'{escape(card.primary_action)}</a>'
        )

    return (
        f'<button type="button" class="acc-card-cta"'
        f'{disabled_attr} '
        f'data-provider="{escape(card.provider)}" '
        f'data-action="{escape(card.primary_action_kind)}">'
        f'{escape(card.primary_action)}</button>'
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
        f'<div class="acc-card-timestamps">'
        f'<span class="acc-card-session">{escape(card.session_verified_label)}</span>'
        f'<span class="acc-card-refreshed">{escape(card.data_refreshed_label)}</span>'
        f"</div>"
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
.acc-card-timestamps{display:flex;flex-direction:column;gap:2px;min-width:0}
.acc-card-session,.acc-card-refreshed{font-size:11px;color:#a8a29e;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
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
