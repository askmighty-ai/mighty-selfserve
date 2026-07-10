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

# ── Account Access Loop (Account Center + extension popup) ───────────────────
# Session/login presentation comes from provider_session_state (via session_access).
ACCOUNT_STATE_NEEDS_SIGN_IN = "needs_sign_in"
ACCOUNT_STATE_UPDATING = "updating"
ACCOUNT_STATE_CHECKING = "checking"
ACCOUNT_STATE_READY = "ready"
ACCOUNT_STATE_NEEDS_ATTENTION = "needs_attention"
ACCOUNT_STATE_UNKNOWN = "unknown"

ACCOUNT_STATE_LABELS: dict[str, str] = {
    ACCOUNT_STATE_NEEDS_SIGN_IN: "Needs sign in",
    ACCOUNT_STATE_UPDATING: "Updating",
    ACCOUNT_STATE_CHECKING: "Checking...",
    ACCOUNT_STATE_READY: "Ready",
    ACCOUNT_STATE_NEEDS_ATTENTION: "Needs attention",
    ACCOUNT_STATE_UNKNOWN: "Unable to verify",
}

ACCOUNT_STATE_CTAS: dict[str, str] = {
    ACCOUNT_STATE_NEEDS_SIGN_IN: "Sign in",
    ACCOUNT_STATE_UPDATING: "Updating…",
    ACCOUNT_STATE_CHECKING: "Checking…",
    ACCOUNT_STATE_READY: "View",
    ACCOUNT_STATE_NEEDS_ATTENTION: "Fix",
    ACCOUNT_STATE_UNKNOWN: "Unable to verify",
}

CTA_SIGN_IN = ACCOUNT_STATE_CTAS[ACCOUNT_STATE_NEEDS_SIGN_IN]
CTA_UPDATING = ACCOUNT_STATE_CTAS[ACCOUNT_STATE_UPDATING]
CTA_VIEW = ACCOUNT_STATE_CTAS[ACCOUNT_STATE_READY]
CTA_FIX = ACCOUNT_STATE_CTAS[ACCOUNT_STATE_NEEDS_ATTENTION]

EXT_ACCOUNT_NEEDS_SIGN_IN_HINT = "Sign in to refresh this account"
EXT_ACCOUNT_UPDATING_HINT = "Mighty is updating this account"

# Timestamp copy — session verification vs data refresh are distinct events.
SESSION_VERIFIED_PREFIX = "Session verified"
DATA_REFRESHED_PREFIX = "Data refreshed"
SESSION_NEVER_VERIFIED = "Session not verified yet"
DATA_NEVER_REFRESHED = "No data yet"

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
    "up_to_date": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_READY],
    "updating": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING],
    "checking": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_CHECKING],
    "needs_login": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_SIGN_IN],
    "waiting_for_extension": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UPDATING],
    "error": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_NEEDS_ATTENTION],
    "unknown": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UNKNOWN],
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
WORKER_OPEN_ACCOUNT_CENTER = "Open Account Center"
WORKER_OPEN_DASHBOARD = WORKER_OPEN_ACCOUNT_CENTER
WORKER_SUBTITLE_BACKGROUND = "Working in the background"
WORKER_STATUS_KEEPING_UPDATED = "Keeping your accounts up to date"
WORKER_STATUS_OPEN_ACCOUNT_CENTER = "Open Account Center to manage connections"
WORKER_NOT_UPDATED_YET = "Not updated yet"
WORKER_ACCESS_LOOP_UPDATING = "Mighty is updating your accounts"
WORKER_FIRST_UPDATE_SOON = "First update soon"
WORKER_WAITING_FIRST_UPDATE = "Waiting for first update…"
WORKER_LAST_COMPLETED_PREFIX = "Last completed"
WORKER_NEXT_UPDATE_PREFIX = "Next update in"
WORKER_ACCOUNTS_UPDATED = "{n} account{s} updated"
WORKER_ACCOUNTS_UPDATED_PARTIAL = "{ok} of {total} accounts updated"

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


def access_loop_count_needs_sign_in(count: int) -> str:
    if count == 1:
        return "1 needs sign in"
    return f"{count} need sign in"


def access_loop_count_updating(count: int) -> str:
    if count == 1:
        return "1 updating"
    return f"{count} updating"


def access_loop_count_ready(count: int) -> str:
    if count == 1:
        return "1 ready"
    return f"{count} ready"


