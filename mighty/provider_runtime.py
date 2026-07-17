"""Local Provider Runtime for Mighty.

This first implementation manages an isolated, persistent Chrome profile for
American Express and exposes a localhost control API. It keeps provider browser
work out of the user's normal Chrome profiles and never reads credentials.

Commands:
    python scripts/provider_runtime.py bootstrap amex
    python scripts/provider_runtime.py serve
    python scripts/provider_runtime.py verify amex
    python scripts/provider_runtime.py status
    python scripts/provider_runtime.py stop
    python scripts/provider_runtime.py keepalive-start amex --strategy SESSION_API
    python scripts/provider_runtime.py keepalive-status amex
    python scripts/provider_runtime.py keepalive-stop amex

Lifecycle:
    bootstrap opens a visible native Chrome window for login, verifies over CDP,
    then leaves that authenticated Chrome process running. serve attaches to the
    same CDP endpoint (or launches headless Chrome only when none is live).
    Repeated verify calls reuse the authenticated session without relaunching.
    While serve is running, a maintenance watcher extends Amex sessions when the
    genuine inactivity-expiration dialog appears.
    Developer-only keepalive trials can experiment with controlled background
    actions; they are not automatic production keepalive.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

AMEX_OVERVIEW_URL = "https://global.americanexpress.com/overview"
AMEX_LOGIN_URL = "https://www.americanexpress.com/en-us/account/login"
# Lightest read-only Amex session API already observed by Mighty probes/runtime.
AMEX_READ_USER_SESSION_URL = "https://functions.americanexpress.com/ReadUserSession.v1"

DEFAULT_ROOT = Path.home() / ".mighty" / "provider_runtime"
DEFAULT_PROFILE_DIR = DEFAULT_ROOT / "amex"
DEFAULT_STATE_PATH = DEFAULT_ROOT / "runtime_state.json"
DEFAULT_RESULT_PATH = DEFAULT_ROOT / "amex_last_result.json"
DEFAULT_KEEPALIVE_RESULT_PATH = DEFAULT_ROOT / "amex_keepalive_last_trial.json"
DEFAULT_LOG_PATH = DEFAULT_ROOT / "provider_runtime.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CDP_PORT = 9223

LOGIN_URL_TOKENS = ("/login", "/log-in", "/signin", "/sign-in", "/logon")
AUTHENTICATED_MARKERS = (
    "membership rewards",
    "account home",
    "recent activity",
    "manage account",
    "statement balance",
    "card ending",
    "available credit",
    "payment due",
)
LOGIN_MARKERS = (
    "sign in to your account",
    "log in to your account",
    "user id",
    "show password",
    "forgot password",
)
SESSION_API_MARKERS = ("ReadUserSession.v1", "UpdateUserSession.v1")

MAINTENANCE_POLL_SECONDS = 3.0
MAINTENANCE_DEBOUNCE_SECONDS = 30.0
MAINTENANCE_DIALOG_CLOSE_TIMEOUT_SECONDS = 10.0

KEEPALIVE_STRATEGIES = ("NONE", "SESSION_API", "PAGE_ACTIVITY", "OVERVIEW_RELOAD")
KEEPALIVE_DEFAULT_DURATION_SECONDS = 1800
KEEPALIVE_DEFAULT_INTERVAL_SECONDS = 60
KEEPALIVE_MAX_EVENTS = 200
KEEPALIVE_SENSITIVE_KEYS = (
    "cookie",
    "cookies",
    "authorization",
    "password",
    "credential",
    "credentials",
    "secret",
    "token",
    "body",
    "html",
    "query",
)

MAINTENANCE_RESULT_SESSION_EXTENDED = "SESSION_EXTENDED"
MAINTENANCE_RESULT_EXTENSION_CLICK_FAILED = "EXTENSION_CLICK_FAILED"
MAINTENANCE_RESULT_DIALOG_DID_NOT_CLOSE = "DIALOG_DID_NOT_CLOSE"
MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED = "SESSION_NOT_CONFIRMED"
MAINTENANCE_RESULT_WATCHER_ERROR = "WATCHER_ERROR"
MAINTENANCE_RESULT_NO_DIALOG = "NO_DIALOG"
MAINTENANCE_RESULT_IN_PROGRESS = "IN_PROGRESS"
MAINTENANCE_RESULT_DEBOUNCED = "DEBOUNCED"

EXPIRATION_HEADLINE_PHRASES = (
    "your session is about to expire",
    "session is about to expire",
    "your session will expire",
    "session will expire soon",
)

# Visible dialog inspection runs in the page; keep criteria conservative.
FIND_AMEX_EXPIRATION_DIALOG_JS = """
() => {
  const isVisible = (el) => {
    if (!el || !(el instanceof Element)) return false;
    const style = window.getComputedStyle(el);
    if (
      style.display === "none" ||
      style.visibility === "hidden" ||
      style.opacity === "0"
    ) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };

  const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim().toLowerCase();
  const headlines = [
    "your session is about to expire",
    "session is about to expire",
    "your session will expire",
    "session will expire soon",
  ];
  const hasHeadline = (text) => headlines.some((phrase) => text.includes(phrase));
  const hasExpirationLanguage = (text) => {
    const mentionsSession = text.includes("session");
    const mentionsExpire = text.includes("expir");
    const mentionsInactive =
      text.includes("inactiv") || text.includes("inactive") || text.includes("inactivity");
    return mentionsSession && (mentionsExpire || mentionsInactive);
  };

  const dialogSelector =
    '[role="dialog"], [aria-modal="true"], dialog, .modal, [class*="modal"], [class*="Modal"]';
  const dialogs = Array.from(document.querySelectorAll(dialogSelector)).filter(isVisible);

  const markContinue = (dialog, button) => {
    const token = "mighty-amex-continue-" + Date.now().toString(36);
    button.setAttribute("data-mighty-amex-continue", token);
    return {
      detected: true,
      continue_token: token,
      dialog_text: normalize(dialog.innerText || "").slice(0, 240),
    };
  };

  for (const dialog of dialogs) {
    const text = normalize(dialog.innerText || dialog.textContent || "");
    if (!hasHeadline(text) || !hasExpirationLanguage(text)) {
      continue;
    }
    const buttons = Array.from(
      dialog.querySelectorAll('button, [role="button"], input[type="button"], a')
    ).filter(isVisible);
    for (const button of buttons) {
      const label = normalize(
        button.innerText ||
          button.textContent ||
          button.getAttribute("value") ||
          button.getAttribute("aria-label") ||
          ""
      );
      if (label === "continue" || label.startsWith("continue ")) {
        return markContinue(dialog, button);
      }
    }
  }
  return { detected: false, continue_token: null, dialog_text: null };
}
"""

PAGE_ACTIVITY_JS = """
() => {
  try {
    if (typeof window.focus === "function") {
      window.focus();
    }
    const before = window.scrollY || 0;
    window.scrollBy(0, 24);
    window.scrollTo(0, before);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err && err.name ? err.name : "page_activity_error") };
  }
}
"""

SESSION_API_FETCH_JS = f"""
async () => {{
  const response = await fetch({AMEX_READ_USER_SESSION_URL!r}, {{
    method: "GET",
    credentials: "include",
    headers: {{ Accept: "application/json" }},
    redirect: "manual",
  }});
  return {{ status: response.status, ok: response.ok }};
}}
"""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    try:
        parts = urlsplit(raw_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return str(raw_url).split("?", 1)[0].split("#", 1)[0]


def is_login_url(url: str | None) -> bool:
    path = (urlsplit(url or "").path or "").lower()
    return any(token in path for token in LOGIN_URL_TOKENS)


def count_markers(text: str, markers: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for marker in markers if marker in lowered)


def chrome_binary() -> Path:
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("Google Chrome executable was not found in /Applications")


def profile_processes(profile_dir: Path) -> list[int]:
    """Return process IDs whose command line references this exact profile."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        check=True,
        capture_output=True,
        text=True,
    )
    needle = f"--user-data-dir={profile_dir}"
    found: list[int] = []
    for line in result.stdout.splitlines():
        if needle not in line:
            continue
        try:
            pid_text = line.strip().split(None, 1)[0]
            pid = int(pid_text)
        except (ValueError, IndexError):
            continue
        if pid != os.getpid():
            found.append(pid)
    return found


