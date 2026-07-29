"""
mighty.user_copy
────────────────
Canonical user-facing copy for Mighty's Home ↔ Mighty in Chrome interaction.

Interaction model:
  • Mighty in Chrome watches accounts while you browse.
  • Home answers whether anything needs you; Accounts is for repair.
  • Sign-in is the only manual step. Everything else is automatic.
"""

from __future__ import annotations

# ── Roles ─────────────────────────────────────────────────────────────────────
ROLE_DASHBOARD = "Home"
ROLE_EXTENSION = "Mighty in Chrome"
ROLE_DASHBOARD_DESC = "See what needs you, what Mighty is doing, and what is done."
ROLE_EXTENSION_DESC = "Runs in Chrome and updates accounts while you browse."

# ── Taglines ──────────────────────────────────────────────────────────────────
INTERACTION_TAGLINE = (
    "Mighty runs in Chrome. Home shows what needs you. "
    "Sign-in is the only manual step."
)
AUTO_UPDATE_TAGLINE = "Mighty runs in Chrome while you browse."
EXTENSION_UPDATE_LINE = (
    "Mighty in Chrome updates accounts when you visit provider sites."
)
DASHBOARD_ROLE_LINE = "Home shows results — it never logs into provider sites for you."
MANUAL_STEP_LINE = "Sign-in is the only thing you do manually. Everything else is automatic."
NEEDS_LOGIN_EXPLAINER = "Sign in required means: open the provider in Chrome and sign in."

# ── Account Access Loop (Accounts + Mighty in Chrome popup) ───────────────────
# Session/login presentation comes from provider_session_state (via session_access).
ACCOUNT_STATE_NEEDS_SIGN_IN = "needs_sign_in"
ACCOUNT_STATE_UPDATING = "updating"
ACCOUNT_STATE_CHECKING = "checking"
ACCOUNT_STATE_READY = "ready"
ACCOUNT_STATE_NEEDS_ATTENTION = "needs_attention"
ACCOUNT_STATE_UNKNOWN = "unknown"

ACCOUNT_STATE_LABELS: dict[str, str] = {
    ACCOUNT_STATE_NEEDS_SIGN_IN: "Sign in required",
    ACCOUNT_STATE_UPDATING: "Updating",
    ACCOUNT_STATE_CHECKING: "Checking",
    ACCOUNT_STATE_READY: "Connected",
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
    ACTIVITY_WAITING: "Waiting for Mighty in Chrome",
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
    "unverified": ACCOUNT_STATE_LABELS[ACCOUNT_STATE_UNKNOWN],
}

# Readiness copy — canonical “Connected” requires access + private data.
READINESS_COPY_READY = (
    "Mighty is connected to your logged-in account and can see your data."
)
READINESS_COPY_CHECKING = "Mighty is verifying access and account data."
READINESS_COPY_SIGNED_OUT = "Sign in so Mighty can access your account data."
READINESS_COPY_UNVERIFIED = (
    "Mighty could not confirm both account access and data."
)
READINESS_SECONDARY_BACKGROUND = "Verifying in the background"

# Shared Dashboard / Accounts access debugger labels.
READINESS_STATUS_CONNECTED = "Connected"
READINESS_STATUS_CHECKING = "Checking"
READINESS_STATUS_SIGNED_OUT = "Sign in required"
READINESS_STATUS_UNVERIFIED = "Unable to verify"

ACCESS_MEANING_CONNECTED_SEEN = "Mighty can see your logged-in account data."
ACCESS_MEANING_CONNECTED_NOT_SEEN = (
    "Mighty verified your login but has not read account data yet."
)
ACCESS_MEANING_CHECKING = "Mighty is checking this account now."
ACCESS_MEANING_SIGNED_OUT = (
    "Mighty cannot access this account until you sign in."
)
ACCESS_MEANING_UNKNOWN = "Mighty has not confirmed access yet."
ACCESS_MEANING_EXTRACTION_FAILED = (
    "Mighty reached the account but could not read private data."
)
ACCESS_MEANING_NO_ACCOUNT_DATA = (
    "Mighty can tell you are signed in, but no account details were available yet."
)

ACCESS_DISCOVERED_FROM_PREFIX = "Discovered from"
ACCESS_PRIVATE_DATA_PREFIX = "Private data"
ACCESS_BACKGROUND_PREFIX = "Background"
ACCESS_LAST_CONFIRMED_PREFIX = "Last confirmed"
ACCESS_WHY_SUMMARY = "Why?"

# ── Control Tower (Dashboard presentation) ───────────────────────────────────
TOWER_CURRENT_ACTIVITY = "Current activity"
TOWER_LAST_VERIFIED = "Last verified"
TOWER_LAST_SUCCESSFUL = "Last successful verification"
TOWER_ACTION_NONE = "No action required"
TOWER_ACTION_SIGN_IN = "Sign in required"
TOWER_ACTION_NEEDED = "Needs your help"

