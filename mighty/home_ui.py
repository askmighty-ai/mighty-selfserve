"""
mighty.home_ui
──────────────
Render Mighty Home — attention inbox layout (Daily Brief, health strip, footer).
"""

from __future__ import annotations

from typing import Any, Callable

from mighty.action import Action
from mighty.customer_account_access import CustomerAccountAccessView
from mighty.home_state import HomeState, HomeStateResult
from mighty import user_copy
from mighty.accounts_ui import filter_chip_url


def _secondary_link(label: str, url: str, escape: Callable[[Any], str]) -> str:
    return (
        f'<a href="{escape(url)}" class="dash-brief-onboard-secondary">{escape(label)}</a>'
    )


def _featured_block(result: HomeStateResult, escape: Callable[[Any], str]) -> str:
    featured = result.featured
    body_html = (
        f'<p class="dash-brief-featured-body">{escape(featured.body)}</p>'
        if featured.body
        else ""
    )

    if featured.disabled_cta_label:
        cta_html = (
            f'<span class="dash-brief-featured-cta dash-brief-featured-cta--disabled" '
            f'aria-disabled="true">{escape(featured.disabled_cta_label)}</span>'
        )
    elif featured.cta_label and featured.cta_url:
        external = featured.cta_url.startswith("http")
        target = ' target="_blank" rel="noopener noreferrer"' if external else ""
        cta_html = (
            f'<a href="{escape(featured.cta_url)}" class="dash-brief-featured-cta"{target}>'
            f'{escape(featured.cta_label)}</a>'
        )
    else:
        cta_html = ""

    secondary_html = ""
    if featured.secondary_label and featured.secondary_url:
        secondary_html = (
            f'<div class="dash-brief-onboard-cta">'
            f'{_secondary_link(featured.secondary_label, featured.secondary_url, escape)}'
            f"</div>"
        )

    return (
        f'<article class="dash-brief-featured">'
        f'<h2 class="dash-brief-featured-headline">{escape(featured.headline)}</h2>'
        f"{body_html}"
        f"{cta_html}"
        f"{secondary_html}"
        f"</article>"
    )


def _health_chip(label: str, count: int, filter_key: str, escape: Callable[[Any], str]) -> str:
    return (
        f'<a href="{escape(filter_chip_url(filter_key))}" '
        f'class="dash-brief-else-chip dash-home-health-chip">'
        f"{escape(label)}</a>"
    )


def _fmt_confirmed(value: str | None) -> str:
    if not value:
        return "—"
    # Keep ISO readable without pulling timezone helpers into the renderer.
    text = value.replace("T", " ").replace("+00:00", " UTC")
    if len(text) > 19:
        text = text[:19]
    return text


def render_access_debug_details(
    view: CustomerAccountAccessView,
    escape: Callable[[Any], str],
) -> str:
    rows = "".join(
        f'<div class="dash-access-debug-row">'
        f'<span class="dash-access-debug-key">{escape(key)}</span>'
        f'<span class="dash-access-debug-val">{escape(val)}</span>'
        f"</div>"
        for key, val in view.debug_rows()
    )
    return (
        f'<details class="dash-access-why">'
        f'<summary>{escape(user_copy.ACCESS_WHY_SUMMARY)}</summary>'
        f'<div class="dash-access-debug">{rows}</div>'
        f"</details>"
    )


