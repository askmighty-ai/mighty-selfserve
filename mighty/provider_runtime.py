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
    python scripts/provider_runtime.py keepalive-probe amex --strategy SESSION_API
    python scripts/provider_runtime.py keepalive-status amex
    python scripts/provider_runtime.py keepalive-stop amex
    python scripts/provider_runtime.py browser-inspect amex
    python scripts/provider_runtime.py browser-inspect-debug amex
    python scripts/provider_runtime.py browser-find-text amex "expire"
    python scripts/provider_runtime.py browser-watch-text amex
    python scripts/provider_runtime.py browser-record-expiration amex
    python scripts/provider_runtime.py browser-run-expiration-experiment amex
    python scripts/provider_runtime.py campaign amex
    python scripts/provider_runtime.py campaign amex --analyze
    python scripts/provider_runtime.py analyze-campaign <campaign-directory-or-zip>
    python scripts/provider_runtime.py browser-run-expiration-campaign amex
    python scripts/provider_runtime.py browser-open-latest-expiration-experiment amex
    python scripts/provider_runtime.py inspect-expiration-dialog amex

Lifecycle:
    bootstrap opens a visible native Chrome window for login, verifies over CDP,
    then leaves that authenticated Chrome process running. serve attaches to the
    same CDP endpoint (or launches headless Chrome only when none is live).
    Repeated verify calls reuse the authenticated session without relaunching.
    While serve is running, a maintenance watcher uses the Browser Inspector plus
    an Amex-specific classifier to extend sessions when the genuine
    inactivity-expiration dialog appears.
    Developer-only keepalive trials can experiment with controlled background
    actions; they are not automatic production keepalive.
