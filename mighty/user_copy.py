"""
mighty.user_copy
────────────────
Canonical user-facing copy for Mighty's dashboard ↔ Chrome worker interaction.

Interaction model:
  • Mighty runs continuously in Chrome (the worker).
  • The dashboard is the control center — view, add accounts, see changes.
  • Login is the only manual step. Everything else is automatic.
"""

from __future__ import annotations

# ── Roles ─────────────────────────────────────────────────────────────────────
ROLE_DASHBOARD = "Control center"
ROLE_EXTENSION = "Worker"
ROLE_DASHBOARD_DESC = "View accounts, add providers, and see what's changed."
ROLE_EXTENSION_DESC = "Runs continuously in Chrome and updates accounts automatically."

# ── Taglines ──────────────────────────────────────────────────────────────────
INTERACTION_TAGLINE = (
    "Mighty runs in Chrome. The dashboard is your control center. "
    "Login is the only manual step."
)
AUTO_UPDATE_TAGLINE = "Mighty runs continuously in Chrome."
EXTENSION_UPDATE_LINE = "The worker updates accounts when you visit provider sites."
DASHBOARD_ROLE_LINE = "The dashboard is your control center — it shows results, never logs in."
MANUAL_STEP_LINE = "Login is the only thing you do manually. Everything else is automatic."
NEEDS_LOGIN_EXPLAINER = "Needs login means: open the provider in Chrome and sign in."

# ── Activity model (shared stages) ────────────────────────────────────────────
ACTIVITY_WATCHING = "watching"
ACTIVITY_UPDATING = "updating"
ACTIVITY_UPDATED = "updated"
ACTIVITY_NEEDS_LOGIN = "needs_login"
ACTIVITY_WAITING = "waiting"
ACTIVITY_ERROR = "error"

ACTIVITY_LABELS: dict[str, str] = {
    ACTIVITY_WATCHING: "Running in Chrome",
    ACTIVITY_UPDATING: "Updating…",
    ACTIVITY_UPDATED: "Updated",
    ACTIVITY_NEEDS_LOGIN: "Needs login",
    ACTIVITY_WAITING: "Waiting for worker",
    ACTIVITY_ERROR: "Update error",
}

# ── Canonical status labels (dashboard cards + worker popup) ─────────────────
STATUS_LABEL_UPDATED = ACTIVITY_LABELS[ACTIVITY_UPDATED]
STATUS_LABEL_UPDATING = ACTIVITY_LABELS[ACTIVITY_UPDATING]
STATUS_LABEL_NEEDS_LOGIN = ACTIVITY_LABELS[ACTIVITY_NEEDS_LOGIN]
STATUS_LABEL_WAITING = ACTIVITY_LABELS[ACTIVITY_WAITING]
STATUS_LABEL_ERROR = ACTIVITY_LABELS[ACTIVITY_ERROR]

STATUS_LABELS: dict[str, str] = {
    "up_to_date": STATUS_LABEL_UPDATED,
    "updating": STATUS_LABEL_UPDATING,
    "needs_login": STATUS_LABEL_NEEDS_LOGIN,
    "waiting_for_extension": STATUS_LABEL_WAITING,
    "error": STATUS_LABEL_ERROR,
}

# ── Worker popup copy ─────────────────────────────────────────────────────────
WORKER_NAME = "Mighty"
WORKER_SUBTITLE_RUNNING = ACTIVITY_LABELS[ACTIVITY_WATCHING]
WORKER_SUBTITLE_UPDATING = "Updating accounts"
WORKER_SUBTITLE_NOT_CONFIGURED = "Not configured"
WORKER_SETUP_NEEDED = "Setup needed"
WORKER_SETUP_DETAIL = "Open your control center to connect the worker."
WORKER_SETUP_BOX = (
    "Visit your Mighty dashboard in Chrome — the worker configures itself automatically."
)
WORKER_OPEN_DASHBOARD = "Open control center →"
WORKER_NOT_UPDATED_YET = "Not updated yet"
WORKER_FIRST_UPDATE_SOON = "First update soon"
WORKER_WAITING_FIRST_UPDATE = "Waiting for first update…"
WORKER_LAST_COMPLETED_PREFIX = "Last completed"
WORKER_NEXT_UPDATE_PREFIX = "Next update in"
WORKER_ACCOUNTS_UPDATED = "{n} account{s} updated"
WORKER_ACCOUNTS_UPDATED_PARTIAL = "{ok} of {total} accounts updated"
WORKER_NEEDS_LOGIN_SUBLINE = NEEDS_LOGIN_EXPLAINER

