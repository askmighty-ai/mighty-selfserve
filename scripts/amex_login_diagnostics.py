"""Sanitized Playwright diagnostics for the Amex persistent-browser spike.

Never records request bodies, cookies, authorization headers, credentials, or
full query strings. The purpose is to determine why a user-initiated login does
not navigate in a dedicated Chrome profile.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

AMEX_HOST_TOKENS = ("americanexpress.com", "aexp.com")
LOGIN_RELEVANT_TOKENS = (
    "login",
    "logon",
    "signin",
    "authenticate",
    "authentication",
    "session",
    "identity",
    "oauth",
    "sso",
    "mfa",
    "challenge",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_url(raw_url: str | None) -> str | None:
    if not raw_url:
        return None
    try:
        parts = urlsplit(raw_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except Exception:
        return str(raw_url).split("?", 1)[0].split("#", 1)[0]


def _is_relevant_url(raw_url: str | None) -> bool:
    lowered = str(raw_url or "").lower()
    return any(host in lowered for host in AMEX_HOST_TOKENS) and any(
        token in lowered for token in LOGIN_RELEVANT_TOKENS
    )


class AmexLoginDiagnostics:
    """Collect bounded, sanitized browser events during interactive login."""

    MAX_EVENTS = 300

    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self.started_at = _iso_now()
        self.events: list[dict[str, Any]] = []
        self._page: Any = None

    def _record(self, event_type: str, **fields: Any) -> None:
        if len(self.events) >= self.MAX_EVENTS:
            return
        safe = {"at": _iso_now(), "type": event_type}
        for key, value in fields.items():
            if value is None:
                continue
            if key in {"url", "frame_url"}:
                safe[key] = _sanitize_url(str(value))
            else:
                safe[key] = value
        self.events.append(safe)
        self.flush()

    def attach(self, page: Any) -> None:
        self._page = page
        self._record("diagnostics_started", url=getattr(page, "url", None))

        page.on(
            "console",
            lambda message: self._record(
                "console",
                level=getattr(message, "type", None),
                text=str(getattr(message, "text", ""))[:500],
            ),
        )
        page.on(
            "pageerror",
            lambda error: self._record(
                "page_error",
                message=str(error)[:500],
            ),
        )
        page.on(
            "framenavigated",
            lambda frame: self._record(
                "frame_navigated",
                frame_url=getattr(frame, "url", None),
                is_main_frame=frame == page.main_frame,
            ),
        )
        page.on(
            "requestfailed",
            lambda request: self._record(
                "request_failed",
                url=request.url,
                method=request.method,
                resource_type=request.resource_type,
                failure=str(request.failure or "")[:300],
            ) if _is_relevant_url(request.url) else None,
        )
        page.on(
            "response",
            lambda response: self._record(
                "response",
                url=response.url,
                status=response.status,
                method=response.request.method,
                resource_type=response.request.resource_type,
            ) if _is_relevant_url(response.url) else None,
        )
        page.on(
            "dialog",
            lambda dialog: self._record(
                "dialog",
                dialog_type=dialog.type,
                message=str(dialog.message)[:300],
            ),
        )

    def record_login_controls(self) -> None:
        page = self._page
        if page is None:
            return
        try:
            details = page.evaluate(
                """
                () => {
                  const button = [...document.querySelectorAll('button,input[type="submit"]')]
                    .find((el) => /log\s*in|sign\s*in/i.test(el.innerText || el.value || ''));
                  const form = button && button.closest('form');
                  return {
                    button_found: !!button,
                    button_disabled: !!(button && button.disabled),
                    button_type: button && (button.type || button.tagName),
                    form_found: !!form,
                    form_action: form && form.action ? new URL(form.action, location.href).origin + new URL(form.action, location.href).pathname : null,
                    form_method: form && form.method,
                    document_ready_state: document.readyState,
                    current_url: location.origin + location.pathname,
                  };
                }
                """
            )
            self._record("login_controls", **details)
        except Exception as exc:
            self._record("login_controls_error", message=str(exc)[:300])

    def record_snapshot(self, label: str) -> None:
        page = self._page
        if page is None:
            return
        try:
            snapshot = page.evaluate(
                """
                () => ({
                  url: location.origin + location.pathname,
                  title: document.title,
                  ready_state: document.readyState,
                  body_text_length: (document.body && document.body.innerText || '').length,
                  password_input_count: document.querySelectorAll('input[type="password"]').length,
                  submit_control_count: document.querySelectorAll('button,input[type="submit"]').length,
                  visible_error_text: [...document.querySelectorAll('[role="alert"],.error,[class*="error" i]')]
                    .map((el) => (el.innerText || '').trim())
                    .filter(Boolean)
                    .slice(0, 5)
                    .join(' | ')
                    .slice(0, 500),
                })
                """
            )
            self._record("snapshot", label=label, **snapshot)
        except Exception as exc:
            self._record("snapshot_error", label=label, message=str(exc)[:300])

    def flush(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at": self.started_at,
            "updated_at": _iso_now(),
            "event_count": len(self.events),
            "events": self.events,
        }
        self.output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
