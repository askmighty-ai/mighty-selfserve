"""
mighty.user_copy
────────────────
Canonical user-facing copy for account updates, extension, and Dashboard.

Mental model:
  Mighty updates your accounts automatically. Sometimes we need you to log in
  to a provider site.
"""

from __future__ import annotations

# ── Taglines ──────────────────────────────────────────────────────────────────
AUTO_UPDATE_TAGLINE = "Mighty checks your accounts automatically."
EXTENSION_UPDATE_LINE = (
    "The Chrome extension updates accounts when you visit provider sites."
)
DASHBOARD_ROLE_LINE = (
    "Dashboard shows results; it does not log into provider sites."
)
NEEDS_LOGIN_EXPLAINER = "Needs login means: open the provider and sign in."

# ── How updates work (Accounts page) ──────────────────────────────────────────
HOW_UPDATES_TITLE = "How updates work"
HOW_UPDATES_ITEMS: tuple[tuple[str, str], ...] = (
    (
        "Found from Gmail",
        "Mighty scans Gmail sender addresses to suggest loyalty programs and subscriptions.",
    ),
    (
        "Updated by Chrome extension",
        "When you visit a provider site while logged in, the Mighty Chrome extension "
        "captures account data and sends it here.",
    ),
    (
        "Shown on Dashboard",
        "Balances, perks, and expiry dates appear on your Dashboard. "
        "The Dashboard does not log into provider sites for you.",
    ),
    (
        "Login only when needed",
        "If a provider session expires, open the provider and sign in. "
        "The extension picks up your session on its next visit.",
    ),
)

# ── Lifecycle state labels ────────────────────────────────────────────────────
LIFECYCLE_LABELS: dict[str, str] = {
    "discovered": "Discovered",
    "added": "Added",
    "waiting_for_extension": "Waiting for extension",
    "needs_login": "Needs login",
    "connected": "Connected",
    "synced": "Updated",
}

LIFECYCLE_DESCRIPTIONS: dict[str, str] = {
    "discovered": "Found from your email — not yet added to Mighty.",
    "added": "Added to Mighty — open the provider to get started.",
    "waiting_for_extension": (
        "Install the Mighty Chrome extension and open your provider site."
    ),
    "needs_login": "Sign in to your provider in Chrome so Mighty can verify your session.",
    "connected": (
        "Session verified — visit your account page so the extension can capture data."
    ),
    "synced": "Account data extracted and stored.",
}

# ── Primary CTAs ──────────────────────────────────────────────────────────────
CTA_ADD_TO_MIGHTY = "Add to Mighty"
CTA_OPEN_PROVIDER = "Open provider"
CTA_LOG_IN = "Log in"
CTA_LOG_IN_TO_PROVIDER = "Log in to provider"
CTA_VIEW_ACCOUNT = "View account"
CTA_RETRY_UPDATE = "Retry update"

LIFECYCLE_CTAS: dict[str, str] = {
    "discovered": CTA_ADD_TO_MIGHTY,
    "added": CTA_OPEN_PROVIDER,
    "waiting_for_extension": CTA_OPEN_PROVIDER,
    "needs_login": CTA_LOG_IN,
    "connected": CTA_OPEN_PROVIDER,
    "synced": CTA_VIEW_ACCOUNT,
}

SECONDARY_CTA_EXTENSION_RETRY = "I installed the extension / Retry"

# ── Source labels ─────────────────────────────────────────────────────────────
SOURCE_FOUND_FROM_GMAIL = "Found from Gmail"
SOURCE_EXTENSION = "Chrome extension"
SOURCE_MANUALLY_ADDED = "Manually added"

# ── Login-required display ────────────────────────────────────────────────────
NEEDS_LOGIN_BADGE = "🔐 Needs login"
NEEDS_LOGIN_ACTION_LABEL = "Needs login"
NEEDS_LOGIN_ACTION_VALUE = "{name} — open the provider and sign in"
NEEDS_LOGIN_ACTION_CTA = CTA_LOG_IN_TO_PROVIDER
NEEDS_LOGIN_ACTION_WHY = "Sign in to the provider so Mighty can update this account."
NEEDS_LOGIN_BANNER_SUFFIX = (
    "open each site in Chrome, sign in, and visit your account page. "
    "The extension updates automatically."
)

# ── Timestamps & freshness ────────────────────────────────────────────────────
LAST_UPDATED_PREFIX = "Updated"
NOT_YET_UPDATED = "Not yet updated"
GLOBAL_LAST_UPDATED_TITLE = "Last updated"

# ── Failure recovery (card hero hints) ────────────────────────────────────────
FAILURE_ACTIONS: dict[str, tuple[str, str]] = {
    "login_wall": (
        "Logged out",
        "Log in to this site in Chrome, then visit your account page.",
    ),
    "login_required": (
        "Logged out",
        "Log in to this site in Chrome, then visit your account page.",
    ),
    "no_data": (
        "No data found",
        "Visit your account overview page in Chrome while logged in.",
    ),
    "llm_empty": (
        "No data found",
        "Visit your account overview page in Chrome while logged in.",
    ),
    "low_confidence_only": (
        "Partial data",
        "Visit your full account page in Chrome while logged in.",
    ),
    "stale_date_only": (
        "Dates only",
        "Visit your account overview page — Mighty only found date fields.",
    ),
    "timeout": (
        "Timed out",
        "Visit your account page in Chrome and try again.",
    ),
    "extension_missing": (
        "Extension needed",
        "Install the Mighty Chrome extension, then visit your provider site.",
    ),
    "domain_unreachable": (
        "Site may have moved",
        "This account's website couldn't be reached — it may have changed its address.",
    ),
}

# ── Settings (advanced) ───────────────────────────────────────────────────────
SETTINGS_UPDATES_ADVANCED_TITLE = "Updates (advanced)"
SETTINGS_UPDATES_PRIMARY = (
    "The Mighty Chrome extension updates accounts when you visit provider sites "
    "while logged in."
)
SETTINGS_UPDATES_FALLBACK = (
    "Server-side retry re-runs Mighty's scraper for all connected accounts. "
    "Use only if extension data looks stale — it may not work for sites that "
    "require an active browser session."
)
SETTINGS_RETRY_UPDATE_BTN = CTA_RETRY_UPDATE
SETTINGS_RETRY_RUNNING = "Running…"
SETTINGS_RETRY_STARTED = "Update started — check Dashboard in a few minutes."
SETTINGS_RETRY_FAILED = "Update failed or already running."

# ── Connect modal ─────────────────────────────────────────────────────────────
MODAL_ADD_ACCOUNT = "Add an account"
MODAL_OPEN_IN_CHROME = "Open in Chrome →"
MODAL_CONNECTED_NO_FIELDS = (
    "Session verified. Visit your account page — the extension will capture your data."
)

# ── Daily brief / actions ─────────────────────────────────────────────────────
ACTION_SURFACED_FROM = "Surfaced from {source} during your latest update."


def how_updates_html() -> str:
    """Render the Accounts page 'How updates work' section."""
    items = "".join(
        f"<li><strong>{title}</strong> — {body}</li>"
        for title, body in HOW_UPDATES_ITEMS
    )
    return (
        f'<div class="sync-howto">'
        f'<div class="sync-howto-title">{HOW_UPDATES_TITLE}</div>'
        f'<ol class="sync-howto-list">{items}</ol>'
        f"</div>"
    )


def login_required_action_value(display_name: str) -> str:
    return NEEDS_LOGIN_ACTION_VALUE.format(name=display_name)