TOWER_MEANING_WATCHING = "Mighty can currently read your logged-in account."
TOWER_MEANING_REFRESHING = "Mighty is refreshing this account in the background."
TOWER_MEANING_CHECKING = "Mighty is checking this account now."
TOWER_MEANING_SIGN_IN = "Mighty cannot access this account until you sign in."
TOWER_MEANING_WAITING_FIRST = "Mighty has not confirmed access yet."
TOWER_MEANING_ATTENTION = "Something needs your help before Mighty can continue."

TOWER_HERO_WATCHING = "Mighty is watching your accounts."
TOWER_HERO_WORKING = "Mighty is actively monitoring your financial accounts."
TOWER_HERO_WAITING = "Mighty is getting ready to watch your accounts."
TOWER_HERO_NEEDS_YOU = "Mighty needs your attention."

TOWER_ATTENTION_NONE = "No action needed."
TOWER_ATTENTION_NEEDED = "Needs your attention."

TOWER_SUMMARY_WATCHING = "Watching"
TOWER_SUMMARY_WORKING = "Working"
TOWER_SUMMARY_NEEDS_YOU = "Needs you"
TOWER_SUMMARY_NONE = "None"
TOWER_SUMMARY_SIGN_IN = "Sign in required"

TOWER_SYSTEM_HEALTH = "System Health"
TOWER_HEALTH_WATCHING = "Watching"
TOWER_HEALTH_REFRESHING = "Refreshing"
TOWER_HEALTH_NEEDS_HELP = "Needs your help"
TOWER_HEALTH_WAITING = "Waiting"

TOWER_ACCOUNTS_LABEL = "Accounts"
TOWER_SUMMARY_LABEL = "Summary"


def access_connected_named(names: str) -> str:
    return f"Connected: {names}"


def access_discovered_from(source: str) -> str:
    return f"{ACCESS_DISCOVERED_FROM_PREFIX} {source}"


# ── Mighty in Chrome popup copy ───────────────────────────────────────────────
WORKER_NAME = "Mighty"
WORKER_SUBTITLE_RUNNING = ACTIVITY_LABELS[ACTIVITY_WATCHING]
WORKER_SUBTITLE_UPDATING = "Updating accounts"
WORKER_SUBTITLE_NOT_CONFIGURED = "Not configured"
WORKER_SETUP_NEEDED = "Setup needed"
WORKER_SETUP_DETAIL = "Open Accounts to finish setting up Mighty in Chrome."
WORKER_SETUP_BOX = (
    "Open the setup page in Chrome — Mighty in Chrome configures itself automatically."
)
WORKER_OPEN_ACCOUNT_CENTER = "Open Accounts"
WORKER_OPEN_DASHBOARD = WORKER_OPEN_ACCOUNT_CENTER
WORKER_SUBTITLE_BACKGROUND = "Working in the background"
WORKER_STATUS_KEEPING_UPDATED = "Keeping your accounts up to date"
WORKER_STATUS_OPEN_ACCOUNT_CENTER = "Open Accounts to manage connections"
WORKER_NOT_UPDATED_YET = "Not updated yet"
WORKER_ACCESS_LOOP_UPDATING = "Mighty is updating your accounts"
WORKER_FIRST_UPDATE_SOON = "First update soon"
WORKER_WAITING_FIRST_UPDATE = "Waiting for first update…"
WORKER_LAST_COMPLETED_PREFIX = "Last completed"
WORKER_NEXT_UPDATE_PREFIX = "Next update in"
WORKER_ACCOUNTS_UPDATED = "{n} account{s} updated"
WORKER_ACCOUNTS_UPDATED_PARTIAL = "{ok} of {total} accounts updated"

