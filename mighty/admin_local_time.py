"""Shared admin presentation helper for browser-local timestamps.

Server emits machine-readable UTC metadata; the browser converts to the
user's local timezone. No server-side timezone guessing.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

LOCAL_TIME_CLASS = "mighty-local-time"
TIMEZONE_NOTE = (
    '<p class="muted mighty-tz-note">'
    "Times are shown in your browser’s local timezone."
    "</p>"
)

_SPACE_UTC_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:\s*UTC)?$"
)


def _he(value: Any) -> str:
    return html.escape(str(value), quote=True)


def parse_admin_timestamp(value: Any) -> datetime | None:
    """Parse common admin timestamp shapes into an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text or text == "—":
        return None
    # Epoch as string
    if re.fullmatch(r"\d+(\.\d+)?", text):
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    # "YYYY-MM-DD HH:MM:SS UTC" / naive ISO without offset
    m = _SPACE_UTC_RE.match(text)
    if m:
        try:
            return datetime(
                int(m.group(1)[0:4]),
                int(m.group(1)[5:7]),
                int(m.group(1)[8:10]),
                int(m.group(2)[0:2]),
                int(m.group(2)[3:5]),
                int(m.group(2)[6:8]),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    try:
        normalized = text.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_utc_iso_z(dt: datetime) -> str:
    """Format an aware datetime as UTC ISO-8601 ending in Z."""
    utc = dt.astimezone(timezone.utc)
    # Keep microseconds only when present for fidelity; strip otherwise.
    if utc.microsecond:
        return utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_admin_local_time(value: Any) -> str:
    """Return a <time class="mighty-local-time"> element for client enhancement.

    Visible fallback text is the original UTC value (or em dash for null).
    datetime/title attributes preserve machine-readable UTC for inspection.
    """
    if value is None or value == "":
        return "—"

    original = str(value).strip() if not isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (int, float)):
        original = str(value)

    dt = parse_admin_timestamp(value)
    if dt is None:
        # Invalid / unparseable — show original without crashing.
        return _he(original) if original else "—"

    iso_z = to_utc_iso_z(dt)
    return (
        f'<time class="{LOCAL_TIME_CLASS}" datetime="{_he(iso_z)}" '
        f'title="UTC: {_he(iso_z)}">{_he(iso_z)}</time>'
    )


def timezone_note_html() -> str:
    """Small note for diagnostic pages that show timestamps."""
    return TIMEZONE_NOTE