def access_loop_count_needs_attention(count: int) -> str:
    if count == 1:
        return "1 needs attention"
    return f"{count} need attention"


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
    "login_required": EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
    "login_wall": EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
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


# ── Home (attention inbox) ───────────────────────────────────────────────────
HOME_EMPTY_HEADLINE = "Your accounts, watched quietly."
HOME_EMPTY_BODY = (
    "Mighty finds airlines, hotels, and card programs from your Gmail and keeps them current "
    "while you browse in Chrome. You sign in when asked — Mighty handles the rest."
)
HOME_EMPTY_WORKER_NOTE = (
    "Use desktop Chrome with the Mighty worker installed. "
    "The dashboard shows results; it never logs into provider sites for you."
)
HOME_EMPTY_CTA = "Connect Gmail"
HOME_EMPTY_SECONDARY = "Add an account manually"

HOME_WAITING_BODY = (
    "Visit your account page in Chrome while logged in — that's when the worker captures "
    "your balances and perks. Usually takes one visit; no sync button needed."
)
HOME_VIEW_WAITING_LABEL = "View all accounts"
HOME_VIEW_NEEDS_LOGIN_LABEL = "View all accounts needing login"
HOME_VIEW_ACCOUNTS_LABEL = "View accounts"

HOME_UPDATE_BODY = (
    "This usually takes a few seconds. You can leave this tab open or come back — "
    "Home will refresh when your data is ready."
)

HOME_ALL_CLEAR_HEADLINE = "You're all set."
HOME_ALL_CLEAR_FOR_NOW_HEADLINE = "You're all set for now."

HOME_PRIORITY_WAITING = "Getting your first update."
HOME_PRIORITY_LOGIN = "One thing needs you."
HOME_PRIORITY_UPDATE = "Almost there."
HOME_PRIORITY_ALL_CLEAR = "Nothing urgent today."
HOME_PRIORITY_RECOMMENDATION = "1 thing worth your attention."

HOME_FOOTER_WORKER = "Mighty runs in Chrome"
HOME_FOOTER_LAST_CHECKED = "Last checked {time}"
HOME_ACTIVITY_LINK = "{count} awaiting decision"
HOME_HEALTH_STILL_SETTING_UP = "still setting up"

# ── Accounts maintenance page (/credentials) ─────────────────────────────────
ACCOUNTS_PAGE_TITLE = "Accounts"
ACCOUNTS_PAGE_SUBTITLE = "Every account Mighty knows about."
ACCOUNTS_EMPTY_HEADLINE = "No accounts yet"
ACCOUNTS_EMPTY_BODY = "Add accounts from Gmail or pick a provider manually."
ACCOUNTS_EMPTY_CTA_EMAIL = "Find accounts from email"
ACCOUNTS_EMPTY_CTA_MANUAL = "Add account manually"
ACCOUNTS_ADD_COVERAGE_NOTE = "Add accounts from Gmail or pick a provider manually."
ACCOUNTS_FILTER_ALL = "All"
ACCOUNTS_FILTER_NEEDS_ATTENTION = "Needs attention"
ACCOUNTS_FILTER_WAITING = "Still setting up"
ACCOUNTS_FILTER_UP_TO_DATE = "Up to date"
ACCOUNTS_NOT_CHECKED_YET = "Not checked yet"
ACCOUNTS_STATUS_SETTING_UP = "Setting up"
ACCOUNTS_STATUS_AWAITING_FIRST = "Awaiting first check"
ACCOUNTS_STATUS_CHECKING = "Checking now"
ACCOUNTS_STATUS_NOT_VERIFIED = "Not yet verified"
ACCOUNTS_SUBLINE_FIRST_VISIT = "Mighty will check this account automatically."
ACCOUNTS_SUBLINE_CONNECTED = "Connected — awaiting data"
ACCOUNTS_SUBLINE_UPDATING = "Updating…"
ACCOUNTS_SUBLINE_CHECKING = "Mighty is verifying this account."
ACCOUNTS_SUBLINE_UNKNOWN = "Mighty hasn't confirmed access yet."
ACCOUNTS_DISCONNECT = "Disconnect"
ACCOUNTS_VIEW_ACCOUNT = CTA_VIEW_ACCOUNT
ACCOUNTS_FILTER_EMPTY = "No accounts in this view."
ACCOUNTS_FILTER_CLEAR = "Show all accounts"