# ── Mighty in Chrome setup (Chrome extension install / troubleshooting) ───────
EXT_PRODUCT_NAME = "Mighty in Chrome"
EXT_SETUP_LINK = "Set up Mighty in Chrome"
EXT_SETUP_TITLE = "Connect Mighty in Chrome"
EXT_SETUP_BODY = (
    "Mighty in Chrome only works in a normal Chrome window — not Incognito or Guest. "
    "Confirm that first, install, then ask Mighty to check."
)
EXT_SETUP_CONTEXT_HEADING = "Before you install"
EXT_SETUP_CONTEXT_LEDE = (
    "Use the same normal Chrome window for this page, chrome://extensions, "
    "and later Amex. Incognito and Guest windows block extensions by default, "
    "so Mighty cannot see this page or send a heartbeat."
)
EXT_SETUP_CONTEXT_CONFIRM = (
    "I’m in a normal Chrome window (not Incognito, not Guest)"
)
EXT_SETUP_CONTEXT_BLOCKED = (
    "This looks like Incognito or a restricted window. "
    "Open Mighty in a normal Chrome window, sign in again if needed, "
    "then return here. Chrome — not Mighty — is blocking the extension."
)
EXT_SETUP_CONTEXT_NOT_CHROME = (
    "Mighty in Chrome requires desktop Google Chrome. "
    "Open this page in Chrome to continue. This is a browser requirement, not a Mighty outage."
)
EXT_SETUP_INSTALL_HEADING = "Install (desktop Chrome)"
EXT_SETUP_DOWNLOAD_LABEL = "Download Mighty in Chrome"
EXT_SETUP_INSTALL_STEPS: tuple[str, ...] = (
    "Stay in this normal Chrome window. Download Mighty in Chrome (zip) and unzip it.",
    "In this same window, open chrome://extensions → turn on Developer mode.",
    "Click Load unpacked and select the unzipped mighty-in-chrome folder "
    "(the folder that contains manifest.json).",
    "Pin Mighty from the puzzle-piece menu. If it was already installed, "
    "click Reload on chrome://extensions first.",
)
EXT_SETUP_RELOAD_HINT = (
    "Already installed? Reload Mighty on chrome://extensions in this same "
    "normal window, then use I’ve installed Mighty."
)
EXT_SETUP_HEARTBEAT_LISTENING = "Listening for Mighty in Chrome…"
EXT_SETUP_HEARTBEAT_SEEN = "Heartbeat received"
EXT_SETUP_HEARTBEAT_NONE = "No heartbeat yet"
EXT_SETUP_VERIFY_CTA = "I've installed Mighty"
EXT_SETUP_VERIFYING = "Checking for Mighty in Chrome…"
EXT_SETUP_WAITING = "Waiting for Mighty in Chrome…"
EXT_SETUP_SUCCESS = "Mighty in Chrome is connected"
EXT_SETUP_CONTINUE = "Continue to Home"
EXT_SETUP_CONTINUE_HINT = "You can go to Home and finish this later."
EXT_SETUP_NOT_DETECTED = (
    "Mighty still doesn’t see a heartbeat from the extension."
)
EXT_SETUP_FAIL_CHROME = (
    "Chrome context — Stay in a normal Chrome window (not Incognito/Guest). "
    "Extensions can’t reach this page there."
)
EXT_SETUP_FAIL_EXTENSION = (
    "Extension — On chrome://extensions, confirm Mighty is loaded and enabled, "
    "then click Reload and try again."
)
EXT_SETUP_FAIL_MIGHTY = (
    "Mighty — If Chrome and the extension look correct, reload this page and try again. "
    "If it still fails, tell support the Live connection panel stayed on “No heartbeat yet.”"
)
EXT_SETUP_TRY_AGAIN = "Try again"
EXT_SETUP_GO_HOME = "Go to Home"
EXT_NOT_DETECTED_SHORT = "Mighty in Chrome not detected."
EXT_INSTALL_TOAST = "Install Mighty in Chrome to update your accounts."
MOBILE_WORKER_TOAST = "Use desktop Chrome with Mighty in Chrome to update your accounts."

# ── How updates work (Accounts page) ──────────────────────────────────────────
HOW_UPDATES_TITLE = "How Mighty works"
HOW_UPDATES_ITEMS: tuple[tuple[str, str], ...] = (
    (
        "Home",
        "Home shows what needs you and what Mighty has finished. "
        "It never logs into provider sites for you.",
    ),
    (
        "Mighty in Chrome",
        "When you visit a provider site while signed in, "
        "Mighty in Chrome captures account data for Accounts and Home.",
    ),
    (
        "Automatic updates",
        "Mighty checks your accounts while you browse provider sites. "
        "No manual sync needed.",
    ),
    (
        "Sign in only when needed",
        "If access expires, open the provider in Chrome and sign in. "
        "Mighty in Chrome picks up your next visit.",
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
    "Mighty in Chrome <strong>automatically captures your data</strong> — "
    "nothing else for you to do"
)
CONNECT_WAITING = "Waiting for Mighty in Chrome to verify access…"
CONNECT_WAITING_SUB = (
    "Stay on your account page after signing in — usually takes 5–15 seconds"
)
CONNECT_TROUBLE_TITLE = "Mighty in Chrome didn't detect your sign-in. Try these:"
CONNECT_TROUBLE_EXT = (
    "Make sure the <a href=\"/extension-setup\" target=\"_blank\" "
    "style=\"color:#b91c1c;font-weight:500\">Chrome extension is installed</a>"
)
CONNECT_MODAL_INTRO = (
    "Make sure you're <strong>signed into <span id=\"modal-ext-site-name\"></span></strong> in Chrome, "
    "then click the button below. Mighty in Chrome captures your account data automatically."
)
CONNECT_MODAL_WAITING = "Waiting for Mighty in Chrome…"
CONNECT_MODAL_WAITING_SUB = (
    "Open your provider site in Chrome while signed in. "
    "Mighty in Chrome verifies access — visiting the domain alone is not enough."
)
CONNECT_MODAL_NEEDS_LOGIN = (
    "Sign in required. Sign in to your provider in Chrome, "
    "then keep this tab open while Mighty in Chrome verifies access."
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
        "Set up Mighty in Chrome and open your provider site."
    ),
    "needs_login": "Sign in to your provider in Chrome — the only manual step.",
    "connected": (
        "Access verified — visit your account page so Mighty in Chrome can capture data."
    ),
    "synced": "Account data is ready in Accounts and Home.",
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
    "waiting_for_extension": "Mighty in Chrome is verifying access…",
    "needs_login": STATUS_LABEL_NEEDS_LOGIN,
    "connected": "Connected — awaiting data",
}

