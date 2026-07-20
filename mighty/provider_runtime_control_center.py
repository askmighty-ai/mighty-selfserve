"""Mighty Access Control Center — long-running operational console.

Owns Provider Runtime + managed browser, runs an Access Supervisor loop that
continuously verifies and maintains authenticated access, and renders a live
console from a provider-independent AccessState.

This is not a campaign UI. It is the recommended development operational
interface for demonstrating continuous authenticated access.
"""

from __future__ import annotations

import json
import os
import select
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_SUPERVISOR_INTERVAL_SECONDS = 60.0
DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 300.0
DEFAULT_KEEPALIVE_STRATEGY = "SESSION_API"
DEFAULT_EVENT_HISTORY_LIMIT = 100
DEFAULT_RECENT_EVENTS_DISPLAY = 8
DEFAULT_AUTH_RECOVERY_COOLDOWN_SECONDS = 120.0
DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS = 1

RECOVERY_EPISODE_IDLE = "idle"
RECOVERY_EPISODE_ACTIVE = "active"
RECOVERY_EPISODE_EXHAUSTED = "exhausted"

ACCESS_HEALTH_HEALTHY = "healthy"
ACCESS_HEALTH_DEGRADED = "degraded"
ACCESS_HEALTH_UNAVAILABLE = "unavailable"
ACCESS_HEALTH_RECOVERING = "recovering"

RUNTIME_STATUS_RUNNING = "running"
RUNTIME_STATUS_STARTING = "starting"
RUNTIME_STATUS_STOPPED = "stopped"
RUNTIME_STATUS_UNHEALTHY = "unhealthy"

BROWSER_STATUS_HEALTHY = "healthy"
BROWSER_STATUS_LAUNCHING = "launching"
BROWSER_STATUS_UNHEALTHY = "unhealthy"
BROWSER_STATUS_MISSING = "missing"

RECOVERY_STATUS_IDLE = "idle"
RECOVERY_STATUS_PLANNING = "planning"
RECOVERY_STATUS_RECOVERING = "recovering"
RECOVERY_STATUS_AWAITING_USER = "awaiting_user"
RECOVERY_STATUS_FAILED = "failed"

SCHEDULER_STATUS_RUNNING = "running"
SCHEDULER_STATUS_PAUSED = "paused"
SCHEDULER_STATUS_STOPPED = "stopped"

EVENT_VERIFICATION_SUCCESS = "verification_success"
EVENT_VERIFICATION_FAILURE = "verification_failure"
EVENT_KEEPALIVE_SUCCESS = "keepalive_success"
EVENT_KEEPALIVE_FAILURE = "keepalive_failure"
EVENT_RECOVERY_STARTED = "recovery_started"
EVENT_RECOVERY_COMPLETED = "recovery_completed"
EVENT_RECOVERY_EPISODE_STARTED = "recovery_episode_started"
EVENT_RECOVERY_CONFIRM_VERIFY_STARTED = "recovery_confirm_verify_started"
EVENT_RECOVERY_CONFIRM_VERIFY_SUCCEEDED = "recovery_confirm_verify_succeeded"
EVENT_RECOVERY_CONFIRM_VERIFY_FAILED = "recovery_confirm_verify_failed"
EVENT_RECOVERY_SESSION_ENSURE_STARTED = "recovery_session_ensure_started"
EVENT_RECOVERY_SESSION_ENSURE_FAILED = "recovery_session_ensure_failed"
EVENT_RECOVERY_SURFACE_ENSURE_STARTED = "recovery_surface_ensure_started"
EVENT_RECOVERY_SURFACE_ENSURE_FAILED = "recovery_surface_ensure_failed"
EVENT_RECOVERY_BROWSER_RESTART_STARTED = "recovery_browser_restart_started"
EVENT_RECOVERY_BROWSER_RESTART_SKIPPED = "recovery_browser_restart_skipped"
EVENT_RECOVERY_SUCCEEDED = "recovery_succeeded"
EVENT_RECOVERY_EXHAUSTED = "recovery_exhausted"
EVENT_AWAITING_USER = "awaiting_user"
EVENT_BROWSER_RESTART = "browser_restart"
EVENT_USER_INTERRUPTION = "user_interruption"
EVENT_CONNECTOR_REFRESH = "connector_refresh"
EVENT_OVERVIEW_OK = "overview_ok"
EVENT_OVERVIEW_FAILED = "overview_failed"
EVENT_SCHEDULER_TICK = "scheduler_tick"
EVENT_CONTROL_CENTER_STARTED = "control_center_started"
EVENT_CONTROL_CENTER_STOPPED = "control_center_stopped"

KEYBOARD_COMMANDS = {
    "v": "verify",
    "k": "keepalive",
    "r": "connector_refresh",
    "l": "login_recover",
    "q": "quit",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_relative(iso_timestamp: str | None, *, now: datetime | None = None) -> str:
    parsed = parse_iso(iso_timestamp)
    if parsed is None:
        return "—"
    current = now or datetime.now(timezone.utc)
    age = (current - parsed).total_seconds()
    return f"{format_age(age)} ago"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


@dataclass(frozen=True)
class AccessEvent:
    """One rolling history event for the Control Center."""

    event_type: str
    message: str
    observed_at: str
    ok: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "message": self.message,
            "observed_at": self.observed_at,
            "ok": self.ok,
            "details": dict(self.details),
        }


class EventHistory:
    """In-memory rolling event buffer (newest last)."""

    def __init__(self, limit: int = DEFAULT_EVENT_HISTORY_LIMIT) -> None:
        self._limit = max(1, int(limit))
        self._events: deque[AccessEvent] = deque(maxlen=self._limit)
        self._lock = threading.RLock()

    def append(
        self,
        event_type: str,
        message: str,
        *,
        ok: bool = True,
        details: dict[str, Any] | None = None,
        observed_at: str | None = None,
    ) -> AccessEvent:
        event = AccessEvent(
            event_type=str(event_type),
            message=str(message),
            observed_at=observed_at or iso_now(),
            ok=bool(ok),
            details=dict(details or {}),
        )
        with self._lock:
            self._events.append(event)
        return event

    def list_events(self, limit: int | None = None) -> list[AccessEvent]:
        with self._lock:
            events = list(self._events)
        if limit is None:
            return events
        if limit <= 0:
            return []
        return events[-int(limit) :]

    def clear(self) -> None:
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


@dataclass
class AccessState:
    """Provider-independent access snapshot. UI reads only this object."""

    provider: str = "amex"
    runtime_status: str = RUNTIME_STATUS_STOPPED
    browser_status: str = BROWSER_STATUS_MISSING
    recovery_planner_status: str = RECOVERY_STATUS_IDLE
    scheduler_status: str = SCHEDULER_STATUS_STOPPED
    authentication_state: str = "LOGIN_UNKNOWN"
    access_health: str = ACCESS_HEALTH_UNAVAILABLE
    session_started_at: str | None = None
    last_verification_at: str | None = None
    last_verification_result: str | None = None
    last_keepalive_at: str | None = None
    last_keepalive_result: str | None = None
    current_strategy: str | None = DEFAULT_KEEPALIVE_STRATEGY
    recovery_attempt_count: int = 0
    recovery_success_count: int = 0
    recovery_failure_count: int = 0
    last_recovery_action: str | None = None
    last_recovery_result: str | None = None
    recovery_episode_state: str = RECOVERY_EPISODE_IDLE
    escalation_reason: str | None = None
    user_interruption_count: int = 0
    ready_for_extraction: bool = False
    ready_for_connector: bool = False
    overview_ok: bool = False
    last_error: str | None = None
    updated_at: str = field(default_factory=iso_now)
    recent_events: tuple[AccessEvent, ...] = ()

    @property
    def recovery_count(self) -> int:
        """Deprecated alias for attempt count (kept for older callers/tests)."""
        return int(self.recovery_attempt_count)

    def session_age_seconds(self, *, now: datetime | None = None) -> float | None:
        started = parse_iso(self.session_started_at)
        if started is None:
            return None
        current = now or datetime.now(timezone.utc)
        return max(0.0, (current - started).total_seconds())

    def snapshot(self, history: EventHistory | None = None, *, recent_limit: int = 20) -> AccessState:
        events = tuple(history.list_events(recent_limit)) if history is not None else self.recent_events
        return replace(self, recent_events=events, updated_at=iso_now())

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "runtime_status": self.runtime_status,
            "browser_status": self.browser_status,
            "recovery_planner_status": self.recovery_planner_status,
            "scheduler_status": self.scheduler_status,
            "authentication_state": self.authentication_state,
            "access_health": self.access_health,
            "session_started_at": self.session_started_at,
            "session_age_seconds": self.session_age_seconds(),
            "last_verification_at": self.last_verification_at,
            "last_verification_result": self.last_verification_result,
            "last_keepalive_at": self.last_keepalive_at,
            "last_keepalive_result": self.last_keepalive_result,
            "current_strategy": self.current_strategy,
            "recovery_attempt_count": self.recovery_attempt_count,
            "recovery_success_count": self.recovery_success_count,
            "recovery_failure_count": self.recovery_failure_count,
            "recovery_count": self.recovery_attempt_count,
            "last_recovery_action": self.last_recovery_action,
            "last_recovery_result": self.last_recovery_result,
            "recovery_episode_state": self.recovery_episode_state,
            "escalation_reason": self.escalation_reason,
            "user_interruption_count": self.user_interruption_count,
            "ready_for_extraction": self.ready_for_extraction,
            "ready_for_connector": self.ready_for_connector,
            "overview_ok": self.overview_ok,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "recent_events": [event.to_dict() for event in self.recent_events],
        }