def render_account_access_row(
    view: CustomerAccountAccessView,
    *,
    escape: Callable[[Any], str],
    show_debug: bool = False,
) -> str:
    """Render one provider's canonical access-and-data state."""
    action_html = ""
    if view.user_action_required and view.user_action_text:
        if view.user_action_url:
            action_html = (
                f'<a class="dash-access-action" href="{escape(view.user_action_url)}">'
                f'{escape(view.user_action_text)}</a>'
            )
        else:
            action_html = (
                f'<span class="dash-access-action dash-access-action--static">'
                f'{escape(view.user_action_text)}</span>'
            )

    secondary = ""
    if view.cached_data_label and view.readiness != "ready":
        secondary = (
            f'<p class="dash-access-secondary">{escape(view.cached_data_label)}</p>'
        )
    elif view.secondary_label:
        secondary = (
            f'<p class="dash-access-secondary">{escape(view.secondary_label)}</p>'
        )

    debug_html = render_access_debug_details(view, escape) if show_debug else ""

    return (
        f'<article class="dash-access-row" data-provider="{escape(view.provider)}" '
        f'data-readiness="{escape(view.readiness)}" '
        f'data-live-access="{escape(view.live_access)}" '
        f'data-private-data="{escape(view.private_data_state)}" '
        f'data-background="{escape(view.background_work)}">'
        f'<div class="dash-access-row-main">'
        f'<div class="dash-access-identity">'
        f'<h3 class="dash-access-name">{escape(view.display_name)}</h3>'
        f'<p class="dash-access-status">{escape(view.status_label)}</p>'
        f'<p class="dash-access-discovered">'
        f'{escape(user_copy.access_discovered_from(view.discovered_from))}</p>'
        f"</div>"
        f'<dl class="dash-access-facts">'
        f'<div><dt>Live access</dt><dd>{escape(view.live_access)}</dd></div>'
        f'<div><dt>{escape(user_copy.ACCESS_PRIVATE_DATA_PREFIX)}</dt>'
        f'<dd>{escape(view.private_data_label)}</dd></div>'
        f'<div><dt>{escape(user_copy.ACCESS_LAST_CONFIRMED_PREFIX)}</dt>'
        f'<dd>{escape(_fmt_confirmed(view.last_confirmed_at))}</dd></div>'
        f'<div><dt>{escape(user_copy.ACCESS_BACKGROUND_PREFIX)}</dt>'
        f'<dd>{escape(view.background_work)}</dd></div>'
        f"</dl>"
        f'<p class="dash-access-meaning">{escape(view.meaning)}</p>'
        f"{secondary}"
        f"{debug_html}"
        f"</div>"
        f"{action_html}"
        f"</article>"
    )


def render_account_access_table(
    views: list[CustomerAccountAccessView],
    *,
    escape: Callable[[Any], str],
    show_debug: bool = False,
) -> str:
    if not views:
        return ""
    rows = "".join(
        render_account_access_row(view, escape=escape, show_debug=show_debug)
        for view in views
    )
    return (
        f'<section class="dash-access-table" aria-label="Account access">'
        f'<p class="dash-brief-else-label">Account access</p>'
        f'<div class="dash-access-rows">{rows}</div>'
        f"</section>"
    )


def _account_health_strip(result: HomeStateResult, escape: Callable[[Any], str]) -> str:
    if not result.show_health:
        return ""

    chips: list[str] = []
    health = result.health
    if health.up_to_date:
        label = health.connected_label or f"{health.up_to_date} connected"
        chips.append(_health_chip(label, health.up_to_date, "up_to_date", escape))
    if health.waiting:
        label = f"{health.waiting} {user_copy.HOME_HEALTH_STILL_SETTING_UP}"
        chips.append(_health_chip(label, health.waiting, "waiting", escape))
    attention = health.attention_required
    if attention:
        label = f"{attention} need{'s' if attention == 1 else ''} attention"
        chips.append(_health_chip(label, attention, "needs_attention", escape))

    freshness = escape(result.freshness_label or "")
    freshness_html = (
        f'<span class="dash-home-freshness">{freshness}</span>' if freshness else ""
    )
    if (
        result.updating_display_name
        and result.state not in (HomeState.UPDATE,)
    ):
        updating_note = escape(
            user_copy.home_update_headline(result.updating_display_name).rstrip("…")
        )
        freshness_html += (
            f'<span class="dash-home-freshness dash-home-freshness--updating">'
            f"{updating_note}…</span>"
        )

    rows_html = ""
    if result.waiting_rows and result.state == HomeState.WAITING:
        row_items = "".join(
            f'<div class="dash-home-waiting-row">'
            f'<span class="dash-home-waiting-name">{escape(row.display_name)}</span>'
            f'<span class="dash-home-waiting-status">{escape(row.status_label)}</span>'
            f"</div>"
            for row in result.waiting_rows
        )
        rows_html = f'<div class="dash-home-waiting-rows">{row_items}</div>'

    if not chips and not freshness_html and not rows_html:
        return ""

    chips_html = "".join(chips)
    return (
        f'<section class="dash-home-health" aria-label="Account health">'
        f'<p class="dash-brief-else-label">Account health</p>'
        f'<div class="dash-brief-else-chips">{chips_html}{freshness_html}</div>'
        f"{rows_html}"
        f"</section>"
    )