CONNECTION_SUBCOPY: dict[str, str] = {
    "connecting": "Setting up your account…",
    "waiting_for_extension": (
        "Waiting for Mighty in Chrome — set it up and open the provider while signed in."
    ),
    "needs_login": (
        "Sign in required — sign in in Chrome so Mighty can verify access."
    ),
    "connected": (
        "Connected — access verified. Mighty in Chrome will capture data on your next visit."
    ),
}

# ── Primary CTAs ──────────────────────────────────────────────────────────────
CTA_ADD_TO_MIGHTY = "Add to Mighty"
CTA_OPEN_PROVIDER = "Open provider"
CTA_LOG_IN = "Log in"
CTA_LOG_IN_TO_PROVIDER = "Log in to provider"
CTA_VIEW_ACCOUNT = "View account"
CTA_RETRY_UPDATE = "Retry update"
CTA_SET_UP_WORKER = "Set up Mighty in Chrome"

LIFECYCLE_CTAS: dict[str, str] = {
    "discovered": CTA_ADD_TO_MIGHTY,
    "added": CTA_OPEN_PROVIDER,
    "waiting_for_extension": CTA_OPEN_PROVIDER,
    "needs_login": CTA_LOG_IN,
    "connected": CTA_OPEN_PROVIDER,
    "synced": CTA_VIEW_ACCOUNT,
}

SECONDARY_CTA_EXTENSION_RETRY = "Mighty in Chrome installed / Retry"

# ── Source labels ─────────────────────────────────────────────────────────────
SOURCE_FOUND_FROM_GMAIL = "Found from Gmail"
SOURCE_EXTENSION = "Mighty in Chrome"
SOURCE_MANUALLY_ADDED = "Manually added"

# ── Login-required display ────────────────────────────────────────────────────
NEEDS_LOGIN_BADGE = "🔐 Needs login"
NEEDS_LOGIN_ACTION_LABEL = STATUS_LABEL_NEEDS_LOGIN
NEEDS_LOGIN_ACTION_VALUE = "{name} — open the provider in Chrome and sign in"
NEEDS_LOGIN_ACTION_CTA = CTA_LOG_IN_TO_PROVIDER
NEEDS_LOGIN_ACTION_WHY = "Sign in to the provider in Chrome — the only manual step."
NEEDS_LOGIN_BANNER_SUFFIX = (
    "open each site in Chrome and sign in. Mighty in Chrome updates automatically."
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
        "Mighty in Chrome needed",
        "Set up Mighty in Chrome, then visit your provider site.",
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
    "Mighty in Chrome updates accounts automatically "
    "when you visit provider sites while signed in."
)
SETTINGS_UPDATES_FALLBACK = (
    "Server-side retry re-runs Mighty's scraper for all connected accounts. "
    "Use only if account data looks stale — it may not work for sites that "
    "require an active browser visit."
)
SETTINGS_RETRY_UPDATE_BTN = CTA_RETRY_UPDATE
SETTINGS_RETRY_RUNNING = "Running…"
SETTINGS_RETRY_STARTED = "Update started — check Home in a few minutes."
SETTINGS_RETRY_FAILED = "Update failed or already running."

# ── Connect modal ─────────────────────────────────────────────────────────────
MODAL_ADD_ACCOUNT = "Add an account"
MODAL_OPEN_IN_CHROME = "Open in Chrome →"
MODAL_CONNECTED_NO_FIELDS = (
    "Access verified. Visit your account page — Mighty in Chrome will capture your data."
)

