"""
Internal Storybook-style showcase for the Mighty design system.

Served at /admin/design-system (admin-only). Demonstrates every component
and key states without altering customer-facing production pages.
"""

from __future__ import annotations

from mighty.design_system.components import (
    render_account_row,
    render_banner,
    render_brand,
    render_button,
    render_card,
    render_checkbox,
    render_empty_state,
    render_hero,
    render_modal,
    render_navigation,
    render_permission_card,
    render_progress_stepper,
    render_quiet_field,
    render_section,
    render_status_badge,
    render_switch,
    render_text_field,
    render_timeline,
    render_toast,
    render_trust_card,
)
from mighty.design_system.icons import ICONS, render_icon
from mighty.design_system.tokens import (
    COLORS,
    MOTION,
    RADII,
    SHADOWS,
    SPACING,
    TYPE_RAMP,
)


def _story(title: str, body: str, note: str = "") -> str:
    note_html = f'<p class="mds-meta" style="margin:0 0 1rem">{note}</p>' if note else ""
    return (
        f'<article class="mds-card mds-card--pad-lg" style="margin-bottom:1.5rem">'
        f'<h2 class="mds-heading" style="margin:0 0 0.35rem">{title}</h2>'
        f"{note_html}"
        f'<div class="mds-stack-md">{body}</div>'
        f"</article>"
    )


def _swatch(name: str, value: str) -> str:
    return (
        f'<div style="display:grid;gap:0.35rem">'
        f'<div style="height:48px;border-radius:10px;background:{value};'
        f'border:1px solid var(--mds-line)"></div>'
        f'<code class="mds-meta">{name}</code>'
        f'<span class="mds-meta">{value}</span>'
        f"</div>"
    )


