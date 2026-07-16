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

Lifecycle:
    bootstrap opens a visible native Chrome window for login, verifies over CDP,
    then leaves that authenticated Chrome process running. serve attaches to the
    same CDP endpoint (or launches headless Chrome only when none is live).
    Repeated verify calls reuse the authenticated session without relaunching.
    While serve is running, a maintenance watcher extends Amex sessions when the
    genuine inactivity-expiration dialog appears.
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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

AMEX_OVERVIEW_URL = "https://global.americanexpress.com/overview"
AMEX_LOGIN_URL = "https://www.americanexpress.com/en-us/account/login"

DEFAULT_ROOT = Path.home() / ".mighty" / "provider_runtime"
DEFAULT_PROFILE_DIR = DEFAULT_ROOT / "amex"
DEFAULT_STATE_PATH = DEFAULT_ROOT / "runtime_state.json"
DEFAULT_RESULT_PATH = DEFAULT_ROOT / "amex_last_result.json"
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
    ) -> None:
        self.root = root
        self.profile_dir = root / "amex"
        self.cdp_port = cdp_port
        self.state_path = state_path
        self.result_path = result_path
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
        }
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

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
            }

    def stop(self) -> None:
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

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/", "/health", "/status"}:
            self._send_json(HTTPStatus.OK, self.server.runtime.status())
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


def request_json(method: str, url: str) -> dict[str, Any]:
    from urllib.request import Request

    request = Request(url, method=method)
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def run_server(args: argparse.Namespace) -> int:
    runtime = ProviderRuntime(
        root=args.root,
        cdp_port=args.cdp_port,
        state_path=args.state_path,
        result_path=args.result_path,
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

    subparsers = parser.add_subparsers(dest="command", required=True)
    bootstrap = subparsers.add_parser("bootstrap")
    bootstrap.add_argument("provider", choices=("amex",))
    subparsers.add_parser("serve")
    verify = subparsers.add_parser("verify")
    verify.add_argument("provider", choices=("amex",))
    subparsers.add_parser("status")
    subparsers.add_parser("stop")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.root = args.root.expanduser().resolve()
    args.state_path = args.state_path.expanduser().resolve()
    args.result_path = args.result_path.expanduser().resolve()

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
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