# ── Onboarding ────────────────────────────────────────────────────────────────
ONBOARDING_TITLE = "How Mighty works"
ONBOARDING_BODY = (
    "Mighty reads your connected account pages and extracts only the facts you care about "
    "— balances, expiry dates, due dates, and credits. "
    "<strong>Raw page text is never shared</strong> and is discarded after extraction."
)
ONBOARDING_CHROME_LINE = (
    "Use <strong>desktop Chrome</strong> — Mighty in Chrome updates accounts while you browse. "
    "Home shows what needs you."
)
SIGNUP_HEADLINE = "Let's get started."
SIGNUP_SUB = (
    "Create your Mighty account. You’ll choose what Mighty monitors next."
)
SIGNUP_CTA = "Create account"
SIGNUP_REASSURE = (
    "Mighty does not monitor anything until you choose which accounts to watch."
)
SIGNUP_PASSWORD_HELPER = "At least 6 characters."
SIGNUP_DUPLICATE_TITLE = "An account with that email already exists."
SIGNUP_DUPLICATE_RESTART_LINK = "Delete account and start over"
SIGNUP_DUPLICATE_HINT = (
    "Factory reset deletes your Mighty account for this email. "
    "You’ll return to the public landing and create a new account — "
    "as if Mighty has never seen you."
)
SIGNUP_FACTORY_RESET_DONE = (
    "Your Mighty account was deleted. Create a new account to begin — "
    "Mighty will treat this like the first time."
)

# ── Invite-only factory reset (same email) ────────────────────────────────────
BETA_RESTART_HEADLINE = "Factory reset"
BETA_RESTART_LEDE = (
    "Delete this Mighty account and all Mighty data for this email. "
    "You’ll return to the public landing and can create a new account — "
    "as if Mighty has never seen you. Your Gmail and Amex logins are unchanged."
)
BETA_RESTART_CTA = "Delete data and start over"
BETA_RESTART_PASSWORD_HELPER = (
    "Enter the password for this Mighty account to confirm it’s yours."
)
BETA_RESTART_CONFIRM = (
    "I understand this permanently deletes my Mighty account and all Mighty data "
    "for this email. I will create a new account to begin again."
)
BETA_RESTART_REASSURE = (
    "This only deletes Mighty. It does not change your Google or American Express accounts."
)
BETA_RESTART_SIGN_IN_PROMPT = "Want to keep your account instead?"
BETA_RESTART_WRONG_PASSWORD = (
    "That email and password don’t match. Try again, or reset your password first."
)
BETA_RESTART_NO_ACCOUNT = (
    "No Mighty account uses that email. You can create one instead."
)
BETA_RESTART_NEED_CONFIRM = "Check the confirmation box to continue."

# ── Enable Monitoring (CP-004) ────────────────────────────────────────────────
ENABLE_MONITORING_EYEBROW = "Accounts selected · Set up updates next"
ENABLE_MONITORING_HEADLINE = "Keep your accounts current while you browse"
ENABLE_MONITORING_LEDE = (
    "You chose what Mighty should watch. Updates start after Mighty in Chrome "
    "is connected — then when you visit those accounts signed in, Mighty can "
    "refresh what Home shows. You sign in yourself."
)
ENABLE_MONITORING_TEACH_1_TITLE = "You chose what to watch"
ENABLE_MONITORING_TEACH_1_BODY = (
    "Mighty only follows the accounts you confirmed. Live updates still need "
    "Mighty in Chrome."
)
ENABLE_MONITORING_TEACH_2_TITLE = "Updates happen while you browse"
ENABLE_MONITORING_TEACH_2_BODY = (
    "When Mighty in Chrome is connected and you open a watched account signed in, "
    "Mighty can refresh what Home shows."
)
ENABLE_MONITORING_TEACH_3_TITLE = "You stay the operator"
ENABLE_MONITORING_TEACH_3_BODY = (
    "Mighty never signs in as you. Sign-in is the only manual step."
)
ENABLE_MONITORING_WHY_LABEL = "Why the browser"
ENABLE_MONITORING_WHY_BODY = (
    "Balances live on provider sites. The browser is how Mighty can refresh what "
    "you already see when you’re signed in — then show it calmly on Home."
)
ENABLE_MONITORING_ROLE_YOU = "You sign in when needed"
ENABLE_MONITORING_ROLE_MIGHTY = "Mighty refreshes Home from pages you open"
ENABLE_MONITORING_CTA = "Enable updates"
ENABLE_MONITORING_SECONDARY = "Not now — go to Home"
ENABLE_MONITORING_REASSURE = (
    "Takes a moment in a normal desktop Chrome window (not Incognito). "
    "You’ll sign into providers yourself when needed."
)
ENABLE_MONITORING_MOBILE_NOTE = (
    "Use desktop Chrome to keep accounts current while you browse. "
    "On this device you can continue to Home and finish updates later."
)
ENABLE_MONITORING_DETAILS_SUMMARY = "What updates involve"
ENABLE_MONITORING_DETAILS_WHEN = (
    "Updates run when you visit a watched account page in Chrome while signed in — "
    "not as a remote login into your providers."
)
ENABLE_MONITORING_DETAILS_SEES = (
    "Mighty can read account information on pages you open for programs it’s watching, "
    "enough to update balances and status for Home. It is not a recorder of everything "
    "you do online."
)
ENABLE_MONITORING_DETAILS_OFF = (
    "You can turn updates off anytime by disabling or removing Mighty in Chrome. "
    "What Mighty watches stays yours to change."
)
ENABLE_MONITORING_DETAILS_SKIP = (
    "If you skip for now, Mighty still remembers what you chose to watch. "
    "Verified numbers wait until updates are enabled and you visit a provider signed in."
)
ENABLE_MONITORING_DETAILS_HOME = (
    "Home shows what needs you and what’s already verified. "
    "Mighty in Chrome is how account facts stay current while you browse. "
    "Home never logs into provider sites for you."
)
ENABLE_MONITORING_READY_EYEBROW = "Updates ready"
ENABLE_MONITORING_READY_HEADLINE = "Mighty in Chrome is ready"
ENABLE_MONITORING_READY_LEDE = (
    "Next, visit one of your watched accounts while signed in so Home can show "
    "your first update — or go to Home and do it when you’re ready."
)
ENABLE_MONITORING_READY_CTA = "Go to Home"
ENABLE_MONITORING_PAGE_TITLE = "Enable updates"

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
    "Use desktop Chrome with Mighty in Chrome when it is time to verify access. "
    "Home shows results; it never logs into provider sites for you."
)
HOME_EMPTY_CTA = "Connect Gmail"
HOME_EMPTY_SECONDARY = "Add an account manually"