"""

from __future__ import annotations

import argparse
import base64
import csv
import inspect
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
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
DEFAULT_DIAGNOSTICS_DIR = DEFAULT_ROOT / "diagnostics"
DEFAULT_LOG_PATH = DEFAULT_ROOT / "provider_runtime.log"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CDP_PORT = 9223

SOURCE_TYPE_DOM = "DOM"
SOURCE_TYPE_IFRAME = "IFRAME"
SOURCE_TYPE_SHADOW_DOM = "SHADOW_DOM"

AUTH_STATE_SOURCE_LATEST_CANONICAL = "LATEST_CANONICAL"
AUTH_STATE_SOURCE_FRESH_VERIFICATION = "FRESH_VERIFICATION"
AUTH_STATE_SOURCE_BROWSER_OBSERVATION = "BROWSER_OBSERVATION"
AUTH_STATE_SOURCE_NONE = "NONE"

IGNORED_PAGE_URL_PREFIXES = (
    "chrome://",
    "chrome-extension://",
    "devtools://",
    "edge://",
    "about:blank",
    "about:srcdoc",
)

AMEX_HOSTNAME_SUFFIXES = ("americanexpress.com",)
AMEX_PREFERRED_HOSTNAMES = ("global.americanexpress.com",)

INSPECTION_TEXT_MAX_CHARS = 300
LONG_DIGIT_RE = re.compile(r"\d{6,}")
MODAL_TEXT_KEYWORDS = (
    "session",
    "expire",
    "inactivity",
    "continue",
    "log out",
    "sign in",
    "verify",
    "challenge",
    "security",
    "authentication",
)

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
KEEPALIVE_MAX_ATTEMPTS = 500
KEEPALIVE_ATTEMPTS_FILENAME = "keepalive-attempts.jsonl"
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
EXPIRATION_LANGUAGE_TOKENS = ("expir", "inactiv")
LOGOUT_LANGUAGE_PHRASES = (
    "log out",
    "logout",
    "logged out",
    "sign out",
    "signed out",
    "sign-out",
    "log-out",
)
# Backward-compatible aliases used by older tests/call sites.
EXPIRATION_SNIPPET_KEYWORDS = MODAL_TEXT_KEYWORDS
EXPIRATION_SNIPPET_MAX_CHARS = INSPECTION_TEXT_MAX_CHARS
EXPIRATION_DIALOG_CONTAINER_SELECTORS = (
    '[role="dialog"]',
    '[aria-modal="true"]',
    "dialog",
    ".modal",
    '[class*="modal"]',
    '[class*="Modal"]',
    '[class*="dialog"]',
    '[class*="Dialog"]',
    '[class*="overlay"]',
    '[class*="Overlay"]',
    '[class*="drawer"]',
    '[class*="Drawer"]',
    '[class*="popover"]',
    '[class*="Popover"]',
    '[class*="popup"]',
    '[class*="Popup"]',
    '[class*="timeout"]',
    '[class*="Timeout"]',
    '[class*="session"]',
    '[class*="Session"]',
    '[class*="expire"]',
    '[class*="Expire"]',
    '[data-testid*="session"]',
    '[data-testid*="timeout"]',
    '[id*="session"]',
    '[id*="timeout"]',
)

# Browser Inspector candidates are collected via CDP DOM/Accessibility APIs.
# Amex monkey-patches eval, so Playwright frame.evaluate/page.evaluate cannot
# be used for live inspection. BROWSER_INSPECTOR_JS is retired from production.
BROWSER_INSPECTOR_JS = (
    "/* retired: Browser Inspector uses CDP DOM/AX, not in-page evaluate */"
)

# Backward-compatible aliases used by older call sites/tests.
INSPECT_EXPIRATION_DIALOG_IN_DOCUMENT_JS = BROWSER_INSPECTOR_JS
FIND_AMEX_EXPIRATION_DIALOG_JS = BROWSER_INSPECTOR_JS

# Retired: Amex monkey-patches eval, so page.evaluate/fetch-in-page fails with
# "eval is disabled". SESSION_API and PAGE_ACTIVITY now use non-evaluate paths
# (context.request + Playwright mouse wheel). Kept as documentation only.
PAGE_ACTIVITY_JS = (
    "/* retired: PAGE_ACTIVITY uses Playwright mouse.wheel, not evaluate */"
)
SESSION_API_FETCH_JS = (
    "/* retired: SESSION_API uses page.context.request.get, not evaluate */"
)


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


CDP_ATTACH_NO_TARGETS_MESSAGE = (
    "Unable to attach to the managed Amex browser.\n"
    "\n"
    "Browser websocket is alive, but there are no page targets.\n"
    "\n"
    "This usually indicates the managed Chrome session exited or is in an "
    "invalid state.\n"
    "\n"
    "Recommended recovery:\n"
    "1. provider_runtime.py stop\n"
    "2. provider_runtime.py bootstrap amex\n"
    "3. provider_runtime.py serve"
)

CDP_ATTACH_FAILED_MESSAGE = (
    "Unable to attach to the managed Amex browser.\n"
    "\n"
    "Playwright could not connect over CDP to the managed Chrome session.\n"
    "\n"
    "This usually indicates the managed Chrome session exited or is in an "
    "invalid state.\n"
    "\n"
    "Recommended recovery:\n"
    "1. provider_runtime.py stop\n"
    "2. provider_runtime.py bootstrap amex\n"
    "3. provider_runtime.py serve"
)


def cdp_http_base_url(cdp_url: str) -> str:
    """Normalize a CDP HTTP or websocket URL to an HTTP origin for /json/*."""
    parts = urlsplit(cdp_url)
    scheme = parts.scheme.lower()
    if scheme in {"ws", "wss"}:
        http_scheme = "https" if scheme == "wss" else "http"
        return f"{http_scheme}://{parts.netloc}"
    if scheme in {"http", "https"} and parts.netloc:
        return f"{scheme}://{parts.netloc}"
    return cdp_url.rstrip("/")


def fetch_cdp_json(url: str, *, timeout: float = 1.0) -> Any:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def playwright_supports_connect_over_cdp_no_defaults() -> bool:
    """True when the installed Playwright sync API accepts no_defaults=True."""
    try:
        from playwright.sync_api._generated import BrowserType

        return "no_defaults" in inspect.signature(BrowserType.connect_over_cdp).parameters
    except Exception:
        return False


def ensure_cdp_page_targets_available(cdp_url: str) -> None:
    """Raise a clear error when the browser WS is up but has no page targets.

    Soft-skips when CDP HTTP diagnostics are unreachable so callers can still
    attempt ``connect_over_cdp`` (and unit tests can mock attach without a live
    Chrome).
    """
    base = cdp_http_base_url(cdp_url)
    try:
        version_payload = fetch_cdp_json(f"{base}/json/version")
        targets_payload = fetch_cdp_json(f"{base}/json/list")
    except Exception:
        return

    if not isinstance(version_payload, dict):
        return
    websocket_url = version_payload.get("webSocketDebuggerUrl")
    if not websocket_url:
        return
    if not isinstance(targets_payload, list):
        return
    if len(targets_payload) == 0:
        raise RuntimeError(CDP_ATTACH_NO_TARGETS_MESSAGE)


def connect_chromium_over_cdp(playwright: Any, cdp_url: str) -> Browser:
    """Attach to an externally managed Chrome over CDP with safer defaults.

    Uses ``no_defaults=True`` when supported so Playwright skips applying
    ``Browser.setDownloadBehavior`` (and related overrides) to the existing
    persistent context. Surfaces zero-target CDP states with a recovery hint
    instead of a raw Playwright protocol stack.
    """
    ensure_cdp_page_targets_available(cdp_url)
    connect = playwright.chromium.connect_over_cdp
    kwargs: dict[str, Any] = {}
    if playwright_supports_connect_over_cdp_no_defaults():
        kwargs["no_defaults"] = True
    try:
        return connect(cdp_url, **kwargs)
    except Exception as exc:
        raise RuntimeError(CDP_ATTACH_FAILED_MESSAGE) from exc


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


@dataclass
class InspectionCandidate:
    source_type: str
    page_url: str | None
    frame_url: str | None
    tag_name: str | None
    role: str | None
    class_summary: str | None
    text_snippet: str | None
    visible_button_labels: list[str] = field(default_factory=list)
    visible_link_labels: list[str] = field(default_factory=list)
    bounding_box: dict[str, Any] | None = None
    viewport_coverage_ratio: float | None = None
    z_index: Any = None
    fixed_or_absolute: bool = False
    aria_modal: bool | None = None
    accessible_name: str | None = None
    detector_tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    host_tag_class_summary: str | None = None
    continue_token: str | None = None

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "page_url": self.page_url,
            "frame_url": self.frame_url,
            "tag_name": self.tag_name,
            "role": self.role,
            "class_summary": self.class_summary,
            "host_tag_class_summary": self.host_tag_class_summary,
            "text_snippet": self.text_snippet,
            "visible_button_labels": list(self.visible_button_labels),
            "visible_link_labels": list(self.visible_link_labels),
            "bounding_box": self.bounding_box,
            "viewport_coverage_ratio": self.viewport_coverage_ratio,
            "z_index": self.z_index,
            "fixed_or_absolute": self.fixed_or_absolute,
            "aria_modal": self.aria_modal,
            "accessible_name": self.accessible_name,
            "detector_tags": list(self.detector_tags),
            "errors": list(self.errors),
        }


@dataclass
class BrowserInspection:
    inspected_at: str
    selected_page_url: str | None
    page_count: int
    frame_count: int
    candidate_count: int
    candidates: list[InspectionCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    developer_diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_sanitized_dict(self) -> dict[str, Any]:
        return {
            "inspected_at": self.inspected_at,
            "selected_page_url": self.selected_page_url,
            "page_count": self.page_count,
            "frame_count": self.frame_count,
            "candidate_count": self.candidate_count,
            "candidates": [item.to_sanitized_dict() for item in self.candidates],
            "errors": list(self.errors),
            "screenshot_path": self.screenshot_path,
            "diagnostics": dict(self.diagnostics),
            # Full failure detail for developers only (includes tracebacks).
            "developer_diagnostics": dict(self.developer_diagnostics),
        }


def redact_long_digit_sequences(text: str) -> str:
    return LONG_DIGIT_RE.sub("[REDACTED_NUMBER]", text)


def sanitize_inspection_snippet(text: str | None) -> str | None:
    """Bound and redact candidate text. Returns None for empty/unrelated blobs."""
    if not text:
        return None
    lowered = " ".join(str(text).lower().split())
    lowered = redact_long_digit_sequences(lowered)
    if not any(keyword in lowered for keyword in MODAL_TEXT_KEYWORDS):
        return None
    return lowered[:INSPECTION_TEXT_MAX_CHARS]


def sanitize_expiration_snippet(text: str | None) -> str | None:
    """Backward-compatible alias for sanitized modal candidate snippets."""
    return sanitize_inspection_snippet(text)


def snippet_is_expiration_candidate(text: str) -> bool:
    lowered = " ".join((text or "").lower().split())
    return any(keyword in lowered for keyword in MODAL_TEXT_KEYWORDS)


def _normalize_action_labels(labels: Any) -> list[str]:
    cleaned: list[str] = []
    for label in labels or []:
        if not label:
            continue
        normalized = " ".join(str(label).lower().split())[:80]
        if normalized:
            cleaned.append(normalized)
        if len(cleaned) >= 12:
            break
    return cleaned


def _label_is_continue(label: str) -> bool:
    return label == "continue" or label.startswith("continue ")


def _text_has_logout_language(text: str, action_labels: list[str]) -> bool:
    haystacks = [text, *action_labels]
    for haystack in haystacks:
        if any(phrase in haystack for phrase in LOGOUT_LANGUAGE_PHRASES):
            return True
    return False


def classify_amex_expiration_candidate(
    candidate: InspectionCandidate | dict[str, Any],
) -> dict[str, Any]:
    """Classify one Browser Inspector candidate as the Amex expiration dialog."""
    if isinstance(candidate, InspectionCandidate):
        text = candidate.text_snippet or ""
        button_labels = list(candidate.visible_button_labels)
        link_labels = list(candidate.visible_link_labels)
        visible = True
        errors = list(candidate.errors)
    else:
        text = str(candidate.get("text_snippet") or "")
        button_labels = _normalize_action_labels(candidate.get("visible_button_labels"))
        if not button_labels:
            button_labels = _normalize_action_labels(candidate.get("button_labels"))
        link_labels = _normalize_action_labels(candidate.get("visible_link_labels"))
        visible = candidate.get("candidate_visible", True) is not False
        errors = list(candidate.get("errors") or [])

    lowered = " ".join(redact_long_digit_sequences(text).lower().split())
    action_labels = button_labels + link_labels
    headline_match = any(phrase in lowered for phrase in EXPIRATION_HEADLINE_PHRASES)
    expiration_language_match = any(token in lowered for token in EXPIRATION_LANGUAGE_TOKENS)
    continue_action_match = any(_label_is_continue(label) for label in action_labels)
    logout_action_match = _text_has_logout_language(lowered, action_labels)
    candidate_visible = bool(visible) and "inaccessible" not in " ".join(errors).lower()
    classified = bool(
        candidate_visible
        and headline_match
        and expiration_language_match
        and continue_action_match
        and logout_action_match
    )
    return {
        "headline_match": headline_match,
        "expiration_language_match": expiration_language_match,
        "continue_action_match": continue_action_match,
        "logout_action_match": logout_action_match,
        "candidate_visible": candidate_visible,
        "classified_as_expiration_dialog": classified,
    }


def classify_amex_expiration_from_inspection(
    inspection: BrowserInspection,
) -> dict[str, Any]:
    """Return the first classified Amex expiration candidate from an inspection."""
    for candidate in inspection.candidates:
        conditions = classify_amex_expiration_candidate(candidate)
        if conditions["classified_as_expiration_dialog"]:
            return {
                "detected": True,
                "candidate": candidate,
                "conditions": conditions,
                "continue_token": candidate.continue_token,
                "dialog_text": candidate.text_snippet,
                "source_type": candidate.source_type,
            }
    return {
        "detected": False,
        "candidate": None,
        "conditions": None,
        "continue_token": None,
        "dialog_text": None,
        "source_type": None,
    }


def evaluate_expiration_dialog_conditions(
    dialog_text: str,
    *,
    has_continue_button: bool,
    action_labels: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate Amex expiration conditions; includes legacy key aliases."""
    labels = list(action_labels or [])
    if has_continue_button and not any(_label_is_continue(label) for label in labels):
        labels.append("continue")
    conditions = classify_amex_expiration_candidate(
        {
            "text_snippet": dialog_text,
            "visible_button_labels": labels,
            "candidate_visible": True,
        }
    )
    # Legacy aliases kept for older diagnostics/tests.
    conditions["has_headline"] = conditions["headline_match"]
    conditions["has_expiration_language"] = conditions["expiration_language_match"]
    conditions["has_continue_button"] = conditions["continue_action_match"]
    conditions["matched"] = conditions["classified_as_expiration_dialog"]
    legacy_keys = (
        ("has_headline", "headline_match"),
        ("has_expiration_language", "expiration_language_match"),
        ("has_continue_button", "continue_action_match"),
        ("logout_action_match", "logout_action_match"),
        ("candidate_visible", "candidate_visible"),
    )
    passed: list[str] = []
    failed: list[str] = []
    for legacy_key, modern_key in legacy_keys:
        (passed if conditions[modern_key] else failed).append(legacy_key)
        if legacy_key != modern_key:
            (passed if conditions[modern_key] else failed).append(modern_key)
    conditions["passed"] = passed
    conditions["failed"] = failed
    return conditions


def expiration_dialog_criteria_met(dialog_text: str, *, has_continue_button: bool) -> bool:
    """Return True only when Amex expiration-dialog criteria match."""
    return bool(
        evaluate_expiration_dialog_conditions(
            dialog_text,
            has_continue_button=has_continue_button,
        )["classified_as_expiration_dialog"]
    )


def _is_ignored_browser_url(url: str | None) -> bool:
    raw = (url or "").strip().lower()
    if not raw:
        return True
    return any(raw.startswith(prefix) for prefix in IGNORED_PAGE_URL_PREFIXES)


def _hostname(url: str | None) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except Exception:
        return ""


def _host_matches_suffixes(host: str, suffixes: tuple[str, ...]) -> bool:
    if not host:
        return False
    for suffix in suffixes:
        needle = suffix.lower().lstrip(".")
        if host == needle or host.endswith("." + needle):
            return True
    return False


def _page_viewport_area(page: Page) -> float:
    """Viewport area for page ranking; avoids page.evaluate (broken on Amex)."""
    try:
        viewport = getattr(page, "viewport_size", None)
        if isinstance(viewport, dict):
            return float(viewport.get("width", 0) or 0) * float(
                viewport.get("height", 0) or 0
            )
    except Exception:
        pass
    return 0.0


def select_provider_page(
    context: BrowserContext,
    *,
    hostname_suffixes: tuple[str, ...],
    preferred_hostnames: tuple[str, ...] = (),
    deprioritize_login: bool = True,
    create_if_missing: bool = False,
) -> Page | None:
    """Generic provider page selector for multi-tab CDP contexts."""
    ranked: list[tuple[tuple[Any, ...], Page]] = []
    for page in list(context.pages):
        try:
            if page.is_closed():
                continue
        except Exception:
            pass
        url = getattr(page, "url", None)
        if _is_ignored_browser_url(url):
            continue
        host = _hostname(url)
        if not _host_matches_suffixes(host, hostname_suffixes):
            continue
        preferred = 1 if host in {item.lower() for item in preferred_hostnames} else 0
        login_penalty = 0 if (deprioritize_login and is_login_url(url)) else 1
        area = _page_viewport_area(page)
        # Higher tuple wins.
        ranked.append(((login_penalty, preferred, area), page))

    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]
    if not create_if_missing:
        return None
    usable = [
        page
        for page in list(context.pages)
        if not _is_ignored_browser_url(getattr(page, "url", None))
    ]
    if usable:
        return usable[0]
    if context.pages:
        return context.pages[0]
    return context.new_page()


def select_amex_page(
    context: BrowserContext,
    *,
    create_if_missing: bool = False,
) -> Page | None:
    """Prefer global.americanexpress.com over login/public Amex pages."""
    return select_provider_page(
        context,
        hostname_suffixes=AMEX_HOSTNAME_SUFFIXES,
        preferred_hostnames=AMEX_PREFERRED_HOSTNAMES,
        deprioritize_login=True,
        create_if_missing=create_if_missing,
    )


def _iter_page_frames(page: Page) -> list[Any]:
    """Return page frames, falling back to the page itself for simple mocks."""
    try:
        frames = page.frames
    except Exception:
        return [page]
    if frames is None:
        return [page]
    try:
        frame_list = list(frames)
    except TypeError:
        return [page]
    return frame_list or [page]


def _frame_parent_url(frame: Any) -> str | None:
    try:
        parent = getattr(frame, "parent_frame", None)
        if parent is None:
            return None
        return sanitize_url(getattr(parent, "url", None))
    except Exception:
        return None


def _frame_appears_cross_origin(
    frame_url: str | None,
    page_url: str | None,
) -> bool | None:
    frame_host = _hostname(frame_url)
    page_host = _hostname(page_url)
    if not frame_host or not page_host:
        return None
    return frame_host != page_host


CDP_CONTINUE_TOKEN_PREFIX = "cdp-backend:"

INSPECTION_CONTAINER_SELECTORS = EXPIRATION_DIALOG_CONTAINER_SELECTORS

AX_CANDIDATE_ROLES = frozenset({"dialog", "alertdialog", "alert"})
ACTION_NODE_SELECTOR = (
    'button, [role="button"], input[type="button"], input[type="submit"], a'
)
OVERLAY_NODE_SELECTOR = "div, section, aside, article"
CDP_CAPABILITY_PROBES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("Page.getFrameTree", "Page.getFrameTree", None),
    ("DOM.enable", "DOM.enable", None),
    ("CSS.enable", "CSS.enable", None),
    ("Accessibility.enable", "Accessibility.enable", None),
    (
        "DOM.getDocument",
        "DOM.getDocument",
        {"depth": -1, "pierce": True},
    ),
    ("Accessibility.getFullAXTree", "Accessibility.getFullAXTree", None),
)


def open_page_cdp_session(page: Page) -> Any:
    """Open a CDP session bound to the selected page."""
    return page.context.new_cdp_session(page)


def _cdp_send(session: Any, method: str, params: dict[str, Any] | None = None) -> Any:
    if params is None:
        return session.send(method)
    return session.send(method, params)


def enable_inspection_domains(session: Any) -> None:
    _cdp_send(session, "DOM.enable")
    _cdp_send(session, "CSS.enable")
    _cdp_send(session, "Accessibility.enable")


def get_frame_tree(session: Any) -> dict[str, Any]:
    return _cdp_send(session, "Page.getFrameTree")


def get_pierced_document(session: Any) -> dict[str, Any]:
    return _cdp_send(session, "DOM.getDocument", {"depth": -1, "pierce": True})


def query_selector_all(session: Any, node_id: int, selector: str) -> list[int]:
    result = _cdp_send(
        session,
        "DOM.querySelectorAll",
        {"nodeId": int(node_id), "selector": selector},
    )
    return [int(item) for item in (result.get("nodeIds") or []) if item is not None]


def describe_node(
    session: Any,
    *,
    node_id: int | None = None,
    backend_node_id: int | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    params: dict[str, Any] = {"depth": depth}
    if node_id is not None:
        params["nodeId"] = int(node_id)
    if backend_node_id is not None:
        params["backendNodeId"] = int(backend_node_id)
    return _cdp_send(session, "DOM.describeNode", params)


def get_node_attributes(session: Any, node_id: int) -> dict[str, str]:
    result = _cdp_send(session, "DOM.getAttributes", {"nodeId": int(node_id)})
    attrs = result.get("attributes") or []
    out: dict[str, str] = {}
    for index in range(0, len(attrs) - 1, 2):
        out[str(attrs[index])] = str(attrs[index + 1])
    return out


def get_node_box_model(session: Any, backend_node_id: int) -> dict[str, Any]:
    return _cdp_send(
        session,
        "DOM.getBoxModel",
        {"backendNodeId": int(backend_node_id)},
    )


def get_node_computed_style(session: Any, node_id: int) -> dict[str, str]:
    result = _cdp_send(
        session,
        "CSS.getComputedStyleForNode",
        {"nodeId": int(node_id)},
    )
    styles: dict[str, str] = {}
    for item in result.get("computedStyle") or []:
        name = item.get("name")
        if name:
            styles[str(name)] = str(item.get("value") or "")
    return styles


def get_accessibility_tree(session: Any) -> dict[str, Any]:
    return _cdp_send(session, "Accessibility.getFullAXTree")


def resolve_backend_node(session: Any, backend_node_id: int) -> dict[str, Any]:
    return _cdp_send(
        session,
        "DOM.resolveNode",
        {"backendNodeId": int(backend_node_id)},
    )


def _summarize_cdp_result(method: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    if method == "Page.getFrameTree":
        frame = (result.get("frameTree") or {}).get("frame") or {}
        child_count = len((result.get("frameTree") or {}).get("childFrames") or [])
        return {
            "frame_id": frame.get("id"),
            "frame_url": sanitize_url(frame.get("url")),
            "child_frame_count": child_count,
        }
    if method == "DOM.getDocument":
        root = result.get("root") or {}
        return {
            "root_node_id": root.get("nodeId"),
            "root_backend_node_id": root.get("backendNodeId"),
            "node_name": root.get("nodeName"),
            "child_count": len(root.get("children") or []),
        }
    if method == "Accessibility.getFullAXTree":
        nodes = result.get("nodes") or []
        roles = set()
        for node in nodes[:200]:
            role = ((node.get("role") or {}).get("value") or "").lower()
            if role in AX_CANDIDATE_ROLES:
                roles.add(role)
        return {"node_count": len(nodes), "dialog_like_roles": sorted(roles)}
    return {"keys": sorted(str(key) for key in result.keys())[:12]}


def probe_cdp_operation(
    session: Any,
    *,
    probe: str,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one CDP capability probe; never raises."""
    try:
        result = _cdp_send(session, method, params)
        return {
            "probe": probe,
            "cdp_method": method,
            "ok": True,
            "summary": _summarize_cdp_result(method, result),
            "exception_class": None,
            "exception_message": None,
            "traceback": None,
        }
    except Exception as exc:
        return {
            "probe": probe,
            "cdp_method": method,
            "ok": False,
            "summary": None,
            "exception_class": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
        }


def probe_page_cdp_capabilities(
    page: Page,
    *,
    stop_on_first_failure: bool = True,
) -> dict[str, Any]:
    """Temporary internal helper: confirm CDP inspection primitives on a page."""
    session = None
    probes: list[dict[str, Any]] = []
    first_failure: dict[str, Any] | None = None
    try:
        session = open_page_cdp_session(page)
    except Exception as exc:
        failure = {
            "probe": "open_page_cdp_session",
            "cdp_method": "Target.attachToTarget",
            "ok": False,
            "summary": None,
            "exception_class": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "failure_phase": "open_session",
            "failure_scope": "entire_page",
        }
        return {
            "ok": False,
            "probes": [failure],
            "first_failure": failure,
            "stopped_early": True,
        }

    try:
        for probe_name, method, params in CDP_CAPABILITY_PROBES:
            result = probe_cdp_operation(
                session,
                probe=probe_name,
                method=method,
                params=params,
            )
            probes.append(result)
            if not result["ok"]:
                first_failure = {
                    **result,
                    "failure_phase": "cdp_capability_probe",
                    "failure_scope": "entire_page",
                    "frame_url": sanitize_url(getattr(page, "url", None)),
                }
                if stop_on_first_failure:
                    break
    finally:
        try:
            if session is not None and hasattr(session, "detach"):
                session.detach()
        except Exception:
            pass

    return {
        "ok": first_failure is None,
        "probes": probes,
        "first_failure": first_failure,
        "stopped_early": bool(first_failure and stop_on_first_failure),
    }


def _safe_detach_cdp_session(session: Any) -> None:
    try:
        if session is not None and hasattr(session, "detach"):
            session.detach()
    except Exception:
        pass


def _ax_value(node: dict[str, Any], key: str) -> str:
    raw = node.get(key)
    if isinstance(raw, dict):
        value = raw.get("value")
        return str(value) if value is not None else ""
    if raw is None:
        return ""
    return str(raw)


def _normalize_text(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def _class_summary_from_attrs(attrs: dict[str, str]) -> str | None:
    value = (attrs.get("class") or "").strip()
    return value[:120] or None


def _host_summary(tag_name: str | None, class_summary: str | None) -> str | None:
    tag = (tag_name or "").lower()
    if not tag and not class_summary:
        return None
    if class_summary:
        return f"{tag} class={class_summary}"[:160]
    return tag[:160] or None


def _box_from_model(model_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(model_payload, dict):
        return None
    model = model_payload.get("model") or model_payload
    content = model.get("content") if isinstance(model, dict) else None
    if not isinstance(content, list) or len(content) < 8:
        return None
    xs = [float(content[i]) for i in range(0, 8, 2)]
    ys = [float(content[i]) for i in range(1, 8, 2)]
    x = min(xs)
    y = min(ys)
    width = max(xs) - x
    height = max(ys) - y
    if width <= 0 or height <= 0:
        return None
    return {
        "x": round(x),
        "y": round(y),
        "width": round(width),
        "height": round(height),
    }


def _build_dom_index(node: dict[str, Any], index: dict[int, dict[str, Any]], parent_backend_id: int | None = None) -> None:
    backend_id = node.get("backendNodeId")
    node_id = node.get("nodeId")
    if backend_id is not None:
        backend_int = int(backend_id)
        attrs = _attrs_list_to_dict(node.get("attributes"))
        index[backend_int] = {
            "node": node,
            "node_id": int(node_id) if node_id is not None else None,
            "parent_backend_id": parent_backend_id,
            "backend_node_id": backend_int,
            "node_name": str(node.get("nodeName") or ""),
            "node_type": node.get("nodeType"),
            "attributes": attrs,
            "frame_id": node.get("frameId"),
        }
        current_parent = backend_int
    else:
        current_parent = parent_backend_id

    for child in node.get("children") or []:
        if isinstance(child, dict):
            _build_dom_index(child, index, current_parent)
    for shadow in node.get("shadowRoots") or []:
        if isinstance(shadow, dict):
            _build_dom_index(shadow, index, current_parent)
    content = node.get("contentDocument")
    if isinstance(content, dict):
        _build_dom_index(content, index, current_parent)


def _attrs_list_to_dict(attrs: Any) -> dict[str, str]:
    if isinstance(attrs, dict):
        return {str(k): str(v) for k, v in attrs.items()}
    if not isinstance(attrs, list):
        return {}
    out: dict[str, str] = {}
    for index in range(0, len(attrs) - 1, 2):
        out[str(attrs[index])] = str(attrs[index + 1])
    return out


def _is_document_node(node: dict[str, Any]) -> bool:
    node_type = node.get("nodeType")
    # 9 = Document, 11 = DocumentFragment (open shadow roots).
    if node_type in {9, 11}:
        return True
    name = str(node.get("nodeName") or "").upper()
    return name in {"#DOCUMENT", "DOCUMENT", "#DOCUMENT-FRAGMENT", "DOCUMENT-FRAGMENT"}


def _iter_document_contexts(
    node: dict[str, Any],
    *,
    source_type: str,
    frame_url: str | None,
    host_tag_class_summary: str | None = None,
    frame_id: str | None = None,
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []

    def walk(
        current: dict[str, Any],
        *,
        current_source: str,
        current_frame_url: str | None,
        current_host: str | None,
        current_frame_id: str | None,
    ) -> None:
        if _is_document_node(current):
            contexts.append(
                {
                    "document_node": current,
                    "node_id": current.get("nodeId"),
                    "source_type": current_source,
                    "frame_url": current_frame_url,
                    "host_tag_class_summary": current_host,
                    "frame_id": current.get("frameId") or current_frame_id,
                }
            )
        tag = str(current.get("nodeName") or "").lower()
        attrs = _attrs_list_to_dict(current.get("attributes"))
        class_summary = _class_summary_from_attrs(attrs)
        host_summary = _host_summary(tag, class_summary)
        for shadow in current.get("shadowRoots") or []:
            if isinstance(shadow, dict):
                walk(
                    shadow,
                    current_source=SOURCE_TYPE_SHADOW_DOM,
                    current_frame_url=current_frame_url,
                    current_host=host_summary,
                    current_frame_id=current_frame_id,
                )
        content = current.get("contentDocument")
        if isinstance(content, dict):
            walk(
                content,
                current_source=SOURCE_TYPE_IFRAME,
                current_frame_url=current_frame_url,
                current_host=current_host,
                current_frame_id=content.get("frameId") or current_frame_id,
            )
        elif tag == "iframe":
            # Inaccessible / closed iframe document — recorded by caller.
            contexts.append(
                {
                    "document_node": None,
                    "node_id": None,
                    "source_type": SOURCE_TYPE_IFRAME,
                    "frame_url": current_frame_url,
                    "host_tag_class_summary": host_summary,
                    "frame_id": current.get("frameId") or current_frame_id,
                    "inaccessible_iframe": True,
                    "iframe_backend_node_id": current.get("backendNodeId"),
                    "iframe_node_id": current.get("nodeId"),
                }
            )
        for child in current.get("children") or []:
            if isinstance(child, dict):
                walk(
                    child,
                    current_source=current_source,
                    current_frame_url=current_frame_url,
                    current_host=current_host,
                    current_frame_id=current_frame_id,
                )

    walk(
        node,
        current_source=source_type,
        current_frame_url=frame_url,
        current_host=host_tag_class_summary,
        current_frame_id=frame_id,
    )
    return contexts


def _collect_text_from_dom_node(node: dict[str, Any], *, limit: int = INSPECTION_TEXT_MAX_CHARS) -> str:
    parts: list[str] = []

    def walk(current: dict[str, Any]) -> None:
        if sum(len(part) for part in parts) >= limit:
            return
        if current.get("nodeType") == 3:
            value = current.get("nodeValue") or ""
            if value.strip():
                parts.append(str(value))
            return
        for child in current.get("children") or []:
            if isinstance(child, dict):
                walk(child)
        for shadow in current.get("shadowRoots") or []:
            if isinstance(shadow, dict):
                walk(shadow)

    walk(node)
    return _normalize_text(" ".join(parts))[:limit]


def _is_ancestor_backend(
    index: dict[int, dict[str, Any]],
    ancestor_backend_id: int,
    descendant_backend_id: int,
) -> bool:
    current: int | None = descendant_backend_id
    seen: set[int] = set()
    while current is not None and current not in seen:
        if current == ancestor_backend_id:
            return True
        seen.add(current)
        parent = index.get(current, {}).get("parent_backend_id")
        current = int(parent) if parent is not None else None
    return False


def _viewport_size(page: Page, session: Any | None = None) -> tuple[float, float]:
    if session is not None:
        try:
            metrics = _cdp_send(session, "Page.getLayoutMetrics")
            for key in ("cssVisualViewport", "visualViewport", "cssLayoutViewport", "layoutViewport"):
                viewport = metrics.get(key) or {}
                width = float(viewport.get("clientWidth") or viewport.get("width") or 0)
                height = float(viewport.get("clientHeight") or viewport.get("height") or 0)
                if width > 0 and height > 0:
                    return width, height
        except Exception:
            pass
    try:
        viewport = getattr(page, "viewport_size", None)
        if isinstance(viewport, dict):
            width = float(viewport.get("width", 0) or 0)
            height = float(viewport.get("height", 0) or 0)
            if width > 0 and height > 0:
                return width, height
    except Exception:
        pass
    return 0.0, 0.0


def _node_visibility_and_geometry(
    session: Any,
    *,
    node_id: int | None,
    backend_node_id: int | None,
    attrs: dict[str, str],
    node_diagnostics: list[dict[str, Any]],
    frame_url: str | None,
    frame_id: str | None,
) -> tuple[bool, dict[str, str], dict[str, Any] | None, Any, bool]:
    """Return visible, style, bounding_box, z_index, fixed_or_absolute."""
    if attrs.get("hidden") is not None or attrs.get("aria-hidden") == "true":
        return False, {}, None, None, False

    style: dict[str, str] = {}
    if node_id is not None:
        try:
            style = get_node_computed_style(session, node_id)
        except Exception as exc:
            node_diagnostics.append(
                {
                    "frame_url": frame_url,
                    "frame_id": frame_id,
                    "cdp_method": "CSS.getComputedStyleForNode",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                    "failure_phase": "computed_style",
                    "failure_scope": "node",
                    "backend_node_id": backend_node_id,
                }
            )

    display = (style.get("display") or "").lower()
    visibility = (style.get("visibility") or "").lower()
    opacity = style.get("opacity") or "1"
    if display == "none" or visibility == "hidden" or opacity == "0":
        return False, style, None, style.get("z-index"), False

    position = (style.get("position") or "").lower()
    fixed_or_absolute = position in {"fixed", "absolute"}
    z_raw = style.get("z-index")
    try:
        z_index: Any = int(float(z_raw)) if z_raw not in (None, "", "auto") else z_raw
    except Exception:
        z_index = z_raw

    bounding_box = None
    if backend_node_id is not None:
        try:
            bounding_box = _box_from_model(get_node_box_model(session, backend_node_id))
        except Exception as exc:
            node_diagnostics.append(
                {
                    "frame_url": frame_url,
                    "frame_id": frame_id,
                    "cdp_method": "DOM.getBoxModel",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                    "failure_phase": "geometry",
                    "failure_scope": "node",
                    "backend_node_id": backend_node_id,
                }
            )
            return False, style, None, z_index, fixed_or_absolute

    if not bounding_box:
        return False, style, None, z_index, fixed_or_absolute
    return True, style, bounding_box, z_index, fixed_or_absolute


def _action_label_from_node(
    node_info: dict[str, Any],
    *,
    ax_name_by_backend: dict[int, str],
) -> str:
    backend_id = node_info.get("backend_node_id")
    if backend_id is not None and backend_id in ax_name_by_backend:
        label = _normalize_text(ax_name_by_backend[backend_id])
        if label:
            return label[:80]
    attrs = node_info.get("attributes") or {}
    for key in ("aria-label", "value", "title", "name"):
        if attrs.get(key):
            label = _normalize_text(attrs.get(key))
            if label:
                return label[:80]
    text = _collect_text_from_dom_node(node_info["node"], limit=80)
    return text[:80]


def _collect_actions_for_candidate(
    session: Any,
    *,
    candidate_node_id: int,
    index: dict[int, dict[str, Any]],
    ax_name_by_backend: dict[int, str],
    mark_continue: bool,
) -> tuple[list[str], list[str], str | None, int | None]:
    buttons: list[str] = []
    links: list[str] = []
    continue_token: str | None = None
    continue_backend_id: int | None = None
    try:
        action_ids = query_selector_all(session, candidate_node_id, ACTION_NODE_SELECTOR)
    except Exception:
        action_ids = []

    for action_node_id in action_ids:
        info = None
        for item in index.values():
            if item.get("node_id") == action_node_id:
                info = item
                break
        if info is None:
            try:
                described = describe_node(session, node_id=action_node_id, depth=1)
                node = described.get("node") or {}
                backend_id = node.get("backendNodeId")
                info = {
                    "node": node,
                    "node_id": action_node_id,
                    "backend_node_id": int(backend_id) if backend_id is not None else None,
                    "attributes": _attrs_list_to_dict(node.get("attributes")),
                    "node_name": str(node.get("nodeName") or ""),
                }
            except Exception:
                continue
        tag = str(info.get("node_name") or "").lower()
        label = _action_label_from_node(info, ax_name_by_backend=ax_name_by_backend)
        if not label:
            continue
        if tag == "a":
            if len(links) < 12:
                links.append(label)
        elif len(buttons) < 12:
            buttons.append(label)
        if mark_continue and continue_token is None and _label_is_continue(label):
            backend_id = info.get("backend_node_id")
            if backend_id is not None:
                continue_backend_id = int(backend_id)
                continue_token = f"{CDP_CONTINUE_TOKEN_PREFIX}{continue_backend_id}"
    return buttons, links, continue_token, continue_backend_id


def _candidate_raw_from_node(
    session: Any,
    *,
    node_id: int,
    index: dict[int, dict[str, Any]],
    ax_name_by_backend: dict[int, str],
    ax_role_by_backend: dict[int, str],
    source_type: str,
    host_tag_class_summary: str | None,
    viewport_area: float,
    mark_continue: bool,
    node_diagnostics: list[dict[str, Any]],
    frame_url: str | None,
    frame_id: str | None,
    force_tags: list[str] | None = None,
) -> dict[str, Any] | None:
    info = None
    for item in index.values():
        if item.get("node_id") == node_id:
            info = item
            break
    if info is None:
        try:
            described = describe_node(session, node_id=node_id, depth=2)
            node = described.get("node") or {}
            backend_id = node.get("backendNodeId")
            if backend_id is None:
                return None
            info = {
                "node": node,
                "node_id": node_id,
                "backend_node_id": int(backend_id),
                "parent_backend_id": None,
                "attributes": _attrs_list_to_dict(node.get("attributes")),
                "node_name": str(node.get("nodeName") or ""),
            }
            index[int(backend_id)] = info
        except Exception as exc:
            node_diagnostics.append(
                {
                    "frame_url": frame_url,
                    "frame_id": frame_id,
                    "cdp_method": "DOM.describeNode",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                    "failure_phase": "describe_node",
                    "failure_scope": "node",
                }
            )
            return None

    backend_node_id = info.get("backend_node_id")
    # Shadow hosts are inspected via their open shadow document context so the
    # candidate source_type stays SHADOW_DOM instead of the light-DOM host.
    if source_type != SOURCE_TYPE_SHADOW_DOM and (info.get("node") or {}).get("shadowRoots"):
        return None
    attrs = dict(info.get("attributes") or {})
    # Prefer live attributes when available.
    if info.get("node_id") is not None:
        try:
            attrs = get_node_attributes(session, int(info["node_id"])) or attrs
        except Exception as exc:
            node_diagnostics.append(
                {
                    "frame_url": frame_url,
                    "frame_id": frame_id,
                    "cdp_method": "DOM.getAttributes",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                    "failure_phase": "attributes",
                    "failure_scope": "node",
                    "backend_node_id": backend_node_id,
                }
            )

    visible, style, bounding_box, z_index, fixed_or_absolute = _node_visibility_and_geometry(
        session,
        node_id=info.get("node_id"),
        backend_node_id=backend_node_id,
        attrs=attrs,
        node_diagnostics=node_diagnostics,
        frame_url=frame_url,
        frame_id=frame_id,
    )
    if not visible or not bounding_box:
        return None

    role = (attrs.get("role") or "").strip() or None
    if backend_node_id is not None and backend_node_id in ax_role_by_backend:
        role = role or ax_role_by_backend[backend_node_id]
    aria_modal_attr = attrs.get("aria-modal")
    aria_modal = None if aria_modal_attr is None else aria_modal_attr == "true"
    class_summary = _class_summary_from_attrs(attrs)
    tag_name = str(info.get("node_name") or "").lower() or None

    accessible_name = None
    if backend_node_id is not None and backend_node_id in ax_name_by_backend:
        accessible_name = _normalize_text(ax_name_by_backend[backend_node_id])[:120] or None
    if not accessible_name:
        accessible_name = _normalize_text(
            attrs.get("aria-label") or attrs.get("title") or attrs.get("name") or ""
        )[:120] or None

    text = ""
    if accessible_name:
        text = accessible_name
    text_from_dom = _collect_text_from_dom_node(info["node"], limit=INSPECTION_TEXT_MAX_CHARS)
    if len(text_from_dom) > len(text):
        text = text_from_dom
    text = redact_long_digit_sequences(text)

    buttons, links, continue_token, _continue_backend = _collect_actions_for_candidate(
        session,
        candidate_node_id=int(info["node_id"]),
        index=index,
        ax_name_by_backend=ax_name_by_backend,
        mark_continue=mark_continue,
    )
    has_actions = bool(buttons or links)
    coverage = 0.0
    if viewport_area > 0:
        coverage = (bounding_box["width"] * bounding_box["height"]) / viewport_area

    tags: list[str] = list(force_tags or [])
    if role == "dialog":
        tags.append("role_dialog")
    if role in {"alertdialog", "alert"} and "ax_dialog" not in tags:
        tags.append("ax_dialog")
    if aria_modal is True:
        tags.append("aria_modal")
    if fixed_or_absolute and coverage >= 0.15:
        tags.append("substantial_coverage")
    if isinstance(z_index, int) and z_index >= 100:
        tags.append("high_z_index")
    if has_actions and (role == "dialog" or aria_modal is True or fixed_or_absolute):
        tags.append("actionable_controls")
    if coverage >= 0.35 and fixed_or_absolute:
        tags.append("viewport_overlay")
    if text and snippet_is_expiration_candidate(text):
        tags.append("modal_text")
    # Dedupe tags while preserving order.
    seen_tags: set[str] = set()
    ordered_tags: list[str] = []
    for tag in tags:
        if tag not in seen_tags:
            seen_tags.add(tag)
            ordered_tags.append(tag)
    tags = ordered_tags

    if not tags:
        return None
    if (
        "role_dialog" not in tags
        and "aria_modal" not in tags
        and "ax_dialog" not in tags
        and "modal_text" not in tags
        and not ("substantial_coverage" in tags and has_actions)
        and not ("viewport_overlay" in tags and has_actions)
    ):
        return None
    if (
        len(text) < 8
        and "role_dialog" not in tags
        and "aria_modal" not in tags
        and "ax_dialog" not in tags
    ):
        return None

    return {
        "source_type": source_type,
        "tag_name": tag_name,
        "role": role,
        "class_summary": class_summary,
        "host_tag_class_summary": host_tag_class_summary,
        "text_snippet": text[:INSPECTION_TEXT_MAX_CHARS],
        "visible_button_labels": buttons,
        "visible_link_labels": links,
        "bounding_box": bounding_box,
        "viewport_coverage_ratio": round(coverage, 3),
        "z_index": z_index,
        "fixed_or_absolute": fixed_or_absolute,
        "aria_modal": aria_modal,
        "accessible_name": accessible_name,
        "detector_tags": tags,
        "continue_token": continue_token,
        "errors": [],
        "backend_node_id": backend_node_id,
        "node_id": info.get("node_id"),
        "frame_url": frame_url,
        "style_position": style.get("position"),
    }


def _dedupe_candidate_raws(
    raw_candidates: list[dict[str, Any]],
    index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    keep: list[dict[str, Any]] = []
    for item in raw_candidates:
        backend_id = item.get("backend_node_id")
        if backend_id is None:
            keep.append(item)
            continue
        nested_inside_kept = False
        for outer in keep:
            outer_id = outer.get("backend_node_id")
            if outer_id is None or outer_id == backend_id:
                continue
            if _is_ancestor_backend(index, int(outer_id), int(backend_id)):
                nested_inside_kept = True
                break
        if nested_inside_kept:
            continue
        for index_i in range(len(keep) - 1, -1, -1):
            kept_id = keep[index_i].get("backend_node_id")
            if kept_id is None or kept_id == backend_id:
                continue
            if _is_ancestor_backend(index, int(backend_id), int(kept_id)):
                keep.pop(index_i)
        keep.append(item)
    return keep[:40]


def _build_ax_maps(
    ax_tree: dict[str, Any],
) -> tuple[dict[int, str], dict[int, str], list[int]]:
    name_by_backend: dict[int, str] = {}
    role_by_backend: dict[int, str] = {}
    dialog_backends: list[int] = []
    for node in ax_tree.get("nodes") or []:
        if not isinstance(node, dict) or node.get("ignored"):
            continue
        backend_id = node.get("backendDOMNodeId")
        if backend_id is None:
            continue
        backend_int = int(backend_id)
        role = _ax_value(node, "role").lower()
        name = _normalize_text(_ax_value(node, "name"))
        if role:
            role_by_backend[backend_int] = role
        if name:
            name_by_backend[backend_int] = name
        if role in AX_CANDIDATE_ROLES:
            dialog_backends.append(backend_int)
    return name_by_backend, role_by_backend, dialog_backends


def _frame_entries_from_tree(frame_tree: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], *, parent_url: str | None) -> None:
        frame = node.get("frame") or {}
        frame_url = sanitize_url(frame.get("url"))
        entries.append(
            {
                "frame_id": frame.get("id"),
                "frame_url": frame_url,
                "parent_frame_url": parent_url,
                "child_frames": len(node.get("childFrames") or []),
            }
        )
        for child in node.get("childFrames") or []:
            if isinstance(child, dict):
                walk(child, parent_url=frame_url)

    root = frame_tree.get("frameTree") or frame_tree
    if isinstance(root, dict):
        walk(root, parent_url=None)
    return entries


def _build_cdp_frame_failure_diagnostics(
    *,
    frame_url: str | None,
    frame_id: str | None,
    target_id: str | None,
    page_url: str | None,
    parent_frame_url: str | None,
    is_main: bool,
    cdp_method: str,
    exception_class: str | None,
    exception_message: str | None,
    traceback_text: str | None,
    failure_phase: str,
    failure_scope: str = "entire_frame",
) -> dict[str, Any]:
    return {
        "frame_url": frame_url,
        "frame_id": frame_id,
        "target_id": target_id,
        "is_main_frame": bool(is_main),
        "parent_frame_url": parent_frame_url,
        "cdp_method": cdp_method,
        "playwright_operation": f"cdp:{cdp_method}",
        "exception_class": exception_class,
        "exception_message": exception_message,
        "traceback": traceback_text,
        "failure_phase": failure_phase,
        "failure_scope": failure_scope,
        "appears_cross_origin": _frame_appears_cross_origin(frame_url, page_url),
    }


def _collect_candidates_via_cdp(
    page: Page,
    *,
    mark_continue: bool = False,
) -> tuple[list[InspectionCandidate], int, list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """CDP DOM/AX snapshots → Python candidate extraction."""
    page_url = sanitize_url(getattr(page, "url", None))
    errors: list[str] = []
    frame_diagnostics: list[dict[str, Any]] = []
    node_diagnostics: list[dict[str, Any]] = []
    candidates: list[InspectionCandidate] = []
    session = None
    frame_count = 1

    try:
        session = open_page_cdp_session(page)
        enable_inspection_domains(session)
    except Exception as exc:
        diag = _build_cdp_frame_failure_diagnostics(
            frame_url=page_url,
            frame_id=None,
            target_id=None,
            page_url=page_url,
            parent_frame_url=None,
            is_main=True,
            cdp_method="Target.attachToTarget",
            exception_class=type(exc).__name__,
            exception_message=str(exc),
            traceback_text=traceback.format_exc(),
            failure_phase="open_session",
        )
        frame_diagnostics.append(diag)
        errors.append(f"inaccessible_frame:{page_url or 'unknown'}:cdp_session_failed")
        candidates.append(
            InspectionCandidate(
                source_type=SOURCE_TYPE_DOM,
                page_url=page_url,
                frame_url=page_url,
                tag_name=None,
                role=None,
                class_summary=None,
                text_snippet=None,
                errors=["frame_inaccessible"],
                detector_tags=["inaccessible_frame"],
            )
        )
        return candidates, frame_count, errors, frame_diagnostics, node_diagnostics

    try:
        try:
            frame_tree = get_frame_tree(session)
            frame_entries = _frame_entries_from_tree(frame_tree)
            frame_count = max(1, len(frame_entries))
        except Exception as exc:
            frame_entries = [
                {
                    "frame_id": None,
                    "frame_url": page_url,
                    "parent_frame_url": None,
                    "child_frames": 0,
                }
            ]
            frame_diagnostics.append(
                _build_cdp_frame_failure_diagnostics(
                    frame_url=page_url,
                    frame_id=None,
                    target_id=None,
                    page_url=page_url,
                    parent_frame_url=None,
                    is_main=True,
                    cdp_method="Page.getFrameTree",
                    exception_class=type(exc).__name__,
                    exception_message=str(exc),
                    traceback_text=traceback.format_exc(),
                    failure_phase="frame_tree",
                    failure_scope="entire_page",
                )
            )

        try:
            document_payload = get_pierced_document(session)
            root = document_payload.get("root") or {}
        except Exception as exc:
            frame_diagnostics.append(
                _build_cdp_frame_failure_diagnostics(
                    frame_url=page_url,
                    frame_id=None,
                    target_id=None,
                    page_url=page_url,
                    parent_frame_url=None,
                    is_main=True,
                    cdp_method="DOM.getDocument",
                    exception_class=type(exc).__name__,
                    exception_message=str(exc),
                    traceback_text=traceback.format_exc(),
                    failure_phase="get_document",
                )
            )
            errors.append(f"inaccessible_frame:{page_url or 'unknown'}:get_document_failed")
            candidates.append(
                InspectionCandidate(
                    source_type=SOURCE_TYPE_DOM,
                    page_url=page_url,
                    frame_url=page_url,
                    tag_name=None,
                    role=None,
                    class_summary=None,
                    text_snippet=None,
                    errors=["frame_inaccessible"],
                    detector_tags=["inaccessible_frame"],
                )
            )
            return candidates, frame_count, errors, frame_diagnostics, node_diagnostics

        index: dict[int, dict[str, Any]] = {}
        if isinstance(root, dict):
            _build_dom_index(root, index)

        ax_name_by_backend: dict[int, str] = {}
        ax_role_by_backend: dict[int, str] = {}
        ax_dialog_backends: list[int] = []
        try:
            ax_tree = get_accessibility_tree(session)
            ax_name_by_backend, ax_role_by_backend, ax_dialog_backends = _build_ax_maps(ax_tree)
        except Exception as exc:
            node_diagnostics.append(
                {
                    "frame_url": page_url,
                    "frame_id": None,
                    "cdp_method": "Accessibility.getFullAXTree",
                    "exception_class": type(exc).__name__,
                    "exception_message": str(exc),
                    "traceback": traceback.format_exc(),
                    "failure_phase": "accessibility_tree",
                    "failure_scope": "entire_page",
                }
            )

        width, height = _viewport_size(page, session)
        viewport_area = max(1.0, width * height) if width and height else 0.0
        contexts = _iter_document_contexts(
            root if isinstance(root, dict) else {},
            source_type=SOURCE_TYPE_DOM,
            frame_url=page_url,
        )
        # Map frame ids from CDP tree onto iframe contexts when possible.
        frame_url_by_id = {
            entry.get("frame_id"): entry.get("frame_url")
            for entry in frame_entries
            if entry.get("frame_id")
        }
        for context in contexts:
            frame_id = context.get("frame_id")
            if frame_id and frame_url_by_id.get(frame_id):
                context["frame_url"] = frame_url_by_id[frame_id]

        raw_candidates: list[dict[str, Any]] = []
        seen_backend_ids: set[int] = set()
        closed_shadow_note = False

        for context in contexts:
            if context.get("inaccessible_iframe"):
                frame_url = context.get("frame_url")
                # Prefer Playwright frame URLs for cross-origin iframe diagnostics.
                continue

            document_node = context.get("document_node")
            doc_node_id = context.get("node_id")
            if document_node is None or doc_node_id is None:
                continue
            source_type = str(context.get("source_type") or SOURCE_TYPE_DOM)
            frame_url = context.get("frame_url") or page_url
            frame_id = context.get("frame_id")
            host_summary = context.get("host_tag_class_summary")

            selector = ", ".join(INSPECTION_CONTAINER_SELECTORS)
            try:
                node_ids = query_selector_all(session, int(doc_node_id), selector)
            except Exception as exc:
                node_diagnostics.append(
                    {
                        "frame_url": frame_url,
                        "frame_id": frame_id,
                        "cdp_method": "DOM.querySelectorAll",
                        "exception_class": type(exc).__name__,
                        "exception_message": str(exc),
                        "traceback": traceback.format_exc(),
                        "failure_phase": "query_containers",
                        "failure_scope": "entire_frame",
                    }
                )
                node_ids = []
                errors.append("query_containers_failed")

            # Bounded overlay discovery: only common container tags, then style filter.
            try:
                overlay_ids = query_selector_all(
                    session,
                    int(doc_node_id),
                    OVERLAY_NODE_SELECTOR,
                )
            except Exception:
                overlay_ids = []
            # Cap overlay scan to avoid serializing the whole page.
            overlay_ids = overlay_ids[:120]

            candidate_node_ids = list(dict.fromkeys([*node_ids, *overlay_ids]))
            for node_id in candidate_node_ids:
                raw = _candidate_raw_from_node(
                    session,
                    node_id=int(node_id),
                    index=index,
                    ax_name_by_backend=ax_name_by_backend,
                    ax_role_by_backend=ax_role_by_backend,
                    source_type=source_type,
                    host_tag_class_summary=host_summary,
                    viewport_area=viewport_area,
                    mark_continue=mark_continue,
                    node_diagnostics=node_diagnostics,
                    frame_url=frame_url,
                    frame_id=frame_id,
                )
                if raw is None:
                    continue
                # Overlay-only nodes must pass fixed/absolute filter when not selector-hit.
                if (
                    node_id in overlay_ids
                    and node_id not in node_ids
                    and not raw.get("fixed_or_absolute")
                ):
                    continue
                backend_id = raw.get("backend_node_id")
                if backend_id in seen_backend_ids:
                    continue
                if backend_id is not None:
                    seen_backend_ids.add(int(backend_id))
                raw_candidates.append(raw)

        # AX dialog-like roles may point at nodes missed by CSS selectors.
        for backend_id in ax_dialog_backends:
            if backend_id in seen_backend_ids:
                continue
            info = index.get(backend_id)
            if not info or info.get("node_id") is None:
                try:
                    described = describe_node(session, backend_node_id=backend_id, depth=2)
                    node = described.get("node") or {}
                    node_id = node.get("nodeId")
                    if node_id is None:
                        resolved = resolve_backend_node(session, backend_id)
                        object_id = (resolved.get("object") or {}).get("objectId")
                        if not object_id:
                            continue
                        requested = _cdp_send(
                            session,
                            "DOM.requestNode",
                            {"objectId": object_id},
                        )
                        node_id = requested.get("nodeId")
                        if node_id is None:
                            continue
                        described = describe_node(session, node_id=int(node_id), depth=2)
                        node = described.get("node") or node
                    info = {
                        "node": node,
                        "node_id": int(node_id),
                        "backend_node_id": backend_id,
                        "parent_backend_id": None,
                        "attributes": _attrs_list_to_dict(node.get("attributes")),
                        "node_name": str(node.get("nodeName") or ""),
                    }
                    index[backend_id] = info
                except Exception as exc:
                    node_diagnostics.append(
                        {
                            "frame_url": page_url,
                            "frame_id": None,
                            "cdp_method": "DOM.describeNode",
                            "exception_class": type(exc).__name__,
                            "exception_message": str(exc),
                            "traceback": traceback.format_exc(),
                            "failure_phase": "ax_resolve",
                            "failure_scope": "node",
                            "backend_node_id": backend_id,
                        }
                    )
                    continue
            raw = _candidate_raw_from_node(
                session,
                node_id=int(info["node_id"]),
                index=index,
                ax_name_by_backend=ax_name_by_backend,
                ax_role_by_backend=ax_role_by_backend,
                source_type=SOURCE_TYPE_DOM,
                host_tag_class_summary=None,
                viewport_area=viewport_area,
                mark_continue=mark_continue,
                node_diagnostics=node_diagnostics,
                frame_url=page_url,
                frame_id=None,
                force_tags=["ax_dialog"],
            )
            if raw is None:
                continue
            seen_backend_ids.add(backend_id)
            raw_candidates.append(raw)

        # Closed shadow roots are not present in pierced open roots; note once.
        if any("shadow" in " ".join(err.get("exception_message") or "").lower() for err in node_diagnostics):
            closed_shadow_note = True
        if closed_shadow_note:
            errors.append("closed_shadow_root_inaccessible")

        # Cross-origin / inaccessible child frames from Playwright frame list.
        playwright_frames = _iter_page_frames(page)
        main_frame = getattr(page, "main_frame", None)
        accessible_frame_urls = {
            sanitize_url(ctx.get("frame_url"))
            for ctx in contexts
            if ctx.get("document_node") is not None
        }
        accessible_frame_urls.add(page_url)
        for frame in playwright_frames:
            is_main = main_frame is not None and frame is main_frame
            if main_frame is None and frame is page:
                is_main = True
            if is_main:
                continue
            try:
                frame_url = sanitize_url(getattr(frame, "url", None))
            except Exception as exc:
                frame_diagnostics.append(
                    _build_cdp_frame_failure_diagnostics(
                        frame_url=None,
                        frame_id=None,
                        target_id=None,
                        page_url=page_url,
                        parent_frame_url=_frame_parent_url(frame),
                        is_main=False,
                        cdp_method="frame.url",
                        exception_class=type(exc).__name__,
                        exception_message=str(exc),
                        traceback_text=traceback.format_exc(),
                        failure_phase="frame_url",
                    )
                )
                continue
            if frame_url in accessible_frame_urls:
                continue
            appears_xorigin = _frame_appears_cross_origin(frame_url, page_url)
            if appears_xorigin is False:
                continue
            # Treat unresolved child frames as inaccessible rather than fabricating content.
            diag = _build_cdp_frame_failure_diagnostics(
                frame_url=frame_url,
                frame_id=None,
                target_id=None,
                page_url=page_url,
                parent_frame_url=_frame_parent_url(frame),
                is_main=False,
                cdp_method="DOM.getDocument",
                exception_class="RuntimeError",
                exception_message="Frame document not present in pierced DOM snapshot",
                traceback_text=None,
                failure_phase="frame_document",
            )
            frame_diagnostics.append(diag)
            errors.append(
                f"inaccessible_frame:{frame_url or 'unknown'}:frame_inaccessible:pierced_dom_missing"
            )
            candidates.append(
                InspectionCandidate(
                    source_type=SOURCE_TYPE_IFRAME,
                    page_url=page_url,
                    frame_url=frame_url,
                    tag_name=None,
                    role=None,
                    class_summary=None,
                    text_snippet=None,
                    errors=["frame_inaccessible"],
                    detector_tags=["inaccessible_frame"],
                )
            )

        deduped = _dedupe_candidate_raws(raw_candidates, index)
        for raw in deduped:
            candidate = _candidate_from_raw(
                raw,
                page_url=page_url,
                frame_url=sanitize_url(raw.get("frame_url")) or page_url,
                default_source_type=str(raw.get("source_type") or SOURCE_TYPE_DOM),
            )
            if candidate is not None:
                # Preserve frame_url from context when encoded on raw.
                if raw.get("frame_url"):
                    candidate.frame_url = sanitize_url(raw.get("frame_url"))
                candidates.append(candidate)

        # Attach frame_url onto raw during construction for iframe/shadow contexts.
        # Re-run assignment from contexts for candidates that still point at page URL.
        return candidates, frame_count, errors, frame_diagnostics, node_diagnostics
    finally:
        _safe_detach_cdp_session(session)


def _normalize_inspector_frame_payload(
    payload: dict[str, Any],
    *,
    default_source_type: str,
) -> dict[str, Any]:
    """Accept modern inspector payloads and legacy expiration-dialog mocks."""
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and candidates:
        return payload

    # Legacy shape used by older tests/call sites:
    # {detected, continue_token, dialog_text, source_type, candidates: []}
    dialog_text = payload.get("dialog_text") or payload.get("text_snippet")
    if payload.get("detected") and dialog_text:
        labels = _normalize_action_labels(payload.get("button_labels") or ["continue"])
        return {
            "candidates": [
                {
                    "source_type": payload.get("source_type") or default_source_type,
                    "tag_name": None,
                    "role": None,
                    "class_summary": None,
                    "role_tag_class_summary": payload.get("role_tag_class_summary"),
                    "text_snippet": dialog_text,
                    "visible_button_labels": labels,
                    "visible_link_labels": [],
                    "detector_tags": ["legacy_payload"],
                    "continue_token": payload.get("continue_token"),
                    "errors": [],
                }
            ],
            "errors": [],
        }
    if isinstance(candidates, list):
        return payload
    return {"candidates": [], "errors": list(payload.get("errors") or [])}


def _candidate_from_raw(
    raw: dict[str, Any],
    *,
    page_url: str | None,
    frame_url: str | None,
    default_source_type: str,
) -> InspectionCandidate | None:
    snippet = sanitize_inspection_snippet(raw.get("text_snippet"))
    button_labels = _normalize_action_labels(
        raw.get("visible_button_labels") or raw.get("button_labels")
    )
    link_labels = _normalize_action_labels(raw.get("visible_link_labels"))
    detector_tags = [
        str(tag)[:64] for tag in (raw.get("detector_tags") or []) if tag
    ][:16]
    errors = [str(err)[:120] for err in (raw.get("errors") or []) if err][:12]
    if not snippet and not detector_tags and not errors:
        return None
    if snippet is None and not (
        raw.get("role") == "dialog"
        or raw.get("aria_modal") is True
        or "ax_dialog" in detector_tags
    ):
        # Keep dialog/aria-modal candidates even if keyword gate rejects text.
        snippet = redact_long_digit_sequences(
            " ".join(str(raw.get("text_snippet") or "").lower().split())
        )[:INSPECTION_TEXT_MAX_CHARS] or None
    class_summary = str(raw.get("class_summary") or "")[:120] or None
    role = raw.get("role")
    tag_name = raw.get("tag_name")
    if not class_summary and raw.get("role_tag_class_summary"):
        # Compatibility with older inspector payloads/tests.
        summary = str(raw.get("role_tag_class_summary"))
        class_summary = summary[:120]
    return InspectionCandidate(
        source_type=str(raw.get("source_type") or default_source_type),
        page_url=page_url,
        frame_url=frame_url,
        tag_name=str(tag_name).lower()[:40] if tag_name else None,
        role=str(role)[:40] if role else None,
        class_summary=class_summary,
        text_snippet=snippet,
        visible_button_labels=button_labels,
        visible_link_labels=link_labels,
        bounding_box=raw.get("bounding_box")
        if isinstance(raw.get("bounding_box"), dict)
        else None,
        viewport_coverage_ratio=(
            float(raw["viewport_coverage_ratio"])
            if isinstance(raw.get("viewport_coverage_ratio"), (int, float))
            else None
        ),
        z_index=raw.get("z_index"),
        fixed_or_absolute=bool(raw.get("fixed_or_absolute")),
        aria_modal=raw.get("aria_modal")
        if isinstance(raw.get("aria_modal"), bool)
        else None,
        accessible_name=(
            str(raw.get("accessible_name"))[:120] if raw.get("accessible_name") else None
        ),
        detector_tags=detector_tags,
        errors=errors,
        host_tag_class_summary=(
            str(raw.get("host_tag_class_summary"))[:160]
            if raw.get("host_tag_class_summary")
            else None
        ),
        continue_token=str(raw["continue_token"]) if raw.get("continue_token") else None,
    )


def inspect_page_browser(
    page: Page,
    *,
    mark_continue: bool = False,
) -> tuple[list[InspectionCandidate], int, list[str], list[dict[str, Any]]]:
    """Inspect one page via CDP DOM/AX into generic InspectionCandidate records."""
    candidates, frame_count, errors, frame_diagnostics, node_diagnostics = (
        _collect_candidates_via_cdp(page, mark_continue=mark_continue)
    )
    # Node-level style/geometry failures stay in developer diagnostics only.
    if node_diagnostics:
        frame_diagnostics = [
            *frame_diagnostics,
            *[
                {
                    **item,
                    "playwright_operation": f"cdp:{item.get('cdp_method')}",
                }
                for item in node_diagnostics
            ],
        ]
    return candidates, frame_count, errors, frame_diagnostics


def capture_browser_inspection_screenshot(
    page: Page,
    *,
    provider: str,
    diagnostics_dir: Path,
) -> str:
    """Capture one developer-only screenshot; returns local path only."""
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = diagnostics_dir / f"{provider}_browser_inspection_{stamp}.png"
    print(
        "[Mighty Browser Inspector] WARNING: screenshots may contain sensitive "
        "account information and are stored only on this machine."
    )
    page.screenshot(path=str(path), full_page=False)
    return str(path)


def inspect_browser_context(
    context: BrowserContext,
    *,
    provider: str = "amex",
    capture_screenshot: bool = False,
    mark_continue: bool = False,
    diagnostics_dir: Path | None = None,
    select_page_fn: Any = None,
) -> BrowserInspection:
    """Provider-agnostic Browser Inspector over a CDP browser context."""
    errors: list[str] = []
    pages = list(context.pages)
    selector = select_page_fn or (
        select_amex_page if provider == "amex" else select_provider_page
    )
    if select_page_fn is None and provider == "amex":
        selected = select_amex_page(context, create_if_missing=False)
    elif select_page_fn is None:
        selected = None
        errors.append("no_page_selector_for_provider")
    else:
        selected = selector(context, create_if_missing=False)

    candidates: list[InspectionCandidate] = []
    frame_count = 0
    screenshot_path: str | None = None
    frame_diagnostics: list[dict[str, Any]] = []
    selected_url = sanitize_url(selected.url) if selected is not None else None

    if selected is None:
        errors.append("no_provider_page_selected")
    else:
        page_candidates, page_frames, page_errors, page_frame_diagnostics = (
            inspect_page_browser(
                selected,
                mark_continue=mark_continue,
            )
        )
        candidates.extend(page_candidates)
        frame_count += page_frames
        errors.extend(page_errors)
        frame_diagnostics.extend(page_frame_diagnostics)
        if capture_screenshot:
            try:
                screenshot_path = capture_browser_inspection_screenshot(
                    selected,
                    provider=provider,
                    diagnostics_dir=diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR,
                )
            except Exception as exc:
                errors.append(f"screenshot_failed:{type(exc).__name__}")

    node_failures = [
        item
        for item in frame_diagnostics
        if item.get("failure_scope") == "node"
    ]
    frame_failures = [
        item
        for item in frame_diagnostics
        if item.get("failure_scope") != "node"
    ]
    return BrowserInspection(
        inspected_at=iso_now(),
        selected_page_url=selected_url,
        page_count=len(pages),
        frame_count=frame_count,
        candidate_count=len(candidates),
        candidates=candidates[:80],
        errors=errors,
        screenshot_path=screenshot_path,
        diagnostics={
            "provider": provider,
            "capture_screenshot": bool(capture_screenshot),
            "collector": "cdp_dom_ax",
        },
        developer_diagnostics={
            "inaccessible_frames": frame_failures,
            "inaccessible_frame_count": len(frame_failures),
            "node_diagnostics": node_failures,
            "node_diagnostic_count": len(node_failures),
        },
    )


def debug_inspect_browser_context(
    context: BrowserContext,
    *,
    provider: str = "amex",
    select_page_fn: Any = None,
) -> dict[str, Any]:
    """Temporary developer probe: CDP capability checks on the selected page.

    Confirms Page.getFrameTree, DOM/CSS/Accessibility.enable, pierced
    DOM.getDocument, and Accessibility.getFullAXTree. Does not use
    frame.evaluate/page.evaluate. Stops at the first failed CDP operation.
    """
    pages_info: list[dict[str, Any]] = []
    for index, page in enumerate(list(context.pages)):
        entry: dict[str, Any] = {"index": index}
        try:
            entry["url"] = getattr(page, "url", None)
        except Exception as exc:
            entry["url"] = None
            entry["url_error"] = f"{type(exc).__name__}: {exc}"
        try:
            entry["is_closed"] = bool(page.is_closed()) if hasattr(page, "is_closed") else None
        except Exception as exc:
            entry["is_closed"] = None
            entry["is_closed_error"] = f"{type(exc).__name__}: {exc}"
        pages_info.append(entry)

    selector = select_page_fn or (
        select_amex_page if provider == "amex" else select_provider_page
    )
    if select_page_fn is None and provider == "amex":
        selected = select_amex_page(context, create_if_missing=False)
    elif select_page_fn is None:
        selected = None
    else:
        selected = selector(context, create_if_missing=False)

    selected_url = sanitize_url(getattr(selected, "url", None)) if selected else None
    if selected is None:
        return {
            "ok": False,
            "provider": provider,
            "pages": pages_info,
            "selected_page_url": None,
            "frames": [],
            "cdp_probes": [],
            "first_failure": {
                "cdp_method": "select_provider_page",
                "playwright_operation": "select_provider_page",
                "exception_class": "RuntimeError",
                "exception_message": "No provider page selected",
                "traceback": None,
            },
            "stopped_early": True,
        }

    capability = probe_page_cdp_capabilities(selected, stop_on_first_failure=True)
    first_failure = capability.get("first_failure")
    frames_info = [
        {
            "index": 0,
            "url": selected_url,
            "is_main_frame": True,
            "parent_frame_url": None,
            "is_detached": False,
            "appears_cross_origin": False,
            "probes": capability.get("probes") or [],
            "failed": not capability.get("ok", False),
        }
    ]
    return {
        "ok": bool(capability.get("ok")),
        "provider": provider,
        "pages": pages_info,
        "selected_page_url": selected_url,
        "frames": frames_info,
        "cdp_probes": capability.get("probes") or [],
        "first_failure": first_failure,
        "stopped_early": bool(capability.get("stopped_early")),
    }


def format_browser_inspect_debug_report(payload: dict[str, Any]) -> str:
    """Render browser-inspect-debug payload as a human-readable report."""
    lines: list[str] = []
    pages = payload.get("pages") or []
    lines.append(f"=== Pages ({len(pages)}) ===")
    for page in pages:
        lines.append(
            f"[{page.get('index')}] url={page.get('url')!r} "
            f"is_closed={page.get('is_closed')}"
        )
        if page.get("url_error"):
            lines.append(f"  url_error={page['url_error']}")
    lines.append("")
    lines.append("=== Selected page ===")
    lines.append(f"url={payload.get('selected_page_url')!r}")
    lines.append("")
    lines.append("=== CDP capability probes ===")
    probes = payload.get("cdp_probes") or []
    if not probes:
        for frame in payload.get("frames") or []:
            probes.extend(frame.get("probes") or [])
    for probe in probes:
        if probe.get("ok"):
            lines.append(
                f"PROBE {probe.get('probe')}: OK -> {probe.get('summary')!r}"
            )
        else:
            lines.append(f"PROBE {probe.get('probe')}: FAIL")
            lines.append(
                f"  cdp_method={probe.get('cdp_method')}"
            )
            lines.append(
                f"  exception={probe.get('exception_class')}: "
                f"{probe.get('exception_message')}"
            )
            tb = probe.get("traceback")
            if tb:
                lines.append("  traceback:")
                lines.append(tb.rstrip())
    lines.append("")
    failure = payload.get("first_failure")
    if failure:
        lines.append("=== FIRST FAILURE (stopped) ===")
        lines.append(f"probe={failure.get('probe')!r}")
        lines.append(f"cdp_method={failure.get('cdp_method')!r}")
        lines.append(f"frame_url={failure.get('frame_url')!r}")
        lines.append(
            f"exception={failure.get('exception_class')}: "
            f"{failure.get('exception_message')}"
        )
        tb = failure.get("traceback")
        if tb:
            lines.append("traceback:")
            lines.append(tb.rstrip())
    else:
        lines.append("=== FIRST FAILURE ===")
        lines.append("None (all CDP probes succeeded)")
    return "\n".join(lines) + "\n"


FIND_TEXT_SNIPPET_MAX_CHARS = 200
FIND_TEXT_MAX_MATCHES = 40
FIND_TEXT_PARENT_CHAIN_MAX = 5
FIND_TEXT_ACTION_DESCENDANT_MAX = 12

DEFAULT_BROWSER_WATCH_TERMS = ("expire", "Your session", "Continue", "Log Out")
DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS = 1
DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS = 600

DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS = 1
DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS = 900
DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS = 90
DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS = 1
# ReadUserSession.v1 is also the SESSION_API keepalive action and may refresh
# idle timeout. Keep canonical verification slower than browser evidence polls.
DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS = 5
DEFAULT_BROWSER_RECORD_STARTUP_RETRY_SECONDS = 10
DEFAULT_BROWSER_RECORD_STARTUP_RETRY_INTERVAL_SECONDS = 1
# Observation-only search terms. Deliberately omits "Log Out" (always in nav).
DEFAULT_BROWSER_RECORD_SEARCH_TERMS = (
    "expire",
    "session",
    "continue",
    "stay signed in",
    "still there",
    "timed out",
)
BROWSER_RECORD_TEXT_SUMMARY_MAX_CHARS = INSPECTION_TEXT_MAX_CHARS
BROWSER_RECORD_MATCH_SUMMARY_MAX = 5
# CLI POST body and HTTP parser must stay aligned on these field names.
BROWSER_RECORD_EXPIRATION_REQUEST_FIELDS = (
    "provider",
    "interval_seconds",
    "timeout_seconds",
    "rolling_window_seconds",
    "screenshot_every_seconds",
    "verification_interval_seconds",
    "output_dir",
)
BROWSER_RECORD_DOCUMENTED_OUTCOMES = frozenset(
    {
        "logged_out",
        "timeout",
        "initial_not_signed_in",
        "initial_authentication_unknown",
        "fatal_error",
    }
)
REQUEST_JSON_ERROR_BODY_MAX_CHARS = 4_000

# One-command Amex expiration experiment (developer-only orchestration).
DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS = 600
DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_INTERVAL_SECONDS = 30
DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS = 900
DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS = 1
DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS = 5
DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS = 90
DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS = 1
DEFAULT_EXPIRATION_EXPERIMENT_VERIFY_RETRY_SECONDS = 10
DEFAULT_EXPIRATION_EXPERIMENT_VERIFY_RETRY_INTERVAL_SECONDS = 1
DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_SLACK_SECONDS = 10
DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_MAX_SECONDS = 60
DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_POLL_SECONDS = 1
EXPIRATION_EXPERIMENT_DIR_PREFIX = "amex-expiration-experiment-"
EXPIRATION_CAMPAIGN_DIR_PREFIX = "amex-expiration-campaign-"
EXPIRATION_CAMPAIGN_MANIFEST_FILENAME = "campaign-manifest.json"
EXPIRATION_CAMPAIGN_SUMMARY_JSON = "campaign-summary.json"
EXPIRATION_CAMPAIGN_SUMMARY_CSV = "campaign-summary.csv"
EXPIRATION_CAMPAIGN_REPORT_MD = "campaign-report.md"
EXPIRATION_CAMPAIGN_TRIAL_SUMMARY_FIELDS = (
    "trial_number",
    "strategy",
    "keepalive_interval_seconds",
    "started_at",
    "completed_at",
    "duration_seconds",
    "recorder_outcome",
    "keepalive_outcome",
    "initial_authentication_state",
    "final_authentication_state",
    "idle_warning_detected",
    "idle_warning_first_observed_at",
    "logged_out",
    "logout_observed_at",
    "warning_to_logout_seconds",
    "keepalive_wait_seconds",
    "keepalive_completion_timeout",
    "preflight_ok",
    "result_classification",
    "error",
    "evidence_directory",
)
MANAGED_BROWSER_HEALTHY = "HEALTHY"
MANAGED_BROWSER_ABSENT = "ABSENT"
MANAGED_BROWSER_UNHEALTHY = "UNHEALTHY"
BROWSER_CLEANUP_LEAVE_OPEN = "leave-open"
BROWSER_CLEANUP_CLOSE_ON_COMPLETION = "close-on-completion"
BROWSER_CLEANUP_POLICIES = (
    BROWSER_CLEANUP_LEAVE_OPEN,
    BROWSER_CLEANUP_CLOSE_ON_COMPLETION,
)
DEFAULT_BROWSER_CLEANUP_POLICY = BROWSER_CLEANUP_CLOSE_ON_COMPLETION
DEFAULT_MANAGED_BROWSER_STARTUP_TIMEOUT_SECONDS = 30.0
DEFAULT_PROVIDER_RUNTIME_HEALTH_TIMEOUT_SECONDS = 30.0
DEFAULT_AMEX_CAMPAIGN_NAME = "amex-keepalive-comparison"
DEFAULT_AMEX_CAMPAIGN_TRIALS = (
    "NONE:30",
    "SESSION_API:30",
    "SESSION_API:5",
    "PAGE_ACTIVITY:30",
    "OVERVIEW_RELOAD:30",
)


def expiration_experiment_keepalive_convergence_timeout_seconds(
    keepalive_interval_seconds: float | int,
) -> float:
    """Allow one natural keepalive tick, plus slack, capped for safety."""
    return min(
        float(keepalive_interval_seconds)
        + float(DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_SLACK_SECONDS),
        float(DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_MAX_SECONDS),
    )
EXPIRATION_EXPERIMENT_SERVE_HINT = (
    "Start the Provider Runtime with: "
    "PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py serve"
)
EXPIRATION_EXPERIMENT_BOOTSTRAP_HINT = (
    "Amex is SIGNED_OUT. Sign in first with: "
    "PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py bootstrap amex"
)


class ProviderRuntimeHTTPError(Exception):
    """Raised when a localhost runtime HTTP call returns an unexpected 4xx/5xx."""

    def __init__(self, status: int, path: str, body: str) -> None:
        self.status = status
        self.path = path
        self.body = body
        super().__init__(f"HTTP {status} from {path}")


def _sanitize_validation_error(message: str) -> str:
    """Bound a validation error for logs (no headers, cookies, or page content)."""
    text = " ".join(str(message or "").split())
    if len(text) > 240:
        return text[:240] + "…"
    return text


def log_rejected_diagnostic_request(
    *,
    route: str,
    status: int,
    error: str,
) -> None:
    """Log a sanitized rejection for a developer-diagnostic HTTP request."""
    line = (
        f"{iso_now()} rejected_diagnostic_request "
        f"route={route} status={status} "
        f"error={_sanitize_validation_error(error)}"
    )
    print(line, file=sys.stderr)
    try:
        DEFAULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEFAULT_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def _parse_non_negative_float_field(
    body: dict[str, Any],
    name: str,
    default: float,
) -> float:
    if name not in body or body.get(name) is None:
        raw: Any = default
    else:
        raw = body.get(name)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value != value or value < 0:  # NaN or negative
        raise ValueError(f"{name} must be a non-negative number")
    return value


def parse_browser_record_expiration_request(body: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a browser-record-expiration JSON body.

    Field names/types match the CLI payload constructed for this command.
    Raises ValueError with a sanitized message on malformed input.
    """
    if not isinstance(body, dict):
        raise ValueError("Request body must be a JSON object")

    provider_raw = body.get("provider", "amex")
    if provider_raw is None:
        provider = "amex"
    else:
        provider = str(provider_raw).strip()
    if provider != "amex":
        raise ValueError("provider must be 'amex'")

    interval_seconds = _parse_non_negative_float_field(
        body,
        "interval_seconds",
        float(DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS),
    )
    timeout_seconds = _parse_non_negative_float_field(
        body,
        "timeout_seconds",
        float(DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS),
    )
    rolling_window_seconds = _parse_non_negative_float_field(
        body,
        "rolling_window_seconds",
        float(DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS),
    )
    screenshot_every_seconds = _parse_non_negative_float_field(
        body,
        "screenshot_every_seconds",
        float(DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS),
    )
    verification_interval_seconds = _parse_non_negative_float_field(
        body,
        "verification_interval_seconds",
        float(DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS),
    )

    output_raw = body.get("output_dir")
    if output_raw is not None and not isinstance(output_raw, str):
        raise ValueError("output_dir must be a string or null")
    output_dir = Path(output_raw).expanduser() if output_raw else None

    return {
        "provider": provider,
        "interval_seconds": interval_seconds,
        "timeout_seconds": timeout_seconds,
        "rolling_window_seconds": rolling_window_seconds,
        "screenshot_every_seconds": screenshot_every_seconds,
        "verification_interval_seconds": verification_interval_seconds,
        "output_dir": output_dir,
    }


def browser_record_expiration_http_status(payload: dict[str, Any]) -> HTTPStatus:
    """Map recorder diagnostic payloads to HTTP status codes.

    Documented recorder outcomes (including initial_not_signed_in) are HTTP 200
    with a structured JSON body. Request validation failures use HTTP 400.
    """
    outcome = payload.get("outcome")
    if payload.get("ok") or outcome in BROWSER_RECORD_DOCUMENTED_OUTCOMES:
        return HTTPStatus.OK
    return HTTPStatus.OK


def build_browser_record_expiration_cli_payload(args: argparse.Namespace) -> dict[str, Any]:
    """Build the POST JSON body for the browser-record-expiration CLI command."""
    output_dir = getattr(args, "output_dir", None)
    if output_dir is not None:
        output_dir = Path(output_dir).expanduser().resolve()
    return {
        "provider": str(args.provider),
        "interval_seconds": float(args.interval_seconds),
        "timeout_seconds": float(args.timeout_seconds),
        "rolling_window_seconds": float(args.rolling_window_seconds),
        "screenshot_every_seconds": float(args.screenshot_every_seconds),
        "verification_interval_seconds": float(
            getattr(
                args,
                "verification_interval_seconds",
                DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS,
            )
        ),
        "output_dir": str(output_dir) if output_dir else None,
    }


def default_expiration_experiment_dir(
    diagnostics_dir: Path | None = None,
    *,
    when: datetime | None = None,
) -> Path:
    """Default ~/.mighty/provider_runtime/diagnostics/amex-expiration-experiment-<UTC>/."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
    return base / f"{EXPIRATION_EXPERIMENT_DIR_PREFIX}{stamp}"


def find_latest_expiration_experiment_dir(
    diagnostics_dir: Path | None = None,
) -> Path | None:
    """Return the newest amex-expiration-experiment-* directory, if any."""
    base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
    if not base.is_dir():
        return None
    candidates = sorted(
        (
            path
            for path in base.iterdir()
            if path.is_dir() and path.name.startswith(EXPIRATION_EXPERIMENT_DIR_PREFIX)
        ),
        key=lambda path: path.name,
    )
    return candidates[-1] if candidates else None


def open_latest_expiration_experiment(
    diagnostics_dir: Path | None = None,
    *,
    open_fn: Any = None,
) -> dict[str, Any]:
    """Reveal the latest expiration experiment directory (macOS Finder via ``open``)."""
    latest = find_latest_expiration_experiment_dir(diagnostics_dir)
    if latest is None:
        base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
        return {
            "ok": False,
            "error": "no_expiration_experiment",
            "message": (
                "No Amex expiration experiment directory found under "
                f"{base}. Run browser-run-expiration-experiment amex first."
            ),
            "experiment_dir": None,
        }
    opener = open_fn or (lambda path: subprocess.run(["open", str(path)], check=False))
    opener(latest)
    return {
        "ok": True,
        "experiment_dir": str(latest.resolve()),
        "message": f"Opened {latest.resolve()}",
    }


def create_expiration_experiment_zip(experiment_dir: Path) -> Path:
    """Zip experiment evidence (excluding the zip file itself)."""
    experiment_dir = Path(experiment_dir)
    zip_path = experiment_dir / f"{experiment_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(experiment_dir.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            archive.write(path, arcname=str(path.relative_to(experiment_dir)))
    return zip_path.resolve()


def _expiration_experiment_base_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _auth_state_from_verify_payload(payload: dict[str, Any]) -> str:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        return str(result.get("authentication_state") or "LOGIN_UNKNOWN")
    return str(payload.get("authentication_state") or "LOGIN_UNKNOWN")


def _keepalive_outcome_from_status(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return "unknown"
    reason = status.get("keepalive_final_reason")
    if reason:
        return str(reason)
    if status.get("keepalive_logged_out"):
        return "logged_out"
    if status.get("keepalive_trial_running"):
        return "still_running"
    return "unknown"


def wait_for_keepalive_convergence(
    *,
    base_url: str,
    request_json_fn: Any,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    timeout_seconds: float,
    poll_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_POLL_SECONDS
    ),
) -> dict[str, Any]:
    """Poll keepalive-status until the trial stops or the timeout elapses.

    Used after the expiration recorder reports ``logged_out`` so the ZIP captures
    a converged keepalive status instead of a still-running race. Does not wake
    or stop the keepalive worker; it only observes ``keepalive-status``.
    """
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    started = monotonic()
    deadline = started + max(0.0, float(timeout_seconds))
    poll = max(0.0, float(poll_seconds))
    last_status: dict[str, Any] | None = None
    while True:
        try:
            last_status = request_json_fn(
                "GET",
                f"{base_url}/providers/amex/keepalive/status",
            )
        except Exception as exc:  # noqa: BLE001
            last_status = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "keepalive_trial_running": True,
            }
        running = bool(
            isinstance(last_status, dict) and last_status.get("keepalive_trial_running")
        )
        now = monotonic()
        elapsed = max(0.0, now - started)
        if not running:
            return {
                "status": last_status,
                "timed_out": False,
                "wait_seconds": elapsed,
                "completed_at": iso_now(),
            }
        if now >= deadline:
            return {
                "status": last_status,
                "timed_out": True,
                "wait_seconds": elapsed,
                "completed_at": None,
            }
        sleep(min(poll, max(0.0, deadline - now)))


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _bounded_runtime_diagnostics_snapshot(
    status_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Sanitize/bound status fields suitable for experiment evidence."""
    if not isinstance(status_payload, dict):
        return {"ok": False, "error": "status_unavailable"}
    keys = (
        "ok",
        "runtime_pid",
        "started_at",
        "chrome_pid",
        "chrome_running",
        "cdp_url",
        "profile_dir",
        "maintenance_running",
        "last_maintenance_attempt_at",
        "last_maintenance_result",
        "keepalive_trial_running",
        "keepalive_trial_id",
        "keepalive_strategy",
        "keepalive_started_at",
        "keepalive_completed_at",
        "keepalive_latest_authentication_state",
        "keepalive_latest_authentication_state_source",
        "keepalive_latest_reason",
        "keepalive_latest_observed_at",
        "keepalive_final_authentication_state",
        "keepalive_final_reason",
        "keepalive_logged_out",
        "authentication_state",
    )
    return {key: status_payload.get(key) for key in keys}


def verify_amex_signed_in_for_experiment(
    *,
    base_url: str,
    request_json_fn: Any,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    retry_seconds: float = DEFAULT_EXPIRATION_EXPERIMENT_VERIFY_RETRY_SECONDS,
    retry_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_VERIFY_RETRY_INTERVAL_SECONDS
    ),
) -> dict[str, Any]:
    """Fresh canonical verify with LOGIN_UNKNOWN retry; require SIGNED_IN."""
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    deadline = monotonic() + max(0.0, float(retry_seconds))
    last_payload: dict[str, Any] | None = None
    last_state = "LOGIN_UNKNOWN"
    while True:
        last_payload = request_json_fn("POST", f"{base_url}/providers/amex/verify")
        last_state = _auth_state_from_verify_payload(last_payload)
        if last_state == "SIGNED_IN":
            return {
                "ok": True,
                "authentication_state": last_state,
                "verify_payload": last_payload,
            }
        if last_state == "SIGNED_OUT":
            return {
                "ok": False,
                "authentication_state": last_state,
                "outcome": "initial_not_signed_in",
                "message": EXPIRATION_EXPERIMENT_BOOTSTRAP_HINT,
                "verify_payload": last_payload,
            }
        now = monotonic()
        if now >= deadline:
            return {
                "ok": False,
                "authentication_state": last_state,
                "outcome": "initial_authentication_unknown",
                "message": (
                    "Amex authentication remained LOGIN_UNKNOWN after "
                    f"{float(retry_seconds):g}s. Re-run bootstrap or verify, then retry."
                ),
                "verify_payload": last_payload,
            }
        sleep(min(float(retry_interval_seconds), max(0.0, deadline - now)))


def run_amex_expiration_experiment(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    diagnostics_dir: Path | None = None,
    output_dir: Path | None = None,
    strategy: str = "NONE",
    trial_duration_seconds: int = DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS,
    keepalive_interval_seconds: int = (
        DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_INTERVAL_SECONDS
    ),
    recording_timeout_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS
    ),
    evidence_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS
    ),
    verification_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS
    ),
    rolling_window_seconds: float = DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS,
    screenshot_every_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS
    ),
    request_json_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    wait_poll_seconds: float = 0.2,
    keepalive_convergence_timeout_seconds: float | None = None,
    keepalive_convergence_poll_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_CONVERGENCE_POLL_SECONDS
    ),
) -> dict[str, Any]:
    """Orchestrate keepalive + expiration recorder into one evidence ZIP.

    Client-side only: talks to an already-running ``serve`` over localhost HTTP.
    Does not acquire the runtime lock, does not mutate the Amex page, and does
    not start/stop ``serve`` or kill Chrome.

    After the recorder reports ``logged_out``, waits for the keepalive trial to
    finish on its natural schedule (up to interval + slack, capped) so the ZIP
    captures a consistent final state. Does not wake or stop the worker.
    """
    selected_strategy = str(strategy or "NONE")
    if selected_strategy not in KEEPALIVE_STRATEGIES:
        raise ValueError(
            f"Unsupported keepalive strategy {selected_strategy!r}. "
            f"Expected one of {', '.join(KEEPALIVE_STRATEGIES)}"
        )
    http = request_json_fn or request_json
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    base_url = _expiration_experiment_base_url(host, port)
    diagnostics = Path(diagnostics_dir) if diagnostics_dir else DEFAULT_DIAGNOSTICS_DIR
    started_at = iso_now()
    experiment_started_mono = monotonic()
    if keepalive_convergence_timeout_seconds is None:
        resolved_convergence_timeout_seconds = (
            expiration_experiment_keepalive_convergence_timeout_seconds(
                keepalive_interval_seconds
            )
        )
    else:
        resolved_convergence_timeout_seconds = float(
            keepalive_convergence_timeout_seconds
        )
    call_log: list[str] = []

    def tracked_request(method: str, url: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        path = urlsplit(url).path or url
        call_log.append(f"{method} {path}")
        if payload is None:
            return http(method, url, **kwargs)
        return http(method, url, payload, **kwargs)

    # 1) Runtime must already be serving.
    try:
        tracked_request("GET", f"{base_url}/health")
    except (URLError, TimeoutError, OSError, ConnectionError) as exc:
        return {
            "ok": False,
            "outcome": "runtime_unavailable",
            "keepalive_outcome": None,
            "final_authentication_state": None,
            "experiment_dir": None,
            "zip_path": None,
            "message": EXPIRATION_EXPERIMENT_SERVE_HINT,
            "error": f"{type(exc).__name__}: {exc}",
            "exit_code": 1,
            "http_calls": list(call_log),
        }

    # 2) Fresh canonical verification before any trial/recorder work.
    verified = verify_amex_signed_in_for_experiment(
        base_url=base_url,
        request_json_fn=tracked_request,
        sleep_fn=sleep,
        monotonic_fn=monotonic,
    )
    if not verified.get("ok"):
        return {
            "ok": False,
            "outcome": verified.get("outcome") or "initial_not_signed_in",
            "keepalive_outcome": None,
            "final_authentication_state": verified.get("authentication_state"),
            "experiment_dir": None,
            "zip_path": None,
            "message": verified.get("message"),
            "verify_payload": verified.get("verify_payload"),
            "exit_code": 1,
            "http_calls": list(call_log),
        }

    experiment_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else default_expiration_experiment_dir(diagnostics)
    )
    experiment_dir.mkdir(parents=True, exist_ok=True)
    recorder_dir = experiment_dir / "recorder"
    recorder_dir.mkdir(parents=True, exist_ok=True)

    keepalive_start_payload: dict[str, Any] | None = None
    recorder_payload: dict[str, Any] | None = None
    keepalive_status_payload: dict[str, Any] | None = None
    runtime_status_payload: dict[str, Any] | None = None
    interrupted = False
    recorder_error: str | None = None
    outcome = "fatal_error"
    recorder_started_mono: float | None = None
    recorder_completed_at: str | None = None
    recorder_duration_seconds: float | None = None
    keepalive_completed_at: str | None = None
    keepalive_wait_seconds = 0.0
    keepalive_completion_timeout = False

    # 3) Start keepalive trial (returns once the server thread is running).
    try:
        keepalive_start_payload = tracked_request(
            "POST",
            f"{base_url}/providers/amex/keepalive/start",
            {
                "strategy": selected_strategy,
                "duration_seconds": int(trial_duration_seconds),
                "interval_seconds": int(keepalive_interval_seconds),
            },
        )
    except ProviderRuntimeHTTPError as exc:
        return {
            "ok": False,
            "outcome": "keepalive_start_failed",
            "keepalive_outcome": None,
            "final_authentication_state": verified.get("authentication_state"),
            "experiment_dir": str(experiment_dir),
            "zip_path": None,
            "message": (
                f"Failed to start {selected_strategy} keepalive trial: "
                f"HTTP {exc.status}"
            ),
            "error": exc.body or str(exc),
            "exit_code": 1,
            "http_calls": list(call_log),
        }
    if not keepalive_start_payload.get("ok"):
        return {
            "ok": False,
            "outcome": "keepalive_start_failed",
            "keepalive_outcome": None,
            "final_authentication_state": verified.get("authentication_state"),
            "experiment_dir": str(experiment_dir),
            "zip_path": None,
            "message": str(
                keepalive_start_payload.get("reason")
                or keepalive_start_payload.get("error")
                or "keepalive_start_failed"
            ),
            "keepalive_start": keepalive_start_payload,
            "exit_code": 1,
            "http_calls": list(call_log),
        }

    # 4) Start recorder immediately; wait without holding any runtime lock.
    #    Keepalive continues on the server in its own thread (same as the
    #    three-terminal workflow).
    http_timeout = float(recording_timeout_seconds) + 120.0
    recorder_holder: dict[str, Any] = {"payload": None, "error": None}
    recorder_done = threading.Event()

    def _recorder_worker() -> None:
        try:
            recorder_holder["payload"] = tracked_request(
                "POST",
                f"{base_url}/providers/amex/diagnostics/browser-record-expiration",
                {
                    "provider": "amex",
                    "interval_seconds": float(evidence_interval_seconds),
                    "timeout_seconds": float(recording_timeout_seconds),
                    "rolling_window_seconds": float(rolling_window_seconds),
                    "screenshot_every_seconds": float(screenshot_every_seconds),
                    "verification_interval_seconds": float(verification_interval_seconds),
                    "output_dir": str(recorder_dir),
                },
                timeout=http_timeout,
            )
        except Exception as exc:  # noqa: BLE001 - preserve evidence on any failure
            recorder_holder["error"] = exc
        finally:
            recorder_done.set()

    recorder_thread = threading.Thread(
        target=_recorder_worker,
        name="amex-expiration-experiment-recorder",
        daemon=True,
    )
    recorder_started_mono = monotonic()
    recorder_thread.start()
    try:
        while not recorder_done.wait(timeout=max(0.05, float(wait_poll_seconds))):
            # Cooperative wait so Ctrl+C can interrupt orchestration cleanly.
            _ = monotonic()
    except KeyboardInterrupt:
        interrupted = True

    recorder_completed_at = iso_now()
    recorder_finished_mono = monotonic()
    if recorder_started_mono is not None:
        recorder_duration_seconds = max(0.0, recorder_finished_mono - recorder_started_mono)

    if recorder_holder["payload"] is not None:
        recorder_payload = recorder_holder["payload"]
        outcome = str(recorder_payload.get("outcome") or "fatal_error")
    elif recorder_holder["error"] is not None:
        exc = recorder_holder["error"]
        recorder_error = f"{type(exc).__name__}: {exc}"
        outcome = "fatal_error"
    if interrupted:
        outcome = "interrupted"

    # 5) After logged_out, wait for keepalive to converge before packaging.
    #    Other outcomes collect status immediately (no indefinite hang).
    #    Do not wake/stop the worker or invoke canonical verify here.
    convergence_wait_entered = False
    if outcome == "logged_out" and not interrupted:
        convergence_wait_entered = True
        convergence = wait_for_keepalive_convergence(
            base_url=base_url,
            request_json_fn=tracked_request,
            sleep_fn=sleep,
            monotonic_fn=monotonic,
            timeout_seconds=float(resolved_convergence_timeout_seconds),
            poll_seconds=float(keepalive_convergence_poll_seconds),
        )
        keepalive_status_payload = convergence.get("status")
        keepalive_wait_seconds = float(convergence.get("wait_seconds") or 0.0)
        keepalive_completion_timeout = bool(convergence.get("timed_out"))
        keepalive_completed_at = convergence.get("completed_at")
    else:
        try:
            keepalive_status_payload = tracked_request(
                "GET",
                f"{base_url}/providers/amex/keepalive/status",
            )
        except Exception as exc:  # noqa: BLE001
            keepalive_status_payload = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if isinstance(keepalive_status_payload, dict) and not keepalive_status_payload.get(
            "keepalive_trial_running"
        ):
            keepalive_completed_at = (
                keepalive_status_payload.get("keepalive_completed_at") or iso_now()
            )

    try:
        runtime_status_payload = tracked_request("GET", f"{base_url}/status")
    except Exception:
        runtime_status_payload = None

    should_stop_keepalive = bool(
        isinstance(keepalive_status_payload, dict)
        and keepalive_status_payload.get("keepalive_trial_running")
        and (
            interrupted
            or outcome
            in {
                "fatal_error",
                "initial_not_signed_in",
                "initial_authentication_unknown",
            }
            or recorder_error is not None
        )
    )
    if should_stop_keepalive:
        try:
            tracked_request(
                "POST",
                f"{base_url}/providers/amex/keepalive/stop",
            )
            keepalive_status_payload = tracked_request(
                "GET",
                f"{base_url}/providers/amex/keepalive/status",
            )
            if isinstance(keepalive_status_payload, dict) and not keepalive_status_payload.get(
                "keepalive_trial_running"
            ):
                keepalive_completed_at = (
                    keepalive_status_payload.get("keepalive_completed_at") or iso_now()
                )
        except Exception:
            pass

    keepalive_status_path = experiment_dir / "keepalive-status.json"
    if isinstance(keepalive_status_payload, dict):
        status_for_file = dict(keepalive_status_payload)
        attempts_payload = status_for_file.pop("keepalive_attempts", None)
        _write_json_file(keepalive_status_path, status_for_file)
        if isinstance(attempts_payload, list):
            write_keepalive_attempts_jsonl(
                experiment_dir / KEEPALIVE_ATTEMPTS_FILENAME,
                attempts_payload,
            )
    else:
        _write_json_file(
            keepalive_status_path,
            {"ok": False, "error": "keepalive_status_unavailable"},
        )
    diagnostics_path = experiment_dir / "runtime-status.json"
    _write_json_file(
        diagnostics_path,
        _bounded_runtime_diagnostics_snapshot(runtime_status_payload),
    )

    final_auth = None
    if isinstance(recorder_payload, dict):
        final_auth = recorder_payload.get("final_canonical_authentication_state")
        if final_auth is None:
            final_auth = recorder_payload.get("final_authentication_state")
    if final_auth is None and isinstance(keepalive_status_payload, dict):
        final_auth = keepalive_status_payload.get("keepalive_final_authentication_state")
    if final_auth is None:
        final_auth = verified.get("authentication_state")

    keepalive_outcome = _keepalive_outcome_from_status(keepalive_status_payload)
    completed_at = iso_now()
    experiment_duration_seconds = max(0.0, monotonic() - experiment_started_mono)
    summary = {
        "provider": "amex",
        "outcome": outcome,
        "keepalive_outcome": keepalive_outcome,
        "final_authentication_state": final_auth,
        "started_at": started_at,
        "completed_at": completed_at,
        "recorder_completed_at": recorder_completed_at,
        "keepalive_completed_at": keepalive_completed_at,
        "keepalive_wait_seconds": keepalive_wait_seconds,
        "keepalive_completion_timeout": keepalive_completion_timeout,
        "keepalive_convergence_timeout_seconds": float(
            resolved_convergence_timeout_seconds
        ),
        "recorder_duration_seconds": recorder_duration_seconds,
        "experiment_duration_seconds": experiment_duration_seconds,
        "interrupted": interrupted,
        "keepalive_strategy": selected_strategy,
        "trial_duration_seconds": int(trial_duration_seconds),
        "keepalive_interval_seconds": int(keepalive_interval_seconds),
        "recording_timeout_seconds": float(recording_timeout_seconds),
        "evidence_interval_seconds": float(evidence_interval_seconds),
        "verification_interval_seconds": float(verification_interval_seconds),
        "rolling_window_seconds": float(rolling_window_seconds),
        "screenshot_every_seconds": float(screenshot_every_seconds),
        "experiment_dir": str(experiment_dir),
        "recorder_dir": str(recorder_dir),
        "recorder_outcome": (
            None if recorder_payload is None else recorder_payload.get("outcome")
        ),
        "recorder_error": recorder_error,
        "keepalive_start_ok": bool(
            isinstance(keepalive_start_payload, dict) and keepalive_start_payload.get("ok")
        ),
        "keepalive_trial_id": (
            None
            if not isinstance(keepalive_start_payload, dict)
            else keepalive_start_payload.get("trial_id")
            or keepalive_start_payload.get("keepalive_trial_id")
        ),
    }
    summary_path = experiment_dir / "experiment-summary.json"
    _write_json_file(summary_path, summary)

    zip_path = create_expiration_experiment_zip(experiment_dir)
    summary["zip_path"] = str(zip_path)
    _write_json_file(summary_path, summary)

    exit_code = 130 if interrupted else 0
    if outcome == "fatal_error" and not interrupted:
        exit_code = 1

    return {
        "ok": outcome
        in {
            "logged_out",
            "timeout",
            "interrupted",
            "initial_not_signed_in",
            "initial_authentication_unknown",
        },
        "outcome": outcome,
        "keepalive_outcome": keepalive_outcome,
        "final_authentication_state": final_auth,
        "experiment_dir": str(experiment_dir),
        "zip_path": str(zip_path),
        "summary": summary,
        "recorder": recorder_payload,
        "keepalive_status": keepalive_status_payload,
        "recorder_completed_at": recorder_completed_at,
        "keepalive_completed_at": keepalive_completed_at,
        "keepalive_wait_seconds": keepalive_wait_seconds,
        "keepalive_completion_timeout": keepalive_completion_timeout,
        "keepalive_convergence_timeout_seconds": float(
            resolved_convergence_timeout_seconds
        ),
        "recorder_duration_seconds": recorder_duration_seconds,
        "experiment_duration_seconds": experiment_duration_seconds,
        "convergence_wait_entered": convergence_wait_entered,
        "exit_code": exit_code,
        "http_calls": list(call_log),
        "message": None,
    }


def print_expiration_experiment_result(result: dict[str, Any]) -> None:
    """Print the concise experiment CLI result (or early failure hint)."""
    message = result.get("message")
    if message and result.get("zip_path") is None:
        print(str(message), file=sys.stderr)
        return

    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    outcome = result.get("outcome")
    keepalive_outcome = result.get("keepalive_outcome")
    strategy = summary.get("keepalive_strategy") or "NONE"
    wait_seconds = result.get("keepalive_wait_seconds")
    if wait_seconds is None:
        wait_seconds = summary.get("keepalive_wait_seconds")
    timed_out = result.get("keepalive_completion_timeout")
    if timed_out is None:
        timed_out = summary.get("keepalive_completion_timeout")
    interval_seconds = summary.get("keepalive_interval_seconds")
    convergence_timeout = (
        result.get("keepalive_convergence_timeout_seconds")
        if result.get("keepalive_convergence_timeout_seconds") is not None
        else summary.get("keepalive_convergence_timeout_seconds")
    )
    keepalive_status = (
        result.get("keepalive_status")
        if isinstance(result.get("keepalive_status"), dict)
        else {}
    )
    latest_state = keepalive_status.get("keepalive_latest_authentication_state")
    final_state = keepalive_status.get("keepalive_final_authentication_state")
    if latest_state is None:
        latest_state = summary.get("keepalive_latest_authentication_state")
    if final_state is None:
        final_state = result.get("final_authentication_state")

    print("----------------------------------------")
    print(f"Strategy: {strategy}")
    print(f"Recorder outcome: {outcome}")
    if outcome == "logged_out":
        print()
        print("Waiting for keepalive convergence...")
        if interval_seconds is not None:
            print(f"    keepalive interval: {int(interval_seconds)} seconds")
        if convergence_timeout is not None:
            print(f"    maximum wait: {float(convergence_timeout):.0f} seconds")
        if timed_out:
            print(
                f"    timed out after {float(convergence_timeout or wait_seconds or 0):.0f} seconds"
            )
        else:
            print(f"    finished after {float(wait_seconds or 0.0):.1f} seconds")
    print()
    print(f"Keepalive outcome: {keepalive_outcome}")
    if latest_state is not None:
        print(f"Latest observed state: {latest_state}")
    print(f"Final auth state: {final_state}")
    print()
    print("Creating evidence ZIP...")
    print("Done.")
    print()
    print("Evidence ZIP:")
    print(str(result.get("zip_path") or ""))


def parse_expiration_campaign_trial_spec(spec: str) -> dict[str, Any]:
    """Parse ``STRATEGY:KEEPALIVE_INTERVAL_SECONDS`` into a validated trial dict."""
    raw = str(spec or "").strip()
    if ":" not in raw:
        raise ValueError(
            f"Invalid trial specification {raw!r}. Expected STRATEGY:INTERVAL "
            f"(e.g. NONE:30). Strategies: {', '.join(KEEPALIVE_STRATEGIES)}"
        )
    strategy_raw, interval_raw = raw.split(":", 1)
    strategy = strategy_raw.strip().upper()
    if strategy not in KEEPALIVE_STRATEGIES:
        raise ValueError(
            f"Unsupported keepalive strategy {strategy_raw.strip()!r}. "
            f"Expected one of {', '.join(KEEPALIVE_STRATEGIES)}"
        )
    interval_text = interval_raw.strip()
    if not interval_text or not re.fullmatch(r"[0-9]+", interval_text):
        raise ValueError(
            f"Invalid keepalive interval {interval_raw!r} in trial {raw!r}. "
            "Expected a positive integer number of seconds."
        )
    interval_seconds = int(interval_text)
    if interval_seconds <= 0:
        raise ValueError(
            f"Invalid keepalive interval {interval_seconds} in trial {raw!r}. "
            "Expected a positive integer number of seconds."
        )
    return {
        "strategy": strategy,
        "keepalive_interval_seconds": interval_seconds,
        "spec": f"{strategy}:{interval_seconds}",
    }


def parse_expiration_campaign_trial_specs(
    specs: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    """Validate all campaign trial specs before any trial starts."""
    if not specs:
        raise ValueError("At least one --trial STRATEGY:INTERVAL is required")
    return [parse_expiration_campaign_trial_spec(spec) for spec in specs]


def expiration_campaign_trial_dirname(
    trial_number: int,
    strategy: str,
    keepalive_interval_seconds: int,
) -> str:
    """Stable per-trial directory name, e.g. ``001-none-30s``."""
    slug = str(strategy).lower().replace("_", "-")
    return f"{int(trial_number):03d}-{slug}-{int(keepalive_interval_seconds)}s"


def default_expiration_campaign_dir(
    diagnostics_dir: Path | None = None,
    *,
    when: datetime | None = None,
) -> Path:
    """Default ``~/.mighty/provider_runtime/diagnostics/amex-expiration-campaign-<UTC>/``."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
    return base / f"{EXPIRATION_CAMPAIGN_DIR_PREFIX}{stamp}"


def create_expiration_campaign_zip(campaign_dir: Path) -> Path:
    """Zip campaign summaries + trial evidence (excluding the zip itself)."""
    campaign_dir = Path(campaign_dir)
    zip_path = campaign_dir / f"{campaign_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(campaign_dir.rglob("*")):
            if not path.is_file() or path == zip_path:
                continue
            # Nested experiment ZIPs are evidence; include them. Exclude only
            # the campaign-level archive being written.
            archive.write(path, arcname=str(path.relative_to(campaign_dir)))
    return zip_path.resolve()


def _usable_cdp_page_targets(targets_payload: Any) -> list[dict[str, Any]]:
    """Return CDP targets that are usable page contexts for Amex work."""
    if not isinstance(targets_payload, list):
        return []
    usable: list[dict[str, Any]] = []
    for item in targets_payload:
        if not isinstance(item, dict):
            continue
        target_type = str(item.get("type") or "page").lower()
        if target_type in {"page", "webview"}:
            usable.append(item)
    return usable


def classify_managed_amex_browser(
    cdp_port: int,
    *,
    fetch_cdp_json_fn: Any = None,
) -> dict[str, Any]:
    """Classify the managed Amex CDP endpoint as HEALTHY / ABSENT / UNHEALTHY."""
    fetch = fetch_cdp_json_fn or fetch_cdp_json
    base = f"http://127.0.0.1:{int(cdp_port)}"
    try:
        version_payload = fetch(f"{base}/json/version")
        targets_payload = fetch(f"{base}/json/list")
    except Exception as exc:
        return {
            "state": MANAGED_BROWSER_ABSENT,
            "cdp_url": None,
            "websocket_url": None,
            "page_target_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    websocket_url = None
    if isinstance(version_payload, dict):
        websocket_url = version_payload.get("webSocketDebuggerUrl")
    if not websocket_url:
        return {
            "state": MANAGED_BROWSER_ABSENT,
            "cdp_url": None,
            "websocket_url": None,
            "page_target_count": 0,
            "error": "cdp_version_missing_websocket",
        }

    usable = _usable_cdp_page_targets(targets_payload)
    if not usable:
        return {
            "state": MANAGED_BROWSER_UNHEALTHY,
            "cdp_url": base,
            "websocket_url": str(websocket_url),
            "page_target_count": 0,
            "error": "zero_page_targets",
        }
    return {
        "state": MANAGED_BROWSER_HEALTHY,
        "cdp_url": base,
        "websocket_url": str(websocket_url),
        "page_target_count": len(usable),
        "error": None,
    }


def wait_for_managed_browser_ready(
    cdp_port: int,
    *,
    timeout_seconds: float = DEFAULT_MANAGED_BROWSER_STARTUP_TIMEOUT_SECONDS,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
) -> dict[str, Any]:
    """Wait until managed CDP has a websocket and at least one page target."""
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    deadline = monotonic() + max(0.0, float(timeout_seconds))
    last: dict[str, Any] = {
        "state": MANAGED_BROWSER_ABSENT,
        "cdp_url": None,
        "page_target_count": 0,
        "error": "not_ready",
    }
    while True:
        last = classify_managed_amex_browser(
            cdp_port,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
        if last.get("state") == MANAGED_BROWSER_HEALTHY:
            return last
        now = monotonic()
        if now >= deadline:
            raise RuntimeError(
                "Managed Amex Chrome did not become ready within "
                f"{float(timeout_seconds):g}s "
                f"(last_state={last.get('state')}, error={last.get('error')})"
            )
        sleep(min(0.25, max(0.0, deadline - now)))


def managed_chrome_appears_headless(
    profile_dir: Path,
    *,
    profile_processes_fn: Any = None,
    process_command_lines_fn: Any = None,
) -> bool:
    """True when a managed profile Chrome process command line includes --headless."""
    profile_dir = Path(profile_dir).expanduser().resolve()
    if process_command_lines_fn is not None:
        lines = list(process_command_lines_fn(profile_dir) or [])
        return any("--headless" in str(line) for line in lines)

    finder = profile_processes_fn or profile_processes
    pids = list(finder(profile_dir) or [])
    if not pids:
        return False
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    needle = f"--user-data-dir={profile_dir}"
    for line in result.stdout.splitlines():
        if needle not in line:
            continue
        if "--headless" in line:
            return True
    return False


def launch_managed_amex_browser(
    *,
    profile_dir: Path,
    cdp_port: int,
    initial_url: str = AMEX_LOGIN_URL,
    startup_timeout_seconds: float = DEFAULT_MANAGED_BROWSER_STARTUP_TIMEOUT_SECONDS,
    launch_native_chrome_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
) -> dict[str, Any]:
    """Launch headed managed Amex Chrome via ``launch_native_chrome`` and wait."""
    profile_dir = Path(profile_dir).expanduser().resolve()
    launcher = launch_native_chrome_fn or launch_native_chrome
    process = launcher(
        profile_dir=profile_dir,
        cdp_port=int(cdp_port),
        headless=False,
        initial_url=str(initial_url or AMEX_LOGIN_URL),
    )
    ready = wait_for_managed_browser_ready(
        int(cdp_port),
        timeout_seconds=float(startup_timeout_seconds),
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        fetch_cdp_json_fn=fetch_cdp_json_fn,
    )
    return {
        "ok": True,
        "cdp_url": ready.get("cdp_url"),
        "page_target_count": ready.get("page_target_count"),
        "chrome_pid": getattr(process, "pid", None),
        "profile_dir": str(profile_dir),
        "cdp_port": int(cdp_port),
        "initial_url": str(initial_url or AMEX_LOGIN_URL),
    }


def restart_managed_amex_browser(
    *,
    profile_dir: Path,
    cdp_port: int,
    initial_url: str = AMEX_LOGIN_URL,
    startup_timeout_seconds: float = DEFAULT_MANAGED_BROWSER_STARTUP_TIMEOUT_SECONDS,
    terminate_profile_processes_fn: Any = None,
    wait_for_profile_release_fn: Any = None,
    launch_native_chrome_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
) -> dict[str, Any]:
    """Stop only the Mighty Amex profile Chrome, then relaunch headed."""
    profile_dir = Path(profile_dir).expanduser().resolve()
    terminator = terminate_profile_processes_fn or terminate_profile_processes
    waiter = wait_for_profile_release_fn or wait_for_profile_release
    terminator(profile_dir)
    if not waiter(profile_dir):
        raise RuntimeError(
            f"Managed Amex profile lock was not released for {profile_dir}"
        )
    launched = launch_managed_amex_browser(
        profile_dir=profile_dir,
        cdp_port=int(cdp_port),
        initial_url=initial_url,
        startup_timeout_seconds=startup_timeout_seconds,
        launch_native_chrome_fn=launch_native_chrome_fn,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        fetch_cdp_json_fn=fetch_cdp_json_fn,
    )
    launched["restarted"] = True
    return launched


def bring_managed_amex_browser_to_foreground(
    profile_dir: Path,
    *,
    profile_processes_fn: Any = None,
    subprocess_run_fn: Any = None,
) -> dict[str, Any]:
    """Best-effort macOS foreground of the managed Mighty Chrome process only."""
    profile_dir = Path(profile_dir).expanduser().resolve()
    finder = profile_processes_fn or profile_processes
    runner = subprocess_run_fn or subprocess.run
    pids = list(finder(profile_dir) or [])
    if not pids:
        return {"ok": False, "error": "no_managed_chrome_process"}
    if sys.platform != "darwin":
        return {"ok": False, "error": "foreground_unsupported_platform", "pids": pids}
    pid = int(pids[0])
    script = (
        f'tell application "System Events" to set frontmost of '
        f"(first process whose unix id is {pid}) to true"
    )
    try:
        completed = runner(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "pids": pids}
    return {
        "ok": int(getattr(completed, "returncode", 1) or 0) == 0,
        "pid": pid,
        "pids": pids,
        "error": None
        if int(getattr(completed, "returncode", 1) or 0) == 0
        else (getattr(completed, "stderr", None) or "osascript_failed"),
    }


def ensure_managed_amex_browser_for_campaign(
    *,
    profile_dir: Path,
    cdp_port: int,
    startup_timeout_seconds: float = DEFAULT_MANAGED_BROWSER_STARTUP_TIMEOUT_SECONDS,
    prefer_headed_for_authentication: bool = True,
    classify_fn: Any = None,
    launch_fn: Any = None,
    restart_fn: Any = None,
    headless_fn: Any = None,
    print_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
    launch_native_chrome_fn: Any = None,
    terminate_profile_processes_fn: Any = None,
    wait_for_profile_release_fn: Any = None,
) -> dict[str, Any]:
    """Ensure a dedicated managed Amex Chrome window exists for the campaign."""
    emit = print_fn or print
    profile_dir = Path(profile_dir).expanduser().resolve()
    classify = classify_fn or (
        lambda: classify_managed_amex_browser(
            int(cdp_port),
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
    )
    launch = launch_fn or (
        lambda: launch_managed_amex_browser(
            profile_dir=profile_dir,
            cdp_port=int(cdp_port),
            startup_timeout_seconds=startup_timeout_seconds,
            launch_native_chrome_fn=launch_native_chrome_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
    )
    restart = restart_fn or (
        lambda: restart_managed_amex_browser(
            profile_dir=profile_dir,
            cdp_port=int(cdp_port),
            startup_timeout_seconds=startup_timeout_seconds,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
            wait_for_profile_release_fn=wait_for_profile_release_fn,
            launch_native_chrome_fn=launch_native_chrome_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
    )
    headless_check = headless_fn or (
        lambda: managed_chrome_appears_headless(profile_dir)
    )

    emit("Checking managed Amex browser...")
    classified = classify()
    state = str(classified.get("state") or MANAGED_BROWSER_ABSENT)
    preexisting = state in {MANAGED_BROWSER_HEALTHY, MANAGED_BROWSER_UNHEALTHY}
    launched = False
    restarted = False
    cdp_url = classified.get("cdp_url")

    if state == MANAGED_BROWSER_HEALTHY:
        if prefer_headed_for_authentication and headless_check():
            emit("Managed browser is headless; relaunching a visible window...")
            launched_info = restart()
            launched = True
            restarted = True
            cdp_url = launched_info.get("cdp_url")
            emit("Browser ready.")
        else:
            emit("Reusing existing managed Amex browser.")
            emit("Browser ready.")
    elif state == MANAGED_BROWSER_ABSENT:
        emit("No managed browser found.")
        emit("Launching dedicated Mighty Amex Chrome...")
        launched_info = launch()
        launched = True
        cdp_url = launched_info.get("cdp_url")
        emit("Browser ready.")
    else:
        emit("Managed browser is unhealthy (no usable page targets).")
        emit("Restarting dedicated Mighty Amex Chrome...")
        launched_info = restart()
        launched = True
        restarted = True
        cdp_url = launched_info.get("cdp_url")
        emit("Browser ready.")

    return {
        "ok": True,
        "state": state,
        "cdp_url": cdp_url,
        "managed_browser_preexisting": preexisting,
        "managed_browser_launched_by_campaign": launched,
        "managed_browser_restarted_by_campaign": restarted,
        "managed_cdp_port": int(cdp_port),
        "managed_profile_path": str(profile_dir),
    }


def maybe_close_managed_browser_for_campaign(
    *,
    browser_cleanup: str,
    managed_browser_preexisting: bool,
    managed_browser_launched_by_campaign: bool,
    interrupted: bool,
    profile_dir: Path,
    terminate_profile_processes_fn: Any = None,
) -> dict[str, Any]:
    """Close only a campaign-launched managed browser when policy allows."""
    policy = str(browser_cleanup or DEFAULT_BROWSER_CLEANUP_POLICY)
    if policy not in BROWSER_CLEANUP_POLICIES:
        policy = DEFAULT_BROWSER_CLEANUP_POLICY
    if policy != BROWSER_CLEANUP_CLOSE_ON_COMPLETION:
        return {
            "closed": False,
            "reason": "leave_open",
            "browser_cleanup_policy": policy,
        }
    if interrupted:
        return {
            "closed": False,
            "reason": "interrupted_leave_open",
            "browser_cleanup_policy": policy,
        }
    if managed_browser_preexisting:
        return {
            "closed": False,
            "reason": "preexisting_never_closed",
            "browser_cleanup_policy": policy,
        }
    if not managed_browser_launched_by_campaign:
        return {
            "closed": False,
            "reason": "not_launched_by_campaign",
            "browser_cleanup_policy": policy,
        }
    terminator = terminate_profile_processes_fn or terminate_profile_processes
    terminator(Path(profile_dir))
    return {
        "closed": True,
        "reason": "closed_campaign_launched_browser",
        "browser_cleanup_policy": policy,
        "closed_at": iso_now(),
    }


def _expiration_campaign_auth_pause_message(
    *,
    trial_number: int | None = None,
    browser_launched: bool = False,
) -> str:
    if trial_number is None:
        header = "Authentication required."
    else:
        header = f"Authentication required for trial {int(trial_number)}."
    if browser_launched:
        window_line = "A dedicated Mighty Amex Chrome window has been opened."
    else:
        window_line = "Use the dedicated Mighty Amex Chrome window."
    return (
        f"{header}\n"
        "\n"
        f"{window_line}\n"
        "Sign in and complete MFA.\n"
        "Wait until the account overview is fully loaded.\n"
        "Press Enter here when ready.\n"
    )


def _is_managed_browser_target_error(error: Any) -> bool:
    text = str(error or "")
    markers = (
        "no page targets",
        "Unable to attach to the managed Amex browser",
        "Browser websocket is alive, but there are no page targets",
    )
    return any(marker in text for marker in markers)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _observation_idle_warning_at(observation: dict[str, Any]) -> str | None:
    """Return observation timestamp when structured idle-warning evidence is present."""
    observed_at = observation.get("observed_at")
    inspector = observation.get("browser_inspector")
    if isinstance(inspector, dict):
        for candidate in inspector.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            conditions = classify_amex_expiration_candidate(candidate)
            if conditions.get("classified_as_expiration_dialog"):
                return str(observed_at) if observed_at else None
            # Soft structured signal: expiration language + Continue action.
            if (
                conditions.get("expiration_language_match")
                and conditions.get("continue_action_match")
            ):
                return str(observed_at) if observed_at else None

    searches = observation.get("optional_text_searches")
    if isinstance(searches, list):
        matched_terms = {
            str(item.get("term") or "").lower()
            for item in searches
            if isinstance(item, dict) and int(item.get("match_count") or 0) > 0
        }
        if "expire" in matched_terms and "continue" in matched_terms:
            return str(observed_at) if observed_at else None
    return None


def derive_expiration_campaign_trial_metrics(
    *,
    experiment_result: dict[str, Any] | None,
    experiment_dir: Path | None = None,
) -> dict[str, Any]:
    """Derive warning/logout timing from recorder + keepalive structured evidence."""
    recorder: dict[str, Any] = {}
    if isinstance(experiment_result, dict):
        if isinstance(experiment_result.get("recorder"), dict):
            recorder = experiment_result["recorder"]
        summary = experiment_result.get("summary")
        if not recorder and isinstance(summary, dict):
            recorder_dir = summary.get("recorder_dir")
            if recorder_dir:
                recording_path = Path(str(recorder_dir)) / "recording.json"
                if recording_path.is_file():
                    try:
                        loaded = json.loads(recording_path.read_text(encoding="utf-8"))
                        if isinstance(loaded, dict):
                            recorder = loaded
                    except (OSError, json.JSONDecodeError):
                        pass
    if not recorder and experiment_dir is not None:
        recording_path = Path(experiment_dir) / "recorder" / "recording.json"
        if recording_path.is_file():
            try:
                loaded = json.loads(recording_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    recorder = loaded
            except (OSError, json.JSONDecodeError):
                pass

    keepalive_status: dict[str, Any] = {}
    if isinstance(experiment_result, dict) and isinstance(
        experiment_result.get("keepalive_status"), dict
    ):
        keepalive_status = experiment_result["keepalive_status"]
    elif experiment_dir is not None:
        status_path = Path(experiment_dir) / "keepalive-status.json"
        if status_path.is_file():
            try:
                loaded = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    keepalive_status = loaded
            except (OSError, json.JSONDecodeError):
                pass

    idle_warning_detected = False
    idle_warning_first_observed_at: str | None = None

    events = keepalive_status.get("keepalive_events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") == "expiration_dialog" or event.get(
                "expiration_dialog_detected"
            ):
                idle_warning_detected = True
                idle_warning_first_observed_at = (
                    event.get("observed_at")
                    or event.get("at")
                    or event.get("timestamp")
                )
                if idle_warning_first_observed_at is not None:
                    idle_warning_first_observed_at = str(idle_warning_first_observed_at)
                break
    if not idle_warning_detected and keepalive_status.get(
        "keepalive_expiration_dialog_seen"
    ):
        idle_warning_detected = True

    if not idle_warning_detected:
        for observation in recorder.get("observations") or []:
            if not isinstance(observation, dict):
                continue
            warning_at = _observation_idle_warning_at(observation)
            if warning_at is not None:
                idle_warning_detected = True
                idle_warning_first_observed_at = warning_at
                break

    logout_observed_at = recorder.get("logout_detected_at")
    if logout_observed_at is not None:
        logout_observed_at = str(logout_observed_at)
    recorder_outcome = recorder.get("outcome")
    if isinstance(experiment_result, dict) and recorder_outcome is None:
        recorder_outcome = experiment_result.get("outcome")
    logged_out = bool(
        recorder_outcome == "logged_out"
        or logout_observed_at
        or keepalive_status.get("keepalive_logged_out")
    )
    if logged_out and logout_observed_at is None and isinstance(experiment_result, dict):
        summary = experiment_result.get("summary")
        if isinstance(summary, dict):
            logout_observed_at = summary.get("recorder_completed_at") or summary.get(
                "completed_at"
            )
            if logout_observed_at is not None:
                logout_observed_at = str(logout_observed_at)

    warning_to_logout_seconds: float | None = None
    if idle_warning_first_observed_at and logout_observed_at:
        start = _parse_iso_timestamp(idle_warning_first_observed_at)
        end = _parse_iso_timestamp(logout_observed_at)
        if start is not None and end is not None:
            warning_to_logout_seconds = max(0.0, (end - start).total_seconds())

    initial_authentication_state = recorder.get(
        "initial_canonical_authentication_state"
    ) or recorder.get("initial_authentication_state")
    final_authentication_state = recorder.get(
        "final_canonical_authentication_state"
    ) or recorder.get("final_authentication_state")
    if isinstance(experiment_result, dict):
        if final_authentication_state is None:
            final_authentication_state = experiment_result.get(
                "final_authentication_state"
            )
        summary = experiment_result.get("summary")
        if isinstance(summary, dict) and final_authentication_state is None:
            final_authentication_state = summary.get("final_authentication_state")

    return {
        "idle_warning_detected": idle_warning_detected,
        "idle_warning_first_observed_at": idle_warning_first_observed_at,
        "logged_out": logged_out,
        "logout_observed_at": logout_observed_at,
        "warning_to_logout_seconds": warning_to_logout_seconds,
        "initial_authentication_state": initial_authentication_state,
        "final_authentication_state": final_authentication_state,
        "recorder_outcome": recorder_outcome,
    }


def _load_campaign_manifest(campaign_dir: Path) -> dict[str, Any]:
    path = Path(campaign_dir) / EXPIRATION_CAMPAIGN_MANIFEST_FILENAME
    if not path.is_file():
        return {"trials": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"trials": []}
    if not isinstance(payload, dict):
        return {"trials": []}
    if not isinstance(payload.get("trials"), list):
        payload["trials"] = []
    return payload


def _trial_completed_in_manifest(
    manifest: dict[str, Any],
    *,
    strategy: str,
    keepalive_interval_seconds: int,
) -> bool:
    for item in manifest.get("trials") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("strategy") or "") != strategy:
            continue
        if int(item.get("keepalive_interval_seconds") or -1) != int(
            keepalive_interval_seconds
        ):
            continue
        if str(item.get("status") or "") == "completed":
            return True
    return False


def _completed_trial_summary_from_manifest(
    manifest: dict[str, Any],
    *,
    strategy: str,
    keepalive_interval_seconds: int,
    trial_number: int,
) -> dict[str, Any] | None:
    for item in manifest.get("trials") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("strategy") or "") != strategy:
            continue
        if int(item.get("keepalive_interval_seconds") or -1) != int(
            keepalive_interval_seconds
        ):
            continue
        if str(item.get("status") or "") != "completed":
            continue
        summary = dict(item)
        summary["trial_number"] = int(trial_number)
        return summary
    return None


def write_expiration_campaign_summary_files(
    campaign_dir: Path,
    *,
    campaign_name: str | None,
    started_at: str,
    completed_at: str,
    interrupted: bool,
    trial_summaries: list[dict[str, Any]],
    zip_path: str | None = None,
    managed_browser_preexisting: bool | None = None,
    managed_browser_launched_by_campaign: bool | None = None,
    managed_browser_restarted_by_campaign: bool | None = None,
    browser_cleanup_policy: str | None = None,
    managed_browser_closed_at_completion: bool | None = None,
    managed_cdp_port: int | None = None,
    managed_profile_path: str | None = None,
) -> dict[str, Any]:
    """Write campaign JSON/CSV/Markdown summary artifacts."""
    campaign_dir = Path(campaign_dir)
    campaign_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "provider": "amex",
        "campaign_name": campaign_name,
        "started_at": started_at,
        "completed_at": completed_at,
        "interrupted": interrupted,
        "trial_count": len(trial_summaries),
        "trials": trial_summaries,
        "campaign_dir": str(campaign_dir),
        "zip_path": zip_path,
        "managed_browser_preexisting": managed_browser_preexisting,
        "managed_browser_launched_by_campaign": managed_browser_launched_by_campaign,
        "managed_browser_restarted_by_campaign": managed_browser_restarted_by_campaign,
        "browser_cleanup_policy": browser_cleanup_policy,
        "managed_browser_closed_at_completion": managed_browser_closed_at_completion,
        "managed_cdp_port": managed_cdp_port,
        "managed_profile_path": managed_profile_path,
    }
    _write_json_file(campaign_dir / EXPIRATION_CAMPAIGN_SUMMARY_JSON, summary)

    csv_path = campaign_dir / EXPIRATION_CAMPAIGN_SUMMARY_CSV
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(EXPIRATION_CAMPAIGN_TRIAL_SUMMARY_FIELDS),
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in trial_summaries:
            writer.writerow(
                {key: row.get(key) for key in EXPIRATION_CAMPAIGN_TRIAL_SUMMARY_FIELDS}
            )

    lines = [
        f"# Amex expiration campaign{f': {campaign_name}' if campaign_name else ''}",
        "",
        f"- Started: `{started_at}`",
        f"- Completed: `{completed_at}`",
        f"- Interrupted: `{interrupted}`",
        f"- Trials: `{len(trial_summaries)}`",
        "",
        "| # | Strategy | Interval | Recorder | Keepalive | Idle warning | Logged out | Error |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in trial_summaries:
        lines.append(
            "| "
            f"{row.get('trial_number')} | "
            f"{row.get('strategy')} | "
            f"{row.get('keepalive_interval_seconds')} | "
            f"{row.get('recorder_outcome')} | "
            f"{row.get('keepalive_outcome')} | "
            f"{row.get('idle_warning_detected')} | "
            f"{row.get('logged_out')} | "
            f"{row.get('error') or ''} |"
        )
    lines.extend(["", "## Evidence directories", ""])
    for row in trial_summaries:
        lines.append(
            f"- Trial {row.get('trial_number')}: `{row.get('evidence_directory')}`"
        )
    if zip_path:
        lines.extend(["", f"Campaign ZIP: `{zip_path}`", ""])
    else:
        lines.append("")
    (campaign_dir / EXPIRATION_CAMPAIGN_REPORT_MD).write_text(
        "\n".join(lines),
        encoding="utf-8",
    )
    return summary


def _maybe_analyze_campaign_after_run(result: dict[str, Any]) -> int:
    """Run offline analysis after campaign packaging without mutating evidence.

    Returns 0 on success. On failure prints a clear error and returns nonzero,
    but callers must not treat that as a campaign packaging failure.
    """
    from mighty.provider_runtime_campaign_analysis import run_analyze_campaign_command

    campaign_dir = result.get("campaign_dir")
    zip_path = result.get("zip_path")
    target: Path | None = None
    if campaign_dir:
        candidate = Path(str(campaign_dir)).expanduser()
        if candidate.is_dir():
            target = candidate
    if target is None and zip_path:
        candidate = Path(str(zip_path)).expanduser()
        if candidate.is_file():
            target = candidate
    if target is None:
        print(
            "Campaign analysis skipped: no campaign directory or ZIP was available.",
            file=sys.stderr,
        )
        return 1
    try:
        print("")
        run_analyze_campaign_command(target)
        return 0
    except Exception as exc:  # noqa: BLE001 - analysis must not destroy campaign evidence
        print(
            f"Campaign analysis failed (campaign evidence preserved): "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


def print_expiration_campaign_result(result: dict[str, Any]) -> None:
    """Print only the campaign result and final ZIP path."""
    message = result.get("message")
    if message and result.get("zip_path") is None:
        print(str(message), file=sys.stderr)
        return
    campaign_name = result.get("campaign_name")
    trial_summaries = result.get("trial_summaries") or []
    completed = sum(
        1
        for row in trial_summaries
        if isinstance(row, dict) and row.get("error") is None and not row.get("skipped")
    )
    skipped = sum(
        1 for row in trial_summaries if isinstance(row, dict) and row.get("skipped")
    )
    failed = sum(
        1 for row in trial_summaries if isinstance(row, dict) and row.get("error")
    )
    outcome = result.get("outcome") or ("interrupted" if result.get("interrupted") else "completed")
    print("----------------------------------------")
    if campaign_name:
        print(f"Campaign: {campaign_name}")
    print(f"Outcome: {outcome}")
    print(
        f"Trials: {len(trial_summaries)} "
        f"(completed={completed}, failed={failed}, skipped={skipped})"
    )
    print()
    print("Evidence ZIP:")
    print(str(result.get("zip_path") or ""))


def ensure_expiration_campaign_signed_in(
    *,
    trial_number: int | None,
    base_url: str,
    request_json_fn: Any,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    input_fn: Any = None,
    print_fn: Any = None,
    browser_launched: bool = False,
    bring_to_foreground_fn: Any = None,
    recover_unhealthy_browser_fn: Any = None,
) -> dict[str, Any]:
    """Fresh canonical verify; loop operator login until SIGNED_IN.

    Authentication is recoverable: failed post-Enter verification re-prompts
    instead of failing the pending trial. Ctrl+C returns ``interrupted``.
    """
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    read_input = input_fn or input
    emit = print_fn or print

    def _verify() -> dict[str, Any]:
        try:
            return verify_amex_signed_in_for_experiment(
                base_url=base_url,
                request_json_fn=request_json_fn,
                sleep_fn=sleep,
                monotonic_fn=monotonic,
            )
        except ProviderRuntimeHTTPError as exc:
            if _is_managed_browser_target_error(exc.body) or _is_managed_browser_target_error(
                exc
            ):
                return {
                    "ok": False,
                    "authentication_state": "LOGIN_UNKNOWN",
                    "outcome": "managed_browser_unavailable",
                    "message": str(exc.body or exc),
                    "verify_payload": {"ok": False, "error": str(exc.body or exc)},
                }
            raise
        except (URLError, TimeoutError, OSError, ConnectionError) as exc:
            if _is_managed_browser_target_error(exc):
                return {
                    "ok": False,
                    "authentication_state": "LOGIN_UNKNOWN",
                    "outcome": "managed_browser_unavailable",
                    "message": str(exc),
                    "verify_payload": None,
                }
            raise

    def _needs_auth_pause(payload: dict[str, Any]) -> bool:
        return payload.get("authentication_state") in {
            "SIGNED_OUT",
            "LOGIN_UNKNOWN",
        } or payload.get("outcome") in {
            "initial_not_signed_in",
            "managed_browser_unavailable",
            "initial_authentication_unknown",
            "authentication_reverify_failed",
        }

    verified = _verify()
    if verified.get("ok"):
        return {
            "ok": True,
            "authentication_state": verified.get("authentication_state"),
            "paused": False,
            "verify_payload": verified.get("verify_payload"),
            "browser_recovered": False,
            "prompt_count": 0,
        }

    if not _needs_auth_pause(verified):
        return {
            "ok": False,
            "authentication_state": verified.get("authentication_state"),
            "paused": False,
            "outcome": verified.get("outcome") or "initial_not_signed_in",
            "message": verified.get("message"),
            "verify_payload": verified.get("verify_payload"),
            "browser_recovered": False,
            "prompt_count": 0,
        }

    browser_recovered = False
    if (
        verified.get("outcome") == "managed_browser_unavailable"
        and recover_unhealthy_browser_fn is not None
    ):
        recover_unhealthy_browser_fn()
        browser_recovered = True
        browser_launched = True

    prompt_count = 0
    while True:
        if bring_to_foreground_fn is not None:
            try:
                bring_to_foreground_fn()
            except Exception:
                pass

        emit(
            _expiration_campaign_auth_pause_message(
                trial_number=trial_number,
                browser_launched=browser_launched or browser_recovered,
            )
        )
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            read_input()
        except KeyboardInterrupt:
            return {
                "ok": False,
                "authentication_state": verified.get("authentication_state"),
                "paused": True,
                "interrupted": True,
                "outcome": "interrupted",
                "message": "Authentication interrupted by user",
                "verify_payload": verified.get("verify_payload"),
                "browser_recovered": browser_recovered,
                "prompt_count": prompt_count,
            }
        except EOFError:
            emit("No input received.")
            emit("Please finish signing in and press Enter to try again.")
            continue

        prompt_count += 1
        emit("Input received.")
        emit("Verifying authentication...")
        reverified = _verify()
        if reverified.get("ok"):
            emit("Authentication verified.")
            return {
                "ok": True,
                "authentication_state": reverified.get("authentication_state"),
                "paused": True,
                "verify_payload": reverified.get("verify_payload"),
                "browser_recovered": browser_recovered,
                "prompt_count": prompt_count,
            }

        if (
            reverified.get("outcome") == "managed_browser_unavailable"
            and recover_unhealthy_browser_fn is not None
        ):
            recover_unhealthy_browser_fn()
            browser_recovered = True
            browser_launched = True

        state = (
            reverified.get("authentication_state")
            or reverified.get("outcome")
            or "LOGIN_UNKNOWN"
        )
        reason = reverified.get("message") or state
        emit(f"Authentication was not verified: {reason}.")
        emit("Please finish signing in and press Enter to try again.")
        verified = reverified
        # Recoverable: loop and wait for another Enter. Never fail the trial here.




def run_amex_expiration_campaign(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    root: Path | None = None,
    cdp_port: int = DEFAULT_CDP_PORT,
    diagnostics_dir: Path | None = None,
    output_dir: Path | None = None,
    trials: list[dict[str, Any]] | list[str] | tuple[str, ...] | None = None,
    campaign_name: str | None = None,
    trial_duration_seconds: int = DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS,
    recording_timeout_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS
    ),
    evidence_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS
    ),
    verification_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS
    ),
    rolling_window_seconds: float = DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS,
    screenshot_every_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS
    ),
    browser_cleanup: str = DEFAULT_BROWSER_CLEANUP_POLICY,
    continue_on_error: bool = False,
    skip_completed: bool = False,
    request_json_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    input_fn: Any = None,
    print_fn: Any = None,
    run_experiment_fn: Any = None,
    ensure_managed_browser_fn: Any = None,
    classify_managed_browser_fn: Any = None,
    restart_managed_browser_fn: Any = None,
    bring_to_foreground_fn: Any = None,
    close_managed_browser_fn: Any = None,
    launch_native_chrome_fn: Any = None,
    terminate_profile_processes_fn: Any = None,
    wait_for_profile_release_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
) -> dict[str, Any]:
    """Run multiple Amex expiration experiments sequentially into one campaign ZIP.

    Reuses ``run_amex_expiration_experiment`` as the unit of execution. Ensures a
    dedicated managed Amex Chrome window exists (via ``launch_native_chrome``),
    never targets ordinary Chrome profiles, and does not start/stop ``serve``.
    """
    if trials is None:
        raise ValueError("At least one --trial STRATEGY:INTERVAL is required")
    cleanup_policy = str(browser_cleanup or DEFAULT_BROWSER_CLEANUP_POLICY)
    if cleanup_policy not in BROWSER_CLEANUP_POLICIES:
        raise ValueError(
            f"Unsupported browser cleanup policy {browser_cleanup!r}. "
            f"Expected one of {', '.join(BROWSER_CLEANUP_POLICIES)}"
        )
    if trials and isinstance(trials[0], str):
        trial_specs = parse_expiration_campaign_trial_specs(
            [str(item) for item in trials]  # type: ignore[arg-type]
        )
    else:
        trial_specs = []
        for item in trials:  # type: ignore[union-attr]
            if not isinstance(item, dict):
                raise ValueError(f"Invalid trial specification: {item!r}")
            if "strategy" in item and "keepalive_interval_seconds" in item:
                strategy = str(item["strategy"]).upper()
                interval = int(item["keepalive_interval_seconds"])
                if strategy not in KEEPALIVE_STRATEGIES:
                    raise ValueError(
                        f"Unsupported keepalive strategy {strategy!r}. "
                        f"Expected one of {', '.join(KEEPALIVE_STRATEGIES)}"
                    )
                if interval <= 0:
                    raise ValueError(
                        f"Invalid keepalive interval {interval}. "
                        "Expected a positive integer number of seconds."
                    )
                trial_specs.append(
                    {
                        "strategy": strategy,
                        "keepalive_interval_seconds": interval,
                        "spec": f"{strategy}:{interval}",
                    }
                )
            else:
                trial_specs.append(
                    parse_expiration_campaign_trial_spec(str(item.get("spec") or item))
                )

    http = request_json_fn or request_json
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    read_input = input_fn or input
    emit = print_fn or print
    run_experiment = run_experiment_fn or run_amex_expiration_experiment
    base_url = _expiration_experiment_base_url(host, port)
    runtime_root = Path(root).expanduser().resolve() if root is not None else DEFAULT_ROOT
    profile_dir = (runtime_root / "amex").resolve()
    diagnostics = Path(diagnostics_dir) if diagnostics_dir else runtime_root / "diagnostics"
    campaign_dir = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else default_expiration_campaign_dir(diagnostics)
    )
    campaign_dir.mkdir(parents=True, exist_ok=True)
    trials_root = campaign_dir / "trials"
    trials_root.mkdir(parents=True, exist_ok=True)

    started_at = iso_now()
    campaign_started_mono = monotonic()
    interrupted = False
    trial_summaries: list[dict[str, Any]] = []
    managed_browser_preexisting = False
    managed_browser_launched_by_campaign = False
    managed_browser_restarted_by_campaign = False
    managed_browser_closed_at_completion = False
    browser_just_launched = False
    manifest = _load_campaign_manifest(campaign_dir) if skip_completed else {"trials": []}
    if not isinstance(manifest.get("trials"), list):
        manifest["trials"] = []
    manifest.update(
        {
            "provider": "amex",
            "campaign_name": campaign_name,
            "campaign_dir": str(campaign_dir),
            "started_at": manifest.get("started_at") or started_at,
            "browser_cleanup_policy": cleanup_policy,
            "managed_cdp_port": int(cdp_port),
            "managed_profile_path": str(profile_dir),
        }
    )

    def _browser_metadata() -> dict[str, Any]:
        return {
            "managed_browser_preexisting": managed_browser_preexisting,
            "managed_browser_launched_by_campaign": managed_browser_launched_by_campaign,
            "managed_browser_restarted_by_campaign": managed_browser_restarted_by_campaign,
            "browser_cleanup_policy": cleanup_policy,
            "managed_browser_closed_at_completion": managed_browser_closed_at_completion,
            "managed_cdp_port": int(cdp_port),
            "managed_profile_path": str(profile_dir),
        }

    def _persist_campaign(*, zip_it: bool) -> dict[str, Any]:
        completed_at = iso_now()
        zip_path: Path | None = None
        summary = write_expiration_campaign_summary_files(
            campaign_dir,
            campaign_name=campaign_name,
            started_at=str(manifest.get("started_at") or started_at),
            completed_at=completed_at,
            interrupted=interrupted,
            trial_summaries=trial_summaries,
            zip_path=None,
            **_browser_metadata(),
        )
        manifest["trials"] = list(trial_summaries)
        manifest["completed_at"] = completed_at
        manifest["interrupted"] = interrupted
        manifest.update(_browser_metadata())
        _write_json_file(campaign_dir / EXPIRATION_CAMPAIGN_MANIFEST_FILENAME, manifest)
        if zip_it:
            zip_path = create_expiration_campaign_zip(campaign_dir)
            summary["zip_path"] = str(zip_path)
            _write_json_file(campaign_dir / EXPIRATION_CAMPAIGN_SUMMARY_JSON, summary)
            manifest["zip_path"] = str(zip_path)
            _write_json_file(
                campaign_dir / EXPIRATION_CAMPAIGN_MANIFEST_FILENAME, manifest
            )
        return {
            "summary": summary,
            "zip_path": str(zip_path) if zip_path is not None else None,
            "completed_at": completed_at,
        }

    def _cleanup_browser() -> None:
        nonlocal managed_browser_closed_at_completion
        closer = close_managed_browser_fn or maybe_close_managed_browser_for_campaign
        result = closer(
            browser_cleanup=cleanup_policy,
            managed_browser_preexisting=managed_browser_preexisting,
            managed_browser_launched_by_campaign=managed_browser_launched_by_campaign,
            interrupted=interrupted,
            profile_dir=profile_dir,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
        )
        managed_browser_closed_at_completion = bool(
            isinstance(result, dict) and result.get("closed")
        )

    def _restart_managed() -> dict[str, Any]:
        nonlocal managed_browser_launched_by_campaign
        nonlocal managed_browser_restarted_by_campaign
        nonlocal browser_just_launched
        restarter = restart_managed_browser_fn or (
            lambda: restart_managed_amex_browser(
                profile_dir=profile_dir,
                cdp_port=int(cdp_port),
                launch_native_chrome_fn=launch_native_chrome_fn,
                terminate_profile_processes_fn=terminate_profile_processes_fn,
                wait_for_profile_release_fn=wait_for_profile_release_fn,
                sleep_fn=sleep,
                monotonic_fn=monotonic,
                fetch_cdp_json_fn=fetch_cdp_json_fn,
            )
        )
        restarted = restarter()
        managed_browser_launched_by_campaign = True
        managed_browser_restarted_by_campaign = True
        browser_just_launched = True
        return restarted if isinstance(restarted, dict) else {"ok": True}

    def _ensure_browser_healthy_between_trials() -> None:
        nonlocal managed_browser_restarted_by_campaign
        nonlocal browser_just_launched
        classifier = classify_managed_browser_fn or (
            lambda: classify_managed_amex_browser(
                int(cdp_port),
                fetch_cdp_json_fn=fetch_cdp_json_fn,
            )
        )
        classified = classifier()
        state = str(
            (classified or {}).get("state")
            if isinstance(classified, dict)
            else MANAGED_BROWSER_ABSENT
        )
        if state == MANAGED_BROWSER_HEALTHY:
            return
        emit("Managed browser needs recovery before the next trial...")
        _restart_managed()
        managed_browser_restarted_by_campaign = True
        browser_just_launched = True

    # 1) Validate already done above. 2) Health check before the campaign.
    try:
        http("GET", f"{base_url}/health")
    except (URLError, TimeoutError, OSError, ConnectionError) as exc:
        return {
            "ok": False,
            "outcome": "runtime_unavailable",
            "campaign_name": campaign_name,
            "campaign_dir": str(campaign_dir),
            "zip_path": None,
            "trial_summaries": [],
            "message": EXPIRATION_EXPERIMENT_SERVE_HINT,
            "error": f"{type(exc).__name__}: {exc}",
            "exit_code": 1,
            "interrupted": False,
            **_browser_metadata(),
        }

    # 3) Ensure dedicated managed Amex browser exists (never touches ordinary Chrome).
    try:
        ensure_browser = ensure_managed_browser_fn or ensure_managed_amex_browser_for_campaign
        browser_info = ensure_browser(
            profile_dir=profile_dir,
            cdp_port=int(cdp_port),
            print_fn=emit,
            sleep_fn=sleep,
            monotonic_fn=monotonic,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
            launch_native_chrome_fn=launch_native_chrome_fn,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
            wait_for_profile_release_fn=wait_for_profile_release_fn,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "outcome": "managed_browser_launch_failed",
            "campaign_name": campaign_name,
            "campaign_dir": str(campaign_dir),
            "zip_path": None,
            "trial_summaries": [],
            "message": f"Failed to ensure managed Amex Chrome: {exc}",
            "error": f"{type(exc).__name__}: {exc}",
            "exit_code": 1,
            "interrupted": False,
            **_browser_metadata(),
        }

    if isinstance(browser_info, dict):
        managed_browser_preexisting = bool(
            browser_info.get("managed_browser_preexisting")
        )
        managed_browser_launched_by_campaign = bool(
            browser_info.get("managed_browser_launched_by_campaign")
        )
        managed_browser_restarted_by_campaign = bool(
            browser_info.get("managed_browser_restarted_by_campaign")
        )
        browser_just_launched = bool(
            managed_browser_launched_by_campaign
            or managed_browser_restarted_by_campaign
        )

    foreground = bring_to_foreground_fn or (
        lambda: bring_managed_amex_browser_to_foreground(
            profile_dir,
            profile_processes_fn=None,
        )
    )

    try:
        for index, trial_spec in enumerate(trial_specs, start=1):
            strategy = str(trial_spec["strategy"])
            interval_seconds = int(trial_spec["keepalive_interval_seconds"])
            dirname = expiration_campaign_trial_dirname(
                index, strategy, interval_seconds
            )
            evidence_dir = trials_root / dirname

            if skip_completed and _trial_completed_in_manifest(
                manifest,
                strategy=strategy,
                keepalive_interval_seconds=interval_seconds,
            ):
                existing = _completed_trial_summary_from_manifest(
                    manifest,
                    strategy=strategy,
                    keepalive_interval_seconds=interval_seconds,
                    trial_number=index,
                )
                if existing is None:
                    existing = {
                        "trial_number": index,
                        "strategy": strategy,
                        "keepalive_interval_seconds": interval_seconds,
                        "started_at": None,
                        "completed_at": None,
                        "duration_seconds": None,
                        "recorder_outcome": None,
                        "keepalive_outcome": None,
                        "initial_authentication_state": None,
                        "final_authentication_state": None,
                        "idle_warning_detected": False,
                        "idle_warning_first_observed_at": None,
                        "logged_out": False,
                        "logout_observed_at": None,
                        "warning_to_logout_seconds": None,
                        "keepalive_wait_seconds": None,
                        "keepalive_completion_timeout": None,
                        "error": None,
                        "evidence_directory": str(evidence_dir),
                    }
                existing["trial_number"] = index
                existing["skipped"] = True
                existing["status"] = "completed"
                existing["evidence_directory"] = existing.get(
                    "evidence_directory"
                ) or str(evidence_dir)
                trial_summaries.append(existing)
                continue

            if index > 1:
                _ensure_browser_healthy_between_trials()

            auth = ensure_expiration_campaign_signed_in(
                trial_number=index,
                base_url=base_url,
                request_json_fn=http,
                sleep_fn=sleep,
                monotonic_fn=monotonic,
                input_fn=read_input,
                print_fn=emit,
                browser_launched=browser_just_launched,
                bring_to_foreground_fn=foreground,
                recover_unhealthy_browser_fn=_restart_managed,
            )
            browser_just_launched = False
            if auth.get("browser_recovered"):
                managed_browser_restarted_by_campaign = True
                managed_browser_launched_by_campaign = True
            if auth.get("interrupted") or auth.get("outcome") == "interrupted":
                # Pending trial stays not-started; do not record a failed trial.
                interrupted = True
                break
            if not auth.get("ok"):
                # Unrecoverable auth/runtime error only (not a reverify retry).
                trial_row = {
                    "trial_number": index,
                    "strategy": strategy,
                    "keepalive_interval_seconds": interval_seconds,
                    "started_at": iso_now(),
                    "completed_at": iso_now(),
                    "duration_seconds": 0.0,
                    "recorder_outcome": None,
                    "keepalive_outcome": None,
                    "initial_authentication_state": auth.get("authentication_state"),
                    "final_authentication_state": auth.get("authentication_state"),
                    "idle_warning_detected": False,
                    "idle_warning_first_observed_at": None,
                    "logged_out": False,
                    "logout_observed_at": None,
                    "warning_to_logout_seconds": None,
                    "keepalive_wait_seconds": None,
                    "keepalive_completion_timeout": None,
                    "error": auth.get("outcome")
                    or auth.get("message")
                    or "authentication_required",
                    "evidence_directory": str(evidence_dir),
                    "status": "failed",
                    "skipped": False,
                }
                trial_summaries.append(trial_row)
                if continue_on_error:
                    continue
                _cleanup_browser()
                persisted = _persist_campaign(zip_it=True)
                return {
                    "ok": False,
                    "outcome": "authentication_required",
                    "recoverable": False,
                    "pending_trial_number": index,
                    "campaign_name": campaign_name,
                    "campaign_dir": str(campaign_dir),
                    "zip_path": persisted["zip_path"],
                    "trial_summaries": trial_summaries,
                    "summary": persisted["summary"],
                    "message": auth.get("message"),
                    "exit_code": 1,
                    "interrupted": False,
                    "duration_seconds": max(0.0, monotonic() - campaign_started_mono),
                    **_browser_metadata(),
                }

            emit(
                f"Starting trial {index} of {len(trial_specs)}: "
                f"{strategy} at {interval_seconds} seconds..."
            )
            trial_started_at = iso_now()
            trial_started_mono = monotonic()
            evidence_dir.mkdir(parents=True, exist_ok=True)

            preflight = run_keepalive_preflight_for_campaign_trial(
                strategy=strategy,
                host=host,
                port=port,
                evidence_dir=evidence_dir,
                request_json_fn=http,
            )
            if strategy != "NONE" and not preflight.get("success"):
                trial_completed_at = iso_now()
                duration_seconds = max(0.0, monotonic() - trial_started_mono)
                failure_reason = (
                    preflight.get("error")
                    or preflight.get("reason")
                    or "keepalive_preflight_failed"
                )
                emit(
                    f"Trial {index} preflight failed for {strategy}: {failure_reason}"
                )
                emit("Skipping long observation (OPERATIONALLY_FAILED).")
                trial_row = {
                    "trial_number": index,
                    "strategy": strategy,
                    "keepalive_interval_seconds": interval_seconds,
                    "started_at": trial_started_at,
                    "completed_at": trial_completed_at,
                    "duration_seconds": duration_seconds,
                    "recorder_outcome": "skipped_preflight_failed",
                    "keepalive_outcome": "preflight_failed",
                    "initial_authentication_state": auth.get("authentication_state"),
                    "final_authentication_state": auth.get("authentication_state"),
                    "idle_warning_detected": False,
                    "idle_warning_first_observed_at": None,
                    "logged_out": False,
                    "logout_observed_at": None,
                    "warning_to_logout_seconds": None,
                    "keepalive_wait_seconds": None,
                    "keepalive_completion_timeout": None,
                    "preflight_ok": False,
                    "result_classification": "OPERATIONALLY_FAILED",
                    "error": f"preflight_failed: {failure_reason}",
                    "evidence_directory": str(evidence_dir),
                    "status": "failed",
                    "skipped": False,
                }
                trial_summaries.append(trial_row)
                if continue_on_error:
                    continue
                _cleanup_browser()
                persisted = _persist_campaign(zip_it=True)
                return {
                    "ok": False,
                    "outcome": "preflight_failed",
                    "campaign_name": campaign_name,
                    "campaign_dir": str(campaign_dir),
                    "zip_path": persisted["zip_path"],
                    "trial_summaries": trial_summaries,
                    "summary": persisted["summary"],
                    "message": failure_reason,
                    "exit_code": 1,
                    "interrupted": False,
                    "duration_seconds": max(0.0, monotonic() - campaign_started_mono),
                    **_browser_metadata(),
                }

            if strategy != "NONE":
                emit(f"Preflight OK for {strategy}; starting timed trial...")

            try:
                experiment_result = run_experiment(
                    host=host,
                    port=port,
                    diagnostics_dir=diagnostics,
                    output_dir=evidence_dir,
                    strategy=strategy,
                    trial_duration_seconds=int(trial_duration_seconds),
                    keepalive_interval_seconds=interval_seconds,
                    recording_timeout_seconds=float(recording_timeout_seconds),
                    evidence_interval_seconds=float(evidence_interval_seconds),
                    verification_interval_seconds=float(verification_interval_seconds),
                    rolling_window_seconds=float(rolling_window_seconds),
                    screenshot_every_seconds=float(screenshot_every_seconds),
                    request_json_fn=http,
                    sleep_fn=sleep,
                    monotonic_fn=monotonic,
                )
            except KeyboardInterrupt:
                interrupted = True
                experiment_result = {
                    "ok": False,
                    "outcome": "interrupted",
                    "keepalive_outcome": None,
                    "final_authentication_state": auth.get("authentication_state"),
                    "experiment_dir": str(evidence_dir),
                    "zip_path": None,
                    "summary": {
                        "outcome": "interrupted",
                        "interrupted": True,
                        "keepalive_strategy": strategy,
                        "keepalive_interval_seconds": interval_seconds,
                    },
                    "recorder": None,
                    "keepalive_status": None,
                    "exit_code": 130,
                }
            except Exception as exc:  # noqa: BLE001 - preserve campaign continuity
                experiment_result = {
                    "ok": False,
                    "outcome": "fatal_error",
                    "keepalive_outcome": None,
                    "final_authentication_state": auth.get("authentication_state"),
                    "experiment_dir": str(evidence_dir),
                    "zip_path": None,
                    "summary": {
                        "outcome": "fatal_error",
                        "keepalive_strategy": strategy,
                        "keepalive_interval_seconds": interval_seconds,
                        "recorder_error": f"{type(exc).__name__}: {exc}",
                    },
                    "recorder": None,
                    "keepalive_status": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "exit_code": 1,
                }

            trial_completed_at = iso_now()
            duration_seconds = max(0.0, monotonic() - trial_started_mono)
            metrics = derive_expiration_campaign_trial_metrics(
                experiment_result=experiment_result
                if isinstance(experiment_result, dict)
                else None,
                experiment_dir=evidence_dir,
            )
            summary = (
                experiment_result.get("summary")
                if isinstance(experiment_result, dict)
                and isinstance(experiment_result.get("summary"), dict)
                else {}
            )
            outcome = (
                experiment_result.get("outcome")
                if isinstance(experiment_result, dict)
                else "fatal_error"
            )
            if outcome == "interrupted":
                interrupted = True
            error: str | None = None
            if isinstance(experiment_result, dict):
                error = experiment_result.get("error") or summary.get("recorder_error")
            if outcome in {
                "runtime_unavailable",
                "keepalive_start_failed",
                "initial_not_signed_in",
                "initial_authentication_unknown",
                "fatal_error",
                "authentication_reverify_failed",
            }:
                error = error or str(
                    (experiment_result or {}).get("message") or outcome
                )

            trial_row = {
                "trial_number": index,
                "strategy": strategy,
                "keepalive_interval_seconds": interval_seconds,
                "started_at": trial_started_at,
                "completed_at": trial_completed_at,
                "duration_seconds": duration_seconds,
                "recorder_outcome": metrics.get("recorder_outcome")
                or summary.get("recorder_outcome")
                or outcome,
                "keepalive_outcome": (
                    experiment_result.get("keepalive_outcome")
                    if isinstance(experiment_result, dict)
                    else None
                )
                or summary.get("keepalive_outcome"),
                "initial_authentication_state": metrics.get(
                    "initial_authentication_state"
                )
                or auth.get("authentication_state"),
                "final_authentication_state": metrics.get("final_authentication_state")
                or (
                    experiment_result.get("final_authentication_state")
                    if isinstance(experiment_result, dict)
                    else None
                ),
                "idle_warning_detected": metrics.get("idle_warning_detected"),
                "idle_warning_first_observed_at": metrics.get(
                    "idle_warning_first_observed_at"
                ),
                "logged_out": metrics.get("logged_out"),
                "logout_observed_at": metrics.get("logout_observed_at"),
                "warning_to_logout_seconds": metrics.get("warning_to_logout_seconds"),
                "keepalive_wait_seconds": (
                    experiment_result.get("keepalive_wait_seconds")
                    if isinstance(experiment_result, dict)
                    else None
                )
                if isinstance(experiment_result, dict)
                and experiment_result.get("keepalive_wait_seconds") is not None
                else summary.get("keepalive_wait_seconds"),
                "keepalive_completion_timeout": (
                    experiment_result.get("keepalive_completion_timeout")
                    if isinstance(experiment_result, dict)
                    else None
                )
                if isinstance(experiment_result, dict)
                and experiment_result.get("keepalive_completion_timeout") is not None
                else summary.get("keepalive_completion_timeout"),
                "error": error,
                "evidence_directory": str(evidence_dir),
                "status": (
                    "partial"
                    if interrupted or outcome == "interrupted"
                    else ("failed" if error else "completed")
                ),
                "skipped": False,
            }
            trial_summaries.append(trial_row)
            emit(
                f"Trial {index} completed: "
                f"{trial_row.get('recorder_outcome') or outcome}"
            )

            if interrupted:
                break

            if error and not continue_on_error:
                _cleanup_browser()
                persisted = _persist_campaign(zip_it=True)
                return {
                    "ok": False,
                    "outcome": "trial_failed",
                    "campaign_name": campaign_name,
                    "campaign_dir": str(campaign_dir),
                    "zip_path": persisted["zip_path"],
                    "trial_summaries": trial_summaries,
                    "summary": persisted["summary"],
                    "exit_code": 1,
                    "interrupted": False,
                    "duration_seconds": max(0.0, monotonic() - campaign_started_mono),
                    **_browser_metadata(),
                }
    except KeyboardInterrupt:
        interrupted = True

    _cleanup_browser()
    persisted = _persist_campaign(zip_it=True)
    exit_code = 130 if interrupted else 0
    if not interrupted and any(
        isinstance(row, dict) and row.get("error") for row in trial_summaries
    ):
        exit_code = 1
    outcome = "interrupted" if interrupted else ("completed" if exit_code == 0 else "completed_with_errors")
    return {
        "ok": exit_code in {0, 130},
        "outcome": outcome,
        "campaign_name": campaign_name,
        "campaign_dir": str(campaign_dir),
        "zip_path": persisted["zip_path"],
        "trial_summaries": trial_summaries,
        "summary": persisted["summary"],
        "exit_code": exit_code,
        "interrupted": interrupted,
        "duration_seconds": max(0.0, monotonic() - campaign_started_mono),
        "message": None,
        **_browser_metadata(),
    }


def default_amex_campaign_trials() -> list[str]:
    """Default keepalive comparison matrix for ``campaign amex``."""
    return list(DEFAULT_AMEX_CAMPAIGN_TRIALS)


def resolve_amex_campaign_trials(
    trials: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Use explicit ``--trial`` values when provided; otherwise expand defaults."""
    if trials:
        return [str(item) for item in trials]
    return default_amex_campaign_trials()


def check_provider_runtime_health(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    request_json_fn: Any = None,
) -> dict[str, Any]:
    """Return ``{ok: True}`` when ``serve`` answers ``GET /health``."""
    http = request_json_fn or request_json
    base_url = _expiration_experiment_base_url(host, port)
    try:
        payload = http("GET", f"{base_url}/health")
    except (URLError, TimeoutError, OSError, ConnectionError, ProviderRuntimeHTTPError) as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "base_url": base_url,
        }
    return {"ok": True, "payload": payload, "base_url": base_url}


def wait_for_provider_runtime_health(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout_seconds: float = DEFAULT_PROVIDER_RUNTIME_HEALTH_TIMEOUT_SECONDS,
    request_json_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
) -> dict[str, Any]:
    """Poll runtime health until ready or timeout."""
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    deadline = monotonic() + max(0.0, float(timeout_seconds))
    last: dict[str, Any] = {"ok": False, "error": "not_ready"}
    while True:
        last = check_provider_runtime_health(
            host=host,
            port=port,
            request_json_fn=request_json_fn,
        )
        if last.get("ok"):
            return last
        now = monotonic()
        if now >= deadline:
            raise RuntimeError(
                "Provider Runtime did not become healthy within "
                f"{float(timeout_seconds):g}s "
                f"(last_error={last.get('error')})"
            )
        sleep(min(0.25, max(0.0, deadline - now)))


def default_provider_runtime_script_path() -> Path:
    """Locate ``scripts/provider_runtime.py`` relative to this module when present."""
    candidate = Path(__file__).resolve().parents[1] / "scripts" / "provider_runtime.py"
    return candidate


def start_provider_runtime_serve_subprocess(
    *,
    root: Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    cdp_port: int = DEFAULT_CDP_PORT,
    state_path: Path | None = None,
    result_path: Path | None = None,
    keepalive_result_path: Path | None = None,
    python_executable: str | None = None,
    script_path: Path | None = None,
    popen_fn: Any = None,
    env: dict[str, str] | None = None,
) -> subprocess.Popen[Any]:
    """Launch ``provider_runtime.py serve`` as a detached child process."""
    root = Path(root).expanduser().resolve()
    script = Path(script_path) if script_path is not None else default_provider_runtime_script_path()
    if not script.is_file():
        raise FileNotFoundError(f"Provider Runtime script not found: {script}")
    command = [
        str(python_executable or sys.executable),
        str(script),
        "--root",
        str(root),
        "--host",
        str(host),
        "--port",
        str(int(port)),
        "--cdp-port",
        str(int(cdp_port)),
    ]
    if state_path is not None:
        command.extend(["--state-path", str(Path(state_path).expanduser().resolve())])
    if result_path is not None:
        command.extend(["--result-path", str(Path(result_path).expanduser().resolve())])
    if keepalive_result_path is not None:
        command.extend(
            [
                "--keepalive-result-path",
                str(Path(keepalive_result_path).expanduser().resolve()),
            ]
        )
    command.append("serve")

    child_env = dict(env or os.environ)
    repo_root = Path(__file__).resolve().parents[1]
    existing = child_env.get("PYTHONPATH", "")
    prefix = str(repo_root)
    if existing:
        if prefix not in existing.split(os.pathsep):
            child_env["PYTHONPATH"] = prefix + os.pathsep + existing
    else:
        child_env["PYTHONPATH"] = prefix

    launcher = popen_fn or subprocess.Popen
    return launcher(
        command,
        env=child_env,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_provider_runtime_serve(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    process: Any = None,
    request_json_fn: Any = None,
    wait_timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    """Stop a campaign-owned runtime via ``POST /shutdown`` (and process wait)."""
    http = request_json_fn or request_json
    base_url = _expiration_experiment_base_url(host, port)
    shutdown_error: str | None = None
    try:
        http("POST", f"{base_url}/shutdown")
    except Exception as exc:  # noqa: BLE001
        shutdown_error = f"{type(exc).__name__}: {exc}"

    process_exited = None
    if process is not None and hasattr(process, "wait"):
        try:
            process.wait(timeout=float(wait_timeout_seconds))
            process_exited = True
        except Exception:
            process_exited = False
            try:
                if hasattr(process, "kill"):
                    process.kill()
            except Exception:
                pass
    return {
        "ok": shutdown_error is None or bool(process_exited),
        "shutdown_error": shutdown_error,
        "process_exited": process_exited,
    }


def ensure_provider_runtime_for_campaign(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    root: Path | None = None,
    cdp_port: int = DEFAULT_CDP_PORT,
    state_path: Path | None = None,
    result_path: Path | None = None,
    keepalive_result_path: Path | None = None,
    health_timeout_seconds: float = DEFAULT_PROVIDER_RUNTIME_HEALTH_TIMEOUT_SECONDS,
    request_json_fn: Any = None,
    start_runtime_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    print_fn: Any = None,
) -> dict[str, Any]:
    """Reuse a healthy runtime or launch ``serve`` and remember ownership."""
    emit = print_fn or print
    health = check_provider_runtime_health(
        host=host,
        port=port,
        request_json_fn=request_json_fn,
    )
    if health.get("ok"):
        emit("Provider Runtime already running.")
        return {
            "ok": True,
            "runtime_preexisting": True,
            "runtime_started_by_campaign": False,
            "process": None,
            "base_url": health.get("base_url"),
        }

    emit("Provider Runtime not running.")
    emit("Starting Provider Runtime serve...")
    starter = start_runtime_fn or (
        lambda: start_provider_runtime_serve_subprocess(
            root=Path(root) if root is not None else DEFAULT_ROOT,
            host=host,
            port=port,
            cdp_port=int(cdp_port),
            state_path=state_path,
            result_path=result_path,
            keepalive_result_path=keepalive_result_path,
        )
    )
    try:
        process = starter()
    except Exception as exc:
        return {
            "ok": False,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": False,
            "process": None,
            "error": f"{type(exc).__name__}: {exc}",
            "outcome": "runtime_start_failed",
            "message": f"Failed to start Provider Runtime serve: {exc}",
        }

    try:
        ready = wait_for_provider_runtime_health(
            host=host,
            port=port,
            timeout_seconds=float(health_timeout_seconds),
            request_json_fn=request_json_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
        )
    except Exception as exc:
        # Best-effort cleanup of a process we started but could not health-check.
        try:
            stop_provider_runtime_serve(
                host=host,
                port=port,
                process=process,
                request_json_fn=request_json_fn,
            )
        except Exception:
            pass
        return {
            "ok": False,
            "runtime_preexisting": False,
            "runtime_started_by_campaign": True,
            "process": process,
            "error": f"{type(exc).__name__}: {exc}",
            "outcome": "runtime_start_failed",
            "message": f"Provider Runtime serve started but never became healthy: {exc}",
        }

    emit("Provider Runtime ready.")
    return {
        "ok": True,
        "runtime_preexisting": False,
        "runtime_started_by_campaign": True,
        "process": process,
        "base_url": ready.get("base_url"),
    }


def run_amex_provider_campaign(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    root: Path | None = None,
    cdp_port: int = DEFAULT_CDP_PORT,
    state_path: Path | None = None,
    result_path: Path | None = None,
    keepalive_result_path: Path | None = None,
    diagnostics_dir: Path | None = None,
    output_dir: Path | None = None,
    trials: list[str] | tuple[str, ...] | None = None,
    campaign_name: str | None = None,
    trial_duration_seconds: int = DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS,
    recording_timeout_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS
    ),
    evidence_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS
    ),
    verification_interval_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS
    ),
    rolling_window_seconds: float = DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS,
    screenshot_every_seconds: float = (
        DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS
    ),
    browser_cleanup: str = DEFAULT_BROWSER_CLEANUP_POLICY,
    continue_on_error: bool = False,
    skip_completed: bool = False,
    resume_dir: Path | None = None,
    request_json_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    input_fn: Any = None,
    print_fn: Any = None,
    ensure_runtime_fn: Any = None,
    stop_runtime_fn: Any = None,
    run_campaign_fn: Any = None,
    **campaign_kwargs: Any,
) -> dict[str, Any]:
    """First-class ``campaign amex`` entry: manage runtime, then run the campaign.

    Reuses ``run_amex_expiration_campaign`` for browser + trial orchestration.
    Starts ``serve`` only when needed and stops it only when this command owns it.
    Authentication pauses are handled inside the campaign runner as a recoverable
    loop and never enter this function's runtime cleanup path mid-pause.
    """
    emit = print_fn or print
    resolved_trials = resolve_amex_campaign_trials(trials)
    resolved_name = campaign_name or DEFAULT_AMEX_CAMPAIGN_NAME
    runtime_root = Path(root).expanduser().resolve() if root is not None else DEFAULT_ROOT
    effective_output_dir = output_dir
    effective_skip_completed = bool(skip_completed)
    if resume_dir is not None:
        effective_output_dir = Path(resume_dir).expanduser().resolve()
        effective_skip_completed = True
        emit(f"Resuming campaign from {effective_output_dir}")

    ensure_runtime = ensure_runtime_fn or ensure_provider_runtime_for_campaign
    runtime_info = ensure_runtime(
        host=host,
        port=port,
        root=runtime_root,
        cdp_port=int(cdp_port),
        state_path=state_path,
        result_path=result_path,
        keepalive_result_path=keepalive_result_path,
        request_json_fn=request_json_fn,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        print_fn=emit,
    )
    if not isinstance(runtime_info, dict) or not runtime_info.get("ok"):
        return {
            "ok": False,
            "outcome": (runtime_info or {}).get("outcome") or "runtime_start_failed",
            "campaign_name": resolved_name,
            "zip_path": None,
            "trial_summaries": [],
            "message": (runtime_info or {}).get("message")
            or (runtime_info or {}).get("error")
            or "Failed to ensure Provider Runtime",
            "error": (runtime_info or {}).get("error"),
            "exit_code": 1,
            "interrupted": False,
            "runtime_preexisting": bool((runtime_info or {}).get("runtime_preexisting")),
            "runtime_started_by_campaign": bool(
                (runtime_info or {}).get("runtime_started_by_campaign")
            ),
            "runtime_stopped_by_campaign": False,
            "trials": resolved_trials,
        }

    runtime_preexisting = bool(runtime_info.get("runtime_preexisting"))
    runtime_started_by_campaign = bool(runtime_info.get("runtime_started_by_campaign"))
    runtime_process = runtime_info.get("process")
    runtime_stopped_by_campaign = False

    run_campaign = run_campaign_fn or run_amex_expiration_campaign
    result: dict[str, Any]
    try:
        # Authentication is a recoverable in-campaign loop
        # (ensure_expiration_campaign_signed_in). Owned serve stays up for every
        # auth pause because cleanup runs only in this finally after the campaign
        # returns a terminal outcome.
        result = run_campaign(
            host=host,
            port=port,
            root=runtime_root,
            cdp_port=int(cdp_port),
            diagnostics_dir=diagnostics_dir
            if diagnostics_dir is not None
            else runtime_root / "diagnostics",
            output_dir=effective_output_dir,
            trials=resolved_trials,
            campaign_name=resolved_name,
            trial_duration_seconds=int(trial_duration_seconds),
            recording_timeout_seconds=float(recording_timeout_seconds),
            evidence_interval_seconds=float(evidence_interval_seconds),
            verification_interval_seconds=float(verification_interval_seconds),
            rolling_window_seconds=float(rolling_window_seconds),
            screenshot_every_seconds=float(screenshot_every_seconds),
            browser_cleanup=browser_cleanup,
            continue_on_error=continue_on_error,
            skip_completed=effective_skip_completed,
            request_json_fn=request_json_fn,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            input_fn=input_fn,
            print_fn=emit,
            **campaign_kwargs,
        )
        if not isinstance(result, dict):
            result = {
                "ok": False,
                "outcome": "fatal_error",
                "campaign_name": resolved_name,
                "zip_path": None,
                "trial_summaries": [],
                "exit_code": 1,
                "interrupted": False,
            }
    except KeyboardInterrupt:
        result = {
            "ok": False,
            "outcome": "interrupted",
            "campaign_name": resolved_name,
            "zip_path": None,
            "trial_summaries": [],
            "exit_code": 130,
            "interrupted": True,
            "message": None,
            "campaign_dir": str(effective_output_dir) if effective_output_dir else None,
        }
    except Exception as exc:  # noqa: BLE001 - always clean up owned runtime
        result = {
            "ok": False,
            "outcome": "fatal_error",
            "campaign_name": resolved_name,
            "zip_path": None,
            "trial_summaries": [],
            "exit_code": 1,
            "interrupted": False,
            "error": f"{type(exc).__name__}: {exc}",
            "message": f"{type(exc).__name__}: {exc}",
        }
    finally:
        if runtime_started_by_campaign:
            emit("Stopping Provider Runtime started by this campaign...")
            stopper = stop_runtime_fn or stop_provider_runtime_serve
            stopper(
                host=host,
                port=port,
                process=runtime_process,
                request_json_fn=request_json_fn,
            )
            runtime_stopped_by_campaign = True
        elif runtime_preexisting:
            emit("Leaving preexisting Provider Runtime running.")

    result = dict(result)
    result["runtime_preexisting"] = runtime_preexisting
    result["runtime_started_by_campaign"] = runtime_started_by_campaign
    result["runtime_stopped_by_campaign"] = runtime_stopped_by_campaign
    result["trials"] = resolved_trials
    result["campaign_name"] = result.get("campaign_name") or resolved_name
    return result


def _explorer_tag_label(node: dict[str, Any] | None) -> str:
    if not node:
        return "?"
    name = str(node.get("nodeName") or "").lower()
    if not name or name.startswith("#"):
        return name or "?"
    attrs = _attrs_list_to_dict(node.get("attributes"))
    class_summary = _class_summary_from_attrs(attrs)
    node_id_attr = attrs.get("id")
    parts = [name]
    if node_id_attr:
        parts.append(f"#{node_id_attr}")
    if class_summary:
        first_class = class_summary.split()[0]
        parts.append(f".{first_class}")
    return "".join(parts) if len(parts) == 1 else f"{parts[0]}{''.join(parts[1:])}"


def _build_explorer_index(
    node: dict[str, Any],
    index: dict[int, dict[str, Any]],
    *,
    parent_backend_id: int | None = None,
    shadow_root_depth: int = 0,
    iframe_depth: int = 0,
    frame_url: str | None = None,
    frame_id: str | None = None,
    frame_url_by_id: dict[str, str | None] | None = None,
) -> None:
    """Index pierced DOM for developer text search (depths + frame URLs)."""
    url_by_id = frame_url_by_id or {}
    backend_id = node.get("backendNodeId")
    node_id = node.get("nodeId")
    current_frame_id = node.get("frameId") or frame_id
    current_frame_url = frame_url
    if current_frame_id and current_frame_id in url_by_id:
        current_frame_url = url_by_id.get(current_frame_id) or current_frame_url

    if backend_id is not None:
        backend_int = int(backend_id)
        attrs = _attrs_list_to_dict(node.get("attributes"))
        index[backend_int] = {
            "node": node,
            "node_id": int(node_id) if node_id is not None else None,
            "parent_backend_id": parent_backend_id,
            "backend_node_id": backend_int,
            "node_name": str(node.get("nodeName") or ""),
            "node_type": node.get("nodeType"),
            "attributes": attrs,
            "frame_id": current_frame_id,
            "frame_url": current_frame_url,
            "shadow_root_depth": int(shadow_root_depth),
            "iframe_depth": int(iframe_depth),
            "node_value": node.get("nodeValue"),
        }
        current_parent = backend_int
    else:
        current_parent = parent_backend_id

    for child in node.get("children") or []:
        if isinstance(child, dict):
            _build_explorer_index(
                child,
                index,
                parent_backend_id=current_parent,
                shadow_root_depth=shadow_root_depth,
                iframe_depth=iframe_depth,
                frame_url=current_frame_url,
                frame_id=current_frame_id,
                frame_url_by_id=url_by_id,
            )
    for shadow in node.get("shadowRoots") or []:
        if isinstance(shadow, dict):
            _build_explorer_index(
                shadow,
                index,
                parent_backend_id=current_parent,
                shadow_root_depth=shadow_root_depth + 1,
                iframe_depth=iframe_depth,
                frame_url=current_frame_url,
                frame_id=current_frame_id,
                frame_url_by_id=url_by_id,
            )
    content = node.get("contentDocument")
    if isinstance(content, dict):
        content_frame_id = content.get("frameId") or current_frame_id
        content_frame_url = url_by_id.get(content_frame_id) if content_frame_id else None
        _build_explorer_index(
            content,
            index,
            parent_backend_id=current_parent,
            shadow_root_depth=shadow_root_depth,
            iframe_depth=iframe_depth + 1,
            frame_url=content_frame_url or current_frame_url,
            frame_id=content_frame_id,
            frame_url_by_id=url_by_id,
        )


def _parent_chain_for_backend(
    index: dict[int, dict[str, Any]],
    backend_node_id: int,
    *,
    limit: int = FIND_TEXT_PARENT_CHAIN_MAX,
) -> list[str]:
    chain: list[str] = []
    current: int | None = index.get(backend_node_id, {}).get("parent_backend_id")
    seen: set[int] = set()
    while current is not None and current not in seen and len(chain) < limit:
        seen.add(current)
        info = index.get(current) or {}
        chain.append(_explorer_tag_label(info.get("node")))
        current = info.get("parent_backend_id")
    return chain


def _ancestor_distance(
    index: dict[int, dict[str, Any]],
    backend_node_id: int,
) -> int:
    distance = 0
    current: int | None = index.get(backend_node_id, {}).get("parent_backend_id")
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        distance += 1
        current = (index.get(current) or {}).get("parent_backend_id")
    return distance


def _nearest_element_backend(
    index: dict[int, dict[str, Any]],
    backend_node_id: int,
) -> int | None:
    current: int | None = backend_node_id
    seen: set[int] = set()
    while current is not None and current not in seen:
        seen.add(current)
        info = index.get(current) or {}
        node_type = info.get("node_type")
        name = str(info.get("node_name") or "").upper()
        if node_type == 1 or (name and not name.startswith("#")):
            return current
        current = info.get("parent_backend_id")
    return None


def _collect_action_descendants(
    node: dict[str, Any],
    *,
    ax_name_by_backend: dict[int, str],
) -> tuple[list[dict[str, Any]], list[str]]:
    buttons: list[dict[str, Any]] = []
    links: list[str] = []

    def walk(current: dict[str, Any]) -> None:
        if (
            len(buttons) >= FIND_TEXT_ACTION_DESCENDANT_MAX
            and len(links) >= FIND_TEXT_ACTION_DESCENDANT_MAX
        ):
            return
        name = str(current.get("nodeName") or "").lower()
        backend_id = current.get("backendNodeId")
        if name in {"button", "a"} or (
            name
            and _attrs_list_to_dict(current.get("attributes")).get("role") == "button"
        ):
            info = {
                "node": current,
                "backend_node_id": int(backend_id) if backend_id is not None else None,
                "attributes": _attrs_list_to_dict(current.get("attributes")),
                "node_name": name,
            }
            label = _action_label_from_node(info, ax_name_by_backend=ax_name_by_backend)
            if label:
                if name == "a":
                    if len(links) < FIND_TEXT_ACTION_DESCENDANT_MAX:
                        links.append(label)
                elif len(buttons) < FIND_TEXT_ACTION_DESCENDANT_MAX:
                    buttons.append(
                        {
                            "label": label,
                            "backend_node_id": info.get("backend_node_id"),
                        }
                    )
        for child in current.get("children") or []:
            if isinstance(child, dict):
                walk(child)
        for shadow in current.get("shadowRoots") or []:
            if isinstance(shadow, dict):
                walk(shadow)

    walk(node)
    return buttons, links


def _match_sort_key(match: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if match.get("exact_match") else 1,
        0 if match.get("match_source") == "ax_name" else 1,
        0 if match.get("match_source") == "dom_text" else 1,
        int(match.get("ancestor_distance") or 0),
        str(match.get("tag_name") or ""),
        int(match.get("backend_node_id") or 0),
    )


def _build_text_match_record(
    session: Any,
    *,
    index: dict[int, dict[str, Any]],
    backend_node_id: int,
    matched_text: str,
    match_source: str,
    query: str,
    ax_name_by_backend: dict[int, str],
    ax_role_by_backend: dict[int, str],
) -> dict[str, Any] | None:
    element_backend = _nearest_element_backend(index, backend_node_id)
    if element_backend is None:
        return None
    info = index.get(element_backend) or {}
    node = info.get("node") or {}
    attrs = dict(info.get("attributes") or {})
    role = (attrs.get("role") or "").strip() or None
    if element_backend in ax_role_by_backend:
        role = role or ax_role_by_backend[element_backend]
    accessible_name = ax_name_by_backend.get(element_backend)
    if not accessible_name:
        accessible_name = _normalize_text(
            attrs.get("aria-label") or attrs.get("title") or ""
        ) or None
    text_snippet = _collect_text_from_dom_node(node, limit=FIND_TEXT_SNIPPET_MAX_CHARS)
    if not text_snippet and accessible_name:
        text_snippet = accessible_name[:FIND_TEXT_SNIPPET_MAX_CHARS]
    buttons, links = _collect_action_descendants(
        node,
        ax_name_by_backend=ax_name_by_backend,
    )
    geometry = None
    try:
        geometry = _box_from_model(get_node_box_model(session, element_backend))
    except Exception:
        geometry = None

    query_norm = _normalize_text(query)
    haystacks = [
        _normalize_text(matched_text),
        _normalize_text(accessible_name or ""),
        _normalize_text(text_snippet or ""),
    ]
    exact_match = any(h == query_norm for h in haystacks if h)

    return {
        "frame_url": sanitize_url(info.get("frame_url")),
        "backend_node_id": element_backend,
        "node_id": info.get("node_id"),
        "tag_name": str(info.get("node_name") or "").lower() or None,
        "role": role,
        "accessible_name": (accessible_name[:120] if accessible_name else None),
        "class_summary": _class_summary_from_attrs(attrs),
        "text_snippet": text_snippet or None,
        "matched_text": _normalize_text(matched_text)[:FIND_TEXT_SNIPPET_MAX_CHARS],
        "match_source": match_source,
        "parent_chain": _parent_chain_for_backend(index, element_backend),
        "shadow_root_depth": int(info.get("shadow_root_depth") or 0),
        "iframe_depth": int(info.get("iframe_depth") or 0),
        "attributes": {
            "role": attrs.get("role"),
            "aria-modal": attrs.get("aria-modal"),
            "aria-label": attrs.get("aria-label"),
            "id": attrs.get("id"),
            "class": attrs.get("class"),
        },
        "button_descendants": buttons,
        "link_descendants": links,
        "geometry": geometry,
        "exact_match": exact_match,
        "ancestor_distance": _ancestor_distance(index, element_backend),
    }


def find_text_in_page_cdp(
    page: Page,
    query: str,
) -> dict[str, Any]:
    """Developer-only: locate substring matches via CDP DOM/AX (no evaluate)."""
    needle = _normalize_text(query)
    page_url = sanitize_url(getattr(page, "url", None))
    if not needle:
        return {
            "ok": False,
            "query": query,
            "selected_page_url": page_url,
            "match_count": 0,
            "matches": [],
            "error": "empty_query",
        }

    session = None
    try:
        session = open_page_cdp_session(page)
        enable_inspection_domains(session)
        try:
            frame_tree = get_frame_tree(session)
            frame_entries = _frame_entries_from_tree(frame_tree)
        except Exception:
            frame_entries = [{"frame_id": None, "frame_url": page_url}]
        frame_url_by_id = {
            str(entry["frame_id"]): entry.get("frame_url")
            for entry in frame_entries
            if entry.get("frame_id")
        }
        document_payload = get_pierced_document(session)
        root = document_payload.get("root") or {}
        index: dict[int, dict[str, Any]] = {}
        if isinstance(root, dict):
            _build_explorer_index(
                root,
                index,
                frame_url=page_url,
                frame_url_by_id=frame_url_by_id,
            )

        ax_name_by_backend: dict[int, str] = {}
        ax_role_by_backend: dict[int, str] = {}
        try:
            ax_tree = get_accessibility_tree(session)
            ax_name_by_backend, ax_role_by_backend, _dialog_backends = _build_ax_maps(
                ax_tree
            )
        except Exception:
            ax_tree = {"nodes": []}

        raw_matches: list[dict[str, Any]] = []
        seen_keys: set[tuple[Any, ...]] = set()

        def add_match(
            *,
            backend_node_id: int | None,
            matched_text: str,
            match_source: str,
        ) -> None:
            if backend_node_id is None:
                return
            record = _build_text_match_record(
                session,
                index=index,
                backend_node_id=int(backend_node_id),
                matched_text=matched_text,
                match_source=match_source,
                query=query,
                ax_name_by_backend=ax_name_by_backend,
                ax_role_by_backend=ax_role_by_backend,
            )
            if record is None:
                return
            key = (
                record.get("backend_node_id"),
                record.get("match_source"),
                record.get("matched_text"),
            )
            if key in seen_keys:
                return
            seen_keys.add(key)
            raw_matches.append(record)

        # DOM text nodes in pierced tree (main + shadow + accessible iframes).
        for backend_id, info in list(index.items()):
            if info.get("node_type") != 3:
                continue
            value = str(info.get("node_value") or "")
            normalized = _normalize_text(value)
            if needle not in normalized:
                continue
            add_match(
                backend_node_id=backend_id,
                matched_text=normalized,
                match_source="dom_text",
            )

        # Accessibility names (may surface text not present as DOM text nodes).
        for backend_id, name in ax_name_by_backend.items():
            normalized = _normalize_text(name)
            if needle not in normalized:
                continue
            add_match(
                backend_node_id=backend_id,
                matched_text=normalized,
                match_source="ax_name",
            )

        ordered = sorted(raw_matches, key=_match_sort_key)
        truncated = len(ordered) > FIND_TEXT_MAX_MATCHES
        matches = ordered[:FIND_TEXT_MAX_MATCHES]
        return {
            "ok": True,
            "query": query,
            "selected_page_url": page_url,
            "match_count": len(matches),
            "matches": matches,
            "truncated": truncated,
            "collector": "cdp_dom_ax_text_search",
        }
    except Exception as exc:
        return {
            "ok": False,
            "query": query,
            "selected_page_url": page_url,
            "match_count": 0,
            "matches": [],
            "error": f"{type(exc).__name__}: {exc}",
            "exception_class": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
    finally:
        _safe_detach_cdp_session(session)


def find_text_in_browser_context(
    context: BrowserContext,
    query: str,
    *,
    provider: str = "amex",
    select_page_fn: Any = None,
) -> dict[str, Any]:
    """Run developer text search against the selected provider page."""
    selector = select_page_fn or (
        select_amex_page if provider == "amex" else select_provider_page
    )
    if select_page_fn is None and provider == "amex":
        selected = select_amex_page(context, create_if_missing=False)
    elif select_page_fn is None:
        selected = None
    else:
        selected = selector(context, create_if_missing=False)
    if selected is None:
        return {
            "ok": False,
            "query": query,
            "selected_page_url": None,
            "match_count": 0,
            "matches": [],
            "error": "no_provider_page_selected",
        }
    return find_text_in_page_cdp(selected, query)


def format_browser_find_text_report(payload: dict[str, Any]) -> str:
    """Render browser-find-text matches as a human-readable report."""
    lines: list[str] = []
    lines.append(f"query={payload.get('query')!r}")
    lines.append(f"selected_page_url={payload.get('selected_page_url')!r}")
    lines.append(f"match_count={payload.get('match_count', 0)}")
    if payload.get("error"):
        lines.append(f"error={payload.get('error')}")
    lines.append("")
    matches = payload.get("matches") or []
    if not matches:
        lines.append("NO MATCHES")
        return "\n".join(lines) + "\n"
    for index, match in enumerate(matches, start=1):
        lines.append(f"MATCH {index}")
        lines.append(f"frame:\n{match.get('frame_url')}")
        lines.append(f"backend_node_id:\n{match.get('backend_node_id')}")
        lines.append(f"node_id:\n{match.get('node_id')}")
        lines.append(f"tag:\n{match.get('tag_name')}")
        lines.append(f"role:\n{match.get('role')}")
        lines.append(f"accessible_name:\n{match.get('accessible_name')}")
        lines.append(f"class:\n{match.get('class_summary')}")
        lines.append(f"match_source:\n{match.get('match_source')}")
        lines.append(f"matched_text:\n{match.get('matched_text')}")
        lines.append(f"text:\n{match.get('text_snippet')}")
        lines.append("parent chain:")
        for item in match.get("parent_chain") or []:
            lines.append(f"  {item}")
        lines.append(f"shadow_root_depth:\n{match.get('shadow_root_depth')}")
        lines.append(f"iframe_depth:\n{match.get('iframe_depth')}")
        attrs = match.get("attributes") or {}
        lines.append("attributes:")
        for key in ("role", "aria-modal", "aria-label", "id", "class"):
            lines.append(f"  {key}: {attrs.get(key)}")
        lines.append("buttons:")
        buttons = match.get("button_descendants") or []
        if not buttons:
            lines.append("  (none)")
        for button in buttons:
            lines.append(
                f"  {button.get('label')}  backend_node_id={button.get('backend_node_id')}"
            )
        lines.append("links:")
        links = match.get("link_descendants") or []
        if not links:
            lines.append("  (none)")
        for link in links:
            lines.append(f"  {link}")
        lines.append(f"geometry:\n{match.get('geometry')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def parse_browser_watch_terms(raw: str | None) -> list[str]:
    """Split comma-separated watch terms; preserve caller wording, drop empties."""
    if raw is None:
        return list(DEFAULT_BROWSER_WATCH_TERMS)
    terms = [part.strip() for part in str(raw).split(",")]
    return [term for term in terms if term]


def default_browser_watch_output_path(
    diagnostics_dir: Path | None = None,
    *,
    when: datetime | None = None,
) -> Path:
    """Default ~/.mighty/provider_runtime/diagnostics/amex-text-watch-<UTC>.json."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
    return base / f"amex-text-watch-{stamp}.json"


def matched_terms_in_page_cdp(
    page: Page,
    terms: list[str],
) -> dict[str, Any]:
    """One CDP DOM/AX snapshot; which configured terms match (no evaluate)."""
    page_url = sanitize_url(getattr(page, "url", None))
    cleaned = [term for term in terms if _normalize_text(term)]
    if not cleaned:
        return {
            "ok": False,
            "matched_terms": [],
            "selected_page_url": page_url,
            "error": "empty_terms",
        }

    session = None
    try:
        session = open_page_cdp_session(page)
        enable_inspection_domains(session)
        try:
            frame_tree = get_frame_tree(session)
            frame_entries = _frame_entries_from_tree(frame_tree)
        except Exception:
            frame_entries = [{"frame_id": None, "frame_url": page_url}]
        frame_url_by_id = {
            str(entry["frame_id"]): entry.get("frame_url")
            for entry in frame_entries
            if entry.get("frame_id")
        }
        document_payload = get_pierced_document(session)
        root = document_payload.get("root") or {}
        index: dict[int, dict[str, Any]] = {}
        if isinstance(root, dict):
            _build_explorer_index(
                root,
                index,
                frame_url=page_url,
                frame_url_by_id=frame_url_by_id,
            )

        ax_name_by_backend: dict[int, str] = {}
        try:
            ax_tree = get_accessibility_tree(session)
            ax_name_by_backend, _ax_role_by_backend, _dialog_backends = _build_ax_maps(
                ax_tree
            )
        except Exception:
            ax_name_by_backend = {}

        corpus: list[str] = []
        for info in index.values():
            if info.get("node_type") != 3:
                continue
            corpus.append(_normalize_text(str(info.get("node_value") or "")))
        for name in ax_name_by_backend.values():
            corpus.append(_normalize_text(name))

        matched: list[str] = []
        for term in cleaned:
            needle = _normalize_text(term)
            if any(needle and needle in text for text in corpus):
                matched.append(term)
        return {
            "ok": True,
            "matched_terms": matched,
            "selected_page_url": page_url,
            "collector": "cdp_dom_ax_text_watch_poll",
        }
    except Exception as exc:
        return {
            "ok": False,
            "matched_terms": [],
            "selected_page_url": page_url,
            "error": f"{type(exc).__name__}: {exc}",
            "exception_class": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }
    finally:
        _safe_detach_cdp_session(session)


def _sanitize_watch_text_field(text: str | None) -> str | None:
    """Bound and redact a find-text string for watch diagnostic persistence."""
    if text is None:
        return None
    cleaned = redact_long_digit_sequences(" ".join(str(text).lower().split()))
    return cleaned[:FIND_TEXT_SNIPPET_MAX_CHARS] or None


def _sanitize_find_text_payload_for_watch(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound/redact find-text fields before persisting a watch diagnostic bundle."""
    cleaned = dict(payload)
    matches_out: list[dict[str, Any]] = []
    for match in cleaned.get("matches") or []:
        if not isinstance(match, dict):
            continue
        item = dict(match)
        for key in ("matched_text", "text_snippet", "accessible_name"):
            if key in item:
                item[key] = _sanitize_watch_text_field(
                    None if item[key] is None else str(item[key])
                )
        matches_out.append(item)
    cleaned["matches"] = matches_out
    if cleaned.get("error"):
        cleaned["error"] = redact_long_digit_sequences(str(cleaned["error"]))[:300]
    # Never persist CDP tracebacks with potential page content in watch files.
    cleaned.pop("traceback", None)
    return cleaned


def _capture_browser_text_watch_bundle(
    page: Page,
    *,
    terms: list[str],
    matched_terms: list[str],
    started_at: str,
    matched_at: str,
    interval_seconds: float,
    timeout_seconds: float,
    canonical_authentication_state: str | None,
    canonical_authentication_state_source: str,
    output_file: Path,
    provider: str,
    errors: list[str],
    find_text_fn: Any = None,
    inspect_fn: Any = None,
) -> dict[str, Any]:
    """Build and persist one sanitized text-watch diagnostic bundle."""
    find_fn = find_text_fn or find_text_in_page_cdp
    find_text_results_by_term: dict[str, Any] = {}
    for term in terms:
        try:
            find_text_results_by_term[term] = _sanitize_find_text_payload_for_watch(
                find_fn(page, term)
            )
        except Exception as exc:
            find_text_results_by_term[term] = {
                "ok": False,
                "query": term,
                "match_count": 0,
                "matches": [],
                "error": f"{type(exc).__name__}: {exc}",
            }
            errors.append(f"find_text:{term}:{type(exc).__name__}")

    browser_inspection: dict[str, Any] | None = None
    try:
        if inspect_fn is not None:
            browser_inspection = inspect_fn(page)
        else:
            context = page.context
            inspection = inspect_browser_context(
                context,
                provider=provider,
                capture_screenshot=False,
                mark_continue=False,
                select_page_fn=lambda _ctx, create_if_missing=False: page,
            )
            browser_inspection = inspection.to_sanitized_dict()
    except Exception as exc:
        errors.append(f"browser_inspection:{type(exc).__name__}: {exc}")
        browser_inspection = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "exception_class": type(exc).__name__,
        }

    completed_at = iso_now()
    output_file = Path(output_file)
    bundle = {
        "ok": True,
        "started_at": started_at,
        "matched_at": matched_at,
        "completed_at": completed_at,
        "configured_terms": list(terms),
        "matched_terms": list(matched_terms),
        "interval_seconds": interval_seconds,
        "timeout_seconds": timeout_seconds,
        "selected_page_url": sanitize_url(getattr(page, "url", None)),
        "canonical_authentication_state": canonical_authentication_state,
        "canonical_authentication_state_source": canonical_authentication_state_source,
        "find_text_results_by_term": find_text_results_by_term,
        "browser_inspection": browser_inspection,
        "errors": list(errors),
        "timed_out": False,
        "output_file": str(output_file),
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return bundle


def watch_text_on_page(
    page: Page,
    terms: list[str],
    *,
    interval_seconds: float = DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS,
    stop_after_first_match: bool = True,
    output_file: Path | None = None,
    diagnostics_dir: Path | None = None,
    canonical_authentication_state: str | None = None,
    canonical_authentication_state_source: str = AUTH_STATE_SOURCE_NONE,
    provider: str = "amex",
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    poll_fn: Any = None,
    find_text_fn: Any = None,
    inspect_fn: Any = None,
) -> dict[str, Any]:
    """Poll CDP DOM/AX for configured terms; capture one diagnostic on match.

    Developer diagnostics only: never clicks, never mutates the page, never uses
    page.evaluate / frame.evaluate. Independent of keepalive trial threads.
    """
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    poll = poll_fn or matched_terms_in_page_cdp
    started_at = iso_now()
    started_mono = monotonic()
    deadline = started_mono + max(0.0, float(timeout_seconds))
    interval = max(0.0, float(interval_seconds))
    errors: list[str] = []
    poll_count = 0
    last_poll_at: str | None = None
    output_paths: list[str] = []
    last_bundle: dict[str, Any] | None = None
    configured = list(terms)

    if not configured:
        completed_at = iso_now()
        path = Path(output_file) if output_file else default_browser_watch_output_path(
            diagnostics_dir
        )
        bundle = {
            "ok": False,
            "started_at": started_at,
            "matched_at": None,
            "completed_at": completed_at,
            "configured_terms": [],
            "matched_terms": [],
            "interval_seconds": interval,
            "timeout_seconds": float(timeout_seconds),
            "selected_page_url": sanitize_url(getattr(page, "url", None)),
            "canonical_authentication_state": canonical_authentication_state,
            "canonical_authentication_state_source": canonical_authentication_state_source,
            "find_text_results_by_term": {},
            "browser_inspection": None,
            "errors": ["empty_terms"],
            "timed_out": False,
            "poll_count": 0,
            "last_poll_at": None,
            "matched": False,
            "output_file": str(path),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        return bundle

    while True:
        poll_count += 1
        last_poll_at = iso_now()
        try:
            poll_result = poll(page, configured)
        except Exception as exc:
            errors.append(f"poll:{type(exc).__name__}: {exc}")
            poll_result = {
                "ok": False,
                "matched_terms": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

        if not poll_result.get("ok"):
            err = poll_result.get("error")
            if err:
                errors.append(f"poll:{err}")
            matched_now: list[str] = []
        else:
            matched_now = list(poll_result.get("matched_terms") or [])

        if matched_now:
            matched_at = last_poll_at
            if output_file is not None and not output_paths:
                path = Path(output_file)
            else:
                path = default_browser_watch_output_path(diagnostics_dir)
            try:
                last_bundle = _capture_browser_text_watch_bundle(
                    page,
                    terms=configured,
                    matched_terms=matched_now,
                    started_at=started_at,
                    matched_at=matched_at,
                    interval_seconds=interval,
                    timeout_seconds=float(timeout_seconds),
                    canonical_authentication_state=canonical_authentication_state,
                    canonical_authentication_state_source=(
                        canonical_authentication_state_source
                    ),
                    output_file=path,
                    provider=provider,
                    errors=list(errors),
                    find_text_fn=find_text_fn,
                    inspect_fn=inspect_fn,
                )
                output_paths.append(str(path))
            except Exception as exc:
                errors.append(f"capture:{type(exc).__name__}: {exc}")
                last_bundle = {
                    "ok": False,
                    "started_at": started_at,
                    "matched_at": matched_at,
                    "completed_at": iso_now(),
                    "configured_terms": configured,
                    "matched_terms": matched_now,
                    "interval_seconds": interval,
                    "timeout_seconds": float(timeout_seconds),
                    "selected_page_url": sanitize_url(getattr(page, "url", None)),
                    "canonical_authentication_state": canonical_authentication_state,
                    "canonical_authentication_state_source": (
                        canonical_authentication_state_source
                    ),
                    "find_text_results_by_term": {},
                    "browser_inspection": None,
                    "errors": list(errors),
                    "timed_out": False,
                    "output_file": str(path),
                }
            if stop_after_first_match:
                last_bundle["poll_count"] = poll_count
                last_bundle["last_poll_at"] = last_poll_at
                last_bundle["matched"] = True
                return last_bundle

        now = monotonic()
        if now >= deadline:
            break
        remaining = deadline - now
        if interval > 0:
            sleep(min(interval, remaining))
        elif remaining > 0:
            # Zero interval: still yield so tests/mocks can advance time.
            sleep(0)

    completed_at = iso_now()
    path = Path(output_file) if output_file else default_browser_watch_output_path(
        diagnostics_dir
    )
    if last_bundle is not None:
        # Continued after first match until timeout; return the latest capture.
        last_bundle["poll_count"] = poll_count
        last_bundle["last_poll_at"] = last_poll_at
        last_bundle["matched"] = True
        last_bundle["completed_at"] = completed_at
        last_bundle["timed_out"] = True
        last_bundle["output_files"] = output_paths
        return last_bundle

    bundle = {
        "ok": True,
        "started_at": started_at,
        "matched_at": None,
        "completed_at": completed_at,
        "configured_terms": configured,
        "matched_terms": [],
        "interval_seconds": interval,
        "timeout_seconds": float(timeout_seconds),
        "selected_page_url": sanitize_url(getattr(page, "url", None)),
        "canonical_authentication_state": canonical_authentication_state,
        "canonical_authentication_state_source": canonical_authentication_state_source,
        "find_text_results_by_term": {},
        "browser_inspection": None,
        "errors": list(errors),
        "timed_out": True,
        "poll_count": poll_count,
        "last_poll_at": last_poll_at,
        "matched": False,
        "output_file": str(path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    return bundle


def watch_text_in_browser_context(
    context: BrowserContext,
    terms: list[str],
    *,
    provider: str = "amex",
    select_page_fn: Any = None,
    interval_seconds: float = DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS,
    stop_after_first_match: bool = True,
    output_file: Path | None = None,
    diagnostics_dir: Path | None = None,
    canonical_authentication_state: str | None = None,
    canonical_authentication_state_source: str = AUTH_STATE_SOURCE_NONE,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    poll_fn: Any = None,
    find_text_fn: Any = None,
    inspect_fn: Any = None,
) -> dict[str, Any]:
    """Run developer text watcher against the selected provider page."""
    selector = select_page_fn or (
        select_amex_page if provider == "amex" else select_provider_page
    )
    if select_page_fn is None and provider == "amex":
        selected = select_amex_page(context, create_if_missing=False)
    elif select_page_fn is None:
        selected = None
    else:
        selected = selector(context, create_if_missing=False)
    if selected is None:
        started_at = iso_now()
        path = Path(output_file) if output_file else default_browser_watch_output_path(
            diagnostics_dir
        )
        bundle = {
            "ok": False,
            "started_at": started_at,
            "matched_at": None,
            "completed_at": iso_now(),
            "configured_terms": list(terms),
            "matched_terms": [],
            "interval_seconds": float(interval_seconds),
            "timeout_seconds": float(timeout_seconds),
            "selected_page_url": None,
            "canonical_authentication_state": canonical_authentication_state,
            "canonical_authentication_state_source": (
                canonical_authentication_state_source
            ),
            "find_text_results_by_term": {},
            "browser_inspection": None,
            "errors": ["no_provider_page_selected"],
            "timed_out": False,
            "poll_count": 0,
            "last_poll_at": None,
            "matched": False,
            "output_file": str(path),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
        return bundle
    return watch_text_on_page(
        selected,
        terms,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        stop_after_first_match=stop_after_first_match,
        output_file=output_file,
        diagnostics_dir=diagnostics_dir,
        canonical_authentication_state=canonical_authentication_state,
        canonical_authentication_state_source=canonical_authentication_state_source,
        provider=provider,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        poll_fn=poll_fn,
        find_text_fn=find_text_fn,
        inspect_fn=inspect_fn,
    )


def default_browser_record_output_dir(
    diagnostics_dir: Path | None = None,
    *,
    when: datetime | None = None,
) -> Path:
    """Default ~/.mighty/provider_runtime/diagnostics/amex-expiration-recording-<UTC>/."""
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = diagnostics_dir or DEFAULT_DIAGNOSTICS_DIR
    return base / f"amex-expiration-recording-{stamp}"


def summarize_frame_tree_for_recording(frame_tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Bounded Page.getFrameTree summary for expiration recordings."""
    entries: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], *, parent_frame_id: str | None) -> None:
        frame = node.get("frame") or {}
        frame_id = frame.get("id")
        entries.append(
            {
                "frame_id": frame_id,
                "parent_frame_id": parent_frame_id,
                "frame_url": sanitize_url(frame.get("url")),
                "security_origin": frame.get("securityOrigin"),
                "mime_type": frame.get("mimeType"),
            }
        )
        for child in node.get("childFrames") or []:
            if isinstance(child, dict):
                walk(child, parent_frame_id=str(frame_id) if frame_id is not None else None)

    root = frame_tree.get("frameTree") or frame_tree
    if isinstance(root, dict):
        walk(root, parent_frame_id=None)
    return entries


def summarize_browser_targets_for_recording(
    targets_payload: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Bounded Target.getTargets summary (no cookies/headers/bodies)."""
    out: list[dict[str, Any]] = []
    if not isinstance(targets_payload, dict):
        return out
    for info in targets_payload.get("targetInfos") or []:
        if not isinstance(info, dict):
            continue
        item = {
            "targetId": info.get("targetId"),
            "type": info.get("type"),
            "title": _sanitize_watch_text_field(info.get("title")),
            "url": sanitize_url(info.get("url")),
            "attached": info.get("attached"),
        }
        if info.get("openerId") is not None:
            item["openerId"] = info.get("openerId")
        out.append(item)
    return out


def _bounded_redacted_text_summary(parts: list[str], *, limit: int) -> str:
    joined = _normalize_text(" ".join(part for part in parts if part))
    cleaned = redact_long_digit_sequences(joined)
    return cleaned[:limit]


def capture_viewport_screenshot_cdp(session: Any, path: Path) -> None:
    """Capture a PNG viewport screenshot via CDP Page.captureScreenshot."""
    result = _cdp_send(session, "Page.captureScreenshot", {"format": "png"})
    data = result.get("data") if isinstance(result, dict) else None
    if not data:
        raise RuntimeError("Page.captureScreenshot returned no image data")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data))


def _browser_inspector_summary_for_recording(
    page: Page,
    *,
    inspect_fn: Any = None,
) -> dict[str, Any]:
    """Sanitized Browser Inspector summary for one recording poll."""
    if inspect_fn is not None:
        payload = inspect_fn(page)
        if isinstance(payload, dict):
            candidates = list(payload.get("candidates") or [])
            return {
                "candidate_count": int(
                    payload.get("candidate_count", len(candidates)) or len(candidates)
                ),
                "candidates": candidates,
                "errors": list(payload.get("errors") or []),
            }
        return {"candidate_count": 0, "candidates": [], "errors": ["invalid_inspect_payload"]}

    candidates, _frame_count, errors, _frame_diagnostics = inspect_page_browser(
        page,
        mark_continue=False,
    )
    return {
        "candidate_count": len(candidates),
        "candidates": [item.to_sanitized_dict() for item in candidates],
        "errors": list(errors),
    }


def _optional_text_search_summaries(
    page: Page,
    terms: list[str] | tuple[str, ...],
    *,
    find_text_fn: Any = None,
) -> list[dict[str, Any]]:
    """Run optional CDP text searches; never used as completion trigger."""
    finder = find_text_fn or find_text_in_page_cdp
    summaries: list[dict[str, Any]] = []
    for term in terms:
        try:
            payload = finder(page, term)
        except Exception as exc:
            summaries.append(
                {
                    "term": term,
                    "match_count": 0,
                    "match_summaries": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        cleaned = _sanitize_find_text_payload_for_watch(
            payload if isinstance(payload, dict) else {}
        )
        match_summaries: list[dict[str, Any]] = []
        for match in (cleaned.get("matches") or [])[:BROWSER_RECORD_MATCH_SUMMARY_MAX]:
            if not isinstance(match, dict):
                continue
            match_summaries.append(
                {
                    "matched_text": match.get("matched_text"),
                    "text_snippet": match.get("text_snippet"),
                    "match_source": match.get("match_source"),
                    "frame_url": match.get("frame_url"),
                }
            )
        summaries.append(
            {
                "term": term,
                "match_count": int(cleaned.get("match_count") or 0),
                "match_summaries": match_summaries,
            }
        )
    return summaries


def collect_expiration_recording_observation(
    page: Page,
    *,
    screenshot_path: Path | None = None,
    search_terms: list[str] | tuple[str, ...] = DEFAULT_BROWSER_RECORD_SEARCH_TERMS,
    inspect_fn: Any = None,
    find_text_fn: Any = None,
    screenshot_fn: Any = None,
) -> dict[str, Any]:
    """Collect one bounded non-mutating CDP observation for expiration recording."""
    collection_errors: list[str] = []
    page_url = sanitize_url(getattr(page, "url", None))
    page_title: str | None = None
    try:
        page_title = _sanitize_watch_text_field(page.title())
    except Exception as exc:
        collection_errors.append(f"title:{type(exc).__name__}: {exc}")

    browser_targets: list[dict[str, Any]] = []
    frame_tree_summary: list[dict[str, Any]] = []
    dom_text_summary = ""
    ax_text_summary = ""
    runtime_error: str | None = None
    session = None
    try:
        session = open_page_cdp_session(page)
        enable_inspection_domains(session)
        try:
            targets_payload = _cdp_send(session, "Target.getTargets")
            browser_targets = summarize_browser_targets_for_recording(targets_payload)
        except Exception as exc:
            collection_errors.append(f"Target.getTargets:{type(exc).__name__}: {exc}")
        try:
            frame_tree = get_frame_tree(session)
            frame_tree_summary = summarize_frame_tree_for_recording(frame_tree)
            frame_entries = _frame_entries_from_tree(frame_tree)
        except Exception as exc:
            collection_errors.append(f"Page.getFrameTree:{type(exc).__name__}: {exc}")
            frame_entries = [{"frame_id": None, "frame_url": page_url}]
        frame_url_by_id = {
            str(entry["frame_id"]): entry.get("frame_url")
            for entry in frame_entries
            if entry.get("frame_id")
        }
        try:
            document_payload = get_pierced_document(session)
            root = document_payload.get("root") or {}
            index: dict[int, dict[str, Any]] = {}
            if isinstance(root, dict):
                _build_explorer_index(
                    root,
                    index,
                    frame_url=page_url,
                    frame_url_by_id=frame_url_by_id,
                )
            dom_parts = [
                str(info.get("node_value") or "")
                for info in index.values()
                if info.get("node_type") == 3
            ]
            dom_text_summary = _bounded_redacted_text_summary(
                dom_parts,
                limit=BROWSER_RECORD_TEXT_SUMMARY_MAX_CHARS,
            )
        except Exception as exc:
            collection_errors.append(f"DOM:{type(exc).__name__}: {exc}")
            runtime_error = f"dom_collection_error: {type(exc).__name__}: {exc}"
        try:
            ax_tree = get_accessibility_tree(session)
            ax_name_by_backend, _roles, _dialogs = _build_ax_maps(ax_tree)
            ax_text_summary = _bounded_redacted_text_summary(
                list(ax_name_by_backend.values()),
                limit=BROWSER_RECORD_TEXT_SUMMARY_MAX_CHARS,
            )
        except Exception as exc:
            collection_errors.append(f"Accessibility:{type(exc).__name__}: {exc}")

        if screenshot_path is not None:
            try:
                if screenshot_fn is not None:
                    screenshot_fn(page, screenshot_path)
                else:
                    capture_viewport_screenshot_cdp(session, screenshot_path)
            except Exception as exc:
                collection_errors.append(
                    f"Page.captureScreenshot:{type(exc).__name__}: {exc}"
                )
                screenshot_path = None
    except Exception as exc:
        collection_errors.append(f"cdp_session:{type(exc).__name__}: {exc}")
        runtime_error = f"cdp_session_error: {type(exc).__name__}: {exc}"
    finally:
        _safe_detach_cdp_session(session)

    body_text = " ".join(part for part in (dom_text_summary, ax_text_summary) if part)
    # Browser-observation channel only. Never treat this as canonical auth:
    # no session-API evidence is collected here, and DOM/AX markers must not
    # drive recorder lifecycle decisions.
    browser_state, browser_reason = classify_amex(
        final_url=page_url,
        body_text=body_text,
        session_api_statuses=[],
        runtime_error=runtime_error,
    )

    try:
        inspector = _browser_inspector_summary_for_recording(page, inspect_fn=inspect_fn)
    except Exception as exc:
        collection_errors.append(f"browser_inspector:{type(exc).__name__}: {exc}")
        inspector = {
            "candidate_count": 0,
            "candidates": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }

    try:
        text_searches = _optional_text_search_summaries(
            page,
            search_terms,
            find_text_fn=find_text_fn,
        )
    except Exception as exc:
        collection_errors.append(f"text_search:{type(exc).__name__}: {exc}")
        text_searches = []

    return {
        "observed_at": iso_now(),
        "browser_observation_authentication_state": browser_state,
        "browser_observation_authentication_state_source": (
            AUTH_STATE_SOURCE_BROWSER_OBSERVATION
        ),
        "browser_observation_reason": browser_reason,
        "selected_page_url": page_url,
        "selected_page_title": page_title,
        "login_url_detected": is_login_url(page_url),
        "browser_targets": browser_targets,
        "frame_tree": frame_tree_summary,
        "browser_inspector": inspector,
        "accessibility_text_summary": ax_text_summary or None,
        "dom_text_summary": dom_text_summary or None,
        "optional_text_searches": text_searches,
        "screenshot_path": str(screenshot_path) if screenshot_path is not None else None,
        "collection_errors": collection_errors,
    }


@dataclass
class _RollingObservation:
    mono_at: float
    observation: dict[str, Any]
    screenshot_path: Path | None


class RollingExpirationObservationWindow:
    """Time-bounded in-memory observation buffer with screenshot pruning."""

    def __init__(self, rolling_window_seconds: float) -> None:
        self.rolling_window_seconds = max(0.0, float(rolling_window_seconds))
        self._entries: deque[_RollingObservation] = deque()

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def observations(self) -> list[dict[str, Any]]:
        return [entry.observation for entry in self._entries]

    def add(self, *, mono_at: float, observation: dict[str, Any]) -> None:
        screenshot_raw = observation.get("screenshot_path")
        screenshot_path = Path(screenshot_raw) if screenshot_raw else None
        self._entries.append(
            _RollingObservation(
                mono_at=mono_at,
                observation=observation,
                screenshot_path=screenshot_path,
            )
        )
        self._prune(now_mono=mono_at)

    def _prune(self, *, now_mono: float) -> None:
        if not self._entries:
            return
        cutoff = now_mono - self.rolling_window_seconds
        while len(self._entries) > 1 and self._entries[0].mono_at < cutoff:
            discarded = self._entries.popleft()
            self._delete_screenshot(discarded.screenshot_path)

    @staticmethod
    def _delete_screenshot(path: Path | None) -> None:
        if path is None:
            return
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass


def _write_expiration_recording_bundle(
    *,
    output_dir: Path,
    bundle: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    retained_paths: set[str] = set()
    for observation in bundle.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        raw = observation.get("screenshot_path")
        if not raw:
            continue
        src = Path(str(raw))
        if not src.is_file():
            observation["screenshot_path"] = None
            continue
        dest = screenshots_dir / src.name
        if src.resolve() != dest.resolve():
            try:
                dest.write_bytes(src.read_bytes())
                if src.parent != screenshots_dir:
                    try:
                        src.unlink()
                    except Exception:
                        pass
            except Exception:
                observation["screenshot_path"] = str(src)
                retained_paths.add(str(src))
                continue
        observation["screenshot_path"] = str(dest)
        retained_paths.add(str(dest))

    # Drop any screenshot files in the run directory that are no longer retained.
    try:
        for path in screenshots_dir.glob("*.png"):
            if str(path) not in retained_paths:
                try:
                    path.unlink()
                except Exception:
                    pass
    except Exception:
        pass

    recording_path = output_dir / "recording.json"
    recording_path.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    bundle["output_dir"] = str(output_dir)
    bundle["recording_json"] = str(recording_path)
    return recording_path


def record_amex_expiration_on_page(
    page: Page,
    *,
    interval_seconds: float = DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS,
    rolling_window_seconds: float = DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS,
    screenshot_every_seconds: float = DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS,
    verification_interval_seconds: float = (
        DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS
    ),
    startup_retry_seconds: float = DEFAULT_BROWSER_RECORD_STARTUP_RETRY_SECONDS,
    output_dir: Path | None = None,
    diagnostics_dir: Path | None = None,
    search_terms: list[str] | tuple[str, ...] = DEFAULT_BROWSER_RECORD_SEARCH_TERMS,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    collect_fn: Any = None,
    verify_fn: Any = None,
    inspect_fn: Any = None,
    find_text_fn: Any = None,
    screenshot_fn: Any = None,
) -> dict[str, Any]:
    """Poll browser evidence until canonical SIGNED_IN→SIGNED_OUT or timeout.

    Lifecycle decisions use fresh canonical verification
    (``verify_amex_canonical_on_page`` / same classify policy as ``verify amex``).
    Browser Inspector / DOM/AX classification is retained only as diagnostic
    evidence and never overwrites canonical state.

    Developer diagnostics only: never clicks, navigates, reloads, types, uses
    page.evaluate / frame.evaluate, or invokes keepalive actions. Independent of
    keepalive trial threads.
    """
    sleep = sleep_fn or time.sleep
    monotonic = monotonic_fn or time.monotonic
    collector = collect_fn or collect_expiration_recording_observation
    verifier = verify_fn or (lambda p: verify_amex_canonical_on_page(p))
    started_at = iso_now()
    started_mono = monotonic()
    deadline = started_mono + max(0.0, float(timeout_seconds))
    interval = max(0.0, float(interval_seconds))
    screenshot_every = max(0.0, float(screenshot_every_seconds))
    verification_every = max(0.0, float(verification_interval_seconds))
    startup_retry = max(0.0, float(startup_retry_seconds))
    startup_retry_interval = float(DEFAULT_BROWSER_RECORD_STARTUP_RETRY_INTERVAL_SECONDS)
    run_errors: list[str] = []
    terms = [term for term in search_terms if _normalize_text(term)]
    if any(_normalize_text(term) == "log out" for term in terms):
        terms = [term for term in terms if _normalize_text(term) != "log out"]
        run_errors.append("removed_forbidden_search_term:Log Out")

    out_dir = Path(output_dir) if output_dir is not None else default_browser_record_output_dir(
        diagnostics_dir
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    window = RollingExpirationObservationWindow(rolling_window_seconds)
    saw_signed_in = False
    initial_canonical_state: str | None = None
    initial_canonical_reason: str | None = None
    final_canonical_state: str | None = None
    final_canonical_reason: str | None = None
    last_definitive_canonical_state: str | None = None
    last_canonical_state: str | None = None
    last_canonical_reason: str | None = None
    logout_detected_at: str | None = None
    outcome = "fatal_error"
    screenshot_index = 0
    last_screenshot_mono: float | None = None
    last_verify_mono: float | None = None
    verification_call_count = 0
    poll_count = 0

    def build_bundle(*, ok: bool, completed_at: str) -> dict[str, Any]:
        observations = window.observations
        first_at = observations[0].get("observed_at") if observations else None
        last_at = observations[-1].get("observed_at") if observations else None
        return {
            "ok": ok,
            "outcome": outcome,
            "started_at": started_at,
            "completed_at": completed_at,
            "logout_detected_at": logout_detected_at,
            "interval_seconds": interval,
            "timeout_seconds": float(timeout_seconds),
            "rolling_window_seconds": float(rolling_window_seconds),
            "screenshot_every_seconds": screenshot_every,
            "verification_interval_seconds": verification_every,
            "observation_count": len(observations),
            "first_retained_observation_at": first_at,
            "last_observation_at": last_at,
            "initial_canonical_authentication_state": initial_canonical_state,
            "initial_canonical_reason": initial_canonical_reason,
            "final_canonical_authentication_state": final_canonical_state,
            "final_canonical_reason": final_canonical_reason,
            "last_definitive_canonical_authentication_state": (
                last_definitive_canonical_state
            ),
            # Compatibility aliases (canonical channel).
            "initial_authentication_state": initial_canonical_state,
            "final_authentication_state": final_canonical_state,
            "observations": observations,
            "run_errors": list(run_errors),
            "search_terms": list(terms),
            "verification_call_count": verification_call_count,
            "output_dir": str(out_dir),
        }

    def _collect_browser_observation(
        *,
        screenshot_path: Path | None,
    ) -> dict[str, Any]:
        try:
            observation = collector(
                page,
                screenshot_path=screenshot_path,
                search_terms=terms,
                inspect_fn=inspect_fn,
                find_text_fn=find_text_fn,
                screenshot_fn=screenshot_fn,
            )
        except TypeError:
            observation = collector(page)
        if not isinstance(observation, dict):
            raise RuntimeError("collect:invalid_observation_payload")
        return observation

    def _normalize_observation_channels(
        observation: dict[str, Any],
        *,
        canonical_state: str,
        canonical_reason: str,
        verified_this_poll: bool,
    ) -> dict[str, Any]:
        item = dict(observation)
        browser_state = item.get("browser_observation_authentication_state")
        browser_reason = item.get("browser_observation_reason")
        browser_source = item.get("browser_observation_authentication_state_source")
        # Compatibility: older test doubles may still populate canonical_* with
        # browser-observation values. Prefer explicit browser_* fields.
        if browser_state is None and item.get("canonical_authentication_state") is not None:
            browser_state = item.get("canonical_authentication_state")
            browser_reason = item.get("canonical_reason")
            browser_source = AUTH_STATE_SOURCE_BROWSER_OBSERVATION
        item["browser_observation_authentication_state"] = str(
            browser_state or "LOGIN_UNKNOWN"
        )
        item["browser_observation_authentication_state_source"] = str(
            browser_source or AUTH_STATE_SOURCE_BROWSER_OBSERVATION
        )
        item["browser_observation_reason"] = (
            None if browser_reason is None else str(browser_reason)
        )
        item["canonical_authentication_state"] = canonical_state
        item["canonical_authentication_state_source"] = (
            AUTH_STATE_SOURCE_FRESH_VERIFICATION
        )
        item["canonical_reason"] = canonical_reason
        item["canonical_verified_this_poll"] = bool(verified_this_poll)
        return item

    def _run_canonical_verify() -> VerificationResult:
        nonlocal verification_call_count, last_canonical_state, last_canonical_reason
        nonlocal last_definitive_canonical_state, final_canonical_state
        nonlocal final_canonical_reason, last_verify_mono
        verification_call_count += 1
        result = _coerce_verification_result(verifier(page))
        last_canonical_state = str(result.authentication_state or "LOGIN_UNKNOWN")
        last_canonical_reason = str(result.reason or "")
        final_canonical_state = last_canonical_state
        final_canonical_reason = last_canonical_reason
        if last_canonical_state in {"SIGNED_IN", "SIGNED_OUT"}:
            last_definitive_canonical_state = last_canonical_state
        if result.runtime_error:
            run_errors.append(
                f"verify:{result.runtime_error}"
            )
        last_verify_mono = monotonic()
        return result

    try:
        # --- Startup: require fresh canonical SIGNED_IN before recording. ---
        startup_deadline = started_mono + startup_retry
        while True:
            verification = _run_canonical_verify()
            state = str(verification.authentication_state or "LOGIN_UNKNOWN")
            reason = str(verification.reason or "")
            if initial_canonical_state is None:
                initial_canonical_state = state
                initial_canonical_reason = reason
            else:
                initial_canonical_state = state
                initial_canonical_reason = reason

            if state == "SIGNED_IN":
                saw_signed_in = True
                break
            if state == "SIGNED_OUT":
                outcome = "initial_not_signed_in"
                try:
                    browser_obs = _collect_browser_observation(screenshot_path=None)
                except Exception as exc:
                    run_errors.append(f"collect:{type(exc).__name__}: {exc}")
                    browser_obs = {
                        "observed_at": iso_now(),
                        "browser_observation_authentication_state": "LOGIN_UNKNOWN",
                        "browser_observation_authentication_state_source": (
                            AUTH_STATE_SOURCE_BROWSER_OBSERVATION
                        ),
                        "browser_observation_reason": f"collect_failed: {exc}",
                        "collection_errors": [str(exc)],
                    }
                observation = _normalize_observation_channels(
                    browser_obs,
                    canonical_state=state,
                    canonical_reason=reason,
                    verified_this_poll=True,
                )
                window.add(mono_at=monotonic(), observation=observation)
                run_errors.append(
                    f"initial_canonical_authentication_state_was_{state}; "
                    "recorder requires SIGNED_IN to start"
                )
                if reason:
                    run_errors.append(f"initial_canonical_reason: {reason}")
                bundle = build_bundle(ok=False, completed_at=iso_now())
                recording_path = _write_expiration_recording_bundle(
                    output_dir=out_dir,
                    bundle=bundle,
                )
                bundle["recording_json"] = str(recording_path)
                return bundle

            # LOGIN_UNKNOWN: bounded startup retries.
            now_mono = monotonic()
            if now_mono >= startup_deadline:
                outcome = "initial_authentication_unknown"
                try:
                    browser_obs = _collect_browser_observation(screenshot_path=None)
                except Exception as exc:
                    run_errors.append(f"collect:{type(exc).__name__}: {exc}")
                    browser_obs = {
                        "observed_at": iso_now(),
                        "browser_observation_authentication_state": "LOGIN_UNKNOWN",
                        "browser_observation_authentication_state_source": (
                            AUTH_STATE_SOURCE_BROWSER_OBSERVATION
                        ),
                        "browser_observation_reason": f"collect_failed: {exc}",
                        "collection_errors": [str(exc)],
                    }
                observation = _normalize_observation_channels(
                    browser_obs,
                    canonical_state=state,
                    canonical_reason=reason,
                    verified_this_poll=True,
                )
                window.add(mono_at=now_mono, observation=observation)
                run_errors.append(
                    "initial_canonical_authentication_state_remained_LOGIN_UNKNOWN "
                    f"after {startup_retry:g}s startup retry window"
                )
                if reason:
                    run_errors.append(f"initial_canonical_reason: {reason}")
                bundle = build_bundle(ok=False, completed_at=iso_now())
                recording_path = _write_expiration_recording_bundle(
                    output_dir=out_dir,
                    bundle=bundle,
                )
                bundle["recording_json"] = str(recording_path)
                return bundle

            remaining_startup = startup_deadline - now_mono
            sleep(min(startup_retry_interval, max(0.0, remaining_startup)))

        # --- Rolling recording loop. ---
        while True:
            poll_count += 1
            now_mono = monotonic()
            take_screenshot = False
            if screenshot_every == 0:
                take_screenshot = True
            elif last_screenshot_mono is None:
                take_screenshot = True
            elif (now_mono - last_screenshot_mono) >= screenshot_every:
                take_screenshot = True

            run_verify = False
            if last_verify_mono is None:
                run_verify = True
            elif verification_every == 0:
                run_verify = True
            elif (now_mono - last_verify_mono) >= verification_every:
                run_verify = True

            screenshot_path: Path | None = None
            if take_screenshot:
                screenshot_index += 1
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                screenshot_path = screenshots_dir / f"{screenshot_index:04d}-{stamp}.png"

            try:
                browser_obs = _collect_browser_observation(screenshot_path=screenshot_path)
            except Exception as exc:
                run_errors.append(f"collect:{type(exc).__name__}: {exc}")
                outcome = "fatal_error"
                bundle = build_bundle(ok=False, completed_at=iso_now())
                recording_path = _write_expiration_recording_bundle(
                    output_dir=out_dir,
                    bundle=bundle,
                )
                bundle["recording_json"] = str(recording_path)
                return bundle

            # Injected collectors may omit screenshot scheduling; honor path when present.
            if take_screenshot and browser_obs.get("screenshot_path"):
                last_screenshot_mono = now_mono
            elif (
                take_screenshot
                and screenshot_path is not None
                and Path(screenshot_path).is_file()
            ):
                last_screenshot_mono = now_mono
                browser_obs["screenshot_path"] = str(screenshot_path)
            elif take_screenshot and not browser_obs.get("screenshot_path"):
                browser_obs["screenshot_path"] = None

            verified_this_poll = False
            if run_verify:
                verification = _run_canonical_verify()
                verified_this_poll = True
                state = str(verification.authentication_state or "LOGIN_UNKNOWN")
                reason = str(verification.reason or "")
            else:
                state = str(last_canonical_state or "LOGIN_UNKNOWN")
                reason = str(last_canonical_reason or "")

            observation = _normalize_observation_channels(
                browser_obs,
                canonical_state=state,
                canonical_reason=reason,
                verified_this_poll=verified_this_poll,
            )
            window.add(mono_at=now_mono, observation=observation)

            # Completion decisions only on a fresh canonical verification tick.
            if verified_this_poll:
                if state == "SIGNED_IN":
                    saw_signed_in = True
                elif state == "SIGNED_OUT" and saw_signed_in:
                    logout_detected_at = str(observation.get("observed_at") or iso_now())
                    outcome = "logged_out"
                    bundle = build_bundle(ok=True, completed_at=iso_now())
                    recording_path = _write_expiration_recording_bundle(
                        output_dir=out_dir,
                        bundle=bundle,
                    )
                    bundle["recording_json"] = str(recording_path)
                    return bundle
                # LOGIN_UNKNOWN: record and continue; retain last definitive.

            now_mono = monotonic()
            if now_mono >= deadline:
                outcome = "timeout"
                bundle = build_bundle(ok=True, completed_at=iso_now())
                recording_path = _write_expiration_recording_bundle(
                    output_dir=out_dir,
                    bundle=bundle,
                )
                bundle["recording_json"] = str(recording_path)
                return bundle

            remaining = deadline - now_mono
            if interval > 0:
                sleep(min(interval, remaining))
            elif remaining > 0:
                sleep(0)
    except Exception as exc:
        run_errors.append(f"fatal:{type(exc).__name__}: {exc}")
        outcome = "fatal_error"
        bundle = build_bundle(ok=False, completed_at=iso_now())
        recording_path = _write_expiration_recording_bundle(
            output_dir=out_dir,
            bundle=bundle,
        )
        bundle["recording_json"] = str(recording_path)
        return bundle


def record_amex_expiration_in_browser_context(
    context: BrowserContext,
    *,
    provider: str = "amex",
    select_page_fn: Any = None,
    interval_seconds: float = DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS,
    timeout_seconds: float = DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS,
    rolling_window_seconds: float = DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS,
    screenshot_every_seconds: float = DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS,
    verification_interval_seconds: float = (
        DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS
    ),
    output_dir: Path | None = None,
    diagnostics_dir: Path | None = None,
    search_terms: list[str] | tuple[str, ...] = DEFAULT_BROWSER_RECORD_SEARCH_TERMS,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    collect_fn: Any = None,
    verify_fn: Any = None,
    inspect_fn: Any = None,
    find_text_fn: Any = None,
    screenshot_fn: Any = None,
) -> dict[str, Any]:
    """Run developer expiration recorder against the selected provider page."""
    if select_page_fn is None and provider == "amex":
        selected = select_amex_page(context, create_if_missing=False)
    elif select_page_fn is None:
        selected = None
    else:
        selected = select_page_fn(context, create_if_missing=False)

    out_dir = Path(output_dir) if output_dir is not None else default_browser_record_output_dir(
        diagnostics_dir
    )
    if selected is None:
        started_at = iso_now()
        bundle = {
            "ok": False,
            "outcome": "fatal_error",
            "started_at": started_at,
            "completed_at": iso_now(),
            "logout_detected_at": None,
            "interval_seconds": float(interval_seconds),
            "timeout_seconds": float(timeout_seconds),
            "rolling_window_seconds": float(rolling_window_seconds),
            "screenshot_every_seconds": float(screenshot_every_seconds),
            "verification_interval_seconds": float(verification_interval_seconds),
            "observation_count": 0,
            "first_retained_observation_at": None,
            "last_observation_at": None,
            "initial_canonical_authentication_state": None,
            "initial_canonical_reason": None,
            "final_canonical_authentication_state": None,
            "final_canonical_reason": None,
            "last_definitive_canonical_authentication_state": None,
            "initial_authentication_state": None,
            "final_authentication_state": None,
            "observations": [],
            "run_errors": ["no_provider_page_selected"],
            "search_terms": list(search_terms),
            "output_dir": str(out_dir),
        }
        recording_path = _write_expiration_recording_bundle(
            output_dir=out_dir,
            bundle=bundle,
        )
        bundle["recording_json"] = str(recording_path)
        return bundle

    return record_amex_expiration_on_page(
        selected,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        rolling_window_seconds=rolling_window_seconds,
        screenshot_every_seconds=screenshot_every_seconds,
        verification_interval_seconds=verification_interval_seconds,
        output_dir=out_dir,
        diagnostics_dir=diagnostics_dir,
        search_terms=search_terms,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        collect_fn=collect_fn,
        verify_fn=verify_fn,
        inspect_fn=inspect_fn,
        find_text_fn=find_text_fn,
        screenshot_fn=screenshot_fn,
    )


def inspect_amex_expiration_dialog(page: Page) -> dict[str, Any]:
    """Use Browser Inspector + Amex classifier to find the expiration dialog."""
    candidates, _frame_count, _errors, _frame_diagnostics = inspect_page_browser(
        page,
        mark_continue=True,
    )
    inspection = BrowserInspection(
        inspected_at=iso_now(),
        selected_page_url=sanitize_url(getattr(page, "url", None)),
        page_count=1,
        frame_count=_frame_count,
        candidate_count=len(candidates),
        candidates=candidates,
        errors=list(_errors),
    )
    classified = classify_amex_expiration_from_inspection(inspection)
    if not classified["detected"] or not classified.get("candidate"):
        return {
            "detected": False,
            "continue_token": None,
            "dialog_text": None,
            "source_type": None,
            "role_tag_class_summary": None,
            "conditions": classified.get("conditions"),
        }
    candidate: InspectionCandidate = classified["candidate"]
    summary_parts = [part for part in (candidate.tag_name, candidate.role, candidate.class_summary) if part]
    return {
        "detected": True,
        "continue_token": candidate.continue_token,
        "dialog_text": candidate.text_snippet,
        "source_type": candidate.source_type,
        "role_tag_class_summary": " ".join(summary_parts)[:200] or None,
        "conditions": classified["conditions"],
    }


def diagnose_amex_expiration_dialog_on_page(page: Page) -> dict[str, Any]:
    """Developer diagnostic wrapper over Browser Inspector + Amex classifier."""
    candidates, frame_count, errors, _frame_diagnostics = inspect_page_browser(
        page,
        mark_continue=False,
    )
    flat: list[dict[str, Any]] = []
    for candidate in candidates:
        conditions = classify_amex_expiration_candidate(candidate)
        payload = candidate.to_sanitized_dict()
        payload["button_labels"] = list(candidate.visible_button_labels)
        payload["role_tag_class_summary"] = " ".join(
            part
            for part in (candidate.tag_name, candidate.role, candidate.class_summary)
            if part
        )[:200]
        payload["detector_matched"] = bool(conditions["classified_as_expiration_dialog"])
        legacy = evaluate_expiration_dialog_conditions(
            candidate.text_snippet or "",
            has_continue_button=conditions["continue_action_match"],
            action_labels=list(candidate.visible_button_labels),
        )
        payload["conditions"] = {
            **conditions,
            "has_headline": conditions["headline_match"],
            "has_expiration_language": conditions["expiration_language_match"],
            "has_continue_button": conditions["continue_action_match"],
            "passed": list(legacy.get("passed") or []),
            "failed": list(legacy.get("failed") or []),
        }
        flat.append(payload)
    return {
        "page_url": sanitize_url(getattr(page, "url", None)),
        "frame_count": frame_count,
        "candidate_count": len(flat),
        "detector_matched": any(item.get("detector_matched") for item in flat),
        "errors": errors,
        "frames": [],
        "candidates": flat,
    }


def click_expiration_continue(page: Page, continue_token: str) -> bool:
    """Click the Continue control retained via CDP backendNodeId identity."""
    token = str(continue_token or "")
    if not token.startswith(CDP_CONTINUE_TOKEN_PREFIX):
        return False
    try:
        backend_node_id = int(token[len(CDP_CONTINUE_TOKEN_PREFIX) :])
    except ValueError:
        return False

    session = None
    try:
        session = open_page_cdp_session(page)
        _cdp_send(session, "DOM.enable")
        try:
            _cdp_send(
                session,
                "DOM.scrollIntoViewIfNeeded",
                {"backendNodeId": backend_node_id},
            )
        except Exception:
            pass
        box = _box_from_model(get_node_box_model(session, backend_node_id))
        if not box:
            return False
        cx = float(box["x"]) + float(box["width"]) / 2.0
        cy = float(box["y"]) + float(box["height"]) / 2.0
        for event_type in ("mousePressed", "mouseReleased"):
            _cdp_send(
                session,
                "Input.dispatchMouseEvent",
                {
                    "type": event_type,
                    "x": cx,
                    "y": cy,
                    "button": "left",
                    "clickCount": 1,
                },
            )
        return True
    except Exception:
        return False
    finally:
        _safe_detach_cdp_session(session)


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
    action: str | None = None
    target: str | None = None
    duration_ms: int | None = None


def sanitize_url_for_keepalive_evidence(url: str | None) -> str | None:
    """Return a URL safe for keepalive evidence (no query/fragment secrets)."""
    if url is None:
        return None
    text = str(url).strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return text.split("?", 1)[0].split("#", 1)[0][:240]


def keepalive_strategy_action_metadata(strategy: str) -> tuple[str, str | None]:
    """Return (action, sanitized target) describing the intended strategy action."""
    strategy = str(strategy or "NONE").upper()
    if strategy == "SESSION_API":
        return "session_api_fetch", sanitize_url_for_keepalive_evidence(
            AMEX_READ_USER_SESSION_URL
        )
    if strategy == "PAGE_ACTIVITY":
        return "page_activity_scroll", None
    if strategy == "OVERVIEW_RELOAD":
        return "overview_reload", sanitize_url_for_keepalive_evidence(AMEX_OVERVIEW_URL)
    if strategy == "NONE":
        return "none", None
    return "unknown", None


def sanitize_keepalive_attempt(attempt: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, sanitized keepalive attempt record (no secrets/bodies)."""
    allowed = {
        "attempted_at",
        "strategy",
        "action",
        "target",
        "success",
        "result",
        "reason",
        "duration_ms",
        "authentication_state_after_attempt",
        "error_type",
        "error_message",
        "response_status",
    }
    cleaned: dict[str, Any] = {}
    for key, value in attempt.items():
        lowered = key.lower()
        if key not in allowed:
            continue
        if any(token in lowered for token in KEEPALIVE_SENSITIVE_KEYS):
            continue
        if key == "target":
            value = sanitize_url_for_keepalive_evidence(
                None if value is None else str(value)
            )
        if isinstance(value, str) and len(value) > 240:
            value = value[:240]
        cleaned[key] = value
    return cleaned


def write_keepalive_attempts_jsonl(
    path: Path, attempts: list[dict[str, Any]] | None
) -> Path | None:
    """Persist attempt history as JSONL for trial evidence."""
    if not attempts:
        return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in attempts:
        if not isinstance(item, dict):
            continue
        lines.append(json.dumps(sanitize_keepalive_attempt(item), sort_keys=True))
    if not lines:
        return None
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def keepalive_attempt_record_from_action(
    *,
    strategy: str,
    action_result: KeepaliveActionResult,
    attempted_at: str | None = None,
    authentication_state_after_attempt: str | None = None,
) -> dict[str, Any]:
    """Build one sanitized attempt record from a KeepaliveActionResult."""
    error_text = action_result.error
    error_type = None
    error_message = None
    if error_text:
        if ":" in error_text:
            error_type, error_message = error_text.split(":", 1)
            error_type = error_type.strip()[:80]
            error_message = error_message.strip()[:240]
        else:
            error_type = "action_error"
            error_message = error_text[:240]
    action_name, default_target = keepalive_strategy_action_metadata(strategy)
    return sanitize_keepalive_attempt(
        {
            "attempted_at": attempted_at or iso_now(),
            "strategy": strategy,
            "action": action_result.action or action_name,
            "target": action_result.target or default_target,
            "success": bool(action_result.ok),
            "result": action_result.result,
            "reason": action_result.result
            if action_result.ok
            else (action_result.error or action_result.result),
            "duration_ms": action_result.duration_ms,
            "authentication_state_after_attempt": authentication_state_after_attempt,
            "error_type": error_type,
            "error_message": error_message,
            "response_status": action_result.response_status,
        }
    )


def format_keepalive_probe_terminal_summary(payload: dict[str, Any]) -> str:
    """Human-readable keepalive probe CLI summary."""
    strategy = payload.get("strategy") or "UNKNOWN"
    success = bool(payload.get("success"))
    ok = bool(payload.get("ok"))
    lines = [
        f"Keepalive probe: {strategy}",
        f"Result: {'SUCCESS' if success else 'FAILURE'}",
    ]
    reason = payload.get("reason") or payload.get("error")
    if reason:
        lines.append(f"Reason: {reason}")
    attempt = payload.get("attempt")
    if isinstance(attempt, dict):
        if attempt.get("duration_ms") is not None:
            lines.append(f"Duration: {attempt.get('duration_ms')}ms")
        if attempt.get("target"):
            lines.append(f"Target: {attempt.get('target')}")
        if attempt.get("response_status") is not None:
            lines.append(f"HTTP status: {attempt.get('response_status')}")
    evidence = payload.get("evidence_path")
    if evidence:
        lines.append(f"Evidence: {evidence}")
    if not ok and payload.get("error") and payload.get("error") != reason:
        lines.append(f"Error: {payload.get('error')}")
    return "\n".join(lines) + "\n"


def run_keepalive_preflight_for_campaign_trial(
    *,
    strategy: str,
    host: str,
    port: int,
    evidence_dir: Path,
    request_json_fn: Any = None,
) -> dict[str, Any]:
    """Probe one active strategy before a long campaign trial.

    NONE skips preflight. Active strategies perform exactly one keepalive attempt
    via the running serve probe endpoint.
    """
    strategy = str(strategy or "NONE").upper()
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if strategy == "NONE":
        payload = {
            "ok": True,
            "skipped": True,
            "success": True,
            "strategy": "NONE",
            "reason": "NONE strategy does not require a preflight probe",
            "preflight_ok": True,
            "result_classification": "BASELINE",
        }
        _write_json_file(evidence_dir / "preflight-result.json", payload)
        return payload

    http = request_json_fn or request_json
    base_url = _expiration_experiment_base_url(host, port)
    try:
        payload = http(
            "POST",
            f"{base_url}/providers/amex/keepalive/probe",
            {"strategy": strategy},
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001 - preflight must not crash campaign
        payload = {
            "ok": False,
            "skipped": False,
            "success": False,
            "strategy": strategy,
            "reason": f"{type(exc).__name__}: {exc}",
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not isinstance(payload, dict):
        payload = {
            "ok": False,
            "success": False,
            "strategy": strategy,
            "reason": "invalid_probe_payload",
            "error": "invalid_probe_payload",
        }

    attempt = payload.get("attempt")
    attempts: list[dict[str, Any]] = []
    if isinstance(attempt, dict):
        attempts.append(sanitize_keepalive_attempt(attempt))
        write_keepalive_attempts_jsonl(
            evidence_dir / KEEPALIVE_ATTEMPTS_FILENAME,
            attempts,
        )

    success = bool(payload.get("success"))
    preflight = {
        "ok": bool(payload.get("ok")) and success,
        "skipped": False,
        "success": success,
        "strategy": strategy,
        "reason": payload.get("reason") or payload.get("error"),
        "error": None if success else (payload.get("error") or payload.get("reason")),
        "attempt": attempt if isinstance(attempt, dict) else None,
        "evidence_path": payload.get("evidence_path"),
        "preflight_ok": success,
        "result_classification": None if success else "OPERATIONALLY_FAILED",
        "authentication_state": payload.get("authentication_state"),
    }
    _write_json_file(evidence_dir / "preflight-result.json", preflight)
    return preflight


def inspect_amex_page_signals(
    page: Page,
    *,
    latest_canonical_state: str | None = None,
    fresh_verification_state: str | None = None,
) -> dict[str, Any]:
    """Observation helpers for keepalive/maintenance.

    Browser Inspector classifies the expiration dialog. Authentication remains
    canonical: prefer a fresh verification result, else the latest committed
    canonical state. DOM inspection alone never fabricates LOGIN_UNKNOWN.
    """
    dialog = inspect_amex_expiration_dialog(page)
    final_url = sanitize_url(page.url)
    login_url_detected = is_login_url(final_url)
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=3_000)
    except Exception:
        body_text = ""
    login_hits = count_markers(body_text, LOGIN_MARKERS)
    login_page_detected = bool(login_url_detected or login_hits >= 2)

    if fresh_verification_state:
        auth_state = fresh_verification_state
        auth_source = AUTH_STATE_SOURCE_FRESH_VERIFICATION
    elif latest_canonical_state:
        auth_state = latest_canonical_state
        auth_source = AUTH_STATE_SOURCE_LATEST_CANONICAL
    else:
        auth_state = None
        auth_source = AUTH_STATE_SOURCE_NONE

    # Strong login-page evidence can mark logout without inventing LOGIN_UNKNOWN.
    if login_page_detected:
        auth_state = "SIGNED_OUT"

    return {
        "authentication_state": auth_state,
        "inspection_authentication_state_source": auth_source,
        "expiration_dialog_detected": bool(dialog.get("detected")),
        "login_page_detected": login_page_detected,
        "final_url": final_url,
    }


def perform_keepalive_action(page: Page, strategy: str) -> KeepaliveActionResult:
    """Dispatch one strategy action on the existing Amex page.

    Avoids ``page.evaluate`` / ``frame.evaluate``. Amex monkey-patches eval, so
    in-page fetch/scroll helpers fail with ``Error: eval is disabled``.

    SESSION_API uses the browser context request API (same credentialed
    ReadUserSession.v1 path as canonical verification). PAGE_ACTIVITY uses
    Playwright input APIs for a tiny scroll nudge without navigation.
    """
    action_name, default_target = keepalive_strategy_action_metadata(strategy)
    started = time.monotonic()

    def _with_timing(**kwargs: Any) -> KeepaliveActionResult:
        duration_ms = int(max(0.0, (time.monotonic() - started) * 1000.0))
        return KeepaliveActionResult(
            action=action_name,
            target=kwargs.pop("target", default_target),
            duration_ms=duration_ms,
            **kwargs,
        )

    if strategy == "NONE":
        return _with_timing(ok=True, result="skipped")
    if strategy == "SESSION_API":
        try:
            context = getattr(page, "context", None)
            request_api = getattr(context, "request", None) if context is not None else None
            if request_api is None or not hasattr(request_api, "get"):
                return _with_timing(
                    ok=False,
                    result="failure",
                    error="RuntimeError: browser context request API unavailable",
                )
            response = request_api.get(
                AMEX_READ_USER_SESSION_URL,
                headers={"Accept": "application/json"},
                max_redirects=0,
                timeout=15_000,
            )
            status_int = int(getattr(response, "status", 0) or 0)
            ok = status_int == 200
            return _with_timing(
                ok=ok,
                result="success" if ok else "failure",
                response_status=status_int,
                error=None if ok else f"session_api_http_{status_int}",
            )
        except Exception as exc:
            return _with_timing(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
            )
    if strategy == "PAGE_ACTIVITY":
        page_url = sanitize_url_for_keepalive_evidence(getattr(page, "url", None))
        try:
            # Prefer bringing the Amex tab forward when Playwright exposes it.
            bring_to_front = getattr(page, "bring_to_front", None)
            if callable(bring_to_front):
                try:
                    bring_to_front()
                except Exception:
                    pass
            mouse = getattr(page, "mouse", None)
            if mouse is None or not hasattr(mouse, "wheel"):
                return _with_timing(
                    ok=False,
                    result="failure",
                    error="RuntimeError: page.mouse.wheel unavailable",
                    target=page_url,
                )
            # Harmless activity: tiny scroll down then restore. No clicks, forms,
            # navigation, or credential interaction.
            mouse.wheel(0, 24)
            mouse.wheel(0, -24)
            return _with_timing(ok=True, result="success", target=page_url)
        except Exception as exc:
            return _with_timing(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
                target=page_url,
            )
    if strategy == "OVERVIEW_RELOAD":
        try:
            page.goto(AMEX_OVERVIEW_URL, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(1_000)
            return _with_timing(
                ok=True,
                result="success",
                target=sanitize_url_for_keepalive_evidence(
                    getattr(page, "url", None) or AMEX_OVERVIEW_URL
                ),
            )
        except Exception as exc:
            return _with_timing(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
            )
    return _with_timing(
        ok=False,
        result="failure",
        error=f"unknown_strategy:{strategy}",
    )


def sanitize_keepalive_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, sanitized keepalive event (no secrets or bodies)."""
    allowed = {
        "timestamp",
        "event_type",
        "strategy",
        "action_result",
        "response_status",
        "authentication_state",
        "inspection_authentication_state_source",
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


def verify_amex_canonical_on_page(
    page: Page,
    *,
    result_path: Path | None = None,
    request_fn: Any = None,
) -> VerificationResult:
    """Fresh canonical Amex verification without navigation or page mutation.

    Uses the same classify_amex policy as ``verify_amex_over_cdp``, but obtains
    session-API evidence via a credentialed ``ReadUserSession.v1`` request
    through the browser context APIRequestContext (no ``page.goto``, no
    ``page.evaluate``, no clicks/reloads). DOM markers are intentionally not
    used to infer ``SIGNED_IN`` on this path.

    Important: ``ReadUserSession.v1`` is also the ``SESSION_API`` keepalive
    action and may count as session activity / refresh idle timeout. The
    expiration recorder therefore throttles this call via
    ``verification_interval_seconds`` (default 5s) while continuing 1s browser
    evidence polls.
    """
    runtime_error: str | None = None
    session_api_statuses: list[int] = []
    final_url = sanitize_url(getattr(page, "url", None))
    title: str | None = None
    # Empty on purpose: do not infer SIGNED_IN from DOM/AX markers here.
    body_text = ""

    try:
        title = page.title()
    except Exception:
        pass

    try:
        if request_fn is not None:
            status = request_fn(page)
            if status is not None:
                session_api_statuses.append(int(status))
        else:
            response = page.context.request.get(
                AMEX_READ_USER_SESSION_URL,
                headers={"Accept": "application/json"},
                max_redirects=0,
                timeout=15_000,
            )
            session_api_statuses.append(int(response.status))
    except Exception as exc:
        runtime_error = f"session_api_error: {type(exc).__name__}: {exc}"

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
        login_marker_count=0,
        authenticated_marker_count=0,
        session_api_200_count=sum(1 for status in session_api_statuses if status == 200),
        session_api_denied_count=sum(
            1 for status in session_api_statuses if status in {401, 403}
        ),
        runtime_error=runtime_error,
    )
    if result_path is not None:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(asdict(result), indent=2) + "\n",
            encoding="utf-8",
        )
    return result


def _coerce_verification_result(raw: Any) -> VerificationResult:
    """Normalize verify_fn return values used by the expiration recorder."""
    if isinstance(raw, VerificationResult):
        return raw
    if isinstance(raw, dict):
        state = str(raw.get("authentication_state") or "LOGIN_UNKNOWN")
        return VerificationResult(
            provider=str(raw.get("provider") or "amex"),
            authentication_state=state,
            reason=str(raw.get("reason") or ""),
            observed_at=str(raw.get("observed_at") or iso_now()),
            final_url=raw.get("final_url"),
            page_title=raw.get("page_title"),
            login_url_detected=bool(raw.get("login_url_detected")),
            login_marker_count=int(raw.get("login_marker_count") or 0),
            authenticated_marker_count=int(raw.get("authenticated_marker_count") or 0),
            session_api_200_count=int(raw.get("session_api_200_count") or 0),
            session_api_denied_count=int(raw.get("session_api_denied_count") or 0),
            runtime_error=raw.get("runtime_error"),
        )
    state = str(raw or "LOGIN_UNKNOWN")
    return VerificationResult(
        provider="amex",
        authentication_state=state,
        reason=f"test_double:{state}",
        observed_at=iso_now(),
        final_url=None,
        page_title=None,
        login_url_detected=state == "SIGNED_OUT",
        login_marker_count=0,
        authenticated_marker_count=0,
        session_api_200_count=1 if state == "SIGNED_IN" else 0,
        session_api_denied_count=1 if state == "SIGNED_OUT" else 0,
        runtime_error=None,
    )


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
        browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
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
        self.keepalive_latest_authentication_state: str | None = None
        self.keepalive_latest_authentication_state_source: str | None = None
        self.keepalive_latest_reason: str | None = None
        self.keepalive_latest_observed_at: str | None = None
        self.keepalive_final_authentication_state: str | None = None
        self.keepalive_final_reason: str | None = None
        self.keepalive_events: list[dict[str, Any]] = []
        self.keepalive_attempts: list[dict[str, Any]] = []
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None
        self._keepalive_deadline_mono: float | None = None
        self._shutting_down = False

        self.diagnostics_dir = self.root / "diagnostics"
        self._latest_browser_inspection: dict[str, Any] | None = None

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

    def _keepalive_compatibility_authentication_state(self) -> str | None:
        """Generic ``authentication_state``: latest while running, final after.

        While a trial is running, callers must not treat this as a finalized
        result — prefer ``keepalive_latest_*`` / ``keepalive_final_*``.
        """
        if self.keepalive_trial_running:
            return self.keepalive_latest_authentication_state
        if self.keepalive_completed_at is not None:
            return self.keepalive_final_authentication_state
        return self.keepalive_latest_authentication_state

    def _set_keepalive_latest_observation(
        self,
        *,
        authentication_state: Any,
        authentication_state_source: Any,
        reason: str,
        observed_at: str | None = None,
    ) -> None:
        self.keepalive_latest_authentication_state = (
            None if authentication_state is None else str(authentication_state)
        )
        self.keepalive_latest_authentication_state_source = (
            None
            if authentication_state_source is None
            else str(authentication_state_source)
        )
        self.keepalive_latest_reason = str(reason)
        self.keepalive_latest_observed_at = observed_at or iso_now()

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
            "keepalive_latest_authentication_state": (
                self.keepalive_latest_authentication_state
            ),
            "keepalive_latest_authentication_state_source": (
                self.keepalive_latest_authentication_state_source
            ),
            "keepalive_latest_reason": self.keepalive_latest_reason,
            "keepalive_latest_observed_at": self.keepalive_latest_observed_at,
            "keepalive_final_authentication_state": self.keepalive_final_authentication_state,
            "keepalive_final_reason": self.keepalive_final_reason,
            # Compatibility: latest while running, final after completion.
            "authentication_state": self._keepalive_compatibility_authentication_state(),
            "keepalive_kept_signed_in": kept_signed_in if self.keepalive_completed_at else None,
            "keepalive_events": list(self.keepalive_events),
            # Attempt history for evidence packaging; stripped from keepalive-status.json.
            "keepalive_attempts": list(self.keepalive_attempts),
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
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
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
                    # Observation-only: Browser Inspector + classifier, never click.
                    inspection_candidates, _, _, _ = inspect_page_browser(
                        page,
                        mark_continue=False,
                    )
                    inspection = BrowserInspection(
                        inspected_at=iso_now(),
                        selected_page_url=sanitize_url(getattr(page, "url", None)),
                        page_count=1,
                        frame_count=0,
                        candidate_count=len(inspection_candidates),
                        candidates=inspection_candidates,
                    )
                    classified = classify_amex_expiration_from_inspection(inspection)
                    detected = bool(classified.get("detected"))
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

    def inspect_browser(
        self,
        provider: str = "amex",
        *,
        capture_screenshot: bool = False,
    ) -> dict[str, Any]:
        """Developer-only Browser Inspector over the live provider session."""
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            with sync_playwright() as playwright:
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                inspection = inspect_browser_context(
                    context,
                    provider=provider,
                    capture_screenshot=bool(capture_screenshot),
                    mark_continue=False,
                    diagnostics_dir=self.diagnostics_dir,
                )
                classified = classify_amex_expiration_from_inspection(inspection)
                payload = inspection.to_sanitized_dict()
                payload["ok"] = True
                payload["amex_expiration"] = {
                    "detected": bool(classified.get("detected")),
                    "conditions": classified.get("conditions"),
                    "source_type": classified.get("source_type"),
                }
                # Never persist screenshot bytes — path only, and only when enabled.
                self._latest_browser_inspection = {
                    key: value
                    for key, value in payload.items()
                    if key != "ok"
                }
                return payload

    def latest_browser_inspection(self, provider: str = "amex") -> dict[str, Any]:
        """Return the most recent sanitized inspection metadata (no screenshot bytes)."""
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self._latest_browser_inspection:
                return {
                    "ok": False,
                    "error": "no_browser_inspection",
                    "reason": "No browser inspection has been captured yet",
                }
            payload = dict(self._latest_browser_inspection)
            payload["ok"] = True
            return payload

    def inspect_browser_debug(self, provider: str = "amex") -> dict[str, Any]:
        """Temporary developer probe over the live provider session frames."""
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            with sync_playwright() as playwright:
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                return debug_inspect_browser_context(context, provider=provider)

    def find_browser_text(self, provider: str, query: str) -> dict[str, Any]:
        """Developer-only DOM text explorer over the live provider session."""
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            with sync_playwright() as playwright:
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                return find_text_in_browser_context(
                    context,
                    query,
                    provider=provider,
                )

    def watch_browser_text(
        self,
        provider: str,
        terms: list[str],
        *,
        interval_seconds: float = DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS,
        stop_after_first_match: bool = True,
        output_file: Path | None = None,
    ) -> dict[str, Any]:
        """Developer-only timeout text watcher over the live provider session.

        Runs independently of the keepalive trial thread: does not start a trial,
        does not hold the runtime lock across polls, and never clicks controls.
        """
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            if self.last_result is not None:
                auth_state = self.last_result.authentication_state
                auth_source = AUTH_STATE_SOURCE_LATEST_CANONICAL
            else:
                auth_state = None
                auth_source = AUTH_STATE_SOURCE_NONE
            diagnostics_dir = self.diagnostics_dir

        # CDP attach + poll loop outside the runtime lock so keepalive/maintenance
        # can continue while this developer watcher runs in another terminal.
        with sync_playwright() as playwright:
            browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
            if not browser.contexts:
                raise RuntimeError("Chrome exposed no persistent browser context")
            context = browser.contexts[0]
            return watch_text_in_browser_context(
                context,
                terms,
                provider=provider,
                interval_seconds=interval_seconds,
                timeout_seconds=timeout_seconds,
                stop_after_first_match=stop_after_first_match,
                output_file=output_file,
                diagnostics_dir=diagnostics_dir,
                canonical_authentication_state=auth_state,
                canonical_authentication_state_source=auth_source,
            )

    def record_browser_expiration(
        self,
        provider: str,
        *,
        interval_seconds: float = DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS,
        rolling_window_seconds: float = DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS,
        screenshot_every_seconds: float = DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS,
        verification_interval_seconds: float = (
            DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS
        ),
        output_dir: Path | None = None,
    ) -> dict[str, Any]:
        """Developer-only rolling recorder for Amex SIGNED_IN→SIGNED_OUT evidence.

        Lifecycle uses fresh canonical verification (session API / login URL).
        Browser observation is diagnostic only. Runs independently of the
        keepalive trial thread: does not start a trial, does not hold the
        runtime lock across polls, and never mutates the page.
        """
        if provider != "amex":
            raise ValueError(f"Unsupported provider: {provider}")
        with self.lock:
            if not self.cdp_url:
                raise RuntimeError("Provider runtime is not started")
            cdp_url = self.cdp_url
            diagnostics_dir = self.diagnostics_dir

        # CDP attach + poll loop outside the runtime lock so keepalive/maintenance
        # can continue while this developer recorder runs in another terminal.
        with sync_playwright() as playwright:
            browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
            if not browser.contexts:
                raise RuntimeError("Chrome exposed no persistent browser context")
            context = browser.contexts[0]
            return record_amex_expiration_in_browser_context(
                context,
                provider=provider,
                interval_seconds=interval_seconds,
                timeout_seconds=timeout_seconds,
                rolling_window_seconds=rolling_window_seconds,
                screenshot_every_seconds=screenshot_every_seconds,
                verification_interval_seconds=verification_interval_seconds,
                output_dir=output_dir,
                diagnostics_dir=diagnostics_dir,
            )

    def diagnose_expiration_dialog(self, provider: str = "amex") -> dict[str, Any]:
        """Backward-compatible diagnostic wrapper over Browser Inspector."""
        payload = self.inspect_browser(provider, capture_screenshot=False)
        candidates = []
        for raw in payload.get("candidates") or []:
            conditions = classify_amex_expiration_candidate(raw)
            item = dict(raw)
            item["button_labels"] = list(raw.get("visible_button_labels") or [])
            item["role_tag_class_summary"] = " ".join(
                part
                for part in (
                    raw.get("tag_name"),
                    raw.get("role"),
                    raw.get("class_summary"),
                )
                if part
            )[:200]
            item["detector_matched"] = bool(
                conditions["classified_as_expiration_dialog"]
            )
            item["conditions"] = {
                **conditions,
                "has_headline": conditions["headline_match"],
                "has_expiration_language": conditions["expiration_language_match"],
                "has_continue_button": conditions["continue_action_match"],
                "passed": [
                    key
                    for key, value in conditions.items()
                    if key != "classified_as_expiration_dialog" and value
                ],
                "failed": [
                    key
                    for key, value in conditions.items()
                    if key != "classified_as_expiration_dialog" and not value
                ],
            }
            candidates.append(item)
        return {
            "ok": True,
            "selected_page_url": payload.get("selected_page_url"),
            "page_count": payload.get("page_count"),
            "frame_count": payload.get("frame_count"),
            "candidate_count": len(candidates),
            "detector_matched": any(item.get("detector_matched") for item in candidates),
            "pages": [
                {
                    "url": payload.get("selected_page_url"),
                    "selected": True,
                    "frame_count": payload.get("frame_count"),
                    "candidate_count": len(candidates),
                    "frames": [],
                }
            ],
            "candidates": candidates[:80],
            "errors": payload.get("errors") or [],
        }

    def _append_keepalive_event(self, event: dict[str, Any]) -> None:
        cleaned = sanitize_keepalive_event(event)
        self.keepalive_events.append(cleaned)
        if len(self.keepalive_events) > KEEPALIVE_MAX_EVENTS:
            self.keepalive_events = self.keepalive_events[-KEEPALIVE_MAX_EVENTS:]

    def _append_keepalive_attempt(self, attempt: dict[str, Any]) -> None:
        """Record one sanitized strategy attempt for trial evidence."""
        cleaned = sanitize_keepalive_attempt(attempt)
        self.keepalive_attempts.append(cleaned)
        if len(self.keepalive_attempts) > KEEPALIVE_MAX_ATTEMPTS:
            self.keepalive_attempts = self.keepalive_attempts[-KEEPALIVE_MAX_ATTEMPTS:]

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

    def probe_keepalive_strategy(
        self,
        provider: str,
        *,
        strategy: str,
    ) -> dict[str, Any]:
        """Perform exactly one keepalive strategy attempt for developer preflight.

        Requires an existing signed-in managed Amex session. Does not start a
        trial, wait for expiration, mutate account data, or touch ordinary Chrome.
        """
        if provider != "amex":
            raise ValueError("Only amex is supported in this implementation")
        strategy = str(strategy or "").upper()
        if strategy not in KEEPALIVE_STRATEGIES:
            raise ValueError(
                f"Unsupported keepalive strategy {strategy!r}. "
                f"Expected one of {', '.join(KEEPALIVE_STRATEGIES)}"
            )

        with self.lock:
            if self.keepalive_trial_running:
                return {
                    "ok": False,
                    "success": False,
                    "strategy": strategy,
                    "error": "keepalive_trial_already_running",
                    "reason": "A keepalive trial is already running",
                }
            if not self.cdp_url:
                return {
                    "ok": False,
                    "success": False,
                    "strategy": strategy,
                    "error": "runtime_not_started",
                    "reason": "Provider runtime is not started",
                }
            cdp_url = self.cdp_url

        if strategy == "NONE":
            attempt = keepalive_attempt_record_from_action(
                strategy="NONE",
                action_result=KeepaliveActionResult(
                    ok=True,
                    result="skipped",
                    action="none",
                    duration_ms=0,
                ),
            )
            return {
                "ok": True,
                "success": True,
                "strategy": "NONE",
                "reason": "NONE strategy performs no keepalive action",
                "attempt": attempt,
                "authentication_state": None,
                "evidence_path": None,
            }

        action_result: KeepaliveActionResult
        auth_after: str | None = None
        page_url: str | None = None
        try:
            with sync_playwright() as playwright:
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                page = select_amex_page(context, create_if_missing=False)
                if page is None:
                    raise RuntimeError(
                        "No existing americanexpress.com page for keepalive probe"
                    )
                page_url = sanitize_url_for_keepalive_evidence(getattr(page, "url", None))
                # Light signed-in check (no overview navigation / 5s wait).
                verification = verify_amex_canonical_on_page(
                    page,
                    result_path=self.result_path,
                )
                if verification.authentication_state != "SIGNED_IN":
                    return {
                        "ok": False,
                        "success": False,
                        "strategy": strategy,
                        "error": "not_signed_in",
                        "reason": (
                            "Keepalive probe requires SIGNED_IN; "
                            f"observed {verification.authentication_state}"
                        ),
                        "authentication_state": verification.authentication_state,
                        "selected_page_url": page_url,
                    }
                action_result = perform_keepalive_action(page, strategy)
                try:
                    after = inspect_amex_page_signals(
                        page,
                        latest_canonical_state=verification.authentication_state,
                    )
                    auth_after = after.get("authentication_state")
                except Exception:
                    auth_after = verification.authentication_state
        except Exception as exc:
            action_result = KeepaliveActionResult(
                ok=False,
                result="failure",
                error=f"{type(exc).__name__}: {exc}",
                action=keepalive_strategy_action_metadata(strategy)[0],
                target=keepalive_strategy_action_metadata(strategy)[1] or page_url,
            )

        attempt = keepalive_attempt_record_from_action(
            strategy=strategy,
            action_result=action_result,
            authentication_state_after_attempt=auth_after,
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        evidence_path = (
            self.diagnostics_dir / f"amex-keepalive-probe-{strategy.lower()}-{stamp}.json"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = {
            "ok": bool(action_result.ok),
            "success": bool(action_result.ok),
            "strategy": strategy,
            "reason": (
                action_result.result
                if action_result.ok
                else (action_result.error or action_result.result)
            ),
            "attempt": attempt,
            "authentication_state": auth_after,
            "selected_page_url": page_url,
            "observed_at": iso_now(),
        }
        evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        write_keepalive_attempts_jsonl(
            evidence_path.with_suffix(".jsonl"),
            [attempt],
        )
        return {
            "ok": True,
            "success": bool(action_result.ok),
            "strategy": strategy,
            "reason": evidence["reason"],
            "error": None if action_result.ok else evidence["reason"],
            "attempt": attempt,
            "authentication_state": auth_after,
            "evidence_path": str(evidence_path),
            "selected_page_url": page_url,
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
            self.keepalive_latest_authentication_state = None
            self.keepalive_latest_authentication_state_source = None
            self.keepalive_latest_reason = None
            self.keepalive_latest_observed_at = None
            self.keepalive_final_authentication_state = None
            self.keepalive_final_reason = None
            self.keepalive_events = []
            self.keepalive_attempts = []
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
            self._set_keepalive_latest_observation(
                authentication_state=verification.authentication_state,
                authentication_state_source=AUTH_STATE_SOURCE_FRESH_VERIFICATION,
                reason="trial_started",
                observed_at=self.keepalive_started_at,
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

                interval = float(
                    self.keepalive_interval_seconds or KEEPALIVE_DEFAULT_INTERVAL_SECONDS
                )
                remaining = max(
                    0.0, (self._keepalive_deadline_mono or 0.0) - time.monotonic()
                )
                self._keepalive_stop.wait(
                    min(interval, remaining if remaining > 0 else interval)
                )

            if self._shutting_down:
                final_reason = "runtime_shutdown"
            elif self._keepalive_stop.is_set() and final_reason == "duration_completed":
                # Stop requested before natural completion.
                if (
                    self._keepalive_deadline_mono
                    and time.monotonic() < self._keepalive_deadline_mono
                ):
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
                browser: Browser = connect_chromium_over_cdp(playwright, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Chrome exposed no persistent browser context")
                context = browser.contexts[0]
                page = select_amex_page(context, create_if_missing=False)
                if page is None:
                    raise RuntimeError("No existing americanexpress.com page for keepalive")

                latest_canonical = (
                    self.last_result.authentication_state if self.last_result else None
                )
                before = inspect_amex_page_signals(
                    page,
                    latest_canonical_state=latest_canonical,
                )
                before_auth_source = before.get(
                    "inspection_authentication_state_source",
                    AUTH_STATE_SOURCE_NONE,
                )
                before_observed_at = iso_now()
                self._set_keepalive_latest_observation(
                    authentication_state=before.get("authentication_state"),
                    authentication_state_source=before_auth_source,
                    reason=(
                        "logged_out"
                        if (
                            before["login_page_detected"]
                            or before["authentication_state"] == "SIGNED_OUT"
                        )
                        else "inspection"
                    ),
                    observed_at=before_observed_at,
                )
                if before["expiration_dialog_detected"]:
                    self._note_keepalive_expiration_dialog(source="pre_action")
                if before["login_page_detected"] or before["authentication_state"] == "SIGNED_OUT":
                    self.keepalive_logged_out = True
                    self._append_keepalive_event(
                        {
                            "timestamp": before_observed_at,
                            "event_type": "logged_out",
                            "strategy": strategy,
                            "action_result": None,
                            "response_status": None,
                            "authentication_state": before["authentication_state"],
                            "inspection_authentication_state_source": before_auth_source,
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
                            "inspection_authentication_state_source": before_auth_source,
                            "expiration_dialog_detected": before["expiration_dialog_detected"],
                            "login_page_detected": before["login_page_detected"],
                        }
                    )

                after = inspect_amex_page_signals(
                    page,
                    latest_canonical_state=latest_canonical,
                )
                after_auth_source = after.get(
                    "inspection_authentication_state_source",
                    AUTH_STATE_SOURCE_NONE,
                )
                after_observed_at = iso_now()
                self._set_keepalive_latest_observation(
                    authentication_state=after.get("authentication_state"),
                    authentication_state_source=after_auth_source,
                    reason=(
                        "logged_out"
                        if (
                            after["login_page_detected"]
                            or after["authentication_state"] == "SIGNED_OUT"
                        )
                        else "inspection"
                    ),
                    observed_at=after_observed_at,
                )
                if action_result is not None:
                    error_text = action_result.error
                    error_type = None
                    error_message = None
                    if error_text:
                        if ":" in error_text:
                            error_type, error_message = error_text.split(":", 1)
                            error_type = error_type.strip()[:80]
                            error_message = error_message.strip()[:240]
                        else:
                            error_type = "action_error"
                            error_message = error_text[:240]
                    self._append_keepalive_attempt(
                        {
                            "attempted_at": self.keepalive_last_action_at,
                            "strategy": strategy,
                            "action": action_result.action
                            or keepalive_strategy_action_metadata(strategy)[0],
                            "target": action_result.target
                            or keepalive_strategy_action_metadata(strategy)[1],
                            "success": bool(action_result.ok),
                            "result": action_result.result,
                            "reason": action_result.result,
                            "duration_ms": action_result.duration_ms,
                            "authentication_state_after_attempt": after.get(
                                "authentication_state"
                            ),
                            "error_type": error_type,
                            "error_message": error_message,
                            "response_status": action_result.response_status,
                        }
                    )
                if after["expiration_dialog_detected"]:
                    self._note_keepalive_expiration_dialog(source="post_action")
                self._append_keepalive_event(
                    {
                        "timestamp": after_observed_at,
                        "event_type": "inspection",
                        "strategy": strategy,
                        "action_result": action_result.result if action_result else "skipped",
                        "response_status": action_result.response_status if action_result else None,
                        "authentication_state": after["authentication_state"],
                        "inspection_authentication_state_source": after_auth_source,
                        "expiration_dialog_detected": after["expiration_dialog_detected"],
                        "login_page_detected": after["login_page_detected"],
                    }
                )
                if after["login_page_detected"] or after["authentication_state"] == "SIGNED_OUT":
                    self.keepalive_logged_out = True
                    self._append_keepalive_event(
                        {
                            "timestamp": after_observed_at,
                            "event_type": "logged_out",
                            "strategy": strategy,
                            "action_result": action_result.result if action_result else None,
                            "response_status": action_result.response_status if action_result else None,
                            "authentication_state": after["authentication_state"],
                            "inspection_authentication_state_source": after_auth_source,
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
        if self.path == "/providers/amex/diagnostics/browser-inspection/latest":
            try:
                payload = self.server.runtime.latest_browser_inspection("amex")
                status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.NOT_FOUND
                self._send_json(status, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                )
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
        if self.path == "/providers/amex/keepalive/probe":
            try:
                body = self._read_json_body()
                strategy = str(body.get("strategy") or "")
                payload = self.server.runtime.probe_keepalive_strategy(
                    "amex",
                    strategy=strategy,
                )
                # Always 200 with structured ok/success so the CLI can summarize
                # action failures without treating them as transport errors.
                self._send_json(HTTPStatus.OK, payload)
            except ValueError as exc:
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "success": False, "error": str(exc)},
                )
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "success": False,
                        "error": str(exc),
                    },
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
        if self.path == "/providers/amex/diagnostics/browser-inspection":
            try:
                body = self._read_json_body()
                capture_screenshot = bool(body.get("capture_screenshot", False))
                payload = self.server.runtime.inspect_browser(
                    "amex",
                    capture_screenshot=capture_screenshot,
                )
                self._send_json(HTTPStatus.OK, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                )
            return
        if self.path == "/providers/amex/diagnostics/browser-inspection-debug":
            try:
                payload = self.server.runtime.inspect_browser_debug("amex")
                self._send_json(HTTPStatus.OK, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(exc),
                        "exception_class": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            return
        if self.path == "/providers/amex/diagnostics/browser-find-text":
            try:
                body = self._read_json_body()
                query = str(body.get("query") or "")
                payload = self.server.runtime.find_browser_text("amex", query)
                status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
                self._send_json(status, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(exc),
                        "exception_class": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            return
        if self.path == "/providers/amex/diagnostics/browser-watch-text":
            try:
                body = self._read_json_body()
                terms_raw = body.get("terms")
                if isinstance(terms_raw, list):
                    terms = [str(item).strip() for item in terms_raw if str(item).strip()]
                else:
                    terms = parse_browser_watch_terms(
                        None if terms_raw is None else str(terms_raw)
                    )
                interval_seconds = float(
                    body.get(
                        "interval_seconds",
                        DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS,
                    )
                )
                timeout_seconds = float(
                    body.get(
                        "timeout_seconds",
                        DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS,
                    )
                )
                stop_after_first_match = bool(
                    body.get("stop_after_first_match", True)
                )
                output_raw = body.get("output_file")
                output_file = Path(str(output_raw)).expanduser() if output_raw else None
                payload = self.server.runtime.watch_browser_text(
                    "amex",
                    terms,
                    interval_seconds=interval_seconds,
                    timeout_seconds=timeout_seconds,
                    stop_after_first_match=stop_after_first_match,
                    output_file=output_file,
                )
                status = HTTPStatus.OK if payload.get("ok") else HTTPStatus.BAD_REQUEST
                self._send_json(status, payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(exc),
                        "exception_class": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            return
        if self.path == "/providers/amex/diagnostics/browser-record-expiration":
            try:
                body = self._read_json_body()
                parsed = parse_browser_record_expiration_request(body)
                payload = self.server.runtime.record_browser_expiration(
                    parsed["provider"],
                    interval_seconds=parsed["interval_seconds"],
                    timeout_seconds=parsed["timeout_seconds"],
                    rolling_window_seconds=parsed["rolling_window_seconds"],
                    screenshot_every_seconds=parsed["screenshot_every_seconds"],
                    verification_interval_seconds=parsed[
                        "verification_interval_seconds"
                    ],
                    output_dir=parsed["output_dir"],
                )
                status = browser_record_expiration_http_status(payload)
                self._send_json(status, payload)
            except ValueError as exc:
                error_payload = {
                    "ok": False,
                    "error": str(exc),
                    "error_type": "validation_error",
                }
                log_rejected_diagnostic_request(
                    route=self.path,
                    status=int(HTTPStatus.BAD_REQUEST),
                    error=str(exc),
                )
                self._send_json(HTTPStatus.BAD_REQUEST, error_payload)
            except Exception as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error": str(exc),
                        "exception_class": type(exc).__name__,
                        "traceback": traceback.format_exc(),
                    },
                )
            return
        if self.path == "/providers/amex/diagnostics/inspect-expiration-dialog":
            try:
                payload = self.server.runtime.diagnose_expiration_dialog("amex")
                self._send_json(HTTPStatus.OK, payload)
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


def _format_http_error_body(raw: bytes) -> str:
    """Decode an HTTP error body for CLI display (bounded; no headers/secrets)."""
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    if len(text) > REQUEST_JSON_ERROR_BODY_MAX_CHARS:
        text = text[:REQUEST_JSON_ERROR_BODY_MAX_CHARS] + "\n...[truncated]"
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, indent=2)
    return text


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, method=method, headers=headers)
    path = urlsplit(url).path or url
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = b""
        try:
            raw = exc.read() or b""
        except Exception:
            raw = b""
        body_text = _format_http_error_body(raw)
        print(f"HTTP {exc.code} from {path}", file=sys.stderr)
        if body_text:
            print(body_text, file=sys.stderr)
        raise ProviderRuntimeHTTPError(int(exc.code), path, body_text) from None


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

    keepalive_probe = subparsers.add_parser(
        "keepalive-probe",
        help=(
            "Run one keepalive strategy attempt against the signed-in managed "
            "session (developer preflight; does not wait for expiration)"
        ),
    )
    keepalive_probe.add_argument("provider", choices=("amex",))
    keepalive_probe.add_argument(
        "--strategy",
        required=True,
        choices=KEEPALIVE_STRATEGIES,
        help="Keepalive strategy to probe",
    )

    keepalive_status = subparsers.add_parser("keepalive-status")
    keepalive_status.add_argument("provider", choices=("amex",))

    keepalive_stop = subparsers.add_parser("keepalive-stop")
    keepalive_stop.add_argument("provider", choices=("amex",))

    browser_inspect = subparsers.add_parser(
        "browser-inspect",
        help="Developer-only Browser Inspector over the live provider session",
    )
    browser_inspect.add_argument("provider", choices=("amex",))
    browser_inspect.add_argument(
        "--capture-screenshot",
        action="store_true",
        help=(
            "Optionally capture one local screenshot under "
            "~/.mighty/provider_runtime/diagnostics/ (may contain sensitive data)"
        ),
    )

    browser_inspect_debug = subparsers.add_parser(
        "browser-inspect-debug",
        help=(
            "Temporary developer probe: print CDP capability results "
            "and stop on the first failure with traceback"
        ),
    )
    browser_inspect_debug.add_argument("provider", choices=("amex",))

    browser_find_text = subparsers.add_parser(
        "browser-find-text",
        help=(
            "Developer-only DOM explorer: find where search text occurs via CDP "
            "(no dialog classification)"
        ),
    )
    browser_find_text.add_argument("provider", choices=("amex",))
    browser_find_text.add_argument(
        "query",
        help='Case-insensitive substring to locate (e.g. "expire")',
    )

    browser_watch_text = subparsers.add_parser(
        "browser-watch-text",
        help=(
            "Developer-only: poll CDP DOM/AX for timeout-related text and capture "
            "a sanitized diagnostic bundle on first match (no clicks)"
        ),
    )
    browser_watch_text.add_argument("provider", choices=("amex",))
    browser_watch_text.add_argument(
        "--terms",
        default=",".join(DEFAULT_BROWSER_WATCH_TERMS),
        help=(
            "Comma-separated case-insensitive substrings to watch "
            '(default: "expire,Your session,Continue,Log Out")'
        ),
    )
    browser_watch_text.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS,
        help=f"Poll interval (default: {DEFAULT_BROWSER_WATCH_INTERVAL_SECONDS})",
    )
    browser_watch_text.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS,
        help=f"Give up after this many seconds (default: {DEFAULT_BROWSER_WATCH_TIMEOUT_SECONDS})",
    )
    browser_watch_text.add_argument(
        "--stop-after-first-match",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Stop after the first match capture (default: true)",
    )
    browser_watch_text.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help=(
            "Optional path for the diagnostic JSON "
            "(default: ~/.mighty/provider_runtime/diagnostics/amex-text-watch-<UTC>.json)"
        ),
    )

    browser_record_expiration = subparsers.add_parser(
        "browser-record-expiration",
        help=(
            "Developer-only: retain a rolling CDP evidence window and save it on "
            "SIGNED_IN→SIGNED_OUT (no clicks, no dialog-text trigger)"
        ),
    )
    browser_record_expiration.add_argument("provider", choices=("amex",))
    browser_record_expiration.add_argument(
        "--interval-seconds",
        type=float,
        default=DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS,
        help=f"Poll interval (default: {DEFAULT_BROWSER_RECORD_INTERVAL_SECONDS})",
    )
    browser_record_expiration.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS,
        help=(
            "Give up after this many seconds "
            f"(default: {DEFAULT_BROWSER_RECORD_TIMEOUT_SECONDS})"
        ),
    )
    browser_record_expiration.add_argument(
        "--rolling-window-seconds",
        type=float,
        default=DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS,
        help=(
            "Retain only observations within this trailing window "
            f"(default: {DEFAULT_BROWSER_RECORD_ROLLING_WINDOW_SECONDS})"
        ),
    )
    browser_record_expiration.add_argument(
        "--screenshot-every-seconds",
        type=float,
        default=DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS,
        help=(
            "Capture a viewport PNG on this interval "
            f"(default: {DEFAULT_BROWSER_RECORD_SCREENSHOT_EVERY_SECONDS})"
        ),
    )
    browser_record_expiration.add_argument(
        "--verification-interval-seconds",
        type=float,
        default=DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS,
        help=(
            "Fresh canonical verification cadence for SIGNED_IN→SIGNED_OUT "
            "detection. Uses ReadUserSession.v1 (may count as session activity); "
            "keep slower than screenshot/browser evidence polls "
            f"(default: {DEFAULT_BROWSER_RECORD_VERIFICATION_INTERVAL_SECONDS})"
        ),
    )
    browser_record_expiration.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Optional directory for recording.json + screenshots "
            "(default: ~/.mighty/provider_runtime/diagnostics/"
            "amex-expiration-recording-<UTC>/)"
        ),
    )

    browser_run_expiration_experiment = subparsers.add_parser(
        "browser-run-expiration-experiment",
        help=(
            "Developer-only: one-command Amex keepalive + expiration "
            "recorder, packaged into a single evidence ZIP"
        ),
    )
    browser_run_expiration_experiment.add_argument("provider", choices=("amex",))
    browser_run_expiration_experiment.add_argument(
        "--strategy",
        choices=KEEPALIVE_STRATEGIES,
        default="NONE",
        help=(
            "Keepalive strategy passed to start_keepalive_trial "
            f"(default: NONE; choices: {', '.join(KEEPALIVE_STRATEGIES)})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--trial-duration-seconds",
        type=int,
        default=DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS,
        help=(
            "Keepalive trial duration "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--keepalive-interval-seconds",
        type=int,
        default=DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_INTERVAL_SECONDS,
        help=(
            "Keepalive poll interval "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_KEEPALIVE_INTERVAL_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--recording-timeout-seconds",
        type=float,
        default=DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS,
        help=(
            "Expiration recorder timeout "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--evidence-interval-seconds",
        type=float,
        default=DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS,
        help=(
            "Recorder browser-evidence poll interval "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--verification-interval-seconds",
        type=float,
        default=DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS,
        help=(
            "Recorder fresh canonical verification cadence "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--rolling-window-seconds",
        type=float,
        default=DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS,
        help=(
            "Recorder trailing observation window "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--screenshot-every-seconds",
        type=float,
        default=DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS,
        help=(
            "Recorder screenshot cadence "
            f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS})"
        ),
    )
    browser_run_expiration_experiment.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Optional experiment directory "
            "(default: ~/.mighty/provider_runtime/diagnostics/"
            "amex-expiration-experiment-<UTC>/)"
        ),
    )

    def _add_campaign_run_arguments(
        parser: argparse.ArgumentParser,
        *,
        trials_required: bool,
        include_runtime_lifecycle_help: bool,
    ) -> None:
        parser.add_argument("provider", choices=("amex",))
        parser.add_argument(
            "--trial",
            action="append",
            dest="trials",
            required=trials_required,
            metavar="STRATEGY:INTERVAL",
            help=(
                "Repeatable trial specification STRATEGY:KEEPALIVE_INTERVAL_SECONDS "
                f"(strategies: {', '.join(KEEPALIVE_STRATEGIES)}; example: NONE:30)"
                + (
                    ""
                    if trials_required
                    else (
                        "; default campaign: "
                        + ", ".join(DEFAULT_AMEX_CAMPAIGN_TRIALS)
                    )
                )
            ),
        )
        parser.add_argument(
            "--campaign-name",
            default=None,
            help=(
                "Optional campaign label stored in campaign-summary artifacts "
                f"(default for campaign: {DEFAULT_AMEX_CAMPAIGN_NAME})"
            ),
        )
        parser.add_argument(
            "--trial-duration-seconds",
            type=int,
            default=DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS,
            help=(
                "Keepalive trial duration for each campaign trial "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_TRIAL_DURATION_SECONDS})"
            ),
        )
        parser.add_argument(
            "--recording-timeout-seconds",
            type=float,
            default=DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS,
            help=(
                "Expiration recorder timeout for each trial "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_RECORDING_TIMEOUT_SECONDS})"
            ),
        )
        parser.add_argument(
            "--evidence-interval-seconds",
            type=float,
            default=DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS,
            help=(
                "Recorder browser-evidence poll interval "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_EVIDENCE_INTERVAL_SECONDS})"
            ),
        )
        parser.add_argument(
            "--verification-interval-seconds",
            type=float,
            default=DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS,
            help=(
                "Recorder fresh canonical verification cadence "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_VERIFICATION_INTERVAL_SECONDS})"
            ),
        )
        parser.add_argument(
            "--rolling-window-seconds",
            type=float,
            default=DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS,
            help=(
                "Recorder trailing observation window "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_ROLLING_WINDOW_SECONDS})"
            ),
        )
        parser.add_argument(
            "--screenshot-every-seconds",
            type=float,
            default=DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS,
            help=(
                "Recorder screenshot cadence "
                f"(default: {DEFAULT_EXPIRATION_EXPERIMENT_SCREENSHOT_EVERY_SECONDS})"
            ),
        )
        parser.add_argument(
            "--output-dir",
            type=Path,
            default=None,
            help=(
                "Optional campaign directory "
                "(default: ~/.mighty/provider_runtime/diagnostics/"
                "amex-expiration-campaign-<UTC>/)"
            ),
        )
        parser.add_argument(
            "--browser-cleanup",
            choices=BROWSER_CLEANUP_POLICIES,
            default=DEFAULT_BROWSER_CLEANUP_POLICY,
            help=(
                "Whether to close a campaign-launched managed browser at the end "
                f"(default: {DEFAULT_BROWSER_CLEANUP_POLICY}; "
                "never closes a preexisting managed browser or ordinary Chrome)"
            ),
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Record a failed trial and continue to the next trial",
        )
        parser.add_argument(
            "--skip-completed",
            action="store_true",
            help=(
                "Resume an interrupted campaign by skipping trials already marked "
                "completed in campaign-manifest.json (match strategy + interval)"
            ),
        )
        if include_runtime_lifecycle_help:
            parser.epilog = (
                "Ensures Provider Runtime serve is running (starts it when needed), "
                "manages the dedicated Amex browser, runs the keepalive comparison "
                "campaign, and stops serve only when this command started it."
            )

    campaign = subparsers.add_parser(
        "campaign",
        help=(
            "Run the Amex keepalive comparison campaign end-to-end "
            "(auto-starts serve when needed)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_campaign_run_arguments(
        campaign,
        trials_required=False,
        include_runtime_lifecycle_help=True,
    )
    campaign.add_argument(
        "--resume",
        type=Path,
        default=None,
        metavar="CAMPAIGN_DIR",
        help=(
            "Resume an existing campaign directory "
            "(implies --skip-completed; skips trials already completed in "
            "campaign-manifest.json)"
        ),
    )
    campaign.add_argument(
        "--analyze",
        action="store_true",
        help=(
            "After campaign packaging, run offline campaign analysis and write "
            "campaign-analysis.json/.csv/.md (analysis failure does not fail a "
            "successful campaign)"
        ),
    )

    analyze_campaign = subparsers.add_parser(
        "analyze-campaign",
        help=(
            "Analyze a saved Amex expiration campaign directory or ZIP "
            "(offline; does not start serve or Chrome)"
        ),
    )
    analyze_campaign.add_argument(
        "campaign_path",
        type=Path,
        help="Campaign directory or campaign ZIP path",
    )
    analyze_campaign.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for analysis outputs (default: campaign dir or ZIP parent)",
    )
    analyze_campaign.add_argument(
        "--tolerance-seconds",
        type=float,
        default=15.0,
        help="Baseline comparison tolerance in seconds (default: 15)",
    )

    browser_run_expiration_campaign = subparsers.add_parser(
        "browser-run-expiration-campaign",
        help=(
            "Internal/advanced: run expiration experiments against an already "
            "running serve (prefer: campaign amex)"
        ),
    )
    _add_campaign_run_arguments(
        browser_run_expiration_campaign,
        trials_required=True,
        include_runtime_lifecycle_help=False,
    )

    browser_open_latest_expiration_experiment = subparsers.add_parser(
        "browser-open-latest-expiration-experiment",
        help="Open the latest Amex expiration experiment folder in Finder (macOS)",
    )
    browser_open_latest_expiration_experiment.add_argument(
        "provider",
        choices=("amex",),
    )

    inspect_expiration = subparsers.add_parser(
        "inspect-expiration-dialog",
        help="Deprecated alias for browser-inspect + Amex expiration classification",
    )
    inspect_expiration.add_argument("provider", choices=("amex",))
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
    try:
        return run_client_command(args)
    except ProviderRuntimeHTTPError:
        # request_json already printed status + bounded response body.
        return 1


def run_client_command(args: argparse.Namespace) -> int:
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
    if args.command == "keepalive-probe":
        payload = request_json(
            "POST",
            f"http://{args.host}:{args.port}/providers/{args.provider}/keepalive/probe",
            {"strategy": args.strategy},
            timeout=30.0,
        )
        print(format_keepalive_probe_terminal_summary(payload), end="")
        if payload.get("ok") and payload.get("success"):
            return 0
        return 1
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
    if args.command == "browser-inspect":
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/browser-inspection"
            ),
            {"capture_screenshot": bool(getattr(args, "capture_screenshot", False))},
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    if args.command == "browser-inspect-debug":
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/browser-inspection-debug"
            ),
            {},
        )
        # Prefer human-readable probe report; fall back to raw JSON on transport errors.
        if "pages" in payload and "frames" in payload:
            print(format_browser_inspect_debug_report(payload))
            if payload.get("first_failure") and payload["first_failure"].get("traceback"):
                # Traceback already included in the report; keep JSON available via stderr.
                print(
                    json.dumps(
                        {
                            "ok": payload.get("ok"),
                            "first_failure": payload.get("first_failure"),
                        },
                        indent=2,
                    ),
                    file=sys.stderr,
                )
        else:
            print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    if args.command == "browser-find-text":
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/browser-find-text"
            ),
            {"query": str(args.query)},
        )
        print(format_browser_find_text_report(payload))
        return 0 if payload.get("ok") else 1
    if args.command == "browser-watch-text":
        terms = parse_browser_watch_terms(args.terms)
        output_file = args.output_file
        if output_file is not None:
            output_file = output_file.expanduser().resolve()
        # Allow the HTTP call to outlive the watch timeout (server blocks until done).
        http_timeout = float(args.timeout_seconds) + 120.0
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/browser-watch-text"
            ),
            {
                "terms": terms,
                "interval_seconds": float(args.interval_seconds),
                "timeout_seconds": float(args.timeout_seconds),
                "stop_after_first_match": bool(args.stop_after_first_match),
                "output_file": str(output_file) if output_file else None,
            },
            timeout=http_timeout,
        )
        saved = payload.get("output_file")
        if saved:
            print(str(saved))
        else:
            print(json.dumps(payload, indent=2))
        if payload.get("matched"):
            return 0
        if payload.get("timed_out"):
            return 0
        return 0 if payload.get("ok") else 1
    if args.command == "browser-record-expiration":
        http_timeout = float(args.timeout_seconds) + 120.0
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/browser-record-expiration"
            ),
            build_browser_record_expiration_cli_payload(args),
            timeout=http_timeout,
        )
        saved = payload.get("recording_json") or (
            str(Path(payload["output_dir"]) / "recording.json")
            if payload.get("output_dir")
            else None
        )
        if saved:
            print(str(saved))
        else:
            print(json.dumps(payload, indent=2))
        outcome = payload.get("outcome")
        if outcome in {
            "logged_out",
            "timeout",
            "initial_not_signed_in",
            "initial_authentication_unknown",
        }:
            return 0
        return 0 if payload.get("ok") else 1
    if args.command == "browser-run-expiration-experiment":
        output_dir = args.output_dir
        if output_dir is not None:
            output_dir = output_dir.expanduser().resolve()
        result = run_amex_expiration_experiment(
            host=args.host,
            port=args.port,
            diagnostics_dir=args.root / "diagnostics",
            output_dir=output_dir,
            strategy=str(args.strategy),
            trial_duration_seconds=int(args.trial_duration_seconds),
            keepalive_interval_seconds=int(args.keepalive_interval_seconds),
            recording_timeout_seconds=float(args.recording_timeout_seconds),
            evidence_interval_seconds=float(args.evidence_interval_seconds),
            verification_interval_seconds=float(args.verification_interval_seconds),
            rolling_window_seconds=float(args.rolling_window_seconds),
            screenshot_every_seconds=float(args.screenshot_every_seconds),
        )
        print_expiration_experiment_result(result)
        return int(result.get("exit_code") or (0 if result.get("ok") else 1))
    if args.command == "analyze-campaign":
        from mighty.provider_runtime_campaign_analysis import (
            run_analyze_campaign_command,
        )

        campaign_path = Path(args.campaign_path).expanduser().resolve()
        output_dir = getattr(args, "output_dir", None)
        if output_dir is not None:
            output_dir = Path(output_dir).expanduser().resolve()
        try:
            run_analyze_campaign_command(
                campaign_path,
                output_dir=output_dir,
                tolerance_seconds=float(getattr(args, "tolerance_seconds", 15.0)),
            )
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except Exception as exc:  # noqa: BLE001 - offline analysis errors
            print(f"Campaign analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.command == "campaign":
        output_dir = args.output_dir
        if output_dir is not None:
            output_dir = output_dir.expanduser().resolve()
        resume_dir = getattr(args, "resume", None)
        if resume_dir is not None:
            resume_dir = Path(resume_dir).expanduser().resolve()
        try:
            trial_specs = resolve_amex_campaign_trials(list(args.trials or []))
            parse_expiration_campaign_trial_specs(trial_specs)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        result = run_amex_provider_campaign(
            host=args.host,
            port=args.port,
            root=args.root,
            cdp_port=int(args.cdp_port),
            state_path=args.state_path,
            result_path=args.result_path,
            keepalive_result_path=args.keepalive_result_path,
            diagnostics_dir=args.root / "diagnostics",
            output_dir=output_dir,
            trials=trial_specs,
            campaign_name=args.campaign_name,
            trial_duration_seconds=int(args.trial_duration_seconds),
            recording_timeout_seconds=float(args.recording_timeout_seconds),
            evidence_interval_seconds=float(args.evidence_interval_seconds),
            verification_interval_seconds=float(args.verification_interval_seconds),
            rolling_window_seconds=float(args.rolling_window_seconds),
            screenshot_every_seconds=float(args.screenshot_every_seconds),
            browser_cleanup=str(args.browser_cleanup),
            continue_on_error=bool(args.continue_on_error),
            skip_completed=bool(args.skip_completed),
            resume_dir=resume_dir,
        )
        print_expiration_campaign_result(result)
        exit_code = int(result.get("exit_code") or (0 if result.get("ok") else 1))
        if bool(getattr(args, "analyze", False)):
            analysis_exit = _maybe_analyze_campaign_after_run(result)
            # Analysis failure must not convert a successful campaign into failure.
            if exit_code == 0 and analysis_exit != 0:
                return analysis_exit
        return exit_code
    if args.command == "browser-run-expiration-campaign":
        try:
            trial_specs = parse_expiration_campaign_trial_specs(list(args.trials or []))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        output_dir = args.output_dir
        if output_dir is not None:
            output_dir = output_dir.expanduser().resolve()
        result = run_amex_expiration_campaign(
            host=args.host,
            port=args.port,
            root=args.root,
            cdp_port=int(args.cdp_port),
            diagnostics_dir=args.root / "diagnostics",
            output_dir=output_dir,
            trials=trial_specs,
            campaign_name=args.campaign_name,
            trial_duration_seconds=int(args.trial_duration_seconds),
            recording_timeout_seconds=float(args.recording_timeout_seconds),
            evidence_interval_seconds=float(args.evidence_interval_seconds),
            verification_interval_seconds=float(args.verification_interval_seconds),
            rolling_window_seconds=float(args.rolling_window_seconds),
            screenshot_every_seconds=float(args.screenshot_every_seconds),
            browser_cleanup=str(args.browser_cleanup),
            continue_on_error=bool(args.continue_on_error),
            skip_completed=bool(args.skip_completed),
        )
        print_expiration_campaign_result(result)
        return int(result.get("exit_code") or (0 if result.get("ok") else 1))
    if args.command == "browser-open-latest-expiration-experiment":
        result = open_latest_expiration_experiment(args.root / "diagnostics")
        if result.get("ok"):
            print(result.get("message") or result.get("experiment_dir"))
            return 0
        print(str(result.get("message") or result.get("error")), file=sys.stderr)
        return 1
    if args.command == "inspect-expiration-dialog":
        payload = request_json(
            "POST",
            (
                f"http://{args.host}:{args.port}/providers/"
                f"{args.provider}/diagnostics/inspect-expiration-dialog"
            ),
        )
        print(json.dumps(payload, indent=2))
        return 0 if payload.get("ok") else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
