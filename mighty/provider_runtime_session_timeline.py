"""Provider-agnostic session timeline recording and offline analysis.

Writes newline-delimited JSON events for authenticated browser sessions and
renders ``session-timeline.md``. Never records cookies, tokens, headers,
response bodies, or credentials — only metadata.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

SESSION_TIMELINE_JSONL = "session-timeline.jsonl"
SESSION_TIMELINE_MD = "session-timeline.md"
SESSION_TIMELINE_ANALYSIS_JSON = "session-timeline-analysis.json"

SESSION_TIMELINE_EVENT_TYPES = frozenset(
    {
        "runtime_started",
        "browser_started",
        "browser_reused",
        "authentication_required",
        "authentication_verified",
        "auth_state_changed",
        "keepalive_scheduled",
        "keepalive_started",
        "keepalive_completed",
        "keepalive_failed",
        "verification_started",
        "verification_completed",
        "navigation",
        "redirect",
        "page_reload",
        "http_401",
        "http_403",
        "cookie_added",
        "cookie_removed",
        "logout_detected",
        "campaign_completed",
    }
)

# Keys / substrings that must never appear in recorded payloads.
TIMELINE_SENSITIVE_KEY_TOKENS = (
    "cookie",
    "cookies",
    "authorization",
    "password",
    "credential",
    "credentials",
    "secret",
    "token",
    "header",
    "headers",
    "body",
    "html",
    "query",
    "set-cookie",
    "set_cookie",
)

# Explicit allowlist for cookie *metadata* events (never values).
COOKIE_METADATA_KEYS = frozenset(
    {
        "name",
        "domain",
        "path",
        "expires",
        "secure",
        "http_only",
        "httpOnly",
        "same_site",
        "sameSite",
        "cookie_count",
        "change",
    }
)

PAYLOAD_STRING_MAX_CHARS = 240


def iso_now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_timestamp(value: Any) -> datetime | None:
    if value is None:
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
    return parsed.astimezone(timezone.utc)


def sanitize_timeline_url(raw_url: str | None) -> str | None:
    """Return scheme/host/path only (strip query and fragment)."""
    if raw_url is None:
        return None
    text = str(raw_url).strip()
    if not text:
        return None
    try:
        parts = urlsplit(text)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return text.split("?", 1)[0].split("#", 1)[0][:PAYLOAD_STRING_MAX_CHARS]


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in TIMELINE_SENSITIVE_KEY_TOKENS)


def sanitize_timeline_payload(
    payload: dict[str, Any] | None,
    *,
    event_type: str | None = None,
) -> dict[str, Any]:
    """Return a metadata-only payload with secrets stripped."""
    if not payload:
        return {}
    if not isinstance(payload, dict):
        return {}

    cleaned: dict[str, Any] = {}
    cookie_event = event_type in {"cookie_added", "cookie_removed"}

    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if cookie_event:
            if key not in COOKIE_METADATA_KEYS:
                continue
            # Never allow cookie value even if misnamed.
            if key.lower() in {"value", "cookie", "cookies"}:
                continue
        elif _key_is_sensitive(key):
            continue

        cleaned[key] = _sanitize_payload_value(key, value)

    return cleaned


def _sanitize_payload_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if "url" in lowered or lowered in {"from", "to", "target", "path"}:
            return sanitize_timeline_url(value)
        if len(value) > PAYLOAD_STRING_MAX_CHARS:
            return value[:PAYLOAD_STRING_MAX_CHARS]
        return value
    if isinstance(value, list):
        out: list[Any] = []
        for item in value[:50]:
            if isinstance(item, dict):
                out.append(sanitize_timeline_payload(item))
            elif isinstance(item, str):
                out.append(item[:PAYLOAD_STRING_MAX_CHARS])
            elif isinstance(item, (bool, int, float)) or item is None:
                out.append(item)
        return out
    if isinstance(value, dict):
        return sanitize_timeline_payload(value)
    return str(value)[:PAYLOAD_STRING_MAX_CHARS]


def cookie_metadata_from_playwright(cookie: dict[str, Any]) -> dict[str, Any]:
    """Extract non-secret cookie metadata from a Playwright cookie dict."""
    return sanitize_timeline_payload(
        {
            "name": cookie.get("name"),
            "domain": cookie.get("domain"),
            "path": cookie.get("path"),
            "expires": cookie.get("expires"),
            "secure": cookie.get("secure"),
            "http_only": cookie.get("httpOnly", cookie.get("http_only")),
            "same_site": cookie.get("sameSite", cookie.get("same_site")),
        },
        event_type="cookie_added",
    )


def diff_cookie_metadata(
    previous: list[dict[str, Any]] | None,
    current: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (added, removed) cookie metadata entries by name+domain+path."""

    def _key(item: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(item.get("name") or ""),
            str(item.get("domain") or ""),
            str(item.get("path") or ""),
        )

    prev_map = {_key(item): item for item in (previous or []) if isinstance(item, dict)}
    curr_map = {_key(item): item for item in (current or []) if isinstance(item, dict)}
    added = [curr_map[key] for key in curr_map.keys() - prev_map.keys()]
    removed = [prev_map[key] for key in prev_map.keys() - curr_map.keys()]
    return added, removed