def terminate_profile_processes(profile_dir: Path, timeout: float = 15.0) -> None:
    """Terminate only Chrome processes launched with the dedicated profile."""
    pids = profile_processes(profile_dir)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = profile_processes(profile_dir)
        if not remaining:
            break
        time.sleep(0.25)

    for pid in profile_processes(profile_dir):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + 5
    while profile_processes(profile_dir) and time.monotonic() < deadline:
        time.sleep(0.25)


def wait_for_profile_release(profile_dir: Path, timeout: float = 20.0) -> bool:
    lock_names = ("SingletonLock", "SingletonCookie", "SingletonSocket")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        locked = any((profile_dir / name).exists() for name in lock_names)
        if not locked and not profile_processes(profile_dir):
            return True
        time.sleep(0.25)
    return False


def wait_for_cdp(port: int, timeout: float = 20.0) -> str:
    endpoint = f"http://127.0.0.1:{port}/json/version"
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(endpoint, timeout=1.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
            websocket_url = payload.get("webSocketDebuggerUrl")
            if websocket_url:
                return f"http://127.0.0.1:{port}"
        except Exception as exc:
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Chrome CDP endpoint did not become ready: {last_error}")


def cdp_endpoint_available(port: int, timeout: float = 1.0) -> str | None:
    """Return the CDP base URL if an endpoint is already listening, else None."""
    try:
        return wait_for_cdp(port, timeout=timeout)
    except RuntimeError:
        return None


def launch_native_chrome(
    *,
    profile_dir: Path,
    cdp_port: int,
    headless: bool,
    initial_url: str,
) -> subprocess.Popen[Any]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(chrome_binary()),
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={cdp_port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
    ]
    if headless:
        command.extend(["--headless=new", "--disable-gpu"])
    else:
        command.append("--new-window")
    command.append(initial_url)
    return subprocess.Popen(
        command,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@dataclass(frozen=True)
class VerificationResult:
    provider: str
    authentication_state: str
    reason: str
    observed_at: str
    final_url: str | None
    page_title: str | None
    login_url_detected: bool
    login_marker_count: int
    authenticated_marker_count: int
    session_api_200_count: int
    session_api_denied_count: int
    runtime_error: str | None = None


def classify_amex(
    *,
    final_url: str | None,
    body_text: str,
    session_api_statuses: list[int],
    runtime_error: str | None,
) -> tuple[str, str]:
    if runtime_error:
        return "LOGIN_UNKNOWN", runtime_error
    if any(status == 200 for status in session_api_statuses):
        return "SIGNED_IN", "Amex session API returned 200"
    if any(status in {401, 403} for status in session_api_statuses):
        return "SIGNED_OUT", "Amex session API denied access"
    if is_login_url(final_url):
        return "SIGNED_OUT", "Amex login page detected"
    auth_hits = count_markers(body_text, AUTHENTICATED_MARKERS)
    login_hits = count_markers(body_text, LOGIN_MARKERS)
    if auth_hits >= 2 and login_hits == 0:
        return "SIGNED_IN", "Authenticated Amex account page observed"
    if login_hits >= 2 and auth_hits == 0:
        return "SIGNED_OUT", "Amex login form observed"
    return "LOGIN_UNKNOWN", "Verification completed without definitive evidence"


def expiration_dialog_criteria_met(dialog_text: str, *, has_continue_button: bool) -> bool:
    """Return True only when conservative Amex expiration-dialog criteria match."""
    if not has_continue_button:
        return False
    lowered = " ".join((dialog_text or "").lower().split())
    has_headline = any(phrase in lowered for phrase in EXPIRATION_HEADLINE_PHRASES)
    mentions_session = "session" in lowered
    mentions_expire = "expir" in lowered
    mentions_inactive = "inactiv" in lowered
    has_expiration_language = mentions_session and (mentions_expire or mentions_inactive)
    return has_headline and has_expiration_language


def select_amex_page(
    context: BrowserContext,
    *,
    create_if_missing: bool = False,
) -> Page | None:
    """Prefer an existing americanexpress.com page; optionally create for verify."""
    for page in context.pages:
        try:
            host = (urlsplit(page.url).hostname or "").lower()
        except Exception:
            continue
        if host == "americanexpress.com" or host.endswith(".americanexpress.com"):
            return page
    if not create_if_missing:
        return None
    if context.pages:
        return context.pages[0]
    return context.new_page()


def inspect_amex_expiration_dialog(page: Page) -> dict[str, Any]:
    """Inspect the page for a genuine Amex inactivity-expiration dialog."""
    try:
        payload = page.evaluate(FIND_AMEX_EXPIRATION_DIALOG_JS)
    except Exception:
        return {"detected": False, "continue_token": None, "dialog_text": None}
    if not isinstance(payload, dict):
        return {"detected": False, "continue_token": None, "dialog_text": None}
    detected = bool(payload.get("detected"))
    dialog_text = payload.get("dialog_text") or ""
    continue_token = payload.get("continue_token")
    if detected and not expiration_dialog_criteria_met(
        str(dialog_text),
        has_continue_button=bool(continue_token),
    ):
        return {"detected": False, "continue_token": None, "dialog_text": None}
    return {
        "detected": detected,
        "continue_token": continue_token,
        "dialog_text": dialog_text if detected else None,
    }


def click_expiration_continue(page: Page, continue_token: str) -> bool:
    """Click the Continue button marked inside the validated expiration dialog."""
    locator = page.locator(f'[data-mighty-amex-continue="{continue_token}"]')
    try:
        if locator.count() < 1 or not locator.first.is_visible():
            return False
        locator.first.click(timeout=5_000)
        return True
    except Exception:
        return False


def wait_for_expiration_dialog_close(page: Page, timeout_seconds: float) -> bool:
    """Wait until the validated expiration dialog is no longer detected."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        info = inspect_amex_expiration_dialog(page)
        if not info.get("detected"):
            return True
        page.wait_for_timeout(250)
    return False


@dataclass(frozen=True)
class MaintenanceOutcome:
    result: str
    observed_at: str
    dialog_detected: bool
    verification_state: str | None = None
    reason: str | None = None
    runtime_error: str | None = None


def dismiss_amex_expiration_dialog(page: Page) -> MaintenanceOutcome | None:
    """Detect and dismiss the expiration dialog.

    Returns None when Continue was clicked and the dialog closed, so the caller
    should run canonical verification. Otherwise returns a terminal outcome.
    """
    observed_at = iso_now()
    info = inspect_amex_expiration_dialog(page)
    if not info.get("detected") or not info.get("continue_token"):
        return MaintenanceOutcome(
            result=MAINTENANCE_RESULT_NO_DIALOG,
            observed_at=observed_at,
            dialog_detected=False,
            reason="No Amex expiration dialog detected",
        )

    print("[Mighty Maintenance] Amex expiration dialog detected")
    print("[Mighty Maintenance] maintenance_started")
    if not click_expiration_continue(page, str(info["continue_token"])):
        print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_EXTENSION_CLICK_FAILED}")
        return MaintenanceOutcome(
            result=MAINTENANCE_RESULT_EXTENSION_CLICK_FAILED,
            observed_at=observed_at,
            dialog_detected=True,
            reason="Continue click failed inside expiration dialog",
        )
    print("[Mighty Maintenance] Continue clicked")

    if not wait_for_expiration_dialog_close(
        page,
        MAINTENANCE_DIALOG_CLOSE_TIMEOUT_SECONDS,
    ):
        print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_DIALOG_DID_NOT_CLOSE}")
        return MaintenanceOutcome(
            result=MAINTENANCE_RESULT_DIALOG_DID_NOT_CLOSE,
            observed_at=observed_at,
            dialog_detected=True,
            reason="Expiration dialog remained visible after Continue",
        )
    return None


def confirm_session_extended(verification: VerificationResult) -> MaintenanceOutcome:
    """Map canonical verification onto a maintenance outcome."""
    auth_state = verification.authentication_state
    if auth_state == "SIGNED_IN":
        print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_SESSION_EXTENDED}")
        return MaintenanceOutcome(
            result=MAINTENANCE_RESULT_SESSION_EXTENDED,
            observed_at=iso_now(),
            dialog_detected=True,
            verification_state=auth_state,
            reason="Continue clicked and verification returned SIGNED_IN",
        )

    print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED}")
    return MaintenanceOutcome(
        result=MAINTENANCE_RESULT_SESSION_NOT_CONFIRMED,
        observed_at=iso_now(),
        dialog_detected=True,
        verification_state=auth_state,
        reason="Continue clicked but verification did not return SIGNED_IN",
    )


def extend_amex_session_on_page(
    page: Page,
    *,
    verify_fn: Any,
) -> MaintenanceOutcome:
    """Click Continue on a validated dialog and confirm SIGNED_IN via verify_fn."""
    early = dismiss_amex_expiration_dialog(page)
    if early is not None:
        return early
    return confirm_session_extended(verify_fn())


@dataclass
class KeepaliveActionResult:
    ok: bool
    result: str
    response_status: int | None = None
    error: str | None = None


def inspect_amex_page_signals(page: Page) -> dict[str, Any]:
    """Lightweight page inspection without navigation or Continue clicks."""
    dialog = inspect_amex_expiration_dialog(page)
    final_url = sanitize_url(page.url)
    login_url_detected = is_login_url(final_url)
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3_000)
    except Exception:
        body_text = ""
    auth_hits = count_markers(body_text, AUTHENTICATED_MARKERS)
    login_hits = count_markers(body_text, LOGIN_MARKERS)
    if login_url_detected or (login_hits >= 2 and auth_hits == 0):
        auth_state = "SIGNED_OUT"
    elif auth_hits >= 2 and login_hits == 0:
        auth_state = "SIGNED_IN"
    else:
        auth_state = "LOGIN_UNKNOWN"
    return {
        "authentication_state": auth_state,
        "expiration_dialog_detected": bool(dialog.get("detected")),
        "login_page_detected": bool(login_url_detected or login_hits >= 2),
        "final_url": final_url,
    }


def perform_keepalive_action(page: Page, strategy: str) -> KeepaliveActionResult:
    """Dispatch one strategy action on the existing Amex page."""
    if strategy == "NONE":
        return KeepaliveActionResult(ok=True, result="skipped")
    if strategy == "SESSION_API":
        try:
            payload = page.evaluate(SESSION_API_FETCH_JS)
        except Exception as exc:
            return KeepaliveActionResult(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(payload, dict):
            return KeepaliveActionResult(ok=False, result="failure", error="invalid_session_api_payload")
        status = payload.get("status")
        status_int = int(status) if isinstance(status, int) else None
        ok = bool(payload.get("ok")) or status_int == 200
        return KeepaliveActionResult(
            ok=ok,
            result="success" if ok else "failure",
            response_status=status_int,
        )
    if strategy == "PAGE_ACTIVITY":
        try:
            payload = page.evaluate(PAGE_ACTIVITY_JS)
        except Exception as exc:
            return KeepaliveActionResult(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
            )
        ok = bool(isinstance(payload, dict) and payload.get("ok"))
        return KeepaliveActionResult(
            ok=ok,
            result="success" if ok else "failure",
            error=None if ok else "page_activity_failed",
        )
    if strategy == "OVERVIEW_RELOAD":
        try:
            page.goto(AMEX_OVERVIEW_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)
            return KeepaliveActionResult(ok=True, result="success")
        except Exception as exc:
            return KeepaliveActionResult(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
            )
    return KeepaliveActionResult(ok=False, result="failure", error=f"unknown_strategy:{strategy}")


def sanitize_keepalive_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, sanitized keepalive event (no secrets or bodies)."""
    allowed = {
        "timestamp",
        "event_type",
        "strategy",
        "action_result",
        "response_status",
        "authentication_state",
        "expiration_dialog_detected",
        "login_page_detected",
    }
    cleaned: dict[str, Any] = {}
    for key, value in event.items():
        lowered = key.lower()
        if key not in allowed:
            continue
        if any(token in lowered for token in KEEPALIVE_SENSITIVE_KEYS):
            continue
        if isinstance(value, str) and len(value) > 240:
            value = value[:240]
        cleaned[key] = value
    return cleaned


def verify_amex_over_cdp(cdp_url: str, result_path: Path) -> VerificationResult:
    runtime_error: str | None = None
    session_api_statuses: list[int] = []
    final_url: str | None = None
    title: str | None = None
    body_text = ""

    with sync_playwright() as playwright:
        # Attach only. Do not call browser.close() — that can terminate the
        # native Chrome process. Leaving the Playwright context disconnects
        # the client while Chrome stays alive.
        browser: Browser = playwright.chromium.connect_over_cdp(cdp_url)
        if browser.contexts:
            context: BrowserContext = browser.contexts[0]
        else:
            raise RuntimeError("Chrome exposed no persistent browser context")

        page = select_amex_page(context, create_if_missing=True)
        if page is None:
            raise RuntimeError("Chrome exposed no page for Amex verification")

        def on_response(response: Any) -> None:
            if any(marker in response.url for marker in SESSION_API_MARKERS):
                session_api_statuses.append(response.status)

        page.on("response", on_response)
        try:
            page.goto(
                AMEX_OVERVIEW_URL,
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_timeout(5_000)
        except Exception as exc:
            runtime_error = f"navigation_error: {type(exc).__name__}: {exc}"

        final_url = sanitize_url(page.url)
        try:
            title = page.title()
        except Exception:
            pass
        try:
            body_text = page.locator("body").inner_text(timeout=3_000)
        except Exception:
            pass

    state, reason = classify_amex(
        final_url=final_url,
        body_text=body_text,
        session_api_statuses=session_api_statuses,
        runtime_error=runtime_error,
    )
    result = VerificationResult(
        provider="amex",
        authentication_state=state,
        reason=reason,
        observed_at=iso_now(),
        final_url=final_url,
        page_title=title,
        login_url_detected=is_login_url(final_url),
        login_marker_count=count_markers(body_text, LOGIN_MARKERS),
        authenticated_marker_count=count_markers(body_text, AUTHENTICATED_MARKERS),
        session_api_200_count=sum(1 for status in session_api_statuses if status == 200),
        session_api_denied_count=sum(
            1 for status in session_api_statuses if status in {401, 403}
        ),
        runtime_error=runtime_error,
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    return result


class ProviderRuntime:
    def __init__(
        self,
        *,
        root: Path,
        cdp_port: int,
        state_path: Path,
        result_path: Path,
        keepalive_result_path: Path | None = None,
    ) -> None:
        self.root = root
        self.profile_dir = root / "amex"
        self.cdp_port = cdp_port
        self.state_path = state_path
        self.result_path = result_path
        self.keepalive_result_path = keepalive_result_path or (root / "amex_keepalive_last_trial.json")
        self.chrome_process: subprocess.Popen[Any] | None = None
        self.cdp_url: str | None = None
        self.lock = threading.RLock()
        self.started_at = iso_now()
        self.last_result: VerificationResult | None = None

        self.maintenance_running = False
        self.last_maintenance_attempt_at: str | None = None
        self.last_maintenance_result: str | None = None
        self.last_session_extended_at: str | None = None
        self.maintenance_attempt_count = 0
        self.maintenance_success_count = 0

        self._maintenance_stop = threading.Event()
        self._maintenance_thread: threading.Thread | None = None
        self._maintenance_attempt_lock = threading.Lock()
        self._last_maintenance_attempt_mono = 0.0
        self._last_dialog_fingerprint: str | None = None

        self.keepalive_trial_running = False
        self.keepalive_trial_id: str | None = None
        self.keepalive_strategy: str | None = None
        self.keepalive_started_at: str | None = None
        self.keepalive_completed_at: str | None = None
        self.keepalive_duration_seconds: int | None = None
        self.keepalive_interval_seconds: int | None = None
        self.keepalive_action_count = 0
        self.keepalive_action_success_count = 0
        self.keepalive_action_failure_count = 0
        self.keepalive_last_action_at: str | None = None
        self.keepalive_last_action_result: str | None = None
        self.keepalive_expiration_dialog_seen = False
        self.keepalive_logged_out = False
        self.keepalive_final_authentication_state: str | None = None
        self.keepalive_final_reason: str | None = None
        self.keepalive_events: list[dict[str, Any]] = []
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._keepalive_deadline_mono: float | None = None
        self._shutting_down = False

    def matching_chrome_pids(self) -> list[int]:
        """PIDs whose command line uses the exact dedicated Amex profile."""
        return profile_processes(self.profile_dir)

    def primary_chrome_pid(self) -> int | None:
        pids = self.matching_chrome_pids()
        if not pids:
            return None
        if self.chrome_process is not None and self.chrome_process.pid in pids:
            return self.chrome_process.pid
        return min(pids)

    def _maintenance_fields(self) -> dict[str, Any]:
        return {
            "maintenance_running": self.maintenance_running,
            "last_maintenance_attempt_at": self.last_maintenance_attempt_at,
            "last_maintenance_result": self.last_maintenance_result,
            "last_session_extended_at": self.last_session_extended_at,
            "maintenance_attempt_count": self.maintenance_attempt_count,
            "maintenance_success_count": self.maintenance_success_count,
        }

    def _keepalive_fields(self) -> dict[str, Any]:
        kept_signed_in = (
            self.keepalive_final_authentication_state == "SIGNED_IN"
            and self.keepalive_final_reason == "duration_completed"
            and not self.keepalive_logged_out
        )
        return {
            "keepalive_trial_running": self.keepalive_trial_running,
            "keepalive_trial_id": self.keepalive_trial_id,
            "keepalive_strategy": self.keepalive_strategy,
            "keepalive_started_at": self.keepalive_started_at,
            "keepalive_completed_at": self.keepalive_completed_at,
            "keepalive_duration_seconds": self.keepalive_duration_seconds,
            "keepalive_interval_seconds": self.keepalive_interval_seconds,
            "keepalive_action_count": self.keepalive_action_count,
            "keepalive_action_success_count": self.keepalive_action_success_count,
            "keepalive_action_failure_count": self.keepalive_action_failure_count,
            "keepalive_last_action_at": self.keepalive_last_action_at,
            "keepalive_last_action_result": self.keepalive_last_action_result,
            "keepalive_expiration_dialog_seen": self.keepalive_expiration_dialog_seen,
            "keepalive_logged_out": self.keepalive_logged_out,
            "keepalive_final_authentication_state": self.keepalive_final_authentication_state,
            "keepalive_final_reason": self.keepalive_final_reason,
            "keepalive_kept_signed_in": kept_signed_in if self.keepalive_completed_at else None,
            "keepalive_events": list(self.keepalive_events),
        }

    def write_state(self) -> None:
        payload = {
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": iso_now(),
            "cdp_port": self.cdp_port,
            "cdp_url": self.cdp_url,
            "chrome_pid": self.primary_chrome_pid(),
            "chrome_running": bool(self.matching_chrome_pids()),
            "profile_dir": str(self.profile_dir),
            "last_result": asdict(self.last_result) if self.last_result else None,
            **self._maintenance_fields(),
            **self._keepalive_fields(),
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _persist_keepalive_result(self) -> None:
        payload = {
            "observed_at": iso_now(),
            **self._keepalive_fields(),
        }
        self.keepalive_result_path.parent.mkdir(parents=True, exist_ok=True)
        self.keepalive_result_path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )

    def start(self) -> None:
        with self.lock:
            existing = cdp_endpoint_available(self.cdp_port)
            if existing is not None:
                # Attach to the authenticated Chrome left running by bootstrap.
                self.chrome_process = None
                self.cdp_url = existing
                self.write_state()
                return

            terminate_profile_processes(self.profile_dir)
            if not wait_for_profile_release(self.profile_dir):
                raise RuntimeError("Dedicated Amex profile lock was not released")
            self.chrome_process = launch_native_chrome(
                profile_dir=self.profile_dir,
                cdp_port=self.cdp_port,
                headless=True,
                initial_url="about:blank",
            )
            self.cdp_url = wait_for_cdp(self.cdp_port)
            self.write_state()

    def start_maintenance_watcher(self) -> None:
        """Start the Amex session-maintenance daemon once for this runtime."""
        with self.lock:
            if self._maintenance_thread is not None and self._maintenance_thread.is_alive():
                return
            self._maintenance_stop.clear()
            self.maintenance_running = True
            self._maintenance_thread = threading.Thread(
                target=self._maintenance_watcher_loop,
                name="amex-maintenance-watcher",
                daemon=True,
            )
            self._maintenance_thread.start()
            self.write_state()

    def stop_maintenance_watcher(self) -> None:
        self._maintenance_stop.set()
        thread = self._maintenance_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        with self.lock:
            self.maintenance_running = False
            self._maintenance_thread = None
            self.write_state()

    def _maintenance_watcher_loop(self) -> None:
        while not self._maintenance_stop.is_set():
            try:
                self.run_maintenance_once(force=False)
            except Exception as exc:
                self._record_maintenance_outcome(
                    MaintenanceOutcome(
                        result=MAINTENANCE_RESULT_WATCHER_ERROR,
                        observed_at=iso_now(),
                        dialog_detected=False,
                        runtime_error=f"{type(exc).__name__}: {exc}",
                        reason="Watcher loop error",
                    )
                )
                print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_WATCHER_ERROR}")
            self._maintenance_stop.wait(MAINTENANCE_POLL_SECONDS)

    def _record_maintenance_outcome(self, outcome: MaintenanceOutcome) -> None:
        with self.lock:
            if outcome.result in {
                MAINTENANCE_RESULT_NO_DIALOG,
                MAINTENANCE_RESULT_IN_PROGRESS,
                MAINTENANCE_RESULT_DEBOUNCED,
            }:
                return
            self.last_maintenance_attempt_at = outcome.observed_at
            self.last_maintenance_result = outcome.result
            self.maintenance_attempt_count += 1
            if outcome.result == MAINTENANCE_RESULT_SESSION_EXTENDED:
                self.maintenance_success_count += 1
                self.last_session_extended_at = outcome.observed_at
            self.write_state()

    def run_maintenance_once(self, *, force: bool = False) -> dict[str, Any]:
        """Run one maintenance inspection. Does not start a second watcher."""
        if not self._maintenance_attempt_lock.acquire(blocking=False):
            return {
                "ok": True,
                "result": MAINTENANCE_RESULT_IN_PROGRESS,
                "dialog_detected": False,
                "reason": "A maintenance attempt is already in progress",
                **self._maintenance_fields(),
            }

        try:
            if not force:
                elapsed = time.monotonic() - self._last_maintenance_attempt_mono
                if (
                    self._last_maintenance_attempt_mono > 0
                    and elapsed < MAINTENANCE_DEBOUNCE_SECONDS
                    and self._last_dialog_fingerprint is not None
                ):
                    return {
                        "ok": True,
                        "result": MAINTENANCE_RESULT_DEBOUNCED,
                        "dialog_detected": False,
                        "reason": "Debounced recent maintenance attempt",
                        **self._maintenance_fields(),
                    }

            # Hold the runtime lock for all CDP/page work so verify cannot race.
            with self.lock:
                if not self.cdp_url:
                    raise RuntimeError("Provider runtime is not started")
                outcome = self._inspect_and_extend_session(self.cdp_url)

            if outcome.result == MAINTENANCE_RESULT_NO_DIALOG:
                return {
                    "ok": True,
                    "result": outcome.result,
                    "dialog_detected": False,
                    "reason": outcome.reason,
                    **self._maintenance_fields(),
                }

            self._last_maintenance_attempt_mono = time.monotonic()
            if outcome.dialog_detected:
                self._last_dialog_fingerprint = outcome.result
            self._record_maintenance_outcome(outcome)
            return {
                "ok": True,
                "result": outcome.result,
                "dialog_detected": outcome.dialog_detected,
                "verification_state": outcome.verification_state,
                "reason": outcome.reason,
                "runtime_error": outcome.runtime_error,
                **self._maintenance_fields(),
            }
        except Exception as exc:
            outcome = MaintenanceOutcome(
                result=MAINTENANCE_RESULT_WATCHER_ERROR,
                observed_at=iso_now(),
                dialog_detected=False,
                runtime_error=f"{type(exc).__name__}: {exc}",
                reason="Maintenance inspection failed",
            )
            print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_WATCHER_ERROR}")
            self._record_maintenance_outcome(outcome)
            return {
                "ok": False,
                "result": outcome.result,
                "dialog_detected": False,
                "authentication_state": None,
                "reason": outcome.reason,
                "runtime_error": outcome.runtime_error,
                **self._maintenance_fields(),
            }
        finally:
            self._maintenance_attempt_lock.release()

    def _inspect_and_extend_session(self, cdp_url: str) -> MaintenanceOutcome:
        """Inspect/dismiss dialog, then verify on a fresh CDP attach (no nesting)."""
        observation_only = self.keepalive_trial_running
        try:
            with sync_playwright() as playwright:
                browser: Browser = playwright.chromium.connect_over_cdp(cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                page = select_amex_page(context, create_if_missing=False)
                if page is None:
                    return MaintenanceOutcome(
                        result=MAINTENANCE_RESULT_NO_DIALOG,
                        observed_at=iso_now(),
                        dialog_detected=False,
                        reason="No existing americanexpress.com page to inspect",
                    )
                if observation_only:
                    info = inspect_amex_expiration_dialog(page)
                    detected = bool(info.get("detected"))
                    if detected:
                        self._note_keepalive_expiration_dialog(
                            source="maintenance_observation",
                        )
                    return MaintenanceOutcome(
                        result=MAINTENANCE_RESULT_NO_DIALOG,
                        observed_at=iso_now(),
                        dialog_detected=detected,
                        reason=(
                            "Observation-only during keepalive trial; Continue not clicked"
                        ),
                    )
                early = dismiss_amex_expiration_dialog(page)
                if early is not None:
                    return early
        except Exception as exc:
            print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_WATCHER_ERROR}")
            return MaintenanceOutcome(
                result=MAINTENANCE_RESULT_WATCHER_ERROR,
                observed_at=iso_now(),
                dialog_detected=False,
                runtime_error=f"{type(exc).__name__}: {exc}",
                reason="Maintenance page operations failed",
            )

        try:
            verification = verify_amex_over_cdp(cdp_url, self.result_path)
            self.last_result = verification
            self.write_state()
            return confirm_session_extended(verification)
        except Exception as exc:
            print(f"[Mighty Maintenance] {MAINTENANCE_RESULT_WATCHER_ERROR}")
            return MaintenanceOutcome(
                result=MAINTENANCE_RESULT_WATCHER_ERROR,
                observed_at=iso_now(),
                dialog_detected=True,
                runtime_error=f"{type(exc).__name__}: {exc}",
                reason="Post-extension verification failed",
            )

    def verify(self, provider: str) -> VerificationResult:
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            self.last_result = verify_amex_over_cdp(self.cdp_url, self.result_path)
            self.write_state()
            return self.last_result

    def _append_keepalive_event(self, event: dict[str, Any]) -> None:
        cleaned = sanitize_keepalive_event(event)
        self.keepalive_events.append(cleaned)
        if len(self.keepalive_events) > KEEPALIVE_MAX_EVENTS:
            self.keepalive_events = self.keepalive_events[-KEEPALIVE_MAX_EVENTS:]

    def _note_keepalive_expiration_dialog(self, *, source: str) -> None:
        if self.keepalive_expiration_dialog_seen:
            return
        self.keepalive_expiration_dialog_seen = True
        self._append_keepalive_event(
            {
                "timestamp": iso_now(),
                "event_type": "expiration_dialog",
                "strategy": self.keepalive_strategy,
                "action_result": None,
                "response_status": None,
                "authentication_state": None,
                "expiration_dialog_detected": True,
                "login_page_detected": False,
            }
        )
        print(f"[Mighty Keepalive] expiration dialog observed ({source})")

    def keepalive_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                **self._keepalive_fields(),
            }

    def start_keepalive_trial(
        self,
        provider: str,
        *,
        strategy: str,
        duration_seconds: int = KEEPALIVE_DEFAULT_DURATION_SECONDS,
        interval_seconds: int = KEEPALIVE_DEFAULT_INTERVAL_SECONDS,
    ) -> dict[str, Any]:
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        strategy = (strategy or "").strip().upper()
        if strategy not in KEEPALIVE_STRATEGIES:
            raise ValueError(
                f"Unsupported keepalive strategy: {strategy}. "
                f"Expected one of {', '.join(KEEPALIVE_STRATEGIES)}"
            )
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")

        with self.lock:
            if self.keepalive_trial_running:
                return {
                    "ok": False,
                    "error": "keepalive_trial_already_running",
                    "reason": "Only one keepalive trial may run at a time",
                    **self._keepalive_fields(),
                }
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")

            verification = verify_amex_over_cdp(self.cdp_url, self.result_path)
            self.last_result = verification
            if verification.authentication_state != "SIGNED_IN":
                return {
                    "ok": False,
                    "error": "not_signed_in",
                    "reason": "Keepalive trial requires SIGNED_IN before start",
                    "authentication_state": verification.authentication_state,
                    **self._keepalive_fields(),
                }

            self._keepalive_stop.clear()
            self.keepalive_trial_running = True
            self.keepalive_trial_id = str(uuid.uuid4())
            self.keepalive_strategy = strategy
            self.keepalive_started_at = iso_now()
            self.keepalive_completed_at = None
            self.keepalive_duration_seconds = int(duration_seconds)
            self.keepalive_interval_seconds = int(interval_seconds)
            self.keepalive_action_count = 0
            self.keepalive_action_success_count = 0
            self.keepalive_action_failure_count = 0
            self.keepalive_last_action_at = None
            self.keepalive_last_action_result = None
            self.keepalive_expiration_dialog_seen = False
            self.keepalive_logged_out = False
            self.keepalive_final_authentication_state = None
            self.keepalive_final_reason = None
            self.keepalive_events = []
            self._keepalive_deadline_mono = time.monotonic() + float(duration_seconds)
            self._append_keepalive_event(
                {
                    "timestamp": self.keepalive_started_at,
                    "event_type": "trial_started",
                    "strategy": strategy,
                    "action_result": None,
                    "response_status": None,
                    "authentication_state": verification.authentication_state,
                    "expiration_dialog_detected": False,
                    "login_page_detected": False,
                }
            )
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_trial_loop,
                name="amex-keepalive-trial",
                daemon=True,
            )
            self._keepalive_thread.start()
            self.write_state()
            print(
                f"[Mighty Keepalive] trial {self.keepalive_trial_id} started "
                f"strategy={strategy} duration={duration_seconds}s "
                f"interval={interval_seconds}s"
            )
            return {
                "ok": True,
                "trial_id": self.keepalive_trial_id,
                **self._keepalive_fields(),
            }

    def stop_keepalive_trial(self, *, reason: str = "manually_stopped") -> dict[str, Any]:
        with self.lock:
            if not self.keepalive_trial_running and self.keepalive_completed_at:
                return {"ok": True, **self._keepalive_fields()}
            if not self.keepalive_trial_running and not self.keepalive_trial_id:
                return {
                    "ok": False,
                    "error": "no_keepalive_trial",
                    "reason": "No keepalive trial is active or recorded",
                    **self._keepalive_fields(),
                }

        self._keepalive_stop.set()
        thread = self._keepalive_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(5.0, float(self.keepalive_interval_seconds or 5)))

        with self.lock:
            if self.keepalive_trial_running:
                # Thread did not terminalize; force a final result.
                self._finalize_keepalive_trial(reason=reason)
            return {"ok": True, **self._keepalive_fields()}

    def _keepalive_trial_loop(self) -> None:
        final_reason = "duration_completed"
        try:
            while not self._keepalive_stop.is_set() and not self._shutting_down:
                deadline = self._keepalive_deadline_mono or 0.0
                if time.monotonic() >= deadline:
                    final_reason = "duration_completed"
                    break

                try:
                    logged_out = self._keepalive_tick()
                except Exception as exc:
                    # Never convert keepalive exceptions into SIGNED_OUT.
                    with self.lock:
                        self._append_keepalive_event(
                            {
                                "timestamp": iso_now(),
                                "event_type": "action_error",
                                "strategy": self.keepalive_strategy,
                                "action_result": "failure",
                                "response_status": None,
                                "authentication_state": None,
                                "expiration_dialog_detected": False,
                                "login_page_detected": False,
                            }
                        )
                        self.keepalive_last_action_at = iso_now()
                        self.keepalive_last_action_result = "failure"
                        self.keepalive_action_failure_count += 1
                        self.write_state()
                    print(
                        f"[Mighty Keepalive] action error ignored for auth state: "
                        f"{type(exc).__name__}"
                    )
                    logged_out = False

                if logged_out:
                    final_reason = "logged_out"
                    break

                interval = float(self.keepalive_interval_seconds or KEEPALIVE_DEFAULT_INTERVAL_SECONDS)
                remaining = max(0.0, (self._keepalive_deadline_mono or 0.0) - time.monotonic())
                self._keepalive_stop.wait(min(interval, remaining if remaining > 0 else interval))

            if self._shutting_down:
                final_reason = "runtime_shutdown"
            elif self._keepalive_stop.is_set() and final_reason == "duration_completed":
                # Stop requested before natural completion.
                if self._keepalive_deadline_mono and time.monotonic() < self._keepalive_deadline_mono:
                    final_reason = "manually_stopped"
        finally:
            with self.lock:
                if self.keepalive_trial_running:
                    self._finalize_keepalive_trial(reason=final_reason)

    def _keepalive_tick(self) -> bool:
        """Run one inspection (+ optional action). Returns True if logged out."""
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            strategy = self.keepalive_strategy or "NONE"

            with sync_playwright() as playwright:
                browser: Browser = playwright.chromium.connect_over_cdp(cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                page = select_amex_page(context, create_if_missing=False)
                if page is None:
                    raise RuntimeError("No existing americanexpress.com page for keepalive")

                before = inspect_amex_page_signals(page)
                if before["expiration_dialog_detected"]:
                    self._note_keepalive_expiration_dialog(source="pre_action")
                if before["login_page_detected"] or before["authentication_state"] == "SIGNED_OUT":
                    self.keepalive_logged_out = True
                    self._append_keepalive_event(
                        {
                            "timestamp": iso_now(),
                            "event_type": "logged_out",
                            "strategy": strategy,
                            "action_result": None,
                            "response_status": None,
                            "authentication_state": before["authentication_state"],
                            "expiration_dialog_detected": before["expiration_dialog_detected"],
                            "login_page_detected": before["login_page_detected"],
                        }
                    )
                    self.write_state()
                    return True

                action_result: KeepaliveActionResult | None = None
                if strategy != "NONE":
                    action_result = perform_keepalive_action(page, strategy)
                    self.keepalive_action_count += 1
                    self.keepalive_last_action_at = iso_now()
                    self.keepalive_last_action_result = action_result.result
                    if action_result.ok:
                        self.keepalive_action_success_count += 1
                    else:
                        self.keepalive_action_failure_count += 1
                    self._append_keepalive_event(
                        {
                            "timestamp": self.keepalive_last_action_at,
                            "event_type": "action",
                            "strategy": strategy,
                            "action_result": action_result.result,
                            "response_status": action_result.response_status,
                            "authentication_state": before["authentication_state"],
                            "expiration_dialog_detected": before["expiration_dialog_detected"],
                            "login_page_detected": before["login_page_detected"],
                        }
                    )

                after = inspect_amex_page_signals(page)
                if after["expiration_dialog_detected"]:
                    self._note_keepalive_expiration_dialog(source="post_action")
                self._append_keepalive_event(
                    {
                        "timestamp": iso_now(),
                        "event_type": "inspection",
                        "strategy": strategy,
                        "action_result": action_result.result if action_result else "skipped",
                        "response_status": action_result.response_status if action_result else None,
                        "authentication_state": after["authentication_state"],
                        "expiration_dialog_detected": after["expiration_dialog_detected"],
                        "login_page_detected": after["login_page_detected"],
                    }
                )
                if after["login_page_detected"] or after["authentication_state"] == "SIGNED_OUT":
                    self.keepalive_logged_out = True
                    self._append_keepalive_event(
                        {
                            "timestamp": iso_now(),
                            "event_type": "logged_out",
                            "strategy": strategy,
                            "action_result": action_result.result if action_result else None,
                            "response_status": action_result.response_status if action_result else None,
                            "authentication_state": after["authentication_state"],
                            "expiration_dialog_detected": after["expiration_dialog_detected"],
                            "login_page_detected": after["login_page_detected"],
                        }
                    )
                    self.write_state()
                    return True

                self.write_state()
                return False

    def _finalize_keepalive_trial(self, *, reason: str) -> None:
        """Terminalize the trial with canonical verification and persisted result."""
        final_state: str | None = None
        try:
            if self.cdp_url:
                verification = verify_amex_over_cdp(self.cdp_url, self.result_path)
                self.last_result = verification
                final_state = verification.authentication_state
                if final_state == "SIGNED_OUT":
                    self.keepalive_logged_out = True
        except Exception as exc:
            final_state = "LOGIN_UNKNOWN"
            self._append_keepalive_event(
                {
                    "timestamp": iso_now(),
                    "event_type": "final_verification_error",
                    "strategy": self.keepalive_strategy,
                    "action_result": "failure",
                    "response_status": None,
                    "authentication_state": final_state,
                    "expiration_dialog_detected": self.keepalive_expiration_dialog_seen,
                    "login_page_detected": False,
                }
            )
            print(
                f"[Mighty Keepalive] final verification error "
                f"(not treated as SIGNED_OUT): {type(exc).__name__}"
            )

        self.keepalive_final_authentication_state = final_state
        self.keepalive_final_reason = reason
        self.keepalive_completed_at = iso_now()
        self.keepalive_trial_running = False
        self._append_keepalive_event(
            {
                "timestamp": self.keepalive_completed_at,
                "event_type": "trial_completed",
                "strategy": self.keepalive_strategy,
                "action_result": None,
                "response_status": None,
                "authentication_state": final_state,
                "expiration_dialog_detected": self.keepalive_expiration_dialog_seen,
                "login_page_detected": bool(final_state == "SIGNED_OUT"),
            }
        )
        self._persist_keepalive_result()
        self.write_state()
        self._keepalive_thread = None
        print(
            f"[Mighty Keepalive] trial {self.keepalive_trial_id} completed "
            f"reason={reason} auth={final_state}"
        )

    def status(self) -> dict[str, Any]:
        with self.lock:
            pids = self.matching_chrome_pids()
            return {
                "ok": True,
                "runtime_pid": os.getpid(),
                "started_at": self.started_at,
                "chrome_pid": self.primary_chrome_pid(),
                "chrome_running": bool(pids),
                "cdp_url": self.cdp_url,
                "profile_dir": str(self.profile_dir),
                "last_result": asdict(self.last_result) if self.last_result else None,
                **self._maintenance_fields(),
                **self._keepalive_fields(),
            }

    def stop(self) -> None:
        self._shutting_down = True
        if self.keepalive_trial_running:
            self.stop_keepalive_trial(reason="runtime_shutdown")
        self.stop_maintenance_watcher()
        with self.lock:
            # Only terminate Chrome using the exact dedicated Mighty Amex profile.
            terminate_profile_processes(self.profile_dir)
            self.chrome_process = None
            self.cdp_url = None
            self.write_state()


class RuntimeHTTPServer(ThreadingHTTPServer):
    runtime: ProviderRuntime


class RuntimeHandler(BaseHTTPRequestHandler):
    server: RuntimeHTTPServer

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("Request body must be valid JSON")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/", "/health", "/status"}:
            self._send_json(HTTPStatus.OK, self.server.runtime.status())
            return
        if self.path == "/providers/amex/keepalive/status":
            self._send_json(HTTPStatus.OK, self.server.runtime.keepalive_status())
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/providers/amex/verify":
            try:
                result = self.server.runtime.verify("amex")
                self._send_json(HTTPStatus.OK, {"ok": True, "result": asdict(result)})
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                )
            return
        if self.path == "/providers/amex/maintenance/check":
            try:
                payload = self.server.runtime.run_maintenance_once(force=True)
                status = HTTPStatus.OK if payload.get("ok", True) else HTTPStatus.INTERNAL_SERVER_ERROR
                self._send_json(status, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "result": MAINTENANCE_RESULT_WATCHER_ERROR,
                        "error": str(exc),
                    },
                )
            return
        if self.path == "/providers/amex/keepalive/start":
            try:
                body = self._read_json_body()
                strategy = str(body.get("strategy") or "")
                duration_seconds = int(
                    body.get("duration_seconds", KEEPALIVE_DEFAULT_DURATION_SECONDS)
                )
                interval_seconds = int(
                    body.get("interval_seconds", KEEPALIVE_DEFAULT_INTERVAL_SECONDS)
                )
                payload = self.server.runtime.start_keepalive_trial(
                    "amex",
                    strategy=strategy,
                    duration_seconds=duration_seconds,
                    interval_seconds=interval_seconds,
                )
                status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.CONFLICT
                self._send_json(status, payload)
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": str(exc)},
                )
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                )
            return
        if self.path == "/providers/amex/keepalive/stop":
            try:
                payload = self.server.runtime.stop_keepalive_trial(reason="manually_stopped")
                status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.CONFLICT
                self._send_json(status, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                )
            return
        if self.path == "/shutdown":
            self._send_json(HTTPStatus.OK, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})