def derive_access_health(
    *,
    runtime_status: str,
    browser_status: str,
    authentication_state: str,
    overview_ok: bool,
    recovery_planner_status: str,
) -> str:
    if recovery_planner_status in {RECOVERY_STATUS_RECOVERING, RECOVERY_STATUS_AWAITING_USER}:
        return ACCESS_HEALTH_RECOVERING
    if runtime_status != RUNTIME_STATUS_RUNNING:
        return ACCESS_HEALTH_UNAVAILABLE
    if browser_status != BROWSER_STATUS_HEALTHY:
        return ACCESS_HEALTH_DEGRADED
    if authentication_state == "SIGNED_IN" and overview_ok:
        return ACCESS_HEALTH_HEALTHY
    if authentication_state == "SIGNED_IN":
        return ACCESS_HEALTH_DEGRADED
    if authentication_state in {"SIGNED_OUT", "LOGIN_UNKNOWN"}:
        return ACCESS_HEALTH_DEGRADED
    return ACCESS_HEALTH_UNAVAILABLE


def apply_verification_to_access_state(
    state: AccessState,
    *,
    authentication_state: str,
    observed_at: str | None = None,
    overview_ok: bool | None = None,
    runtime_status: str | None = None,
    browser_status: str | None = None,
    recovery_planner_status: str | None = None,
) -> AccessState:
    """Update AccessState from a verification result (provider-independent)."""
    auth = str(authentication_state or "LOGIN_UNKNOWN")
    observed = observed_at or iso_now()
    next_overview = state.overview_ok if overview_ok is None else bool(overview_ok)
    next_runtime = runtime_status or state.runtime_status
    next_browser = browser_status or state.browser_status
    next_recovery = recovery_planner_status or state.recovery_planner_status

    session_started = state.session_started_at
    if auth == "SIGNED_IN" and not session_started:
        session_started = observed
    if auth != "SIGNED_IN":
        # Session continuity broken — clear age anchor until re-authenticated.
        if state.authentication_state == "SIGNED_IN":
            session_started = None

    ready = (
        next_runtime == RUNTIME_STATUS_RUNNING
        and next_browser == BROWSER_STATUS_HEALTHY
        and auth == "SIGNED_IN"
        and next_overview
        and next_recovery == RECOVERY_STATUS_IDLE
    )

    health = derive_access_health(
        runtime_status=next_runtime,
        browser_status=next_browser,
        authentication_state=auth,
        overview_ok=next_overview,
        recovery_planner_status=next_recovery,
    )

    return replace(
        state,
        authentication_state=auth,
        last_verification_at=observed,
        last_verification_result=auth,
        overview_ok=next_overview,
        runtime_status=next_runtime,
        browser_status=next_browser,
        recovery_planner_status=next_recovery,
        session_started_at=session_started,
        ready_for_extraction=ready,
        ready_for_connector=ready,
        access_health=health,
        updated_at=iso_now(),
        last_error=None if auth == "SIGNED_IN" else state.last_error,
    )


def apply_keepalive_to_access_state(
    state: AccessState,
    *,
    ok: bool,
    strategy: str,
    observed_at: str | None = None,
    result: str | None = None,
) -> AccessState:
    observed = observed_at or iso_now()
    label = result or ("ok" if ok else "failed")
    return replace(
        state,
        last_keepalive_at=observed,
        last_keepalive_result=label,
        current_strategy=str(strategy or state.current_strategy or DEFAULT_KEEPALIVE_STRATEGY),
        updated_at=iso_now(),
    )


