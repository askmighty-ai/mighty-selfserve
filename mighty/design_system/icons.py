"""
Mighty iconography — production stroke icons.

Contracts: docs/MIGHTY_ICONOGRAPHY.md
24×24 optical grid, 1.75px stroke, round joins. Decorative by default.
"""

from __future__ import annotations

from mighty.design_system._html import attrs, classes, esc

# Path data only — viewBox always 0 0 24 24
ICONS: dict[str, str] = {
    "check": "M5 12.5l4.2 4.2L19 7",
    "minus": "M6 12h12",
    "info": "M12 11.5v5M12 7.5h.01M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
    "mail": "M4 7.5h16v9H4zM4 7.5l8 6 8-6",
    "window": "M4 6.5h16v11H4zM4 10h16",
    "accounts": "M8 7h8M7 11h10M6 15h12M8 19h8",
    "activity": "M5 6.5h14M5 12h10M5 17.5h12",
    "plus": "M12 6v12M6 12h12",
    "close": "M7 7l10 10M17 7L7 17",
    "chevron-right": "M10 6l6 6-6 6",
    "warning": "M12 9v4.5M12 16.5h.01M11 4.8L3.8 18a1.2 1.2 0 0 0 1.05 1.8h14.3A1.2 1.2 0 0 0 20.2 18L13 4.8a1.2 1.2 0 0 0-2 0z",
    "horizon-points": "M4 14h16M7 14v0M12 14v0M17 14v0",
}


def render_icon(
    name: str,
    *,
    size: str = "md",
    decorative: bool = True,
    label: str | None = None,
    class_name: str = "",
) -> str:
    """Render an inline SVG icon.

    Decorative icons are aria-hidden. Meaningful icons require ``label``.
    """
    if name not in ICONS:
        raise ValueError(f"Unknown Mighty icon: {name!r}. Known: {sorted(ICONS)}")
    if not decorative and not label:
        raise ValueError(f"Meaningful icon {name!r} requires an accessible label")

    cls = classes("mds-icon", f"mds-icon--{size}" if size != "md" else None, class_name)
    a11y = {"aria-hidden": "true"} if decorative else {"role": "img", "aria-label": label}
    path = ICONS[name]
    # horizon-points uses circles as points — keep stroke path simple
    if name == "horizon-points":
        inner = (
            '<path d="M4 14h16"/>'
            '<circle cx="7" cy="14" r="1.2" fill="currentColor" stroke="none"/>'
            '<circle cx="12" cy="14" r="1.2" fill="currentColor" stroke="none"/>'
            '<circle cx="17" cy="14" r="1.2" fill="currentColor" stroke="none"/>'
        )
    else:
        inner = f'<path d="{esc(path)}"/>'

    return (
        f'<svg class="{esc(cls)}" viewBox="0 0 24 24" width="24" height="24"'
        f"{attrs(a11y)}>{inner}</svg>"
    )