def accounts_last_checked(relative_time: str) -> str:
    return f"Last checked {relative_time}"


def home_waiting_headline(count: int, provider_name: str | None = None) -> str:
    if count == 1 and provider_name:
        return f"Mighty is tracking {provider_name}."
    return f"Mighty is tracking {count} account{'s' if count != 1 else ''}."


def home_login_headline(provider_name: str, *, plural: bool = False, count: int = 1) -> str:
    if plural or count > 1:
        return f"{count} accounts need login."
    return f"{provider_name} needs login."


def home_login_body(provider_name: str) -> str:
    return (
        f"Sign in to {provider_name} in Chrome — the only manual step. "
        "After you log in, keep your account page open for a few seconds so the worker "
        "can verify your session. Mighty never sees or stores your password."
    )


def home_login_cta(provider_name: str) -> str:
    return f"Log in to {provider_name}"


def home_open_provider_cta(provider_name: str) -> str:
    return f"Open {provider_name}"


def home_view_provider_cta(provider_name: str) -> str:
    return f"View {provider_name}"


def home_update_headline(provider_name: str) -> str:
    return f"Updating {provider_name}…"


def home_all_clear_headline(setup_incomplete_count: int = 0) -> str:
    if setup_incomplete_count > 0:
        return HOME_ALL_CLEAR_FOR_NOW_HEADLINE
    return HOME_ALL_CLEAR_HEADLINE


def home_all_clear_body(account_count: int, setup_incomplete_count: int = 0) -> str:
    if setup_incomplete_count > 0:
        n = setup_incomplete_count
        word = "account" if n == 1 else "accounts"
        return (
            f"Nothing needs your attention right now. Mighty is still setting up {n} {word} "
            "and will keep working in the background."
        )
    n = account_count
    word = "account" if n == 1 else "accounts"
    return (
        f"Mighty is monitoring all {n} {word}. "
        "We'll let you know when something needs your attention."
    )


def home_attention_headline(attention_count: int) -> str:
    if attention_count == 1:
        return "One account needs your attention."
    return f"{attention_count} accounts need your attention."


def home_attention_body(setup_incomplete_count: int = 0) -> str:
    if setup_incomplete_count > 0:
        n = setup_incomplete_count
        word = "account" if n == 1 else "accounts"
        return f"Mighty is also still setting up {n} {word} in the background."
    return "Open Accounts to see what needs you — Mighty will keep monitoring everything else."


def home_recommendation_priority(total: int) -> str:
    if total <= 1:
        return HOME_PRIORITY_RECOMMENDATION
    return f"{total} things worth your attention."


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
        "account_state_labels": ACCOUNT_STATE_LABELS,
        "account_state_ctas": ACCOUNT_STATE_CTAS,
        "access_loop": {
            "headline_updating": WORKER_ACCESS_LOOP_UPDATING,
            "open_account_center": WORKER_OPEN_ACCOUNT_CENTER,
            "session_verified_prefix": SESSION_VERIFIED_PREFIX,
            "data_refreshed_prefix": DATA_REFRESHED_PREFIX,
        },
        "worker": {
            "name": WORKER_NAME,
            "subtitle_running": WORKER_SUBTITLE_RUNNING,
            "subtitle_updating": WORKER_SUBTITLE_UPDATING,
            "subtitle_not_configured": WORKER_SUBTITLE_NOT_CONFIGURED,
            "setup_needed": WORKER_SETUP_NEEDED,
            "setup_detail": WORKER_SETUP_DETAIL,
            "open_dashboard": WORKER_OPEN_DASHBOARD,
            "open_account_center": WORKER_OPEN_ACCOUNT_CENTER,
            "not_updated_yet": WORKER_NOT_UPDATED_YET,
            "access_loop_updating": WORKER_ACCESS_LOOP_UPDATING,
            "subtitle_background": WORKER_SUBTITLE_BACKGROUND,
            "status_keeping_updated": WORKER_STATUS_KEEPING_UPDATED,
            "status_open_account_center": WORKER_STATUS_OPEN_ACCOUNT_CENTER,
            "account_needs_sign_in_hint": EXT_ACCOUNT_NEEDS_SIGN_IN_HINT,
            "account_updating_hint": EXT_ACCOUNT_UPDATING_HINT,
        },
        "failure_hints": FAILURE_HINTS,
        "failure_icons": FAILURE_ICONS,
    }