# ── Extension / worker setup ────────────────────────────────────────────────────
EXT_PRODUCT_NAME = "Mighty Worker"
EXT_SETUP_LINK = "Set up worker"
EXT_SETUP_TITLE = "Connect Mighty Worker"
EXT_SETUP_BODY = (
    "The worker reads this page to configure itself automatically. "
    "You can close this tab once it confirms."
)
EXT_SETUP_WAITING = "Waiting for worker…"
EXT_SETUP_SUCCESS = "Worker connected — you can close this tab"
EXT_SETUP_NOT_DETECTED = (
    "Worker not detected — install Mighty in Chrome, enable it, then reload this page."
)
EXT_NOT_DETECTED_SHORT = "Worker not detected."
EXT_INSTALL_TOAST = "Install the Mighty worker in Chrome to update your accounts."
MOBILE_WORKER_TOAST = "Use desktop Chrome with the Mighty worker to update your accounts."

# ── How updates work (Accounts page) ──────────────────────────────────────────
HOW_UPDATES_TITLE = "How Mighty works"
HOW_UPDATES_ITEMS: tuple[tuple[str, str], ...] = (
    (
        "Control center",
        "Your dashboard shows balances, perks, and expiry dates. "
        "Add accounts and see what's changed — it never logs into provider sites.",
    ),
    (
        "Worker in Chrome",
        "Mighty runs continuously in Chrome. When you visit a provider site while logged in, "
        "the worker captures account data and sends it to your control center.",
    ),
    (
        "Automatic updates",
        "The worker checks your accounts on a schedule and whenever you visit provider sites. "
        "No manual sync needed.",
    ),
    (
        "Login only when needed",
        "If a provider session expires, open the provider in Chrome and sign in. "
        "The worker picks up your session on its next visit.",
    ),
)

# ── Connect flow (3 steps — login is the only manual step) ────────────────────
CONNECT_STEPS_TITLE = "How this works"
CONNECT_STEP_1 = (
    "Click <strong>Open in Chrome</strong> — we'll take you to the "
    "<span class=\"dash-site-name-ref\" style=\"font-weight:600\"></span> login page"
)
CONNECT_STEP_2 = (
    "<strong>Log in yourself</strong> <span id=\"dash-cred-type-note\" style=\"color:#6b7280;font-size:12px\"></span> "
    "— this is the only manual step"
)
CONNECT_STEP_3 = (
    "The worker <strong>automatically captures your data</strong> — nothing else for you to do"
)
CONNECT_WAITING = "Waiting for the worker to verify your session…"
CONNECT_WAITING_SUB = (
    "Stay on your account page after logging in — usually takes 5–15 seconds"
)
CONNECT_TROUBLE_TITLE = "The worker didn't detect your login. Try these:"
CONNECT_TROUBLE_EXT = "Make sure the <a href=\"/extension-setup\" target=\"_blank\" style=\"color:#b91c1c;font-weight:500\">Mighty worker is installed</a>"
CONNECT_MODAL_INTRO = (
    "Make sure you're <strong>logged into <span id=\"modal-ext-site-name\"></span></strong> in Chrome, "
    "then click the button below. The worker captures your account data automatically."
)
CONNECT_MODAL_WAITING = "Waiting for worker…"
CONNECT_MODAL_WAITING_SUB = (
    "Open your provider site in Chrome while logged in. "
    "The worker verifies your session — visiting the domain alone is not enough."
)
CONNECT_MODAL_NEEDS_LOGIN = (
    "Sign in required. Log in to your provider in Chrome, "
    "then keep this tab open while the worker verifies your session."
)

# ── Lifecycle state labels ────────────────────────────────────────────────────
LIFECYCLE_LABELS: dict[str, str] = {
    "discovered": "Discovered",
    "added": "Added",
    "waiting_for_extension": STATUS_LABEL_WAITING,
    "needs_login": STATUS_LABEL_NEEDS_LOGIN,
    "connected": "Connected",
    "synced": STATUS_LABEL_UPDATED,
}

LIFECYCLE_DESCRIPTIONS: dict[str, str] = {
    "discovered": "Found from your email — not yet added to Mighty.",
    "added": "Added to Mighty — open the provider in Chrome to get started.",
    "waiting_for_extension": (
        "Install the Mighty worker in Chrome and open your provider site."
    ),
    "needs_login": "Sign in to your provider in Chrome — the only manual step.",
    "connected": (
        "Session verified — visit your account page so the worker can capture data."
    ),
    "synced": "Account data captured and stored in your control center.",
}

# ── Connection state labels (Amex + generic) ──────────────────────────────────
CONNECTION_LABELS: dict[str, str] = {
    "connecting": "Connecting",
    "waiting_for_extension": STATUS_LABEL_WAITING,
    "needs_login": STATUS_LABEL_NEEDS_LOGIN,
    "connected": "Connected",
}

CONNECTION_STATUS_LINES: dict[str, str] = {
    "connecting": "Setting up…",
    "waiting_for_extension": "Worker verifying session…",
    "needs_login": STATUS_LABEL_NEEDS_LOGIN,
    "connected": "Connected — awaiting data",
}