def render_control_center(
    state: AccessState,
    *,
    now: datetime | None = None,
    recent_limit: int = DEFAULT_RECENT_EVENTS_DISPLAY,
    include_header: bool = True,
) -> str:
    """Render AccessState as a fixed console layout (no side effects)."""
    current = now or datetime.now(timezone.utc)
    age = format_age(state.session_age_seconds(now=current))
    lines: list[str] = []
    if include_header:
        lines.extend(
            [
                "══════════════════════════════════════════════════════════",
                "           Mighty Access Control Center",
                "══════════════════════════════════════════════════════════",
                "",
            ]
        )
    lines.extend(
        [
            "SYSTEM",
            f"  Runtime ............ {state.runtime_status}",
            f"  Browser ............ {state.browser_status}",
            f"  Recovery Planner ... {state.recovery_planner_status}",
            f"  Scheduler .......... {state.scheduler_status}",
            "",
            f"PROVIDER  {state.provider}",
            f"  Authentication ..... {state.authentication_state}",
            f"  Access health ...... {state.access_health}",
            f"  Session age ........ {age}",
            (
                f"  Last verification .. {format_relative(state.last_verification_at, now=current)}"
                f"  ({state.last_verification_result or '—'})"
            ),
            (
                f"  Last keepalive ..... {format_relative(state.last_keepalive_at, now=current)}"
                f"  ({state.last_keepalive_result or '—'})"
            ),
            f"  Current strategy ... {state.current_strategy or '—'}",
            f"  User interruptions . {state.user_interruption_count}",
            f"  Ready for extraction {_yes_no(state.ready_for_extraction)}",
            f"  Ready for connector  {_yes_no(state.ready_for_connector)}",
            "",
            "RECOVERY",
            f"  State .............. {state.recovery_episode_state}",
            f"  Attempts ........... {state.recovery_attempt_count}",
            f"  Successes .......... {state.recovery_success_count}",
            f"  Failures ........... {state.recovery_failure_count}",
            f"  Last action ........ {state.last_recovery_action or '—'}",
            f"  Last result ........ {state.last_recovery_result or '—'}",
        ]
    )
    if state.escalation_reason:
        lines.append(f"  Escalation ......... {state.escalation_reason}")
    lines.extend(
        [
            "",
            "RECENT EVENTS",
        ]
    )
    events = list(state.recent_events)[-int(recent_limit) :]
    if not events:
        lines.append("  (none yet)")
    else:
        # Newest first by observed_at (stable for equal timestamps).
        ordered = sorted(
            events,
            key=lambda event: parse_iso(event.observed_at) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for event in ordered:
            stamp = _short_time(event.observed_at)
            status = "ok" if event.ok else "fail"
            lines.append(f"  {stamp}  {event.event_type:<22} {status:<4}  {event.message}")
    lines.extend(
        [
            "",
            "Commands: [v]erify  [k]eepalive  [r]efresh connector  [l]ogin/recover  [q]uit",
        ]
    )
    if state.last_error:
        lines.append(f"Last error: {state.last_error}")
    return "\n".join(lines) + "\n"


def _short_time(iso_timestamp: str) -> str:
    parsed = parse_iso(iso_timestamp)
    if parsed is None:
        return "--:--:--"
    return parsed.astimezone(timezone.utc).strftime("%H:%M:%S")


def dispatch_keyboard_command(key: str) -> str | None:
    """Map a single keystroke to a command name, or None if unknown."""
    if not key:
        return None
    token = str(key).strip().lower()
    if not token:
        return None
    return KEYBOARD_COMMANDS.get(token[0])


class AccessSupervisor:
    """Periodic access maintenance loop. Updates AccessState + EventHistory."""

    def __init__(
        self,
        *,
        provider: str = "amex",
        host: str = "127.0.0.1",
        port: int = 8765,
        cdp_port: int = 9223,
        interval_seconds: float = DEFAULT_SUPERVISOR_INTERVAL_SECONDS,
        keepalive_interval_seconds: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
        keepalive_strategy: str = DEFAULT_KEEPALIVE_STRATEGY,
        auth_recovery_cooldown_seconds: float = DEFAULT_AUTH_RECOVERY_COOLDOWN_SECONDS,
        max_safe_auth_recovery_attempts: int = DEFAULT_MAX_SAFE_AUTH_RECOVERY_ATTEMPTS,
        state: AccessState | None = None,
        history: EventHistory | None = None,
        request_json_fn: Callable[..., Any] | None = None,
        classify_browser_fn: Callable[..., Any] | None = None,
        restart_browser_fn: Callable[..., Any] | None = None,
        sleep_fn: Callable[[float], Any] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        now_fn: Callable[[], str] | None = None,
        on_state_change: Callable[[AccessState], Any] | None = None,
    ) -> None:
        self.provider = str(provider)
        self.host = host
        self.port = int(port)
        self.cdp_port = int(cdp_port)
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.keepalive_interval_seconds = max(0.0, float(keepalive_interval_seconds))
        self.keepalive_strategy = str(keepalive_strategy or DEFAULT_KEEPALIVE_STRATEGY)
        self.auth_recovery_cooldown_seconds = max(0.0, float(auth_recovery_cooldown_seconds))
        self.max_safe_auth_recovery_attempts = max(1, int(max_safe_auth_recovery_attempts))
        self.state = state or AccessState(provider=self.provider, current_strategy=self.keepalive_strategy)
        self.history = history or EventHistory()
        self._request_json = request_json_fn
        self._classify_browser = classify_browser_fn
        self._restart_browser = restart_browser_fn
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic
        self._now = now_fn or iso_now
        self._on_state_change = on_state_change
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._force_verify = False
        self._force_keepalive = False
        self._force_connector_refresh = False
        self._force_login = False
        self._login_fn: Callable[[], dict[str, Any]] | None = None
        self._connector_refresh_fn: Callable[[], dict[str, Any]] | None = None
        # Bounded auth-loss recovery episode (no credentials / MFA automation).
        self._auth_seen_signed_in = self.state.authentication_state == "SIGNED_IN"
        self._auth_loss_eligible = False
        self._recovery_active = False
        self._episode_sequence_count = 0
        self._episode_actions: list[str] = []
        self._last_auth_recovery_mono: float | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def set_login_fn(self, fn: Callable[[], dict[str, Any]] | None) -> None:
        self._login_fn = fn

    def set_connector_refresh_fn(self, fn: Callable[[], dict[str, Any]] | None) -> None:
        self._connector_refresh_fn = fn

    def get_state(self) -> AccessState:
        with self._lock:
            return self.state.snapshot(self.history)

    def request_verify(self) -> None:
        self._force_verify = True
        self._wake.set()

    def request_keepalive(self) -> None:
        self._force_keepalive = True
        self._wake.set()

    def request_connector_refresh(self) -> None:
        self._force_connector_refresh = True
        self._wake.set()

    def request_login_recover(self) -> None:
        self._force_login = True
        self._wake.set()

    def login_recover_now(self) -> AccessState:
        """Run interactive login/recover synchronously (keyboard ``l``)."""
        with self._lock:
            self._force_login = False
        self._run_login_recover()
        self._publish()
        return self.get_state()

    def start(self) -> None:
        with self._lock:
            self.state = replace(
                self.state,
                scheduler_status=SCHEDULER_STATUS_RUNNING,
                current_strategy=self.keepalive_strategy,
                updated_at=self._now(),
            )
        self._stop.clear()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="mighty-access-supervisor",
            daemon=True,
        )
        self._thread.start()
        self._publish()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=float(join_timeout))
        with self._lock:
            self.state = replace(
                self.state,
                scheduler_status=SCHEDULER_STATUS_STOPPED,
                updated_at=self._now(),
            )
        self._publish()

    def run_once(self, *, force_keepalive: bool = False) -> AccessState:
        """Execute one supervisor cycle (also used by tests)."""
        with self._lock:
            if self._force_login:
                self._force_login = False
                self._run_login_recover()
            if self._force_connector_refresh:
                self._force_connector_refresh = False
                self._run_connector_refresh()

        self._refresh_runtime_status()
        self._refresh_browser_status()
        self._verify_auth()
        self._verify_overview()
        self._maybe_repair()

        due = force_keepalive or self._force_keepalive or self._keepalive_due()
        self._force_keepalive = False
        if due and self.state.authentication_state == "SIGNED_IN":
            self._run_keepalive()

        self._force_verify = False
        with self._lock:
            self.state = replace(self.state, updated_at=self._now())
        self._publish()
        return self.get_state()

    def _run_loop(self) -> None:
        # Immediate first tick so the console is useful right away.
        try:
            self.run_once(force_keepalive=False)
        except Exception as exc:  # noqa: BLE001 - supervisor must keep looping
            self._record_error(f"supervisor_tick_error: {exc}")
        while not self._stop.is_set():
            self._wake.wait(timeout=self.interval_seconds)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                self._record_error(f"supervisor_tick_error: {exc}")

    def _publish(self) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(self.get_state())
        except Exception:
            pass

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
        if self._request_json is None:
            raise RuntimeError("request_json_fn is required")
        url = f"{self.base_url}{path}"
        return self._request_json(method, url, body, timeout=timeout)

    def _append(self, event_type: str, message: str, *, ok: bool = True, details: dict[str, Any] | None = None) -> None:
        self.history.append(event_type, message, ok=ok, details=details, observed_at=self._now())

    def _record_error(self, message: str) -> None:
        with self._lock:
            self.state = replace(self.state, last_error=str(message), updated_at=self._now())
        self._append("error", message, ok=False)
        self._publish()

    def _refresh_runtime_status(self) -> None:
        try:
            payload = self._http("GET", "/health", timeout=5.0)
            ok = bool(payload.get("ok", True))
            status = RUNTIME_STATUS_RUNNING if ok else RUNTIME_STATUS_UNHEALTHY
        except Exception:
            status = RUNTIME_STATUS_UNHEALTHY
        with self._lock:
            self.state = replace(self.state, runtime_status=status, updated_at=self._now())
            self._recompute_health_locked()

    def _refresh_browser_status(self) -> None:
        if self._classify_browser is None:
            return
        try:
            classified = self._classify_browser(self.cdp_port) or {}
        except Exception as exc:  # noqa: BLE001
            classified = {"state": "ABSENT", "error": str(exc)}
        raw = str(classified.get("state") or "ABSENT").upper()
        if raw == "HEALTHY":
            status = BROWSER_STATUS_HEALTHY
        elif raw == "UNHEALTHY":
            status = BROWSER_STATUS_UNHEALTHY
        else:
            status = BROWSER_STATUS_MISSING
        with self._lock:
            self.state = replace(self.state, browser_status=status, updated_at=self._now())
            self._recompute_health_locked()

    def _verify_auth(self) -> None:
        previous_auth = self.state.authentication_state
        try:
            payload = self._http("POST", f"/providers/{self.provider}/verify", timeout=45.0)
        except Exception as exc:  # noqa: BLE001
            observed = self._now()
            auth = "LOGIN_UNKNOWN"
            with self._lock:
                self.state = apply_verification_to_access_state(
                    self.state,
                    authentication_state=auth,
                    observed_at=observed,
                    overview_ok=False,
                )
                self.state = replace(self.state, last_error=str(exc))
            self._note_auth_transition(previous_auth, auth)
            self._append(EVENT_VERIFICATION_FAILURE, f"verify error: {exc}", ok=False)
            return

        auth = str(
            payload.get("authentication_state")
            or (payload.get("result") or {}).get("authentication_state")
            or "LOGIN_UNKNOWN"
        )
        observed = str(payload.get("observed_at") or self._now())
        ok = auth == "SIGNED_IN"
        with self._lock:
            recovery = self.state.recovery_planner_status
            if ok and recovery in {
                RECOVERY_STATUS_AWAITING_USER,
                RECOVERY_STATUS_FAILED,
                RECOVERY_STATUS_RECOVERING,
                RECOVERY_STATUS_PLANNING,
            }:
                recovery = RECOVERY_STATUS_IDLE
            self.state = apply_verification_to_access_state(
                self.state,
                authentication_state=auth,
                observed_at=observed,
                recovery_planner_status=recovery,
            )
        self._note_auth_transition(previous_auth, auth)
        self._append(
            EVENT_VERIFICATION_SUCCESS if ok else EVENT_VERIFICATION_FAILURE,
            auth,
            ok=ok,
            details={"authentication_state": auth},
        )

    def _note_auth_transition(self, previous_auth: str, auth: str) -> None:
        if auth == "SIGNED_IN":
            self._auth_seen_signed_in = True
            if not self._recovery_active:
                self._reset_auth_recovery_episode(clear_escalation=True)
            return
        if previous_auth == "SIGNED_IN" and auth in {"SIGNED_OUT", "LOGIN_UNKNOWN"}:
            self._auth_loss_eligible = True

    def _verify_overview(self) -> None:
        if self.state.authentication_state != "SIGNED_IN":
            with self._lock:
                self.state = replace(self.state, overview_ok=False, updated_at=self._now())
                self._recompute_health_locked()
            return
        try:
            payload = self._http(
                "POST",
                f"/providers/{self.provider}/surface/ensure",
                {"surface": "overview"},
                timeout=45.0,
            )
            ok = bool(payload.get("ok"))
        except Exception as exc:  # noqa: BLE001
            ok = False
            self._append(EVENT_OVERVIEW_FAILED, f"overview error: {exc}", ok=False)
            with self._lock:
                self.state = replace(self.state, overview_ok=False, last_error=str(exc), updated_at=self._now())
                self._recompute_health_locked()
            return
        with self._lock:
            self.state = replace(self.state, overview_ok=ok, updated_at=self._now())
            self._recompute_health_locked()
        self._append(
            EVENT_OVERVIEW_OK if ok else EVENT_OVERVIEW_FAILED,
            "overview" if ok else "overview not ready",
            ok=ok,
        )

    def _keepalive_due(self) -> bool:
        if self.keepalive_interval_seconds <= 0:
            return False
        last = parse_iso(self.state.last_keepalive_at)
        if last is None:
            return True
        age = (datetime.now(timezone.utc) - last).total_seconds()
        return age >= self.keepalive_interval_seconds

    def _run_keepalive(self) -> None:
        strategy = self.keepalive_strategy
        try:
            payload = self._http(
                "POST",
                f"/providers/{self.provider}/keepalive/probe",
                {"strategy": strategy},
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001
            observed = self._now()
            with self._lock:
                self.state = apply_keepalive_to_access_state(
                    self.state,
                    ok=False,
                    strategy=strategy,
                    observed_at=observed,
                    result="error",
                )
                self.state = replace(self.state, last_error=str(exc))
            self._append(EVENT_KEEPALIVE_FAILURE, f"{strategy}: {exc}", ok=False)
            return

        ok = bool(payload.get("ok") and payload.get("success", payload.get("ok")))
        observed = self._now()
        reason = str(payload.get("reason") or payload.get("outcome") or ("ok" if ok else "failed"))
        with self._lock:
            self.state = apply_keepalive_to_access_state(
                self.state,
                ok=ok,
                strategy=strategy,
                observed_at=observed,
                result=reason,
            )
        self._append(
            EVENT_KEEPALIVE_SUCCESS if ok else EVENT_KEEPALIVE_FAILURE,
            f"{strategy}: {reason}",
            ok=ok,
            details={"strategy": strategy, "reason": reason},
        )

    def _mark_recovery_attempt(self, **fields: Any) -> None:
        with self._lock:
            self.state = replace(
                self.state,
                recovery_attempt_count=self.state.recovery_attempt_count + 1,
                updated_at=self._now(),
                **fields,
            )

    def _mark_recovery_success(self, **fields: Any) -> None:
        with self._lock:
            self.state = replace(
                self.state,
                recovery_success_count=self.state.recovery_success_count + 1,
                updated_at=self._now(),
                **fields,
            )

    def _mark_recovery_failure(self, **fields: Any) -> None:
        with self._lock:
            self.state = replace(
                self.state,
                recovery_failure_count=self.state.recovery_failure_count + 1,
                updated_at=self._now(),
                **fields,
            )

    def _set_recovery_fields(self, **fields: Any) -> None:
        with self._lock:
            self.state = replace(self.state, updated_at=self._now(), **fields)
            self._recompute_health_locked()

    def _reset_auth_recovery_episode(self, *, clear_escalation: bool = False) -> None:
        self._auth_loss_eligible = False
        self._episode_sequence_count = 0
        self._episode_actions = []
        self._last_auth_recovery_mono = None
        fields: dict[str, Any] = {"recovery_episode_state": RECOVERY_EPISODE_IDLE}
        if clear_escalation:
            fields["escalation_reason"] = None
        with self._lock:
            self.state = replace(self.state, updated_at=self._now(), **fields)

    def _can_attempt_auth_recovery(self) -> bool:
        if self._episode_sequence_count >= self.max_safe_auth_recovery_attempts:
            return False
        if self._last_auth_recovery_mono is None:
            return True
        elapsed = self._monotonic() - self._last_auth_recovery_mono
        return elapsed >= self.auth_recovery_cooldown_seconds

    def _maybe_repair(self) -> None:
        browser = self.state.browser_status
        auth = self.state.authentication_state
        if browser in {BROWSER_STATUS_MISSING, BROWSER_STATUS_UNHEALTHY}:
            self._repair_browser()
            return
        if auth == "SIGNED_IN":
            return
        if auth not in {"SIGNED_OUT", "LOGIN_UNKNOWN"}:
            return
        if self.state.recovery_planner_status != RECOVERY_STATUS_IDLE:
            return
        if self._recovery_active:
            return

        if self._auth_loss_eligible:
            if self._can_attempt_auth_recovery():
                self._run_safe_auth_recovery()
                return
            if self._episode_sequence_count >= self.max_safe_auth_recovery_attempts:
                self._escalate_awaiting_user(
                    reason="safe_recovery_exhausted",
                    actions_attempted=list(self._episode_actions),
                )
                return
            # Cooldown active — wait quietly; do not re-run ensure APIs each tick.
            return

        # Clean signed-out/unknown without a prior SIGNED_IN session: escalate only.
        self._escalate_awaiting_user(
            reason="authentication_required",
            actions_attempted=[],
        )

    def _run_safe_auth_recovery(self) -> None:
        """Bounded safe recovery before escalating to interactive login."""
        self._recovery_active = True
        self._episode_sequence_count += 1
        self._last_auth_recovery_mono = self._monotonic()
        self._episode_actions = []
        attempted: list[str] = []

        self._set_recovery_fields(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            recovery_episode_state=RECOVERY_EPISODE_ACTIVE,
            access_health=ACCESS_HEALTH_RECOVERING,
            escalation_reason=None,
            last_recovery_action="episode_start",
            last_recovery_result="started",
        )
        self._append(
            EVENT_RECOVERY_EPISODE_STARTED,
            "safe auth recovery episode started",
            details={
                "action": "episode_start",
                "outcome": "started",
                "auth_state": self.state.authentication_state,
                "reason": "signed_in_to_signed_out_or_unknown",
                "sequence": self._episode_sequence_count,
            },
        )

        try:
            if self._recovery_action_confirm_verify(attempted):
                return
            if self._recovery_action_session_ensure(attempted):
                return
            if self._recovery_action_surface_ensure(attempted):
                return
            if self._recovery_action_browser_restart_if_unhealthy(attempted):
                return

            self._episode_actions = list(attempted)
            if self._episode_sequence_count >= self.max_safe_auth_recovery_attempts:
                self._mark_recovery_failure(
                    last_recovery_action=attempted[-1] if attempted else "episode",
                    last_recovery_result="exhausted",
                    recovery_episode_state=RECOVERY_EPISODE_EXHAUSTED,
                )
                self._escalate_awaiting_user(
                    reason="real_reauthentication_boundary",
                    actions_attempted=attempted,
                )
            else:
                # Leave planner idle so a later tick may retry after cooldown.
                self._set_recovery_fields(
                    recovery_planner_status=RECOVERY_STATUS_IDLE,
                    recovery_episode_state=RECOVERY_EPISODE_ACTIVE,
                    last_recovery_action=attempted[-1] if attempted else "episode",
                    last_recovery_result="sequence_failed_cooldown",
                )
                self._append(
                    EVENT_RECOVERY_EXHAUSTED,
                    "safe recovery sequence failed; cooldown before retry",
                    ok=False,
                    details={
                        "action": "episode",
                        "outcome": "sequence_failed_cooldown",
                        "auth_state": self.state.authentication_state,
                        "reason": "retry_remaining",
                        "actions_attempted": list(attempted),
                        "sequence": self._episode_sequence_count,
                    },
                )
        finally:
            self._recovery_active = False

    def _recovery_action_confirm_verify(self, attempted: list[str]) -> bool:
        action = "confirm_verify"
        attempted.append(action)
        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            last_recovery_action=action,
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(
            EVENT_RECOVERY_CONFIRM_VERIFY_STARTED,
            "confirming auth denial",
            details={
                "action": action,
                "outcome": "started",
                "auth_state": self.state.authentication_state,
                "reason": "distinguish_transient_denial",
            },
        )
        self._verify_auth()
        auth = self.state.authentication_state
        if auth == "SIGNED_IN":
            self._append(
                EVENT_RECOVERY_CONFIRM_VERIFY_SUCCEEDED,
                "confirm verify restored SIGNED_IN",
                details={
                    "action": action,
                    "outcome": "succeeded",
                    "auth_state": auth,
                    "reason": "transient_denial",
                },
            )
            self._finish_auth_recovery_success(action, reason="confirm_verify_restored_session")
            return True
        self._set_recovery_fields(last_recovery_action=action, last_recovery_result="failed")
        self._append(
            EVENT_RECOVERY_CONFIRM_VERIFY_FAILED,
            f"confirm verify still {auth}",
            ok=False,
            details={
                "action": action,
                "outcome": "failed",
                "auth_state": auth,
                "reason": "hard_logout_or_unknown",
            },
        )
        return False

    def _recovery_action_session_ensure(self, attempted: list[str]) -> bool:
        action = "session_ensure"
        attempted.append(action)
        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            last_recovery_action=action,
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(
            EVENT_RECOVERY_SESSION_ENSURE_STARTED,
            "invoking session/ensure",
            details={
                "action": action,
                "outcome": "started",
                "auth_state": self.state.authentication_state,
                "reason": "runtime_session_ensure",
            },
        )
        try:
            self._http(
                "POST",
                f"/providers/{self.provider}/session/ensure",
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_recovery_fields(
                last_recovery_action=action,
                last_recovery_result="failed",
                last_error=str(exc),
            )
            self._append(
                EVENT_RECOVERY_SESSION_ENSURE_FAILED,
                f"session/ensure error: {exc}",
                ok=False,
                details={
                    "action": action,
                    "outcome": "failed",
                    "auth_state": self.state.authentication_state,
                    "reason": "runtime_error",
                },
            )
            self._verify_auth()
            if self.state.authentication_state == "SIGNED_IN":
                self._finish_auth_recovery_success(action, reason="session_ensure_restored_session")
                return True
            return False

        self._verify_auth()
        auth = self.state.authentication_state
        if auth == "SIGNED_IN":
            self._finish_auth_recovery_success(action, reason="session_ensure_restored_session")
            return True
        self._set_recovery_fields(last_recovery_action=action, last_recovery_result="failed")
        self._append(
            EVENT_RECOVERY_SESSION_ENSURE_FAILED,
            f"session/ensure still {auth}",
            ok=False,
            details={
                "action": action,
                "outcome": "failed",
                "auth_state": auth,
                "reason": "session_not_restored",
            },
        )
        return False

    def _recovery_action_surface_ensure(self, attempted: list[str]) -> bool:
        action = "surface_ensure"
        attempted.append(action)
        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            last_recovery_action=action,
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(
            EVENT_RECOVERY_SURFACE_ENSURE_STARTED,
            "invoking surface/ensure overview",
            details={
                "action": action,
                "outcome": "started",
                "auth_state": self.state.authentication_state,
                "reason": "soft_provider_surface_recovery",
            },
        )
        try:
            self._http(
                "POST",
                f"/providers/{self.provider}/surface/ensure",
                {"surface": "overview"},
                timeout=45.0,
            )
        except Exception as exc:  # noqa: BLE001
            self._set_recovery_fields(
                last_recovery_action=action,
                last_recovery_result="failed",
                last_error=str(exc),
            )
            self._append(
                EVENT_RECOVERY_SURFACE_ENSURE_FAILED,
                f"surface/ensure error: {exc}",
                ok=False,
                details={
                    "action": action,
                    "outcome": "failed",
                    "auth_state": self.state.authentication_state,
                    "reason": "runtime_error",
                },
            )
            self._verify_auth()
            if self.state.authentication_state == "SIGNED_IN":
                self._finish_auth_recovery_success(action, reason="surface_ensure_restored_session")
                return True
            return False

        self._verify_auth()
        auth = self.state.authentication_state
        if auth == "SIGNED_IN":
            self._finish_auth_recovery_success(action, reason="surface_ensure_restored_session")
            return True
        self._set_recovery_fields(last_recovery_action=action, last_recovery_result="failed")
        self._append(
            EVENT_RECOVERY_SURFACE_ENSURE_FAILED,
            f"surface/ensure still {auth}",
            ok=False,
            details={
                "action": action,
                "outcome": "failed",
                "auth_state": auth,
                "reason": "surface_not_authenticated",
            },
        )
        return False

    def _recovery_action_browser_restart_if_unhealthy(self, attempted: list[str]) -> bool:
        self._refresh_browser_status()
        browser = self.state.browser_status
        if browser == BROWSER_STATUS_HEALTHY:
            self._append(
                EVENT_RECOVERY_BROWSER_RESTART_SKIPPED,
                "browser healthy; restart skipped",
                details={
                    "action": "browser_restart",
                    "outcome": "skipped",
                    "auth_state": self.state.authentication_state,
                    "reason": "browser_healthy",
                },
            )
            return False

        action = "browser_restart"
        attempted.append(action)
        if self._restart_browser is None:
            self._set_recovery_fields(last_recovery_action=action, last_recovery_result="unavailable")
            self._append(
                EVENT_RECOVERY_BROWSER_RESTART_STARTED,
                "browser restart unavailable",
                ok=False,
                details={
                    "action": action,
                    "outcome": "unavailable",
                    "auth_state": self.state.authentication_state,
                    "reason": "restart_fn_missing",
                },
            )
            return False

        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            browser_status=BROWSER_STATUS_LAUNCHING,
            last_recovery_action=action,
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(
            EVENT_RECOVERY_BROWSER_RESTART_STARTED,
            "restarting degraded browser during auth recovery",
            details={
                "action": action,
                "outcome": "started",
                "auth_state": self.state.authentication_state,
                "reason": "browser_unhealthy",
            },
        )
        self._append(EVENT_BROWSER_RESTART, "managed browser restart (auth recovery)")
        try:
            result = self._restart_browser() or {}
            ok = bool(result.get("ok", True))
        except Exception as exc:  # noqa: BLE001
            ok = False
            self._set_recovery_fields(
                last_recovery_action=action,
                last_recovery_result="failed",
                last_error=str(exc),
                browser_status=BROWSER_STATUS_UNHEALTHY,
            )
            self._append(
                EVENT_RECOVERY_COMPLETED,
                f"browser restart failed: {exc}",
                ok=False,
                details={
                    "action": action,
                    "outcome": "failed",
                    "auth_state": self.state.authentication_state,
                    "reason": "restart_exception",
                },
            )
            return False

        self._refresh_browser_status()
        self._verify_auth()
        auth = self.state.authentication_state
        if ok and auth == "SIGNED_IN":
            self._finish_auth_recovery_success(action, reason="browser_restart_restored_session")
            return True
        self._set_recovery_fields(
            last_recovery_action=action,
            last_recovery_result="failed",
        )
        self._append(
            EVENT_RECOVERY_COMPLETED,
            f"browser restart did not restore auth ({auth})",
            ok=False,
            details={
                "action": action,
                "outcome": "failed",
                "auth_state": auth,
                "reason": "auth_still_degraded",
            },
        )
        return False

    def _finish_auth_recovery_success(self, action: str, *, reason: str) -> None:
        auth = self.state.authentication_state
        self._mark_recovery_success(
            recovery_planner_status=RECOVERY_STATUS_IDLE,
            recovery_episode_state=RECOVERY_EPISODE_IDLE,
            last_recovery_action=action,
            last_recovery_result="succeeded",
            escalation_reason=None,
        )
        with self._lock:
            self._recompute_health_locked()
        self._append(
            EVENT_RECOVERY_SUCCEEDED,
            f"safe recovery succeeded via {action}",
            details={
                "action": action,
                "outcome": "succeeded",
                "auth_state": auth,
                "reason": reason,
            },
        )
        self._append(
            EVENT_RECOVERY_COMPLETED,
            f"safe recovery succeeded via {action}",
            details={"action": action, "auth_state": auth},
        )
        self._episode_actions = []
        self._auth_loss_eligible = False
        self._episode_sequence_count = 0
        self._last_auth_recovery_mono = None
        # Overview was skipped while signed out; refresh now that auth is restored.
        self._verify_overview()

    def _escalate_awaiting_user(
        self,
        *,
        reason: str,
        actions_attempted: list[str],
    ) -> None:
        auth = self.state.authentication_state
        self._set_recovery_fields(
            recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
            access_health=ACCESS_HEALTH_RECOVERING,
            escalation_reason=reason,
            recovery_episode_state=(
                RECOVERY_EPISODE_EXHAUSTED if actions_attempted else RECOVERY_EPISODE_IDLE
            ),
        )
        if actions_attempted:
            self._append(
                EVENT_RECOVERY_EXHAUSTED,
                "safe autonomous recovery exhausted",
                ok=False,
                details={
                    "action": "episode",
                    "outcome": "exhausted",
                    "auth_state": auth,
                    "reason": reason,
                    "actions_attempted": list(actions_attempted),
                },
            )
        self._append(
            EVENT_AWAITING_USER,
            "real re-authentication boundary; press l for interactive login",
            ok=False,
            details={
                "action": "awaiting_user",
                "outcome": "escalated",
                "auth_state": auth,
                "reason": reason,
                "actions_attempted": list(actions_attempted),
                "final_auth_evidence": auth,
            },
        )
        # Compatibility event for older console/tests.
        self._append(
            EVENT_RECOVERY_STARTED,
            "authentication degraded; awaiting login/recover (press l)",
            ok=False,
            details={
                "authentication_state": auth,
                "autonomous_strategies_attempted": list(actions_attempted),
                "escalation": "awaiting_user",
                "reason": reason,
            },
        )
        self._auth_loss_eligible = False
        self._episode_actions = list(actions_attempted)

    def _repair_browser(self) -> None:
        if self._restart_browser is None:
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
                    access_health=ACCESS_HEALTH_RECOVERING,
                    updated_at=self._now(),
                )
            self._append(EVENT_RECOVERY_STARTED, "browser degraded; restart unavailable", ok=False)
            return
        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            browser_status=BROWSER_STATUS_LAUNCHING,
            last_recovery_action="browser_restart",
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(EVENT_RECOVERY_STARTED, "restarting managed browser")
        self._append(EVENT_BROWSER_RESTART, "managed browser restart")
        try:
            result = self._restart_browser() or {}
            ok = bool(result.get("ok", True))
        except Exception as exc:  # noqa: BLE001
            ok = False
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_FAILED,
                    browser_status=BROWSER_STATUS_UNHEALTHY,
                    last_error=str(exc),
                    last_recovery_action="browser_restart",
                    last_recovery_result="failed",
                    updated_at=self._now(),
                )
            self._append(EVENT_RECOVERY_COMPLETED, f"browser restart failed: {exc}", ok=False)
            return
        self._refresh_browser_status()
        if ok:
            self._mark_recovery_success(
                recovery_planner_status=RECOVERY_STATUS_IDLE,
                last_recovery_action="browser_restart",
                last_recovery_result="succeeded",
            )
            with self._lock:
                self._recompute_health_locked()
        else:
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_FAILED,
                    last_recovery_action="browser_restart",
                    last_recovery_result="failed",
                    updated_at=self._now(),
                )
                self._recompute_health_locked()
        self._append(
            EVENT_RECOVERY_COMPLETED,
            "browser restart ok" if ok else "browser restart failed",
            ok=ok,
        )

    def _run_login_recover(self) -> None:
        # Explicit interactive login begins a fresh episode boundary.
        self._reset_auth_recovery_episode(clear_escalation=True)
        self._mark_recovery_attempt(
            recovery_planner_status=RECOVERY_STATUS_RECOVERING,
            user_interruption_count=self.state.user_interruption_count + 1,
            last_recovery_action="interactive_login",
            last_recovery_result="started",
            access_health=ACCESS_HEALTH_RECOVERING,
        )
        self._append(EVENT_USER_INTERRUPTION, "login/recover requested")
        self._append(EVENT_RECOVERY_STARTED, "interactive login/recover")
        if self._login_fn is None:
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_FAILED,
                    last_error="login_fn unavailable",
                    last_recovery_action="interactive_login",
                    last_recovery_result="failed",
                    updated_at=self._now(),
                )
            self._append(EVENT_RECOVERY_COMPLETED, "login_fn unavailable", ok=False)
            return
        try:
            result = self._login_fn() or {}
        except KeyboardInterrupt:
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_AWAITING_USER,
                    last_recovery_action="interactive_login",
                    last_recovery_result="interrupted",
                    escalation_reason="login_interrupted",
                    updated_at=self._now(),
                )
            self._append(EVENT_RECOVERY_COMPLETED, "login interrupted", ok=False)
            return
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.state = replace(
                    self.state,
                    recovery_planner_status=RECOVERY_STATUS_FAILED,
                    last_error=str(exc),
                    last_recovery_action="interactive_login",
                    last_recovery_result="failed",
                    updated_at=self._now(),
                )
            self._append(EVENT_RECOVERY_COMPLETED, f"login failed: {exc}", ok=False)
            return
        ok = bool(result.get("ok"))
        auth = str(result.get("final_authentication_state") or ("SIGNED_IN" if ok else "LOGIN_UNKNOWN"))
        with self._lock:
            self.state = apply_verification_to_access_state(
                self.state,
                authentication_state=auth,
                observed_at=self._now(),
                overview_ok=ok,
                recovery_planner_status=RECOVERY_STATUS_IDLE if ok else RECOVERY_STATUS_AWAITING_USER,
            )
            self.state = replace(
                self.state,
                last_recovery_action="interactive_login",
                last_recovery_result="succeeded" if ok else "failed",
                escalation_reason=None if ok else "interactive_login_required",
                recovery_episode_state=RECOVERY_EPISODE_IDLE,
            )
        if ok:
            self._auth_seen_signed_in = True
            self._mark_recovery_success()
        self._append(
            EVENT_RECOVERY_COMPLETED,
            f"login/recover → {auth}",
            ok=ok,
        )

    def _run_connector_refresh(self) -> None:
        if self._connector_refresh_fn is None:
            self._append(EVENT_CONNECTOR_REFRESH, "connector refresh unavailable", ok=False)
            return
        try:
            result = self._connector_refresh_fn() or {}
            ok = bool(result.get("ok"))
            status = str(result.get("status") or ("ok" if ok else "failed"))
        except Exception as exc:  # noqa: BLE001
            ok = False
            status = str(exc)
            with self._lock:
                self.state = replace(self.state, last_error=str(exc), updated_at=self._now())
        self._append(EVENT_CONNECTOR_REFRESH, status, ok=ok)
        if ok:
            # Refresh implies session still usable.
            self._verify_auth()

    def _recompute_health_locked(self) -> None:
        ready = (
            self.state.runtime_status == RUNTIME_STATUS_RUNNING
            and self.state.browser_status == BROWSER_STATUS_HEALTHY
            and self.state.authentication_state == "SIGNED_IN"
            and self.state.overview_ok
            and self.state.recovery_planner_status == RECOVERY_STATUS_IDLE
        )
        self.state = replace(
            self.state,
            access_health=derive_access_health(
                runtime_status=self.state.runtime_status,
                browser_status=self.state.browser_status,
                authentication_state=self.state.authentication_state,
                overview_ok=self.state.overview_ok,
                recovery_planner_status=self.state.recovery_planner_status,
            ),
            ready_for_extraction=ready,
            ready_for_connector=ready,
        )