HOME_WAITING_BODY = (
    "Visit your account page in Chrome while signed in — Mighty in Chrome captures "
    "your balances and perks. Usually takes one visit; no sync button needed."
)
HOME_BRIEFING_ANSWER_HANDOFF = "Getting your first update."
HOME_HANDOFF_NEEDS_CHROME_BODY = (
    "Set up Mighty in Chrome so Mighty can verify access while you browse. "
    "This is the only setup step left before your first update."
)
HOME_PROVIDER_VISIT_HELPER = (
    "Mighty stays open in this tab while the provider is open."
)
HOME_PROVIDER_VISIT_OPENED = (
    "Opened in another tab. Mighty is still active here — this page will update when "
    "Mighty sees progress. No sync button needed."
)
HOME_NOT_NOW_LABEL = "Not now"
HOME_HANDOFF_VERIFYING_BODY = (
    "Mighty is verifying access now. You do not need to do anything else — "
    "keep this tab open or return here; Home will update when the check finishes."
)
HOME_VIEW_WAITING_LABEL = "View all accounts"
HOME_VIEW_NEEDS_LOGIN_LABEL = "View all accounts needing login"
HOME_VIEW_ACCOUNTS_LABEL = "View accounts"

HOME_UPDATE_BODY = (
    "This usually takes a few seconds. You can leave this tab open or come back — "
    "Home will refresh when your data is ready."
)
HOME_UPDATING_CTA = "Updating…"

HOME_ALL_CLEAR_HEADLINE = "You're good."
HOME_ALL_CLEAR_FOR_NOW_HEADLINE = "Everything looks current."

HOME_PRIORITY_WAITING = "Getting your first update."
HOME_PRIORITY_LOGIN = "One thing needs you."
HOME_PRIORITY_UPDATE = "Almost there."
HOME_PRIORITY_ALL_CLEAR = "You're good."
HOME_PRIORITY_RECOMMENDATION = "1 thing worth your attention."

# V1B briefing — answer "Am I good?"
HOME_BRIEFING_ANSWER_GOOD = "You're good."
HOME_BRIEFING_ANSWER_ATTENTION = "One thing needs your attention."
HOME_BRIEFING_ANSWER_OPPORTUNITY = "There's value waiting for you."
HOME_BRIEFING_ALL_CLEAR_TITLE = "You're good."
HOME_RECENT_WINS_LABEL = "Recent wins"
HOME_OPS_LABEL = "Working quietly"
HOME_FRESHNESS_PREFIX = "Last verified "
HOME_FRESHNESS_UPDATED_PREFIX = "Updated "
HOME_ACCOUNTS_SOFT_LINK = "Accounts"

# Home V2 — Living Calm regions
HOME_V2_EVIDENCE_LABEL = "Evidence"
HOME_V2_ACTIVITY_LABEL = "Activity"
HOME_V2_ACTIVITY_EMPTY = "No recent changes — Mighty is watching quietly."
HOME_V2_WORKING_QUIETLY = "Working quietly"
HOME_V2_NEEDS_YOU = "Needs you"
HOME_V2_VALUE_WAITING = "Value waiting"
HOME_V2_GETTING_READY = "Getting ready"
HOME_EVIDENCE_GMAIL_CONNECTED = "Gmail connected"
HOME_EVIDENCE_GMAIL_NEEDED = "Gmail not connected"
HOME_EVIDENCE_CHROME_ACTIVE = "Chrome active"
HOME_EVIDENCE_CHROME_NEEDED = "Set up Mighty in Chrome"