class SessionTimelineRecorder:
    """Append-only NDJSON recorder for provider session timeline events."""

    def __init__(
        self,
        *,
        path: Path | str,
        session_id: str | None = None,
        provider: str = "unknown",
        started_at: str | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.session_id = session_id or str(uuid.uuid4())
        self.provider = str(provider or "unknown")
        self.clock = clock or iso_now_utc
        self.started_at = started_at or self.clock()
        self._lock = threading.RLock()
        self._started_dt = parse_iso_timestamp(self.started_at) or datetime.now(
            timezone.utc
        )
        self._last_auth_state: str | None = None
        self._cookie_snapshot: list[dict[str, Any]] = []
        self.event_count = 0

    def elapsed_seconds(self, at: str | None = None) -> float:
        stamp = parse_iso_timestamp(at) if at else datetime.now(timezone.utc)
        if stamp is None:
            stamp = datetime.now(timezone.utc)
        return max(0.0, (stamp - self._started_dt).total_seconds())

    def record(
        self,
        event_type: str,
        *,
        provider: str | None = None,
        payload: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        """Append one sanitized timeline event. Returns the written event."""
        event_type = str(event_type or "").strip()
        if event_type not in SESSION_TIMELINE_EVENT_TYPES:
            raise ValueError(
                f"Unsupported session timeline event_type {event_type!r}. "
                f"Expected one of {', '.join(sorted(SESSION_TIMELINE_EVENT_TYPES))}"
            )

        stamp = timestamp or self.clock()
        event = {
            "timestamp": stamp,
            "elapsed_seconds": round(self.elapsed_seconds(stamp), 3),
            "provider": str(provider or self.provider),
            "session_id": self.session_id,
            "event_type": event_type,
            "payload": sanitize_timeline_payload(payload, event_type=event_type),
        }

        line = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            self.event_count += 1

        return event

    @property
    def last_auth_state(self) -> str | None:
        return self._last_auth_state

    def note_auth_transition(
        self,
        authentication_state: str | None,
        *,
        provider: str | None = None,
        source: str | None = None,
        reason: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Record auth_state_changed plus verified/required/logout as needed."""
        state = None if authentication_state is None else str(authentication_state)
        emitted: list[dict[str, Any]] = []
        base = {
            "authentication_state": state,
            "source": source,
            "reason": reason,
        }
        if extra:
            base.update(extra)

        previous = self._last_auth_state
        if previous is not None and state is not None and previous != state:
            emitted.append(
                self.record(
                    "auth_state_changed",
                    provider=provider,
                    payload={
                        **base,
                        "previous_authentication_state": previous,
                    },
                )
            )

        if state == "SIGNED_IN":
            emitted.append(
                self.record(
                    "authentication_verified",
                    provider=provider,
                    payload=base,
                )
            )
        elif state == "SIGNED_OUT":
            if previous == "SIGNED_IN":
                emitted.append(
                    self.record(
                        "logout_detected",
                        provider=provider,
                        payload=base,
                    )
                )
            else:
                emitted.append(
                    self.record(
                        "authentication_required",
                        provider=provider,
                        payload=base,
                    )
                )
        elif state == "LOGIN_UNKNOWN" and previous != "SIGNED_IN":
            emitted.append(
                self.record(
                    "authentication_required",
                    provider=provider,
                    payload=base,
                )
            )

        if state is not None:
            self._last_auth_state = state
        return emitted

    def observe_cookies(
        self,
        cookies: list[dict[str, Any]] | None,
        *,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        """Diff cookie *metadata* and emit cookie_added / cookie_removed."""
        current = [
            cookie_metadata_from_playwright(item)
            for item in (cookies or [])
            if isinstance(item, dict) and item.get("name")
        ]
        with self._lock:
            previous = list(self._cookie_snapshot)
            added, removed = diff_cookie_metadata(previous, current)
            self._cookie_snapshot = current

        emitted: list[dict[str, Any]] = []
        for item in removed:
            emitted.append(
                self.record(
                    "cookie_removed",
                    provider=provider,
                    payload={**item, "change": "removed", "cookie_count": len(current)},
                )
            )
        for item in added:
            emitted.append(
                self.record(
                    "cookie_added",
                    provider=provider,
                    payload={**item, "change": "added", "cookie_count": len(current)},
                )
            )
        return emitted

    def record_http_status(
        self,
        status: int | None,
        *,
        provider: str | None = None,
        url: str | None = None,
        method: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any] | None:
        """Record http_401 / http_403 when status matches; otherwise None."""
        if status == 401:
            event_type = "http_401"
        elif status == 403:
            event_type = "http_403"
        else:
            return None
        return self.record(
            event_type,
            provider=provider,
            payload={
                "status": int(status),
                "url": sanitize_timeline_url(url),
                "method": method,
                "source": source,
            },
        )


def load_session_timeline(path: Path | str) -> list[dict[str, Any]]:
    """Load NDJSON timeline events from disk (skips corrupt lines)."""
    path = Path(path)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("event_type"):
            events.append(payload)
    return events


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _find_last_event(
    events: list[dict[str, Any]], event_type: str
) -> dict[str, Any] | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            return event
    return None


def _logout_detection_sequence(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    logout_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("event_type") == "logout_detected"
    ]
    if not logout_indexes:
        return []
    index = logout_indexes[0]
    start = max(0, index - 5)
    end = min(len(events), index + 3)
    sequence = []
    for event in events[start:end]:
        sequence.append(
            {
                "timestamp": event.get("timestamp"),
                "elapsed_seconds": event.get("elapsed_seconds"),
                "event_type": event.get("event_type"),
                "authentication_state": _event_payload(event).get(
                    "authentication_state"
                ),
                "reason": _event_payload(event).get("reason"),
                "source": _event_payload(event).get("source"),
            }
        )
    return sequence


def infer_expiration_mechanism(
    events: list[dict[str, Any]],
) -> tuple[str, str, str]:
    """Return (mechanism, confidence, rationale)."""
    if not events:
        return "unknown", "low", "No timeline events were available."

    logout_indexes = [
        index
        for index, event in enumerate(events)
        if event.get("event_type") == "logout_detected"
    ]
    keepalive_completed = [
        event for event in events if event.get("event_type") == "keepalive_completed"
    ]
    keepalive_failed = [
        event for event in events if event.get("event_type") == "keepalive_failed"
    ]

    if not logout_indexes:
        if keepalive_completed and not keepalive_failed:
            return (
                "no_expiration_observed",
                "medium",
                "Keepalive completed successfully and no logout_detected event was recorded.",
            )
        if any(event.get("event_type") == "campaign_completed" for event in events):
            return (
                "campaign_completed_without_logout",
                "medium",
                "Campaign completed without a logout_detected event on this timeline.",
            )
        return (
            "unknown",
            "low",
            "Session ended (or is ongoing) without a recorded logout_detected event.",
        )

    index = logout_indexes[0]
    window = events[max(0, index - 12) : index]
    window_types = {event.get("event_type") for event in window}

    if "http_401" in window_types or "http_403" in window_types:
        return (
            "auth_denied",
            "high",
            "HTTP 401/403 was observed shortly before logout_detected.",
        )
    if "cookie_removed" in window_types:
        return (
            "cookie_cleared",
            "medium",
            "Cookie metadata removal was observed shortly before logout_detected.",
        )
    if "keepalive_failed" in window_types:
        return (
            "keepalive_failure",
            "medium",
            "keepalive_failed preceded logout_detected.",
        )
    if "page_reload" in window_types or "navigation" in window_types:
        return (
            "navigation_triggered_logout",
            "medium",
            "Navigation or reload preceded logout_detected.",
        )
    if keepalive_completed:
        last_success = keepalive_completed[-1]
        logout_event = events[index]
        success_elapsed = last_success.get("elapsed_seconds")
        logout_elapsed = logout_event.get("elapsed_seconds")
        if (
            isinstance(success_elapsed, (int, float))
            and isinstance(logout_elapsed, (int, float))
            and logout_elapsed >= success_elapsed
        ):
            return (
                "idle_timeout_after_keepalive",
                "medium",
                "Logout occurred after at least one successful keepalive "
                "(likely idle/absolute timeout).",
            )
    return (
        "idle_timeout",
        "medium",
        "Logout detected without a nearby auth-denial or cookie-clear signal.",
    )


def analyze_session_timeline(
    source: Path | str | list[dict[str, Any]],
) -> dict[str, Any]:
    """Analyze a session timeline JSONL path or in-memory event list."""
    if isinstance(source, list):
        events = list(source)
        source_path = None
    else:
        source_path = Path(source)
        events = load_session_timeline(source_path)

    events = sorted(
        events,
        key=lambda item: (
            float(item.get("elapsed_seconds") or 0.0),
            str(item.get("timestamp") or ""),
        ),
    )

    first = events[0] if events else None
    last = events[-1] if events else None
    first_ts = first.get("timestamp") if first else None
    last_ts = last.get("timestamp") if last else None
    lifetime_seconds: float | None = None
    if first is not None and last is not None:
        start_dt = parse_iso_timestamp(first_ts)
        end_dt = parse_iso_timestamp(last_ts)
        if start_dt is not None and end_dt is not None:
            lifetime_seconds = max(0.0, (end_dt - start_dt).total_seconds())
        elif isinstance(last.get("elapsed_seconds"), (int, float)):
            lifetime_seconds = float(last["elapsed_seconds"])

    last_keepalive = _find_last_event(events, "keepalive_completed")
    logout_sequence = _logout_detection_sequence(events)
    mechanism, confidence, rationale = infer_expiration_mechanism(events)

    session_ids = sorted(
        {
            str(event.get("session_id"))
            for event in events
            if event.get("session_id")
        }
    )
    providers = sorted(
        {str(event.get("provider")) for event in events if event.get("provider")}
    )

    return {
        "source_path": str(source_path) if source_path is not None else None,
        "session_id": session_ids[0] if len(session_ids) == 1 else None,
        "session_ids": session_ids,
        "providers": providers,
        "event_count": len(events),
        "started_at": first_ts,
        "ended_at": last_ts,
        "session_lifetime_seconds": lifetime_seconds,
        "last_successful_keepalive": (
            {
                "timestamp": last_keepalive.get("timestamp"),
                "elapsed_seconds": last_keepalive.get("elapsed_seconds"),
                "payload": _event_payload(last_keepalive),
            }
            if last_keepalive
            else None
        ),
        "logout_detection_sequence": logout_sequence,
        "inferred_expiration_mechanism": mechanism,
        "confidence": confidence,
        "inference_rationale": rationale,
        "events": events,
    }


def render_session_timeline_markdown(analysis: dict[str, Any]) -> str:
    """Render ``session-timeline.md`` from an analysis dict."""
    events = analysis.get("events") or []
    lifetime = analysis.get("session_lifetime_seconds")
    lifetime_text = f"{lifetime:.1f}s" if isinstance(lifetime, (int, float)) else "n/a"
    last_keepalive = analysis.get("last_successful_keepalive")
    if last_keepalive:
        keepalive_text = (
            f"{last_keepalive.get('timestamp')} "
            f"(elapsed={last_keepalive.get('elapsed_seconds')}s)"
        )
    else:
        keepalive_text = "none"

    lines = [
        "# Session Timeline",
        "",
        "## Summary",
        "",
        f"- Session ID: `{analysis.get('session_id') or 'n/a'}`",
        f"- Providers: `{', '.join(analysis.get('providers') or []) or 'n/a'}`",
        f"- Event count: `{analysis.get('event_count')}`",
        f"- Started at: `{analysis.get('started_at') or 'n/a'}`",
        f"- Ended at: `{analysis.get('ended_at') or 'n/a'}`",
        f"- Session lifetime: `{lifetime_text}`",
        f"- Last successful keepalive: `{keepalive_text}`",
        f"- Inferred expiration mechanism: `{analysis.get('inferred_expiration_mechanism')}`",
        f"- Confidence: `{analysis.get('confidence')}`",
        f"- Rationale: {analysis.get('inference_rationale')}",
        "",
        "## Chronological events",
        "",
        "| Elapsed (s) | Timestamp (UTC) | Event | Auth state | Detail |",
        "| --- | --- | --- | --- | --- |",
    ]

    for event in events:
        payload = _event_payload(event)
        detail_parts: list[str] = []
        for key in (
            "strategy",
            "reason",
            "source",
            "status",
            "url",
            "name",
            "result",
            "campaign_status",
        ):
            value = payload.get(key)
            if value is not None and value != "":
                detail_parts.append(f"{key}={value}")
        if payload.get("previous_authentication_state"):
            detail_parts.append(
                f"from={payload.get('previous_authentication_state')}"
            )
        detail = ", ".join(detail_parts) if detail_parts else ""
        detail = detail.replace("|", "\\|")
        lines.append(
            "| "
            f"{event.get('elapsed_seconds')} | "
            f"{event.get('timestamp')} | "
            f"{event.get('event_type')} | "
            f"{payload.get('authentication_state') or ''} | "
            f"{detail} |"
        )

    lines.extend(["", "## Logout detection sequence", ""])
    sequence = analysis.get("logout_detection_sequence") or []
    if not sequence:
        lines.append("No `logout_detected` event was recorded.")
    else:
        lines.extend(
            [
                "| Elapsed (s) | Timestamp (UTC) | Event | Auth state | Reason |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in sequence:
            lines.append(
                "| "
                f"{item.get('elapsed_seconds')} | "
                f"{item.get('timestamp')} | "
                f"{item.get('event_type')} | "
                f"{item.get('authentication_state') or ''} | "
                f"{item.get('reason') or item.get('source') or ''} |"
            )

    lines.extend(
        [
            "",
            "## Expiration inference",
            "",
            f"- Mechanism: `{analysis.get('inferred_expiration_mechanism')}`",
            f"- Confidence: `{analysis.get('confidence')}`",
            f"- Rationale: {analysis.get('inference_rationale')}",
            "",
        ]
    )
    return "\n".join(lines)


def write_session_timeline_analysis(
    output_dir: Path | str,
    analysis: dict[str, Any] | None = None,
    *,
    timeline_path: Path | str | None = None,
) -> dict[str, str]:
    """Write ``session-timeline.md`` (+ analysis JSON) under ``output_dir``."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if analysis is None:
        if timeline_path is None:
            timeline_path = output_dir / SESSION_TIMELINE_JSONL
        analysis = analyze_session_timeline(timeline_path)

    # Avoid dumping the full event list twice into analysis JSON if huge;
    # keep events in MD only via the analysis used for rendering.
    md_path = output_dir / SESSION_TIMELINE_MD
    json_path = output_dir / SESSION_TIMELINE_ANALYSIS_JSON
    md_path.write_text(render_session_timeline_markdown(analysis), encoding="utf-8")

    serializable = {
        key: value
        for key, value in analysis.items()
        if key != "events"
    }
    serializable["event_types"] = [
        event.get("event_type") for event in (analysis.get("events") or [])
    ]
    json_path.write_text(json.dumps(serializable, indent=2) + "\n", encoding="utf-8")
    return {
        "markdown_path": str(md_path),
        "json_path": str(json_path),
    }