class TerminalKeyboard:
    """Non-blocking single-key reader (cbreak when stdin is a TTY)."""

    def __init__(self, *, stdin: Any = None) -> None:
        self._stdin = stdin if stdin is not None else sys.stdin
        self._fd: int | None = None
        self._old_term: Any = None
        self._enabled = False

    def __enter__(self) -> TerminalKeyboard:
        self.enable()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.disable()

    def enable(self) -> None:
        if self._enabled:
            return
        try:
            if not hasattr(self._stdin, "fileno") or not self._stdin.isatty():
                return
            import termios
            import tty

            self._fd = self._stdin.fileno()
            self._old_term = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
            self._enabled = True
        except Exception:
            self._enabled = False

    def disable(self) -> None:
        if not self._enabled or self._fd is None or self._old_term is None:
            self._enabled = False
            return
        try:
            import termios

            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_term)
        except Exception:
            pass
        self._enabled = False
        self._fd = None
        self._old_term = None

    def poll_key(self, timeout: float = 0.25) -> str | None:
        try:
            fd = self._stdin.fileno()
        except Exception:
            return None
        ready, _, _ = select.select([fd], [], [], max(0.0, float(timeout)))
        if not ready:
            return None
        try:
            data = os.read(fd, 8)
        except Exception:
            return None
        if not data:
            return None
        try:
            text = data.decode("utf-8", errors="ignore")
        except Exception:
            return None
        return text[:1] if text else None


