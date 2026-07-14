"""Customer-facing local-time presentation for the Truth Dashboard.

Server emits machine-readable UTC ISO timestamps; the browser converts to the
user's local timezone. No server-side timezone guessing.
"""

from __future__ import annotations

import html
from typing import Any

from mighty.admin_local_time import (
    parse_admin_timestamp,
    to_utc_iso_z,
)

CUSTOMER_LOCAL_TIME_CLASS = "mighty-customer-local-time"


def _he(value: Any) -> str:
    return html.escape(str(value), quote=True)


def format_customer_local_time(value: Any, *, empty: str = "—") -> str:
    """Return a <time> element for browser-side local timezone formatting.

    Visible fallback is the canonical UTC ISO string (or ``empty`` for null).
    datetime/title preserve machine-readable UTC for tooltips and sorting attrs.
    """
    if value is None or value == "":
        return empty

    original = (
        value.isoformat()
        if hasattr(value, "isoformat") and not isinstance(value, (str, bytes))
        else str(value).strip()
    )
    if isinstance(value, (int, float)):
        original = str(value)

    dt = parse_admin_timestamp(value)
    if dt is None:
        return _he(original) if original else empty

    iso_z = to_utc_iso_z(dt)
    return (
        f'<time class="{CUSTOMER_LOCAL_TIME_CLASS}" datetime="{_he(iso_z)}" '
        f'title="UTC: {_he(iso_z)}">{_he(iso_z)}</time>'
    )


def customer_local_time_script_tag() -> str:
    """Script tag to load the shared customer local-time enhancer."""
    return '<script src="/static/customer_local_time.js" defer></script>'
