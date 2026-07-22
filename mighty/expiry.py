"""Shared expiry-day parsing (provider-independent).

Extracted for Value Intelligence and Action Center reuse. Deterministic when
``today`` is supplied.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_MM_MAP = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _as_date(today: date | datetime | None) -> date:
    if today is None:
        return date.today()
    if isinstance(today, datetime):
        return today.date()
    return today


def parse_expiry_days(
    label: Any,
    value: Any,
    *,
    today: date | datetime | None = None,
) -> int | None:
    """Return days until expiry from a label+value string, or None."""
    combined = f"{label or ''} {value or ''}"
    ref = _as_date(today)

    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", combined)
    if m:
        try:
            mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if yr < 100:
                yr += 2000
            return (date(yr, mo, da) - ref).days
        except Exception:
            pass

    m3 = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{1,2}),?\s+(\d{4})",
        combined,
        re.I,
    )
    if m3:
        try:
            mo = _MM_MAP[m3.group(1)[:3].lower()]
            da = int(m3.group(2))
            yr = int(m3.group(3))
            return (date(yr, mo, da) - ref).days
        except Exception:
            pass

    m2 = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})",
        combined,
        re.I,
    )
    if m2:
        try:
            mo = _MM_MAP[m2.group(1)[:3].lower()]
            yr = int(m2.group(2))
            return (date(yr, mo, 1) - ref).days
        except Exception:
            pass
    return None


def exp_date_iso(days_left: int | None, *, today: date | datetime | None = None) -> str | None:
    if days_left is None or days_left < 0:
        return None
    ref = _as_date(today)
    from datetime import timedelta

    return (ref + timedelta(days=days_left)).isoformat()