CONNECTION_SUBCOPY: dict[str, str] = {
    "connecting": "Setting up your account…",
    "waiting_for_extension": (
        "Waiting for worker — install Mighty in Chrome and open the provider while logged in."
    ),
    "needs_login": "Needs login — sign in in Chrome so the worker can verify your session.",
    "connected": "Connected — session verified. The worker will capture data on your next visit.",
}

# ── Primary CTAs ──────────────────────────────────────────────────────────────
CTA_ADD_TO_MIGHTY = "Add to Mighty"
CTA_OPEN_PROVIDER = "Open provider"
CTA_LOG_IN = "Log in"
CTA_LOG_IN_TO_PROVIDER = "Log in to provider"
CTA_VIEW_ACCOUNT = "View account"
CTA_RETRY_UPDATE = "Retry update"
CTA_SET_UP_WORKER = "Set up worker →"

LIFECYCLE_CTAS: dict[str, str] = {
    "discovered": CTA_ADD_TO_MIGHTY,
    "added": CTA_OPEN_PROVIDER,
    "waiting_for_extension": CTA_OPEN_PROVIDER,
    "needs_login": CTA_LOG_IN,
    "connected": CTA_OPEN_PROVIDER,
    "synced": CTA_VIEW_ACCOUNT,
}

SECONDARY_CTA_EXTENSION_RETRY = "Worker installed / Retry"

# ── Source labels ─────────────────────────────────────────────────────────────
SOURCE_FOUND_FROM_GMAIL = "Found from Gmail"
SOURCE_EXTENSION = "Chrome worker"
SOURCE_MANUALLY_ADDED = "Manually added"

# ── Login-required display ────────────────────────────────────────────────────
NEEDS_LOGIN_BADGE = "🔐 Needs login"
NEEDS_LOGIN_ACTION_LABEL = STATUS_LABEL_NEEDS_LOGIN
NEEDS_LOGIN_ACTION_VALUE = "{name} — open the provider in Chrome and sign in"
NEEDS_LOGIN_ACTION_CTA = CTA_LOG_IN_TO_PROVIDER
NEEDS_LOGIN_ACTION_WHY = "Sign in to the provider in Chrome — the only manual step."
NEEDS_LOGIN_BANNER_SUFFIX = (
    "open each site in Chrome and sign in. The worker updates automatically."
)
NEEDS_LOGIN_ACCOUNT_HINT = "{name} — sign in in Chrome"

# ── Summary headlines (dashboard header + worker popup) ───────────────────────
def summary_updating(name: str) -> str:
    return f"Updating {name}"


def summary_updating_plural(count: int) -> str:
    return f"{count} accounts updating"


def summary_needs_login(name: str) -> str:
    return f"{name} needs login"


def summary_needs_login_plural(count: int) -> str:
    return f"{count} accounts need login"


# ── Timestamps & freshness ────────────────────────────────────────────────────
LAST_UPDATED_PREFIX = STATUS_LABEL_UPDATED
NOT_YET_UPDATED = "Not yet updated"
GLOBAL_LAST_UPDATED_TITLE = "Last updated"

# ── Failure recovery (card hero hints + worker popup) ─────────────────────────
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
        "Will retry on the next automatic update.",
    ),
    "extension_missing": (
        "Worker needed",
        "Install the Mighty worker in Chrome, then visit your provider site.",
    ),
    "domain_unreachable": (
        "Site may have moved",
        "This account's website couldn't be reached — it may have changed its address.",
    ),
}

FAILURE_HINTS: dict[str, str] = {
    "login_required": NEEDS_LOGIN_EXPLAINER,
    "login_wall": NEEDS_LOGIN_EXPLAINER,
    "timeout": "Site took too long — will retry on the next automatic update",
    "no_data": "Could not read account data",
    "domain_unreachable": "Site unreachable",
}

FAILURE_ICONS: dict[str, str] = {
    "login_required": "🔐",
    "login_wall": "🔐",
    "timeout": "⏱",
    "no_data": "⚠️",
    "domain_unreachable": "🌐",
}

# ── Settings (advanced) ───────────────────────────────────────────────────────
SETTINGS_UPDATES_ADVANCED_TITLE = "Updates (advanced)"
SETTINGS_UPDATES_PRIMARY = (
    "The Mighty worker runs in Chrome and updates accounts automatically "
    "when you visit provider sites while logged in."
)
SETTINGS_UPDATES_FALLBACK = (
    "Server-side retry re-runs Mighty's scraper for all connected accounts. "
    "Use only if worker data looks stale — it may not work for sites that "
    "require an active browser session."
)
SETTINGS_RETRY_UPDATE_BTN = CTA_RETRY_UPDATE
SETTINGS_RETRY_RUNNING = "Running…"
SETTINGS_RETRY_STARTED = "Update started — check your control center in a few minutes."
SETTINGS_RETRY_FAILED = "Update failed or already running."