def redraw_console(text: str, *, stream: Any = None, use_ansi: bool = True) -> None:
    """Replace the terminal contents with ``text`` without flooding scrollback."""
    out = stream if stream is not None else sys.stdout
    if use_ansi and hasattr(out, "isatty") and out.isatty():
        out.write("\033[H\033[J")
    out.write(text)
    if not text.endswith("\n"):
        out.write("\n")
    out.flush()


class ControlCenterStartupError(RuntimeError):
    """Fatal Control Center startup failure with a stable stage name."""

    def __init__(self, stage: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.stage = str(stage)
        self.details = dict(details or {})


def format_control_center_startup_failure(
    *,
    stage: str,
    error: str,
    ownership: dict[str, Any] | None = None,
    diagnostic_path: str | None = None,
) -> str:
    """Human-readable fatal startup report (never return to the shell silently)."""
    lines = [
        "Mighty Access Control Center failed to start.",
        f"Stage: {stage}",
        f"Error: {error}",
    ]
    own = ownership or {}
    if own:
        lines.append(
            "Cleanup: "
            f"runtime_started={own.get('runtime_started_by_command')} "
            f"runtime_stopped={own.get('runtime_stopped_by_command')} "
            f"browser_launched={own.get('managed_browser_launched_by_command')} "
            f"browser_closed={own.get('managed_browser_closed_at_completion')} "
            f"browser_cleanup={own.get('browser_cleanup_policy')}"
        )
    if diagnostic_path:
        lines.append(f"Diagnostic: {diagnostic_path}")
    return "\n".join(lines)


def write_control_center_startup_diagnostic(
    diagnostics_dir: Path,
    payload: dict[str, Any],
) -> str:
    """Persist a sanitized startup-failure diagnostic JSON and return its path."""
    diagnostics_dir = Path(diagnostics_dir).expanduser().resolve()
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = diagnostics_dir / f"control-center-startup-failure-{stamp}.json"
    body = dict(payload)
    body["written_at"] = iso_now()
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(path)


def run_control_center(
    *,
    provider: str = "amex",
    host: str = "127.0.0.1",
    port: int = 8765,
    root: Path | None = None,
    cdp_port: int = 9223,
    state_path: Path | None = None,
    result_path: Path | None = None,
    keepalive_result_path: Path | None = None,
    interval_seconds: float = DEFAULT_SUPERVISOR_INTERVAL_SECONDS,
    keepalive_interval_seconds: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS,
    keepalive_strategy: str = DEFAULT_KEEPALIVE_STRATEGY,
    browser_cleanup: str = "leave-open",
    request_json_fn: Any = None,
    sleep_fn: Any = None,
    monotonic_fn: Any = None,
    input_fn: Any = None,
    print_fn: Any = None,
    ensure_runtime_fn: Any = None,
    stop_runtime_fn: Any = None,
    prepare_session_fn: Any = None,
    ensure_managed_browser_fn: Any = None,
    ensure_signed_in_fn: Any = None,
    close_managed_browser_fn: Any = None,
    bring_to_foreground_fn: Any = None,
    restart_managed_browser_fn: Any = None,
    launch_native_chrome_fn: Any = None,
    terminate_profile_processes_fn: Any = None,
    wait_for_profile_release_fn: Any = None,
    fetch_cdp_json_fn: Any = None,
    classify_browser_fn: Any = None,
    connector_refresh_fn: Any = None,
    keyboard: TerminalKeyboard | None = None,
    redraw_fn: Callable[[str], Any] | None = None,
    render_fn: Callable[[AccessState], str] | None = None,
    should_continue: Callable[[], bool] | None = None,
    max_loops: int | None = None,
) -> dict[str, Any]:
    """Start Control Center with runtime ownership and live console."""
    from mighty.provider_runtime import (
        DEFAULT_BROWSER_CLEANUP_POLICY,
        DEFAULT_CDP_PORT,
        DEFAULT_HOST,
        DEFAULT_KEEPALIVE_RESULT_PATH,
        DEFAULT_PORT,
        DEFAULT_RESULT_PATH,
        DEFAULT_ROOT,
        DEFAULT_STATE_PATH,
        BROWSER_CLEANUP_POLICIES,
        classify_managed_amex_browser,
        ensure_managed_amex_browser_for_campaign,
        ensure_provider_runtime_for_campaign,
        maybe_close_managed_browser_for_campaign,
        prepare_managed_amex_session_for_command,
        request_json,
        restart_managed_amex_browser,
        run_connector_refresh_with_runtime,
        stop_provider_runtime_serve,
        _expiration_experiment_base_url,
    )

    emit = print_fn or print
    http = request_json_fn or request_json
    sleep = sleep_fn or time.sleep
    runtime_root = Path(root or DEFAULT_ROOT).expanduser().resolve()
    profile_dir = runtime_root / "amex"
    diagnostics_dir = runtime_root / "diagnostics"
    host = host or DEFAULT_HOST
    port = int(port or DEFAULT_PORT)
    cdp_port = int(cdp_port or DEFAULT_CDP_PORT)
    state_path = Path(state_path or DEFAULT_STATE_PATH).expanduser().resolve()
    result_path = Path(result_path or DEFAULT_RESULT_PATH).expanduser().resolve()
    keepalive_result_path = Path(
        keepalive_result_path or DEFAULT_KEEPALIVE_RESULT_PATH
    ).expanduser().resolve()

    cleanup_policy = str(browser_cleanup or "leave-open")
    if cleanup_policy not in BROWSER_CLEANUP_POLICIES:
        cleanup_policy = DEFAULT_BROWSER_CLEANUP_POLICY

    runtime_preexisting = False
    runtime_started_by_command = False
    runtime_stopped_by_command = False
    runtime_process = None
    managed_browser_preexisting = False
    managed_browser_launched_by_command = False
    managed_browser_restarted_by_command = False
    managed_browser_closed_at_completion = False
    interrupted = False
    exit_code = 1
    history = EventHistory()
    access_state = AccessState(
        provider=provider,
        current_strategy=keepalive_strategy,
        runtime_status=RUNTIME_STATUS_STARTING,
        scheduler_status=SCHEDULER_STATUS_STOPPED,
    )
    browser_info: dict[str, Any] = {}

    def _ownership() -> dict[str, Any]:
        return {
            "runtime_preexisting": runtime_preexisting,
            "runtime_started_by_command": runtime_started_by_command,
            "runtime_stopped_by_command": runtime_stopped_by_command,
            "managed_browser_preexisting": managed_browser_preexisting,
            "managed_browser_launched_by_command": managed_browser_launched_by_command,
            "managed_browser_restarted_by_command": managed_browser_restarted_by_command,
            "browser_cleanup_policy": cleanup_policy,
            "managed_browser_closed_at_completion": managed_browser_closed_at_completion,
        }

    def _fail_startup(
        stage: str,
        error: str,
        *,
        outcome: str,
        exit_status: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal runtime_stopped_by_command
        if runtime_started_by_command and not runtime_stopped_by_command:
            stopper = stop_runtime_fn or stop_provider_runtime_serve
            try:
                stopper(
                    host=host,
                    port=port,
                    process=runtime_process,
                    request_json_fn=http,
                )
                runtime_stopped_by_command = True
            except Exception:
                runtime_stopped_by_command = False
        payload = {
            "ok": False,
            "provider": provider,
            "outcome": outcome,
            "stage": stage,
            "error": error,
            "exit_code": int(exit_status),
            "interrupted": bool(exit_status == 130 or interrupted),
            **_ownership(),
            **(extra or {}),
        }
        try:
            diagnostic_path = write_control_center_startup_diagnostic(
                diagnostics_dir,
                payload,
            )
        except Exception as diag_exc:  # noqa: BLE001
            diagnostic_path = None
            payload["diagnostic_write_error"] = f"{type(diag_exc).__name__}: {diag_exc}"
        payload["diagnostic_path"] = diagnostic_path
        emit(
            format_control_center_startup_failure(
                stage=stage,
                error=error,
                ownership=_ownership(),
                diagnostic_path=diagnostic_path,
            )
        )
        return payload

    # Browser before runtime so serve attaches to a headed, usable CDP session
    # instead of a foreign/zombie listener on the configured port.
    ensure_browser = ensure_managed_browser_fn or ensure_managed_amex_browser_for_campaign
    ensure_browser_kwargs: dict[str, Any] = {
        "profile_dir": profile_dir,
        "cdp_port": cdp_port,
        "print_fn": emit,
        "sleep_fn": sleep_fn,
        "monotonic_fn": monotonic_fn,
        "fetch_cdp_json_fn": fetch_cdp_json_fn,
        "launch_native_chrome_fn": launch_native_chrome_fn,
        "terminate_profile_processes_fn": terminate_profile_processes_fn,
        "wait_for_profile_release_fn": wait_for_profile_release_fn,
    }
    if restart_managed_browser_fn is not None:
        # ensure_managed_amex_browser_for_campaign calls restart_fn() with no args.
        ensure_browser_kwargs["restart_fn"] = restart_managed_browser_fn
    try:
        browser_info = ensure_browser(**ensure_browser_kwargs)
    except KeyboardInterrupt:
        return _fail_startup(
            "managed_browser",
            "interrupted",
            outcome="interrupted",
            exit_status=130,
        )
    except Exception as exc:  # noqa: BLE001 - startup must never exit silently
        return _fail_startup(
            "managed_browser",
            f"{type(exc).__name__}: {exc}",
            outcome="browser_start_failed",
        )

    if not isinstance(browser_info, dict) or browser_info.get("ok") is False:
        return _fail_startup(
            "managed_browser",
            str(
                (browser_info or {}).get("error")
                or (browser_info or {}).get("message")
                or "Failed to ensure managed Amex browser"
            ),
            outcome="browser_start_failed",
            extra={"browser_info": browser_info},
        )

    managed_browser_preexisting = bool(browser_info.get("managed_browser_preexisting"))
    managed_browser_launched_by_command = bool(
        browser_info.get("managed_browser_launched_by_campaign")
        or browser_info.get("managed_browser_launched")
    )
    managed_browser_restarted_by_command = bool(
        browser_info.get("managed_browser_restarted_by_campaign")
        or browser_info.get("managed_browser_restarted")
    )

    ensure_runtime = ensure_runtime_fn or ensure_provider_runtime_for_campaign
    runtime_info = ensure_runtime(
        host=host,
        port=port,
        root=runtime_root,
        cdp_port=cdp_port,
        state_path=state_path,
        result_path=result_path,
        keepalive_result_path=keepalive_result_path,
        request_json_fn=http,
        sleep_fn=sleep_fn,
        monotonic_fn=monotonic_fn,
        print_fn=emit,
    )
    if not isinstance(runtime_info, dict) or not runtime_info.get("ok"):
        return _fail_startup(
            "runtime",
            str(
                (runtime_info or {}).get("error")
                or (runtime_info or {}).get("message")
                or "Failed to ensure Provider Runtime"
            ),
            outcome=(runtime_info or {}).get("outcome") or "runtime_start_failed",
            extra={"runtime_info": runtime_info},
        )

    runtime_preexisting = bool(runtime_info.get("runtime_preexisting"))
    runtime_started_by_command = bool(runtime_info.get("runtime_started_by_campaign"))
    runtime_process = runtime_info.get("process")
    base_url = _expiration_experiment_base_url(host, port)
    access_state = replace(access_state, runtime_status=RUNTIME_STATUS_RUNNING)

    def _reuse_ready_browser(**_kwargs: Any) -> dict[str, Any]:
        return {
            "ok": True,
            **browser_info,
            "managed_browser_launched": managed_browser_launched_by_command,
            "managed_browser_restarted": managed_browser_restarted_by_command,
        }

    prepare = prepare_session_fn or prepare_managed_amex_session_for_command
    try:
        session = prepare(
            profile_dir=profile_dir,
            cdp_port=cdp_port,
            base_url=base_url,
            request_json_fn=http,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            input_fn=input_fn,
            print_fn=emit,
            ensure_managed_browser_fn=_reuse_ready_browser,
            ensure_signed_in_fn=ensure_signed_in_fn,
            bring_to_foreground_fn=bring_to_foreground_fn,
            restart_managed_browser_fn=restart_managed_browser_fn,
            launch_native_chrome_fn=launch_native_chrome_fn,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
            wait_for_profile_release_fn=wait_for_profile_release_fn,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
    except KeyboardInterrupt:
        interrupted = True
        session = {"ok": False, "interrupted": True, "outcome": "interrupted"}
    except Exception as exc:  # noqa: BLE001
        return _fail_startup(
            "authentication",
            f"{type(exc).__name__}: {exc}",
            outcome="authentication_failed",
        )

    if not isinstance(session, dict):
        session = {"ok": False, "outcome": "authentication_failed"}

    interrupted = interrupted or bool(session.get("interrupted"))
    if session.get("managed_browser_launched"):
        managed_browser_launched_by_command = True
    if session.get("managed_browser_restarted"):
        managed_browser_restarted_by_command = True

    if interrupted or not session.get("ok"):
        outcome = (
            "interrupted"
            if interrupted
            else str(session.get("outcome") or "authentication_required")
        )
        return _fail_startup(
            "authentication" if not interrupted else "interrupted",
            str(session.get("error") or session.get("message") or outcome),
            outcome=outcome,
            exit_status=130 if interrupted else 1,
            extra={"session": {
                k: session.get(k)
                for k in (
                    "outcome",
                    "error",
                    "message",
                    "final_authentication_state",
                    "authentication_attempt_count",
                )
            }},
        )

    auth = str(session.get("final_authentication_state") or "SIGNED_IN")
    started_at = iso_now()
    access_state = apply_verification_to_access_state(
        access_state,
        authentication_state=auth,
        observed_at=started_at,
        overview_ok=True,
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
    )
    access_state = replace(access_state, session_started_at=started_at)
    history.append(EVENT_CONTROL_CENTER_STARTED, "Access Control Center started", details={"provider": provider})
    history.append(EVENT_VERIFICATION_SUCCESS, auth, details={"source": "initial_auth"})
    if session.get("authentication_attempt_count"):
        history.append(
            EVENT_USER_INTERRUPTION,
            "initial authentication",
            details={"attempts": session.get("authentication_attempt_count")},
        )
        access_state = replace(
            access_state,
            user_interruption_count=int(session.get("authentication_attempt_count") or 0),
        )

    classify = classify_browser_fn or (
        lambda port_value: classify_managed_amex_browser(
            int(port_value),
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
    )

    def _restart() -> dict[str, Any]:
        nonlocal managed_browser_launched_by_command, managed_browser_restarted_by_command
        restarter = restart_managed_browser_fn or (
            lambda: restart_managed_amex_browser(
                profile_dir=profile_dir,
                cdp_port=cdp_port,
                launch_native_chrome_fn=launch_native_chrome_fn,
                terminate_profile_processes_fn=terminate_profile_processes_fn,
                wait_for_profile_release_fn=wait_for_profile_release_fn,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
                fetch_cdp_json_fn=fetch_cdp_json_fn,
            )
        )
        result = restarter()
        managed_browser_launched_by_command = True
        managed_browser_restarted_by_command = True
        return result if isinstance(result, dict) else {"ok": True}

    def _login() -> dict[str, Any]:
        nonlocal managed_browser_launched_by_command, managed_browser_restarted_by_command
        result = prepare(
            profile_dir=profile_dir,
            cdp_port=cdp_port,
            base_url=base_url,
            request_json_fn=http,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            input_fn=input_fn,
            print_fn=emit,
            ensure_managed_browser_fn=ensure_managed_browser_fn,
            ensure_signed_in_fn=ensure_signed_in_fn,
            bring_to_foreground_fn=bring_to_foreground_fn,
            restart_managed_browser_fn=restart_managed_browser_fn,
            launch_native_chrome_fn=launch_native_chrome_fn,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
            wait_for_profile_release_fn=wait_for_profile_release_fn,
            fetch_cdp_json_fn=fetch_cdp_json_fn,
        )
        if isinstance(result, dict):
            if result.get("managed_browser_launched"):
                managed_browser_launched_by_command = True
            if result.get("managed_browser_restarted"):
                managed_browser_restarted_by_command = True
        return result if isinstance(result, dict) else {"ok": False}

    def _connector() -> dict[str, Any]:
        runner = connector_refresh_fn or (
            lambda: run_connector_refresh_with_runtime(
                provider=provider,
                host=host,
                port=port,
                root=runtime_root,
                cdp_port=cdp_port,
                state_path=state_path,
                result_path=result_path,
                keepalive_result_path=keepalive_result_path,
                browser_cleanup="leave-open",
                as_json=False,
                persist=False,
                request_json_fn=http,
                sleep_fn=sleep_fn,
                monotonic_fn=monotonic_fn,
                input_fn=input_fn,
                print_fn=lambda *_a, **_k: None,
                ensure_runtime_fn=lambda **_kwargs: {
                    "ok": True,
                    "runtime_preexisting": True,
                    "runtime_started_by_campaign": False,
                    "process": None,
                    "base_url": base_url,
                },
                stop_runtime_fn=lambda **_kwargs: {"ok": True},
                prepare_session_fn=lambda **_kwargs: {
                    "ok": True,
                    "interrupted": False,
                    "managed_browser_preexisting": True,
                    "managed_browser_launched": False,
                    "managed_browser_restarted": False,
                    "final_authentication_state": "SIGNED_IN",
                    "authentication_attempt_count": 0,
                },
                emit_summary=False,
            )
        )
        return runner()

    state_lock = threading.Lock()
    latest_state = access_state.snapshot(history)

    def _on_change(state: AccessState) -> None:
        nonlocal latest_state
        with state_lock:
            latest_state = state

    supervisor = AccessSupervisor(
        provider=provider,
        host=host,
        port=port,
        cdp_port=cdp_port,
        interval_seconds=interval_seconds,
        keepalive_interval_seconds=keepalive_interval_seconds,
        keepalive_strategy=keepalive_strategy,
        state=access_state,
        history=history,
        request_json_fn=http,
        classify_browser_fn=classify,
        restart_browser_fn=_restart,
        sleep_fn=sleep,
        monotonic_fn=monotonic_fn,
        on_state_change=_on_change,
    )
    supervisor.set_login_fn(_login)
    supervisor.set_connector_refresh_fn(_connector)

    render = render_fn or (lambda st: render_control_center(st))
    draw = redraw_fn or (lambda text: redraw_console(text))
    kb = keyboard if keyboard is not None else TerminalKeyboard()

    quit_requested = False
    loops = 0
    outcome = "stopped"

    try:
        supervisor.start()
        kb.enable()
        while True:
            if should_continue is not None and not should_continue():
                break
            if max_loops is not None and loops >= int(max_loops):
                break
            with state_lock:
                screen = render(latest_state)
            draw(screen)
            key = kb.poll_key(timeout=0.5)
            command = dispatch_keyboard_command(key) if key else None
            if command == "quit":
                quit_requested = True
                break
            if command == "verify":
                supervisor.request_verify()
            elif command == "keepalive":
                supervisor.request_keepalive()
            elif command == "connector_refresh":
                supervisor.request_connector_refresh()
            elif command == "login_recover":
                # Disable cbreak so interactive Enter prompts work.
                kb.disable()
                try:
                    draw(render(supervisor.get_state()) + "\nLogin/recover — follow prompts…\n")
                    supervisor.login_recover_now()
                finally:
                    kb.enable()
            loops += 1
            sleep(0.05)
        exit_code = 0
        outcome = "quit" if quit_requested else "stopped"
    except KeyboardInterrupt:
        interrupted = True
        exit_code = 130
        outcome = "interrupted"
    finally:
        kb.disable()
        supervisor.stop()
        history.append(EVENT_CONTROL_CENTER_STOPPED, "Access Control Center stopped")
        if runtime_started_by_command:
            stopper = stop_runtime_fn or stop_provider_runtime_serve
            try:
                stopper(host=host, port=port, process=runtime_process, request_json_fn=http)
                runtime_stopped_by_command = True
                emit("Stopping Provider Runtime started by Control Center...")
            except Exception:
                runtime_stopped_by_command = False
        closer = close_managed_browser_fn or maybe_close_managed_browser_for_campaign
        close_result = closer(
            browser_cleanup=cleanup_policy,
            managed_browser_preexisting=managed_browser_preexisting,
            managed_browser_launched_by_campaign=managed_browser_launched_by_command,
            interrupted=interrupted,
            profile_dir=profile_dir,
            terminate_profile_processes_fn=terminate_profile_processes_fn,
        )
        managed_browser_closed_at_completion = bool(
            isinstance(close_result, dict) and close_result.get("closed")
        )

    final_state = supervisor.get_state()
    return {
        "ok": exit_code == 0,
        "provider": provider,
        "outcome": outcome,
        "exit_code": exit_code,
        "interrupted": interrupted,
        "access_state": final_state.to_dict(),
        "event_count": len(history),
        **_ownership(),
    }
