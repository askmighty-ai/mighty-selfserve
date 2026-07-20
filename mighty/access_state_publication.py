"""Publish canonical AccessState from the local Access Supervisor to Railway.

Transport: authenticated HTTP (X-Mighty-Key) from the local machine to Railway.
AccessState ownership stays with AccessSupervisor; this module only serializes
and publishes. Publication never blocks verification, keepalive, or recovery.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from mighty.provider_runtime_control_center import AccessState, iso_now

SCHEMA_VERSION = 2
DEFAULT_HEARTBEAT_SECONDS = 60.0
DEFAULT_BASE_URL = "https://mighty-selfserve-production.up.railway.app"
DEFAULT_MAX_BACKOFF_SECONDS = 60.0
DEFAULT_INITIAL_BACKOFF_SECONDS = 1.0

# Fields that define a material AccessState change (heartbeat ignores these).
MATERIAL_FIELDS: tuple[str, ...] = (
    "provider",
    "authentication_state",
    "access_health",
    "runtime_state",
    "browser_state",
    "recovery_state",
    "recovery_attempts",
    "recovery_successes",
    "recovery_failures",
    "last_recovery_action",
    "last_recovery_result",
    "escalation_reason",
    "ready_for_extraction",
    "ready_for_connector",
    "initial_authentication_prompt_count",
    "mid_run_user_intervention_count",
    "runtime_instance_id",
    "autonomous_since_at",
    "last_user_intervention_at",
)

# Names that must never appear in a published payload.
FORBIDDEN_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "cookie",
        "cookies",
        "token",
        "tokens",
        "credential",
        "credentials",
        "password",
        "passwd",
        "secret",
        "authorization",
        "api_key",
        "session_storage",
        "local_storage",
        "browser_storage",
        "raw_response",
        "raw_provider_response",
        "account_number",
        "full_account_number",
        "card_number",
        "page_content",
        "html",
        "dom",
    }
)


def ensure_runtime_instance_id(root: Path | None = None) -> str:
    """Return a stable local runtime instance id, creating one if needed."""
    base = Path(root or Path.home() / ".mighty" / "provider_runtime").expanduser()
    base.mkdir(parents=True, exist_ok=True)
    path = base / "instance_id"
    if path.is_file():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    instance_id = str(uuid.uuid4())
    path.write_text(instance_id + "\n", encoding="utf-8")
    return instance_id


def serialize_access_state(
    state: AccessState,
    *,
    runtime_instance_id: str,
    schema_version: int = SCHEMA_VERSION,
    published_at: str | None = None,
) -> dict[str, Any]:
    """Build the versioned AccessState publication payload (no secrets)."""
    initial = int(getattr(state, "initial_authentication_prompt_count", 0) or 0)
    interruptions = int(state.user_interruption_count or 0)
    mid_run = max(0, interruptions - initial)
    payload = {
        "schema_version": int(schema_version),
        "provider": str(state.provider),
        "authentication_state": str(state.authentication_state),
        "access_health": str(state.access_health),
        "runtime_state": str(state.runtime_status),
        "browser_state": str(state.browser_status),
        "recovery_state": str(state.recovery_planner_status),
        "recovery_attempts": int(state.recovery_attempt_count),
        "recovery_successes": int(state.recovery_success_count),
        "recovery_failures": int(state.recovery_failure_count),
        "last_recovery_action": state.last_recovery_action,
        "last_recovery_result": state.last_recovery_result,
        "escalation_reason": state.escalation_reason,
        "runtime_started_at": state.runtime_started_at,
        "authenticated_session_started_at": state.authenticated_session_started_at,
        "autonomous_since_at": state.autonomous_since_at,
        "authentication_state_changed_at": state.authentication_state_changed_at,
        "last_user_intervention_at": state.last_user_intervention_at,
        "last_verified_at": state.last_verification_at,
        "last_verification_result": state.last_verification_result,
        "last_keepalive_at": state.last_keepalive_at,
        "last_keepalive_result": state.last_keepalive_result,
        "ready_for_extraction": bool(state.ready_for_extraction),
        "ready_for_connector": bool(state.ready_for_connector),
        "initial_authentication_prompt_count": initial,
        "mid_run_user_intervention_count": mid_run,
        "updated_at": str(state.updated_at or iso_now()),
        "runtime_instance_id": str(runtime_instance_id),
        "published_at": published_at or iso_now(),
    }
    assert_no_sensitive_fields(payload)
    return payload


def material_fingerprint(payload: dict[str, Any]) -> tuple[Any, ...]:
    """Stable fingerprint of fields that warrant an immediate publish."""
    return tuple(payload.get(key) for key in MATERIAL_FIELDS)


def assert_no_sensitive_fields(payload: dict[str, Any]) -> None:
    """Raise if a forbidden sensitive key appears anywhere in the payload."""

    def _walk(node: Any, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower()
                next_path = f"{path}.{key}" if path else str(key)
                if key_l in FORBIDDEN_SENSITIVE_KEYS:
                    raise ValueError(f"sensitive field forbidden in AccessState payload: {next_path}")
                # Substring guards for compound names (avoid matching authentication_state).
                for part in ("cookie", "password", "credential", "passwd"):
                    if part in key_l:
                        raise ValueError(
                            f"sensitive field forbidden in AccessState payload: {next_path}"
                        )
                if key_l == "token" or key_l.endswith("_token") or key_l.startswith("token_"):
                    raise ValueError(
                        f"sensitive field forbidden in AccessState payload: {next_path}"
                    )
                _walk(value, next_path)
        elif isinstance(node, (list, tuple)):
            for idx, value in enumerate(node):
                _walk(value, f"{path}[{idx}]")

    _walk(payload)


def default_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST JSON with X-Mighty-Key. Used by the local publisher."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Mighty-Key": api_key,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {"ok": True, "raw": data}
            data.setdefault("ok", True)
            data["http_status"] = getattr(response, "status", 200)
            return data
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"error": raw or str(exc)}
        if not isinstance(data, dict):
            data = {"error": str(data)}
        data["ok"] = False
        data["http_status"] = int(exc.code)
        return data
    except Exception as exc:  # noqa: BLE001 - publisher must never raise to supervisor
        return {"ok": False, "error": str(exc), "http_status": 0}


@dataclass
class PublishAttempt:
    ok: bool
    reason: str
    payload: dict[str, Any] | None = None
    response: dict[str, Any] | None = None


class AccessStatePublisher:
    """Non-blocking AccessState publisher with material-change + heartbeat."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        runtime_instance_id: str,
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        initial_backoff_seconds: float = DEFAULT_INITIAL_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_MAX_BACKOFF_SECONDS,
        post_fn: Callable[..., dict[str, Any]] | None = None,
        sleep_fn: Callable[[float], Any] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        now_fn: Callable[[], str] | None = None,
        enabled: bool = True,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.runtime_instance_id = str(runtime_instance_id)
        self.heartbeat_seconds = max(5.0, float(heartbeat_seconds))
        self.initial_backoff_seconds = max(0.1, float(initial_backoff_seconds))
        self.max_backoff_seconds = max(self.initial_backoff_seconds, float(max_backoff_seconds))
        self._post = post_fn or default_post_json
        self._sleep = sleep_fn or time.sleep
        self._monotonic = monotonic_fn or time.monotonic
        self._now = now_fn or iso_now
        self.enabled = bool(enabled and self.api_key)
        self._lock = threading.RLock()
        self._pending: dict[str, Any] | None = None
        self._last_sent_fingerprint: tuple[Any, ...] | None = None
        self._last_success_mono: float | None = None
        self._next_attempt_mono: float = 0.0
        self._backoff_seconds = self.initial_backoff_seconds
        self._worker: threading.Thread | None = None
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._last_error: str | None = None
        self._publish_count = 0
        self._failure_count = 0

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/runtime/access-state"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def publish_count(self) -> int:
        return self._publish_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def start(self) -> None:
        if not self.enabled:
            return
        self._stop.clear()
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._run_loop,
            name="mighty-access-state-publisher",
            daemon=True,
        )
        self._worker.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=float(join_timeout))

    def notify(self, state: AccessState) -> None:
        """Queue latest state for publish. Never blocks the supervisor."""
        if not self.enabled:
            return
        try:
            payload = serialize_access_state(
                state,
                runtime_instance_id=self.runtime_instance_id,
                published_at=self._now(),
            )
        except Exception as exc:  # noqa: BLE001
            self._last_error = f"serialize_failed: {exc}"
            return
        with self._lock:
            # Latest-only buffer — never an unbounded queue.
            self._pending = payload
        self._wake.set()

    def should_publish(
        self,
        payload: dict[str, Any],
        *,
        now_mono: float | None = None,
    ) -> tuple[bool, str]:
        """Return whether payload should be sent now (material change or heartbeat)."""
        fingerprint = material_fingerprint(payload)
        if self._last_sent_fingerprint is None or fingerprint != self._last_sent_fingerprint:
            return True, "material_change"
        current = self._monotonic() if now_mono is None else float(now_mono)
        if self._last_success_mono is None:
            return True, "heartbeat"
        age = current - self._last_success_mono
        if age >= self.heartbeat_seconds:
            return True, "heartbeat"
        return False, "suppressed"

    def flush_once(self) -> PublishAttempt:
        """Synchronously attempt one publish (tests / forced flush)."""
        with self._lock:
            payload = self._pending
        if payload is None:
            return PublishAttempt(ok=True, reason="empty")
        return self._attempt_publish(payload)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if self._stop.is_set():
                break
            with self._lock:
                payload = self._pending
                ready_at = self._next_attempt_mono
            if payload is None:
                continue
            now_mono = self._monotonic()
            if now_mono < ready_at:
                continue
            should, reason = self.should_publish(payload, now_mono=now_mono)
            if not should:
                # Drop duplicate pending once a successful fingerprint matches.
                with self._lock:
                    if self._pending is payload:
                        self._pending = None
                continue
            self._attempt_publish(payload, reason=reason)

    def _attempt_publish(
        self,
        payload: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> PublishAttempt:
        if reason is None:
            should, reason = self.should_publish(payload)
            if not should:
                with self._lock:
                    if self._pending is payload:
                        self._pending = None
                return PublishAttempt(ok=True, reason=reason, payload=payload)

        response = self._post(
            self.endpoint,
            payload,
            api_key=self.api_key,
            timeout=10.0,
        )
        ok = bool(response.get("ok")) and int(response.get("http_status") or 0) < 400
        # Out-of-order rejection is still a successful delivery acknowledgment.
        if response.get("accepted") is False and response.get("reason") == "out_of_order":
            ok = True
        if ok:
            with self._lock:
                self._last_sent_fingerprint = material_fingerprint(payload)
                self._last_success_mono = self._monotonic()
                self._backoff_seconds = self.initial_backoff_seconds
                self._next_attempt_mono = 0.0
                self._publish_count += 1
                self._last_error = None
                # Clear only if still the same pending object / same fingerprint.
                if self._pending is not None and material_fingerprint(self._pending) == material_fingerprint(
                    payload
                ):
                    self._pending = None
            return PublishAttempt(ok=True, reason=reason, payload=payload, response=response)

        with self._lock:
            self._failure_count += 1
            self._last_error = str(response.get("error") or response.get("http_status") or "publish_failed")
            delay = self._backoff_seconds
            self._backoff_seconds = min(self.max_backoff_seconds, max(self.initial_backoff_seconds, delay * 2))
            self._next_attempt_mono = self._monotonic() + delay
            # Preserve latest unsent state (pending remains).
        return PublishAttempt(ok=False, reason=reason or "publish_failed", payload=payload, response=response)


def resolve_publisher_config(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Resolve API key / base URL from args, env, or local config file."""
    config_path = Path(root or Path.home() / ".mighty" / "provider_runtime").expanduser()
    config_path = config_path / "railway_publish.json"
    file_cfg: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                file_cfg = loaded
        except Exception:
            file_cfg = {}
    key = (
        (api_key or "").strip()
        or str(os.environ.get("MIGHTY_API_KEY") or "").strip()
        or str(file_cfg.get("api_key") or "").strip()
    )
    url = (
        (base_url or "").strip()
        or str(os.environ.get("MIGHTY_BASE_URL") or "").strip()
        or str(file_cfg.get("base_url") or "").strip()
        or DEFAULT_BASE_URL
    )
    return {
        "api_key": key,
        "base_url": url.rstrip("/"),
        "enabled": bool(key),
        "config_path": str(config_path),
    }


def build_publisher_from_env(
    *,
    root: Path | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    post_fn: Callable[..., dict[str, Any]] | None = None,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
) -> AccessStatePublisher | None:
    """Construct a publisher when credentials are available; else None."""
    cfg = resolve_publisher_config(api_key=api_key, base_url=base_url, root=root)
    if not cfg["enabled"]:
        return None
    instance_id = ensure_runtime_instance_id(root)
    publisher = AccessStatePublisher(
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        runtime_instance_id=instance_id,
        heartbeat_seconds=heartbeat_seconds,
        post_fn=post_fn,
    )
    return publisher