# ── Connect modal ─────────────────────────────────────────────────────────────
MODAL_ADD_ACCOUNT = "Add an account"
MODAL_OPEN_IN_CHROME = "Open in Chrome →"
MODAL_CONNECTED_NO_FIELDS = (
    "Session verified. Visit your account page — the worker will capture your data."
)

# ── Onboarding ────────────────────────────────────────────────────────────────
ONBOARDING_TITLE = "How Mighty works"
ONBOARDING_BODY = (
    "Mighty reads your connected account pages and extracts only the facts you care about "
    "— balances, expiry dates, due dates, and credits. "
    "<strong>Raw page text is never shared</strong> and is discarded after extraction."
)
ONBOARDING_CHROME_LINE = (
    "Use <strong>desktop Chrome</strong> — the worker runs continuously and updates accounts automatically. "
    "The dashboard is your control center."
)
SIGNUP_SUB = (
    "You'll be connected in about 5 minutes. "
    "Account syncing requires desktop Chrome with the Mighty worker."
)

# ── Daily brief / actions ─────────────────────────────────────────────────────
ACTION_SURFACED_FROM = "Surfaced from {source} during your latest update."


def how_updates_html() -> str:
    """Render the Accounts page 'How Mighty works' section."""
    items = "".join(
        f"<li><strong>{title}</strong> — {body}</li>"
        for title, body in HOW_UPDATES_ITEMS
    )
    return (
        f'<div class="sync-howto">'
        f'<div class="sync-howto-title">{HOW_UPDATES_TITLE}</div>'
        f'<ol class="sync-howto-list">{items}</ol>'
        f'<p class="sync-howto-tagline">{MANUAL_STEP_LINE}</p>'
        f"</div>"
    )


def connect_steps_html(*, step2_extra: str = "") -> str:
    """Three-step connect flow for dashboard modals."""
    step2 = CONNECT_STEP_2
    if step2_extra:
        step2 = step2.replace(
            '<span id="dash-cred-type-note" style="color:#6b7280;font-size:12px"></span>',
            step2_extra,
        )
    steps = (CONNECT_STEP_1, step2, CONNECT_STEP_3)
    rows = ""
    for i, text in enumerate(steps, 1):
        rows += (
            f'<div style="display:flex;align-items:flex-start;gap:10px">'
            f'<div style="width:22px;height:22px;border-radius:50%;background:#059669;color:#fff;'
            f'font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;'
            f'flex-shrink:0;margin-top:1px">{i}</div>'
            f'<div style="font-size:13px;color:#374151;line-height:1.5">{text}</div>'
            f"</div>"
        )
    return (
        f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;'
        f'letter-spacing:.05em;color:#9ca3af;margin-bottom:10px">{CONNECT_STEPS_TITLE}</div>'
        f'<div style="display:flex;flex-direction:column;gap:10px">{rows}</div>'
    )


def login_required_action_value(display_name: str) -> str:
    return NEEDS_LOGIN_ACTION_VALUE.format(name=display_name)


def failure_hint(reason: str, *, fallback: str = "Update failed") -> str:
    return FAILURE_HINTS.get(reason, fallback)


def failure_icon(reason: str) -> str:
    return FAILURE_ICONS.get(reason, "⚠️")


def api_copy_bundle() -> dict:
    """Shared vocabulary for dashboard JS and Chrome worker popup."""
    return {
        "roles": {
            "dashboard": ROLE_DASHBOARD,
            "extension": ROLE_EXTENSION,
            "dashboard_desc": ROLE_DASHBOARD_DESC,
            "extension_desc": ROLE_EXTENSION_DESC,
        },
        "taglines": {
            "interaction": INTERACTION_TAGLINE,
            "auto_update": AUTO_UPDATE_TAGLINE,
            "manual_step": MANUAL_STEP_LINE,
        },
        "activity_labels": ACTIVITY_LABELS,
        "status_labels": STATUS_LABELS,
        "worker": {
            "name": WORKER_NAME,
            "subtitle_running": WORKER_SUBTITLE_RUNNING,
            "subtitle_updating": WORKER_SUBTITLE_UPDATING,
            "subtitle_not_configured": WORKER_SUBTITLE_NOT_CONFIGURED,
            "setup_needed": WORKER_SETUP_NEEDED,
            "setup_detail": WORKER_SETUP_DETAIL,
            "open_dashboard": WORKER_OPEN_DASHBOARD,
            "not_updated_yet": WORKER_NOT_UPDATED_YET,
            "needs_login_subline": WORKER_NEEDS_LOGIN_SUBLINE,
        },
        "failure_hints": FAILURE_HINTS,
        "failure_icons": FAILURE_ICONS,
    }