def _secondary_recommendations(
    actions: list[Action],
    escape: Callable[[Any], str],
) -> str:
    if not actions:
        return ""
    rows = ""
    for action in actions[:2]:
        url = (action.action_url or "/credentials").strip()
        rows += (
            f'<a href="{escape(url)}" class="dash-brief-row">'
            f'<span class="dash-brief-row-body">'
            f'<span class="dash-brief-row-headline">{escape(action.title)}</span>'
            f"</span>"
            f'<span class="dash-brief-row-arrow" aria-hidden="true">'
            f'<svg viewBox="0 0 16 16" fill="none"><path d="M6 3.5l4.5 4.5L6 12.5" '
            f'stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg></span>'
            f"</a>"
        )
    return (
        f'<section class="dash-brief-secondary" aria-label="More opportunities">'
        f"{rows}"
        f"</section>"
    )


def _metrics_section(result: HomeStateResult, escape: Callable[[Any], str]) -> str:
    if not result.show_metrics:
        return ""
    items: list[str] = []
    if result.metrics_accounts:
        n = result.metrics_accounts
        items.append(f"{n} account{'s' if n != 1 else ''}")
    if result.metrics_benefits:
        n = result.metrics_benefits
        items.append(f"{n} benefit{'s' if n != 1 else ''}")
    if result.metrics_value:
        items.append(f"{escape(result.metrics_value)} tracked")
    if not items:
        return ""
    chips = "".join(f'<span class="dash-brief-else-chip">{item}</span>' for item in items)
    return (
        f'<section class="dash-brief-else" aria-label="Overview">'
        f'<p class="dash-brief-else-label">Also</p>'
        f'<div class="dash-brief-else-chips">{chips}</div>'
        f"</section>"
    )


def _activity_link(result: HomeStateResult, escape: Callable[[Any], str]) -> str:
    if not result.activity_pending_count:
        return ""
    label = user_copy.HOME_ACTIVITY_LINK.format(count=result.activity_pending_count)
    return (
        f'<p class="dash-home-activity-link">'
        f'<a href="#pending-badge" class="dash-brief-onboard-secondary">{escape(label)}</a>'
        f"</p>"
    )


def _footer_strip(last_checked: str, escape: Callable[[Any], str]) -> str:
    checked = escape(last_checked) if last_checked else "—"
    return (
        f'<footer class="dash-home-footer">'
        f"{escape(user_copy.HOME_FOOTER_WORKER)} · "
        f"{escape(user_copy.HOME_FOOTER_LAST_CHECKED.format(time=checked))}"
        f"</footer>"
    )


def render_home_page(
    result: HomeStateResult,
    *,
    first_name: str,
    today_label: str,
    last_checked: str = "",
    escape: Callable[[Any], str],
) -> str:
    """Render the full Home attention-inbox layout."""
    safe_name = escape(first_name)
    summary_html = ""
    if result.priority_summary:
        summary_html = (
            f'<p class="dash-home-priority-summary">{escape(result.priority_summary)}</p>'
        )

    access_table = render_account_access_table(
        result.access_views,
        escape=escape,
        show_debug=result.show_access_debug,
    )

    return (
        f'<div class="dash-hero">'
        f'<div class="dash-brief-card dash-brief-card--exec">'
        f'<div class="dash-brief-exec">'
        f'<header class="dash-brief-header">'
        f'<h1 class="dash-brief-greeting" id="hero-greeting">Hello, {safe_name}</h1>'
        f'<div class="dash-brief-meta">'
        f'<time class="dash-brief-today-date">{escape(today_label)}</time>'
        f"</div>"
        f"{summary_html}"
        f"</header>"
        f'<section class="dash-brief-primary" aria-label="Featured">'
        f"{_featured_block(result, escape)}"
        f"</section>"
        f"{_secondary_recommendations(result.secondary_recommendations, escape)}"
        f"{_account_health_strip(result, escape)}"
        f"{access_table}"
        f"{_metrics_section(result, escape)}"
        f"{_activity_link(result, escape)}"
        f"{_footer_strip(last_checked, escape)}"
        f"</div>"
        f"</div>"
        f"<script>"
        f"(function(){{"
        f'  var h=new Date().getHours();'
        f'  var g=h<12?"Good morning":h<17?"Good afternoon":"Good evening";'
        f'  var el=document.getElementById("hero-greeting");'
        f'  if(el) el.textContent=g+", {safe_name}";'
        f"}})();"
        f"</script>"
        f"</div>"
    )
