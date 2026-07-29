"""Shared MDS authenticated application shell (P0 visual surface migration).

One continuous Mighty chrome for Home, Accounts, Activity, Settings, and related
authenticated routes. Component family: brand, navigation, page frame, account
menu — not per-page Inter sidebars.
"""

from __future__ import annotations

from html import escape as html_escape
from typing import Any, Callable, Sequence

from mighty.design_system.components import render_brand, render_navigation

Escape = Callable[[Any], str]

NAV_HOME = "home"
NAV_ACCOUNTS = "accounts"
NAV_ACTIVITY = "activity"
NAV_SETTINGS = "settings"
NAV_FIND = "email-scan"

_FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com"/>\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&'
    'family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet"/>\n'
    '<link rel="stylesheet" href="/static/design-system/mighty-ds.css"/>\n'
)

# Shell-level continuity CSS (tokens already under .mds).
_SHELL_CSS = """
.mds.app-auth{min-height:100vh;background:var(--mds-bg,#f7f4ee);color:var(--mds-ink,#1a1f1c)}
.mds.app-auth .mds-nav-header{border-bottom:1px solid var(--mds-line,rgba(26,31,28,.08));background:var(--mds-surface,#fffefb)}
.mds.app-auth .app-auth__main{max-width:var(--mds-measure-wide,52rem);margin:0 auto;padding:var(--mds-space-6,1.5rem) var(--mds-space-5,1.25rem) var(--mds-space-10,3rem)}
.mds.app-auth .app-auth__main--flush{max-width:none;padding:0}
.mds.app-auth .app-auth__menu{position:relative}
.mds.app-auth .app-auth__avatar{list-style:none;cursor:pointer;width:2.25rem;height:2.25rem;border-radius:999px;border:1px solid var(--mds-line,rgba(26,31,28,.12));background:var(--mds-pine-soft,#e8f2ef);color:var(--mds-pine,#1f4b3a);display:inline-flex;align-items:center;justify-content:center;font:600 0.85rem/1 "Plus Jakarta Sans",system-ui,sans-serif}
.mds.app-auth .app-auth__avatar::-webkit-details-marker{display:none}
.mds.app-auth .app-auth__menu-panel{position:absolute;right:0;top:calc(100% + 0.4rem);min-width:11rem;padding:0.35rem;border-radius:var(--mds-radius,14px);border:1px solid var(--mds-line,rgba(26,31,28,.1));background:var(--mds-surface,#fffefb);box-shadow:0 8px 28px rgba(26,31,28,.08);z-index:40}
.mds.app-auth .app-auth__menu-item{display:block;width:100%;text-align:left;padding:0.55rem 0.75rem;border:0;border-radius:10px;background:transparent;color:var(--mds-ink,#1a1f1c);font:500 0.875rem/1.3 "Plus Jakarta Sans",system-ui,sans-serif;text-decoration:none;cursor:pointer}
.mds.app-auth .app-auth__menu-item:hover{background:var(--mds-pine-soft,#e8f2ef)}
.mds.app-auth .app-auth__sign-out{margin:0}
@media (max-width:720px){
  .mds.app-auth .mds-nav--app{flex-wrap:wrap;gap:0.25rem}
  .mds.app-auth .app-auth__main{padding:1rem 1rem 2.5rem}
}
"""


def _esc(value: Any, escape: Escape | None = None) -> str:
    if escape is not None:
        return escape(value)
    return html_escape("" if value is None else str(value), quote=True)


def nav_items(
    active: str,
    *,
    show_activity: bool = False,
    home_href: str = "/home",
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {"label": "Home", "href": home_href, "current": active == NAV_HOME},
        {"label": "Accounts", "href": "/credentials", "current": active == NAV_ACCOUNTS},
    ]
    if show_activity:
        items.append(
            {
                "label": "Activity",
                "href": "/activity",
                "current": active == NAV_ACTIVITY,
            }
        )
    items.extend(
        [
            {
                "label": "Find accounts",
                "href": "/email-scan",
                "current": active == NAV_FIND,
            },
            {
                "label": "Settings",
                "href": "/settings",
                "current": active == NAV_SETTINGS,
            },
        ]
    )
    return items


def render_account_menu(
    *,
    display_name: str,
    csrf_token: str,
    escape: Escape | None = None,
) -> str:
    esc = lambda v: _esc(v, escape)  # noqa: E731
    initial = (display_name[:1] or "M").upper()
    return (
        f'<details class="app-auth__menu">'
        f'<summary class="app-auth__avatar" aria-label="Account menu for {esc(display_name)}">'
        f'<span aria-hidden="true">{esc(initial)}</span>'
        f"</summary>"
        f'<div class="app-auth__menu-panel" role="menu">'
        f'<a role="menuitem" class="app-auth__menu-item" href="/settings">Settings</a>'
        f'<form method="POST" action="/logout" class="app-auth__sign-out">'
        f'<input type="hidden" name="_csrf" value="{esc(csrf_token)}"/>'
        f'<button type="submit" role="menuitem" class="app-auth__menu-item">Sign out</button>'
        f"</form>"
        f"</div></details>"
    )


