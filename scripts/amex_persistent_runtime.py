#!/usr/bin/env python3
"""Developer-only Amex persistent-browser proof of concept.

This spike answers one question: can Mighty preserve and re-open an Amex session
using a dedicated local browser profile, with a visible surface only for login?

It does not collect credentials, bypass MFA/CAPTCHA, or publish account data.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import signal
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from amex_login_diagnostics import AmexLoginDiagnostics

from playwright.sync_api import (
    BrowserContext,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

AMEX_OVERVIEW_URL = "https://global.americanexpress.com/overview"
AMEX_LOGIN_URL = "https://www.americanexpress.com/en-us/account/login"
DEFAULT_PROFILE_DIR = Path.home() / ".mighty" / "provider_runtime" / "amex"
DEFAULT_RESULT_PATH = Path.home() / ".mighty" / "provider_runtime" / "amex_last_result.json"
DEFAULT_DIAGNOSTICS_PATH = (
    Path.home() / ".mighty" / "provider_runtime" / "amex_login_diagnostics.json"
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


@dataclass(frozen=True)
class RuntimeResult:
    mode: str
    outcome: str
    started_at: str
    completed_at: str
    profile_dir: str
    browser_channel: str
    headless: bool
    requested_url: str
    final_url: str | None
    page_title: str | None
    login_url_detected: bool
    login_marker_count: int
    authenticated_marker_count: int
    session_api_200_count: int
    session_api_denied_count: int
    page_loaded: bool
    runtime_error: str | None = None


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


def count_markers(text: str, markers: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for marker in markers if marker in lowered)


def is_login_url(url: str | None) -> bool:
    path = (urlsplit(url or "").path or "").lower()
    return any(token in path for token in LOGIN_URL_TOKENS)


def profile_lock_hint(profile_dir: Path) -> str:
    return (
        f"The dedicated profile appears to be in use: {profile_dir}\n"
        "Close any other Mighty Amex runtime process and try again."
    )


def launch_context(
    *,
    profile_dir: Path,
    headless: bool,
    channel: str,
    slow_mo_ms: int,
) -> BrowserContext:
    profile_dir.mkdir(parents=True, exist_ok=True)
    return sync_playwright().start().chromium.launch_persistent_context(
        str(profile_dir),
        channel=channel,
        headless=headless,
        no_viewport=True,
        slow_mo=slow_mo_ms,
        args=[
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
        ],
    )


def install_signal_shutdown(context: BrowserContext) -> None:
    def _shutdown(_signum: int, _frame: Any) -> None:
        try:
            context.close()
        finally:
            raise SystemExit(130)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)


def collect_result(
    *,
    page: Page,
    mode: str,
    started_at: str,
    profile_dir: Path,
    channel: str,
    headless: bool,
    session_api_statuses: list[int],
    page_loaded: bool,
    runtime_error: str | None = None,
) -> RuntimeResult:
    final_url = sanitize_url(page.url)
    title: str | None = None
    body_text = ""
    try:
        title = page.title()
    except PlaywrightError:
        pass
    try:
        body_text = page.locator("body").inner_text(timeout=3_000)
    except PlaywrightError:
        pass

    return RuntimeResult(
        mode=mode,
        outcome=classify_outcome(
            final_url=final_url,
            body_text=body_text,
            session_api_statuses=session_api_statuses,
            runtime_error=runtime_error,
        ),
        started_at=started_at,
        completed_at=iso_now(),
        profile_dir=str(profile_dir),
        browser_channel=channel,
        headless=headless,
        requested_url=AMEX_OVERVIEW_URL,
        final_url=final_url,
        page_title=title,
        login_url_detected=is_login_url(final_url),
        login_marker_count=count_markers(body_text, LOGIN_MARKERS),
        authenticated_marker_count=count_markers(body_text, AUTHENTICATED_MARKERS),
        session_api_200_count=sum(1 for status in session_api_statuses if status == 200),
        session_api_denied_count=sum(1 for status in session_api_statuses if status in {401, 403}),
        page_loaded=page_loaded,
        runtime_error=runtime_error,
    )


def classify_outcome(
    *,
    final_url: str | None,
    body_text: str,
    session_api_statuses: list[int],
    runtime_error: str | None,
) -> str:
    if runtime_error:
        return "RUNTIME_ERROR"
    if any(status == 200 for status in session_api_statuses):
        return "AUTHENTICATED"
    if any(status in {401, 403} for status in session_api_statuses):
        return "SIGNED_OUT"
    if is_login_url(final_url):
        return "SIGNED_OUT"
    auth_hits = count_markers(body_text, AUTHENTICATED_MARKERS)
    login_hits = count_markers(body_text, LOGIN_MARKERS)
    if auth_hits >= 2 and login_hits == 0:
        return "AUTHENTICATED"
    if login_hits >= 2 and auth_hits == 0:
        return "SIGNED_OUT"
    return "INCONCLUSIVE"


def write_result(result: RuntimeResult, result_path: Path) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")


def print_result(result: RuntimeResult) -> None:
    print(json.dumps(asdict(result), indent=2))


def run_login(
    *,
    profile_dir: Path,
    result_path: Path,
    diagnostics_path: Path,
    channel: str,
    timeout_seconds: int,
) -> int:
    """Establish the Amex session in ordinary installed Chrome.

    Playwright is deliberately not attached during login. Amex's login flow
    failed under Playwright control because its cross-origin submit path was
    blocked. The user performs the normal login ceremony in native Chrome; the
    dedicated profile is then reused by verify mode.
    """
    del result_path, diagnostics_path, channel, timeout_seconds
    if sys.platform != "darwin":
        print(
            "Native login bootstrap is currently implemented for macOS only.",
            file=sys.stderr,
        )
        return 2

    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "open",
        "-na",
        "Google Chrome",
        "--args",
        f"--user-data-dir={profile_dir}",
        "--new-window",
        AMEX_LOGIN_URL,
    ]
    print(
        "\nOpening ordinary Google Chrome with Mighty's dedicated Amex profile.\n"
        "1. Sign in normally and complete any MFA requested by American Express.\n"
        "2. Confirm that you can see your authenticated Amex account.\n"
        "3. Close the entire dedicated Chrome window.\n"
        "4. Return here and press Enter.\n\n"
        "Mighty does not read or store your password.\n"
    )
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"Could not launch native Chrome: {exc}", file=sys.stderr)
        return 2

    try:
        input("Press Enter only after the dedicated Chrome window is fully closed: ")
    except (EOFError, KeyboardInterrupt):
        print("\nLogin bootstrap cancelled.", file=sys.stderr)
        return 130

    # Chrome may take a moment to release its profile lock after the window closes.
    time.sleep(2)
    lock_candidates = (
        profile_dir / "SingletonLock",
        profile_dir / "SingletonCookie",
        profile_dir / "SingletonSocket",
    )
    existing_locks = [str(path) for path in lock_candidates if path.exists()]
    if existing_locks:
        print(
            "\nThe dedicated profile still appears to be in use.\n"
            "Close all Chrome windows using the Mighty Amex profile, wait a few "
            "seconds, and then run the verify command.\n"
            f"Observed lock files: {existing_locks}",
            file=sys.stderr,
        )
        return 1

    print(
        "\nNative Amex login bootstrap complete.\n"
        "Now test invisible session reuse with:\n\n"
        "  python scripts/amex_persistent_runtime.py verify\n"
    )
    return 0


def run_verify(
    *,
    profile_dir: Path,
    result_path: Path,
    channel: str,
    headless: bool,
    navigation_timeout_ms: int,
) -> int:
    started_at = iso_now()
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                channel=channel,
                headless=headless,
                no_viewport=True,
            )
        except PlaywrightError as exc:
            print(profile_lock_hint(profile_dir), file=sys.stderr)
            print(str(exc), file=sys.stderr)
            return 2

        install_signal_shutdown(context)
        page = context.pages[0] if context.pages else context.new_page()
        session_api_statuses: list[int] = []

        def on_response(response: Any) -> None:
            if any(marker in response.url for marker in SESSION_API_MARKERS):
                session_api_statuses.append(response.status)

        page.on("response", on_response)

        page_loaded = False
        runtime_error: str | None = None
        try:
            page.goto(
                AMEX_OVERVIEW_URL,
                wait_until="domcontentloaded",
                timeout=navigation_timeout_ms,
            )
            page_loaded = True
            page.wait_for_timeout(5_000)
        except PlaywrightTimeoutError as exc:
            runtime_error = f"navigation_timeout: {exc}"
        except PlaywrightError as exc:
            runtime_error = f"navigation_error: {exc}"

        result = collect_result(
            page=page,
            mode="verify",
            started_at=started_at,
            profile_dir=profile_dir,
            channel=channel,
            headless=headless,
            session_api_statuses=session_api_statuses,
            page_loaded=page_loaded,
            runtime_error=runtime_error,
        )
        write_result(result, result_path)
        print_result(result)
        context.close()
        return 0 if result.outcome == "AUTHENTICATED" else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Amex persistent-browser proof of concept for Mighty."
    )
    parser.add_argument(
        "mode",
        choices=("login", "verify"),
        help="login opens one visible sign-in window; verify reuses the profile.",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=DEFAULT_PROFILE_DIR,
        help=f"Dedicated browser profile (default: {DEFAULT_PROFILE_DIR})",
    )
    parser.add_argument(
        "--result-path",
        type=Path,
        default=DEFAULT_RESULT_PATH,
        help=f"Sanitized result JSON (default: {DEFAULT_RESULT_PATH})",
    )
    parser.add_argument(
        "--diagnostics-path",
        type=Path,
        default=DEFAULT_DIAGNOSTICS_PATH,
        help=f"Sanitized login diagnostics JSON (default: {DEFAULT_DIAGNOSTICS_PATH})",
    )
    parser.add_argument(
        "--channel",
        default=os.environ.get("MIGHTY_BROWSER_CHANNEL", "chrome"),
        help="Playwright browser channel (default: chrome).",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run verification visibly for diagnosis. Login is always headed.",
    )
    parser.add_argument(
        "--login-timeout-seconds",
        type=int,
        default=300,
        help="Maximum time for interactive login (default: 300).",
    )
    parser.add_argument(
        "--navigation-timeout-ms",
        type=int,
        default=30_000,
        help="Verification navigation timeout (default: 30000).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.profile_dir = args.profile_dir.expanduser().resolve()
    args.result_path = args.result_path.expanduser().resolve()
    args.diagnostics_path = args.diagnostics_path.expanduser().resolve()

    if args.mode == "login":
        return run_login(
            profile_dir=args.profile_dir,
            result_path=args.result_path,
            diagnostics_path=args.diagnostics_path,
            channel=args.channel,
            timeout_seconds=args.login_timeout_seconds,
        )

    return run_verify(
        profile_dir=args.profile_dir,
        result_path=args.result_path,
        channel=args.channel,
        headless=not args.headed,
        navigation_timeout_ms=args.navigation_timeout_ms,
    )


if __name__ == "__main__":
    raise SystemExit(main())