HOME_FOOTER_WORKER = "Mighty runs in Chrome"
HOME_FOOTER_LAST_CHECKED = "Last checked {time}"
HOME_ACTIVITY_LINK = "{count} awaiting your decision"
HOME_HEALTH_LABEL = "Account health"
HOME_HEALTH_STILL_SETTING_UP = "being verified"
HOME_SECONDARY_LABEL = "Also worth a look"
HOME_METRICS_LABEL = "Also"

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
ACCOUNTS_STATUS_CHECKING = "Checking"
ACCOUNTS_STATUS_NOT_VERIFIED = "Unable to verify"
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
        return f"Mighty is beginning to manage {provider_name}."
    return (
        f"Mighty is beginning to manage {count} "
        f"account{'s' if count != 1 else ''}."
    )


def home_handoff_needs_visit_body(provider_name: str | None = None) -> str:
    """Orientation for Visit provider — Mighty stays home base (system of engagement)."""
    name = (provider_name or "the provider").strip() or "the provider"
    return (
        f"Mighty is ready to verify access. {name} opens in a new tab — "
        f"keep this Mighty tab open. Sign in yourself on {name}; Mighty watches and "
        f"does not sign in as you. Return here afterward — Home updates when Mighty "
        f"can verify access."
    )


def home_handoff_body(
    *,
    needs_chrome: bool = False,
    verifying: bool = False,
    provider_name: str | None = None,
) -> str:
    """Lightweight enrollment confirmation body — not a permanent Home section."""
    if needs_chrome:
        return HOME_HANDOFF_NEEDS_CHROME_BODY
    if verifying:
        return HOME_HANDOFF_VERIFYING_BODY
    return home_handoff_needs_visit_body(provider_name)


def home_visit_provider_cta(provider_name: str) -> str:
    return f"Visit {provider_name}"


def home_login_headline(provider_name: str, *, plural: bool = False, count: int = 1) -> str:
    if plural or count > 1:
        return f"{count} accounts need a sign-in."
    return f"Sign in to {provider_name}."


def home_login_body(provider_name: str) -> str:
    """Role-split body for first / routine provider sign-in (CP-005)."""
    return (
        f"{provider_name} needs your sign-in so Mighty can keep this account current. "
        f"{provider_name} opens in a new tab — keep this Mighty tab open. "
        f"You'll sign in on {provider_name}'s site; Mighty does not sign in as you. "
        f"Return here afterward — Home updates when Mighty sees progress."
    )


def home_login_mfa_body(provider_name: str) -> str:
    return (
        f"Finish the {provider_name} sign-in challenge on {provider_name}'s site. "
        "Mighty does not sign in as you — once you finish, Mighty can keep watching."
    )


def home_login_captcha_body(provider_name: str) -> str:
    return (
        f"Complete the {provider_name} security check on {provider_name}'s site. "
        "Mighty does not sign in as you."
    )


def home_login_consent_body(provider_name: str) -> str:
    return (
        f"Approve access for {provider_name} on {provider_name}'s site. "
        "Mighty does not sign in as you."
    )


def home_login_cta(provider_name: str) -> str:
    return f"Sign in to {provider_name}"


def home_journey_waiting_headline(provider_name: str) -> str:
    return f"Waiting on {provider_name}"


def home_journey_waiting_body(provider_name: str, *, action_label: str = "opened") -> str:
    return (
        f"You {action_label} {provider_name}. Mighty is waiting for Chrome to confirm "
        f"you're signed in — keep the {provider_name} tab open and return here. "
        f"Home will update when Mighty sees progress."
    )


def home_journey_progress_headline(provider_name: str) -> str:
    return f"Checking {provider_name}"


def home_journey_progress_body(provider_name: str, *, action_label: str = "opened") -> str:
    return (
        f"You {action_label} {provider_name}. Mighty is verifying access now — "
        f"keep this tab open."
    )


def home_journey_non_progress_headline(provider_name: str) -> str:
    return f"Still waiting on {provider_name}"


def home_journey_non_progress_body(provider_name: str, *, action_label: str = "opened") -> str:
    return (
        f"You {action_label} {provider_name}, but Mighty has not confirmed a signed-in "
        f"session yet. Keep the {provider_name} tab open, finish signing in there if needed, "
        f"then return here."
    )


def home_journey_repeat_ask_headline(provider_name: str) -> str:
    return f"Sign in to {provider_name} still needed"