def render_authenticated_chrome(
    *,
    active: str,
    display_name: str,
    csrf_token: str,
    show_activity: bool = False,
    home_href: str = "/home",
    escape: Escape | None = None,
) -> str:
    """MDS nav header + brand + account menu (component family: navigation)."""
    brand = render_brand(href=home_href, wordmark="Mighty")
    status = render_account_menu(
        display_name=display_name,
        csrf_token=csrf_token,
        escape=escape,
    )
    return render_navigation(
        nav_items(active, show_activity=show_activity, home_href=home_href),
        variant="app",
        brand_html=brand,
        status_html=status,
        sticky=True,
        label="Application",
        class_name="app-auth__nav",
    )


def render_authenticated_document(
    *,
    title: str,
    active: str,
    display_name: str,
    csrf_token: str,
    main_html: str,
    show_activity: bool = False,
    home_href: str = "/home",
    body_class: str = "",
    body_attrs: str = "",
    extra_head: str = "",
    extra_css: str = "",
    flush_main: bool = False,
    escape: Escape | None = None,
) -> str:
    """Full HTML document — one MDS shell for authenticated application surfaces."""
    esc = lambda v: _esc(v, escape)  # noqa: E731
    chrome = render_authenticated_chrome(
        active=active,
        display_name=display_name,
        csrf_token=csrf_token,
        show_activity=show_activity,
        home_href=home_href,
        escape=escape,
    )
    main_cls = "app-auth__main app-auth__main--flush" if flush_main else "app-auth__main"
    body_cls = "mds app-auth" + (f" {body_class}" if body_class else "")
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8"/>\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1"/>\n'
        '<meta name="color-scheme" content="light"/>\n'
        f"<title>{esc(title)} — Mighty</title>\n"
        f"{_FONT_LINK}"
        f"<style>{_SHELL_CSS}\n{extra_css}</style>\n"
        f"{extra_head}"
        "</head>\n"
        f'<body class="{esc(body_cls)}" data-app-shell="mds"{body_attrs}>\n'
        f"{chrome}\n"
        f'<main class="{main_cls}" id="app-auth-main">\n'
        f"{main_html}\n"
        f"</main>\n"
        "</body>\n"
        "</html>\n"
    )


def bridge_page_css_for_mds() -> str:
    """Map legacy page-local classes onto MDS tokens when content is reused."""
    return """
.mds.app-auth .page,.mds.app-auth .activity-page,.mds.app-auth .page-wrap{
  background:transparent;color:var(--mds-ink,#1a1f1c);font-family:"Plus Jakarta Sans",system-ui,sans-serif}
.mds.app-auth .page-title,.mds.app-auth .activity-title,.mds.app-auth h1{
  font-family:Fraunces,Georgia,serif;font-weight:600;color:var(--mds-ink,#1a1f1c);letter-spacing:-0.03em}
.mds.app-auth .page-subtitle,.mds.app-auth .activity-subtitle,.mds.app-auth .page-subtitle{
  color:var(--mds-muted,#5c6b5a)}
.mds.app-auth .card,.mds.app-auth .activity-item,.mds.app-auth .acct-row{
  background:var(--mds-surface,#fffefb);border:1px solid var(--mds-line,rgba(26,31,28,.08));
  border-radius:var(--mds-radius,14px);box-shadow:none}
.mds.app-auth .btn-connect-new,.mds.app-auth .activity-btn-approve,.mds.app-auth .btn-settings-primary,
.mds.app-auth .acct-maint-cta--primary{
  background:var(--mds-pine,#1f4b3a)!important;color:#fff!important;border:none!important;
  border-radius:var(--mds-radius-sm,10px)!important;font-family:inherit}
.mds.app-auth .btn-sm,.mds.app-auth .activity-btn-deny,.mds.app-auth .acct-maint-cta--secondary{
  border-radius:var(--mds-radius-sm,10px)!important;border-color:var(--mds-line,rgba(26,31,28,.14))!important;
  color:var(--mds-pine,#1f4b3a)!important;background:var(--mds-surface,#fffefb)!important}
.mds.app-auth .activity-status,.mds.app-auth .acct-status-chip{
  border-radius:999px;font-weight:600}
.mds.app-auth .activity-page{padding:0;background:transparent}
.mds.app-auth .activity-shell{max-width:40rem;margin:0 auto}
.mds.app-auth .acct-empty,.mds.app-auth .activity-empty{
  padding:2.5rem 0.5rem;max-width:34ch}
.mds.app-auth .toggle-row input[type=checkbox]:checked{
  background:var(--mds-pine,#1f4b3a);border-color:var(--mds-pine,#1f4b3a)}
.mds.app-auth .btn-settings-primary{background:var(--mds-pine,#1f4b3a)!important}
.mds.app-auth .ntfy-link,.mds.app-auth a.btn-sm{color:var(--mds-pine,#1f4b3a)!important}
"""
