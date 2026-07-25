"""Small HTML helpers for design-system renderers."""

from __future__ import annotations

import html
from typing import Any, Mapping


def esc(value: Any) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def classes(*parts: str | None | bool) -> str:
    out: list[str] = []
    for part in parts:
        if not part or part is True:
            continue
        out.append(str(part))
    return " ".join(out)


def attrs(mapping: Mapping[str, Any] | None = None, **extra: Any) -> str:
    data = dict(mapping or {})
    data.update(extra)
    chunks: list[str] = []
    for key, value in data.items():
        if value is None or value is False:
            continue
        if value is True:
            chunks.append(esc(key))
            continue
        chunks.append(f'{esc(key)}="{esc(value)}"')
    return (" " + " ".join(chunks)) if chunks else ""