def home_journey_repeat_ask_body(provider_name: str, *, action_label: str = "opened") -> str:
    """R1: explain why previous attempt did not produce the expected outcome."""
    return (
        f"You already {action_label} {provider_name}, but Mighty still does not have a "
        f"confirmed signed-in session — that is why we are asking again. "
        f"Open {provider_name} in a new tab, complete sign-in yourself, keep this Mighty "
        f"tab open, and return here afterward."
    )


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
            f"Watching your connected accounts while {n} {word} "
            f"{'is' if n == 1 else 'are'} being verified."
        )
    return home_briefing_all_clear_body(account_count)


def home_briefing_all_clear_body(account_count: int = 0) -> str:
    """Outcome-oriented all-clear copy — no account counts or system jargon."""
    del account_count  # Counts belong on Accounts, not the briefing.
    return (
        "Nothing needs your attention right now.\n"
        "We'll let you know if anything changes."
    )


def home_v2_healthy_body() -> str:
    """Ambient all-clear — permission to leave (after first-success settles)."""
    return (
        "Nothing needs your attention right now. "
        "Mighty will watch quietly and let you know if anything changes."
    )


def home_first_success_body(provider_name: str) -> str:
    """One-shot return-to-Home after first verification (CP-005)."""
    return (
        f"Mighty verified {provider_name} and will watch quietly from here. "
        "Nothing else needs you right now."
    )


def home_first_success_partial_body(provider_name: str) -> str:
    """Access verified; first data still arriving."""
    return (
        f"Access verified for {provider_name}. "
        "Balances will show as Mighty finishes the first update."
    )


def home_evidence_watching(count: int) -> str:
    if count <= 0:
        return "No accounts watched yet"
    if count == 1:
        return "Watching 1 account"
    return f"Watching {count} accounts"


def home_ops_refreshing(provider_name: str) -> str:
    return f"Updating {provider_name}"


def home_ops_setting_up(count: int) -> str:
    del count
    return "Getting an account ready"


def home_ops_setting_up_provider(provider_name: str) -> str:
    return f"Setting up {provider_name}"


def home_ops_needs_login(count: int) -> str:
    del count
    return "A sign-in is needed"


def home_steady_needs_sign_in_body(count: int = 1) -> str:
    """Honesty fallback when Attention is silent but portfolio needs sign-in (CP-006)."""
    if count > 1:
        return (
            f"{count} accounts need your sign-in so Mighty can keep watching. "
            "You’ll sign in on each provider’s site — Mighty does not sign in as you."
        )
    return (
        "An account needs your sign-in so Mighty can keep watching. "
        "You’ll sign in on the provider’s site — Mighty does not sign in as you."
    )


def home_freshness_label(when: str) -> str:
    """Subtle story freshness — e.g. 'Updated just now' / 'Last verified 2 minutes ago'."""
    text = (when or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered.startswith("updated ") or lowered.startswith("last verified "):
        return text
    if lowered in {"just now", "a moment ago", "moments ago"}:
        return f"{HOME_FRESHNESS_UPDATED_PREFIX}{lowered}"
    return f"{HOME_FRESHNESS_PREFIX}{text}"


def home_opportunity_headline(provider_name: str, topic: str | None = None) -> str:
    if topic:
        return f"{topic[0].upper() + topic[1:]} available on {provider_name}"
    return f"Value waiting on {provider_name}"


def home_opportunity_body(provider_name: str, topic: str | None = None) -> str:
    if topic:
        return (
            f"Claiming this {topic} on {provider_name} could put real value "
            "back in your pocket. Open it when you're ready — we'll keep handling the rest."
        )
    return (
        f"We found something on {provider_name} that could put money or perks "
        "back in your pocket. Review it when you're ready — we'll keep handling the rest."
    )


def home_value_at_risk_headline(provider_name: str, topic: str | None = None) -> str:
    if topic:
        return f"{topic[0].upper() + topic[1:]} on {provider_name} won't wait"
    return f"Time-sensitive value on {provider_name}"


def home_value_at_risk_body(provider_name: str, topic: str | None = None) -> str:
    if topic:
        return (
            f"This {topic} on {provider_name} may expire soon. "
            "Review it now and we'll take care of the follow-through."
        )
    return (
        f"There's value on {provider_name} that may expire soon. "
        "Review it now and we'll take care of the follow-through."
    )


def home_attention_headline(attention_count: int) -> str:
    if attention_count == 1:
        return TOWER_HERO_NEEDS_YOU
    return f"{attention_count} accounts need your attention."


def home_attention_body(setup_incomplete_count: int = 0) -> str:
    if setup_incomplete_count > 0:
        n = setup_incomplete_count
        word = "account" if n == 1 else "accounts"
        return (
            f"Mighty is also verifying {n} {word} in the background. "
            f"{TOWER_ATTENTION_NEEDED}"
        )
    return TOWER_ATTENTION_NEEDED


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
