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

        page: Page = context.pages[0] if context.pages else context.new_page()

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
            }

    def stop(self) -> None:
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