def render_showcase_page() -> str:
    """Return a complete HTML document for the design-system showcase."""
    stories: list[str] = []

    # Tokens
    color_grid = "".join(_swatch(k, v) for k, v in list(COLORS.items())[:18])
    stories.append(
        _story(
            "Design tokens",
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:1rem">'
            f"{color_grid}</div>"
            f'<p class="mds-meta" style="margin-top:1.25rem">'
            f"Spacing: {', '.join(f'{k}={v}' for k, v in SPACING.items())}. "
            f"Radii: {', '.join(RADII.keys())}. "
            f"Shadows: {', '.join(SHADOWS.keys())}. "
            f"Motion ease: {MOTION['ease']}.</p>"
            f'<p class="mds-meta">Type ramp: {", ".join(TYPE_RAMP.keys())}.</p>',
            "Canonical values from MIGHTY_VISUAL_SYSTEM_V1.md",
        )
    )

    # Buttons
    stories.append(
        _story(
            "Button",
            '<div class="mds-cluster">'
            + render_button("Primary", variant="primary")
            + render_button("Secondary", variant="secondary")
            + render_button("Ghost", variant="ghost")
            + render_button("Destructive", variant="destructive")
            + render_button("Link action", variant="link")
            + "</div>"
            + '<div class="mds-cluster">'
            + render_button("Large", variant="primary", size="lg")
            + render_button("Small", variant="secondary", size="sm")
            + render_button("Loading", variant="primary", loading=True)
            + render_button("Disabled", variant="primary", disabled=True)
            + render_button("With icon", variant="primary", icon="mail")
            + "</div>"
            + render_button("Block primary", variant="primary", block=True),
            "States: default · hover · active · focus-visible · disabled · loading",
        )
    )

    # Card / Section / Hero
    stories.append(
        _story(
            "Card",
            render_card("<p class='mds-body' style='margin:0'>Surface card — earned grouping only.</p>")
            + render_card(
                "<p class='mds-body' style='margin:0'>Soft recessed grouping.</p>",
                variant="soft",
            )
            + render_card(
                "<p style='margin:0;color:var(--mds-on-field)'>Quiet Field surface.</p>",
                variant="field",
            ),
        )
    )

    stories.append(
        _story(
            "Section",
            render_section(
                title="What’s happening",
                body="One job per region — teach, then optional content.",
                eyebrow="Home",
                content="<p class='mds-meta'>Section content slot</p>",
            ),
        )
    )

    stories.append(
        _story(
            "Hero",
            render_hero(
                title="You’re good.",
                lede="Mighty is watching your accounts quietly. Nothing needs you right now.",
                variant="home",
                actions_html=render_button("View accounts", variant="secondary"),
            )
            + render_hero(
                title="One step left for your first update",
                lede="Set up Mighty in Chrome so verification can finish.",
                variant="home",
                state="attention",
                actions_html=render_button("Set up Mighty in Chrome", variant="primary", size="lg"),
            )
            + render_hero(
                title="Mighty",
                lede="Watches the accounts you already have and tells you when something is worth your time.",
                variant="marketing",
                actions_html=render_button("Get started", variant="primary", size="lg"),
                aside_html=render_quiet_field(ambient=True),
            ),
        )
    )

    # Badges
    stories.append(
        _story(
            "Status Badge",
            '<div class="mds-cluster">'
            + render_status_badge("Current", variant="quiet")
            + render_status_badge("Needs Chrome", variant="waiting")
            + render_status_badge("Sign in required", variant="attention")
            + render_status_badge("Review", variant="review")
            + render_status_badge("Working quietly", variant="neutral")
            + "</div>",
            "Text labels required — never color alone.",
        )
    )

    stories.append(
        _story(
            "Trust Card",
            render_trust_card(
                "Nothing is connected yet.",
                "Creating an account does not grant Gmail or Chrome access.",
                variant="reassure",
            )
            + render_trust_card(
                "What Mighty will not do",
                "Not send email as you. Not sign into providers as you.",
                variant="limit",
            )
            + render_trust_card(
                "If you continue",
                "Mighty will start watching the accounts you confirm.",
                variant="consequence",
            ),
        )
    )

    stories.append(
        _story(
            "Permission Card",
            render_permission_card(
                [
                    {
                        "title": "Why Gmail",
                        "body": "To find loyalty and card accounts from known program senders.",
                    },
                    {
                        "title": "What is accessed",
                        "body": "Mail metadata used for discovery — not inbox management.",
                    },
                    {
                        "title": "What is not accessed",
                        "body": "Mighty does not send email or manage your inbox.",
                        "limits": True,
                    },
                    {
                        "title": "What happens next",
                        "body": "After approval, Mighty scans and shows what it found for review.",
                    },
                ],
                eyebrow="Informed consent · Before Google",
                title="Connect Gmail",
                lede="Connect Gmail so Mighty can find loyalty and card accounts from your mail.",
                primary_action_html=render_button(
                    "Continue to Google", variant="primary", size="lg", block=True
                ),
                secondary_action_html=render_button(
                    "Not now", variant="ghost", block=True
                ),
            ),
        )
    )

    stories.append(
        _story(
            "Timeline",
            render_timeline(
                [
                    {
                        "kind": "authorized",
                        "title": "Gmail connected",
                        "body": "You authorized mail discovery.",
                        "time": "Today · 9:12 AM",
                        "datetime": "2026-07-25T09:12:00",
                    },
                    {
                        "kind": "lifecycle",
                        "title": "Found 4 account candidates",
                        "body": "Matched from known program senders.",
                        "time": "Today · 9:14 AM",
                        "datetime": "2026-07-25T09:14:00",
                    },
                    {
                        "kind": "completed",
                        "title": "American Express verified",
                        "body": "Access confirmed after your visit in Chrome.",
                        "time": "Today · 9:40 AM",
                        "datetime": "2026-07-25T09:40:00",
                    },
                ]
            ),
        )
    )

    stories.append(
        _story(
            "Account Row",
            render_account_row(
                name="American Express",
                monogram="AX",
                meta="Matched from mail · watching",
                balance="84,200 pts",
                status_html=render_status_badge("Current", variant="quiet"),
                action_html=render_button("Details", variant="ghost", size="sm"),
            )
            + render_account_row(
                name="United MileagePlus",
                monogram="UA",
                meta="Needs a decision",
                status_html=render_status_badge("Review", variant="review"),
                variant="suggestion",
                selectable=True,
                checkbox_name="watch",
                selected=False,
            )
            + render_account_row(
                name="Chase Ultimate Rewards",
                monogram="CH",
                meta="Session missing",
                status_html=render_status_badge("Sign in required", variant="attention"),
                action_html=render_button("Sign in", variant="secondary", size="sm"),
                selected=True,
                variant="selectable",
                selectable=True,
                checkbox_name="watch",
                checkbox_value="chase",
            ),
        )
    )

    stories.append(
        _story(
            "Empty State",
            render_empty_state(
                title="Home stays quiet on purpose",
                body="When nothing needs you, this page stays calm. That means Mighty is working.",
                future="Connect Gmail to find the accounts you already have.",
                action_html=render_button("Connect Gmail", variant="primary"),
                icon="mail",
                variant="first-use",
            )
            + render_empty_state(
                title="No accounts found yet",
                body="Mighty scanned known program senders and didn’t find a confident match.",
                future="You can add an account manually, or try again later.",
                action_html=render_button("Add an account manually", variant="secondary"),
                variant="no-results",
                icon="accounts",
            ),
        )
    )

    stories.append(
        _story(
            "Modal",
            render_button("Open confirm modal", variant="secondary", extra_attrs={"data-mds-open-modal": "demo-modal"})
            + render_modal(
                title="Disconnect Gmail?",
                body="Mighty will stop discovering accounts from this mailbox. You can reconnect later.",
                actions_html=(
                    render_button("Cancel", variant="ghost", extra_attrs={"data-mds-modal-dismiss": True})
                    + render_button("Disconnect Gmail", variant="destructive")
                ),
                open=False,
                modal_id="demo-modal",
            ),
            "Focus trap + Esc dismiss wired in showcase.js",
        )
    )

    stories.append(
        _story(
            "Progress Stepper",
            render_progress_stepper(
                [
                    {"label": "Create account", "state": "done"},
                    {"label": "Welcome", "state": "done"},
                    {"label": "How it works", "state": "live"},
                    {"label": "Connect Gmail", "state": "upcoming"},
                ],
                variant="horizontal",
            )
            + render_progress_stepper(
                [
                    {"label": "Gmail connected", "state": "done", "meta": "Authorized"},
                    {"label": "Checking senders", "state": "live", "meta": "Known program domains"},
                    {"label": "Matching accounts", "state": "upcoming"},
                    {"label": "Preparing results", "state": "upcoming"},
                ],
                variant="discovery",
                live=True,
            ),
        )
    )

    stories.append(
        _story(
            "Navigation",
            render_navigation(
                [
                    {"label": "Home", "href": "#", "current": True},
                    {"label": "Accounts", "href": "#"},
                    {"label": "Activity", "href": "#"},
                ],
                variant="app",
                brand_html=render_brand(href="#"),
                status_html=render_status_badge("Working quietly", variant="neutral"),
                sticky=False,
            )
            + '<div style="margin-top:1rem">'
            + render_navigation(
                [
                    {"label": "How it works", "href": "#"},
                    {"label": "Sign in", "href": "#"},
                ],
                variant="marketing",
                brand_html=render_brand(href="#"),
                status_html=render_button("Get started", variant="primary", size="sm"),
            )
            + "</div>",
        )
    )

    stories.append(
        _story(
            "Form Controls",
            '<form class="mds-form-stack" onsubmit="return false">'
            + render_text_field(
                label="Email",
                name="email",
                input_type="email",
                helper="Used only to sign into Mighty.",
                autocomplete="email",
                required=True,
            )
            + render_text_field(
                label="Password",
                name="password",
                input_type="password",
                error="Use at least 8 characters.",
                autocomplete="new-password",
            )
            + render_checkbox(label="I agree to the Terms", name="tos")
            + render_switch(label="Watch American Express", name="watch_amex", checked=True)
            + "</form>",
        )
    )

    stories.append(
        _story(
            "Toast",
            '<div class="mds-cluster" style="position:relative">'
            + render_toast("Gmail disconnected", variant="success")
            + render_toast("Stopped watching United MileagePlus", variant="info")
            + render_toast("Chrome setup still needed", variant="attention")
            + render_toast("Couldn’t save changes", variant="error", action_label="Retry")
            + "</div>",
        )
    )

    stories.append(
        _story(
            "Banner",
            render_banner(
                "Mighty in Chrome isn’t set up yet.",
                variant="waiting",
                action_html=render_button("Set up", variant="ghost", size="sm"),
                dismissible=True,
            )
            + render_banner(
                "Chase needs your sign-in to stay current.",
                variant="attention",
                action_html=render_button("Sign in", variant="secondary", size="sm"),
            )
            + render_banner("Accounts are up to date.", variant="success"),
        )
    )

    icon_grid = "".join(
        f'<div style="display:grid;justify-items:center;gap:0.35rem;padding:0.75rem;'
        f'border:1px solid var(--mds-line);border-radius:12px;background:var(--mds-surface)">'
        f'{render_icon(name)}<code class="mds-meta">{name}</code></div>'
        for name in sorted(ICONS)
    )
    stories.append(
        _story(
            "Icons",
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:0.75rem">'
            f"{icon_grid}</div>",
            "Stroke icons · 24px grid · monochrome · decorative by default",
        )
    )

    body = "\n".join(stories)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Mighty Design System — Showcase</title>
  <link rel="stylesheet" href="/static/design-system/mighty-ds.css"/>
  <style>
    body {{ margin: 0; }}
    .mds-showcase-top {{
      padding: 1.25rem 0;
      border-bottom: 1px solid var(--mds-line);
      margin-bottom: 1.75rem;
    }}
    .mds-showcase-top p {{ margin: 0.35rem 0 0; max-width: 40rem; }}
  </style>
</head>
<body class="mds mds-atmosphere">
  <a class="mds-skip-link" href="#main">Skip to content</a>
  <div class="mds-container-app" style="padding-bottom:4rem">
    <header class="mds-showcase-top">
      {render_brand(href="#")}
      <h1 class="mds-display mds-display-md" style="margin-top:1rem">Design system showcase</h1>
      <p class="mds-body">
        Internal preview of production tokens and components.
        Opt-in foundation only — existing customer pages are unchanged.
      </p>
    </header>
    <main id="main">
      {body}
    </main>
  </div>
  <script src="/static/design-system/showcase.js" defer></script>
</body>
</html>
"""