def bootstrap_amex(root: Path, cdp_port: int, result_path: Path) -> int:
    profile_dir = root / "amex"
    terminate_profile_processes(profile_dir)
    wait_for_profile_release(profile_dir)

    launch_native_chrome(
        profile_dir=profile_dir,
        cdp_port=cdp_port,
        headless=False,
        initial_url=AMEX_LOGIN_URL,
    )
    cdp_url = wait_for_cdp(cdp_port)
    print(
        "\nMighty opened an isolated Chrome window for American Express.\n"
        "Sign in normally and complete any MFA. Your other Chrome windows and\n"
        "profiles are not affected. When you can see your Amex account, return\n"
        "here and press Enter.\n"
    )
    input("Press Enter after Amex is authenticated: ")
    result = verify_amex_over_cdp(cdp_url, result_path)
    print(json.dumps(asdict(result), indent=2))

    if result.authentication_state != "SIGNED_IN":
        print(
            "\nThe dedicated browser did not produce definitive signed-in evidence.\n"
            "The authenticated Amex Chrome process has been left running for diagnosis.\n"
            "Close that window manually when finished, or run stop after serve attaches.",
            file=sys.stderr,
        )
        return 1

    print(
        "\nAmex authentication succeeded.\n"
        "The authenticated Amex Chrome process will remain running with CDP enabled.\n"
        "Do not close that browser window.\n"
        "\n"
        "In a second terminal, start the runtime so it can attach to this process:\n"
        "\n"
        "  .venv/bin/python scripts/provider_runtime.py serve\n"
    )
    return 0


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, method=method, headers=headers)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def run_server(args: argparse.Namespace) -> int:
    runtime = ProviderRuntime(
        root=args.root,
        cdp_port=args.cdp_port,
        state_path=args.state_path,
        result_path=args.result_path,
        keepalive_result_path=args.keepalive_result_path,
    )
    runtime.start()
    runtime.start_maintenance_watcher()
    server = RuntimeHTTPServer((args.host, args.port), RuntimeHandler)
    server.runtime = runtime

    def shutdown(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f"Mighty Provider Runtime listening at http://{args.host}:{args.port}")
    print(f"Amex CDP: {runtime.cdp_url}")
    if runtime.chrome_process is None:
        print("Attached to existing authenticated Amex Chrome over CDP.")
    else:
        print("Launched headless Amex Chrome (no live CDP endpoint was found).")
    print("Amex session maintenance watcher started.")
    try:
        server.serve_forever()
    finally:
        runtime.stop()
        server.server_close()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mighty local Provider Runtime")
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"Runtime data root (default: {DEFAULT_ROOT})",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT)
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
    )
    parser.add_argument(
        "--result-path",
        type=Path,
        default=DEFAULT_RESULT_PATH,
    )
    parser.add_argument(
        "--keepalive-result-path",
        type=Path,
        default=DEFAULT_KEEPALIVE_RESULT_PATH,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("provider", choices=("amex",))
    subparsers.add_parser("serve")
    verify = subparsers.add_parser("verify")
    verify.add_argument("provider", choices=("amex",))
    subparsers.add_parser("status")
    subparsers.add_parser("stop")

    keepalive_start = subparsers.add_parser(
        "keepalive-start",
        help="Start a developer-only Amex keepalive trial (experiment, not production)",
    )
    keepalive_start.add_argument("provider", choices=("amex",))
    keepalive_start.add_argument(
        "--strategy",
        required=True,
        choices=KEEPALIVE_STRATEGIES,
        help="Keepalive strategy to trial",
    )
    keepalive_start.add_argument(
        "--duration-seconds",
        type=int,
        default=KEEPALIVE_DEFAULT_DURATION_SECONDS,
        help=f"Trial duration (default: {KEEPALIVE_DEFAULT_DURATION_SECONDS})",
    )
    keepalive_start.add_argument(
        "--interval-seconds",
        type=int,
        default=KEEPALIVE_DEFAULT_INTERVAL_SECONDS,
        help=f"Action interval (default: {KEEPALIVE_DEFAULT_INTERVAL_SECONDS})",
    )

    keepalive_status = subparsers.add_parser("keepalive-status")
    keepalive_status.add_argument("provider", choices=("amex",))

    keepalive_stop = subparsers.add_parser("keepalive-stop")
    keepalive_stop.add_argument("provider", choices=("amex",))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.root = args.root.expanduser().resolve()
    args.state_path = args.state_path.expanduser().resolve()
    args.result_path = args.result_path.expanduser().resolve()
    args.keepalive_result_path = args.keepalive_result_path.expanduser().resolve()

    if args.command == "bootstrap":
        return bootstrap_amex(args.root, args.cdp_port, args.result_path)
    if args.command == "serve":
        return run_server(args)
    if args.command == "verify":
        payload = request_json(
            "POST",
            f"http://{args.host}:{args.port}/providers/{args.provider}/verify",
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    if args.command == "status":
        payload = request_json("GET", f"http://{args.host}:{args.port}/status")
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "stop":
        payload = request_json("POST", f"http://{args.host}:{args.port}/shutdown")
        print(json.dumps(payload, indent=2))
        return 0
    if args.command == "keepalive-start":
        payload = request_json(
            "POST",
            f"http://{args.host}:{args.port}/providers/{args.provider}/keepalive/start",
            {
                "strategy": args.strategy,
                "duration_seconds": args.duration_seconds,
                "interval_seconds": args.interval_seconds,
            },
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    if args.command == "keepalive-status":
        payload = request_json(
            "GET",
            f"http://{args.host}:{args.port}/providers/{args.provider}/keepalive/status",
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    if args.command == "keepalive-stop":
        payload = request_json(
            "POST",
            f"http://{args.host}:{args.port}/providers/{args.provider}/keepalive/stop",
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
