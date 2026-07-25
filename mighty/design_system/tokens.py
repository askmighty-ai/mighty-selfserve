"""
Mighty Design System — token registry.

Python mirror of static/design-system/tokens.css for tests and documentation
sync. Values match MIGHTY_VISUAL_SYSTEM_V1.md / MIGHTY_COMPONENT_LIBRARY.md.
"""

from __future__ import annotations

from typing import Final

# CSS custom property names (without the leading --)
TOKEN_PREFIX: Final = "mds"

COLORS: Final[dict[str, str]] = {
    "bg": "#F3EEE6",
    "bg-deep": "#E7E0D4",
    "surface": "#FFFCF7",
    "surface-soft": "#F7F1E8",
    "ink": "#1C1915",
    "ink-soft": "#3F3A33",
    "muted": "#6F675C",
    "line": "#E2D9CC",
    "line-strong": "#CFC4B4",
    "pine": "#1F5C4F",
    "pine-hover": "#184A40",
    "pine-soft": "#E3F0EC",
    "pine-ink": "#163E36",
    "success": "#2F6B45",
    "success-soft": "#E5F2E9",
    "waiting": "#9A6A1F",
    "waiting-soft": "#F7EDD8",
    "attention": "#9B4A2E",
    "attention-soft": "#F8E8E1",
    "danger": "#8F2F2F",
    "danger-soft": "#F7E6E6",
    "focus": "rgba(31, 92, 79, 0.35)",
    "field": "#243B36",
    "field-mid": "#2F5A4E",
    "field-glow": "rgba(232, 214, 176, 0.22)",
}

SPACING: Final[dict[str, str]] = {
    "space-1": "4px",
    "space-2": "8px",
    "space-3": "12px",
    "space-4": "16px",
    "space-5": "24px",
    "space-6": "32px",
    "space-7": "48px",
    "space-8": "64px",
}

RADII: Final[dict[str, str]] = {
    "radius-xs": "10px",
    "radius-sm": "12px",
    "radius": "14px",
    "radius-lg": "22px",
    "radius-pill": "999px",
    "radius-monogram": "11px",
    "radius-brand": "8px",
}

SHADOWS: Final[dict[str, str]] = {
    "shadow-sm": "0 1px 2px rgba(28, 25, 21, 0.04), 0 10px 28px rgba(28, 25, 21, 0.05)",
    "shadow-md": "0 2px 6px rgba(28, 25, 21, 0.05), 0 18px 40px rgba(28, 25, 21, 0.08)",
}

FONTS: Final[dict[str, str]] = {
    "font-display": '"Fraunces", Georgia, "Times New Roman", serif',
    "font-ui": '"Plus Jakarta Sans", "Segoe UI", system-ui, sans-serif',
}

TYPE_RAMP: Final[dict[str, dict[str, str]]] = {
    "display-xl": {"size": "clamp(2.75rem, 5vw, 3.9rem)", "weight": "600", "line-height": "1.08"},
    "display-lg": {"size": "clamp(2.1rem, 3.4vw, 2.85rem)", "weight": "600", "line-height": "1.12"},
    "display-md": {"size": "clamp(1.7rem, 2.5vw, 2.2rem)", "weight": "600", "line-height": "1.15"},
    "title": {"size": "1.75rem", "weight": "600", "line-height": "1.2"},
    "heading": {"size": "1.1rem", "weight": "650", "line-height": "1.3"},
    "body": {"size": "1.0625rem", "weight": "400", "line-height": "1.55"},
    "body-sm": {"size": "0.95rem", "weight": "400", "line-height": "1.5"},
    "label": {"size": "0.88rem", "weight": "600", "line-height": "1.35"},
    "meta": {"size": "0.84rem", "weight": "500", "line-height": "1.4"},
    "button": {"size": "0.97rem", "weight": "600", "line-height": "1"},
}

MOTION: Final[dict[str, str]] = {
    "ease": "cubic-bezier(0.22, 1, 0.36, 1)",
    "duration-fast": "180ms",
    "duration": "280ms",
    "duration-slow": "420ms",
    "duration-ambient": "5500ms",
}

LAYOUT_WIDTHS: Final[dict[str, str]] = {
    "width-marketing": "1080px",
    "width-onboarding": "720px",
    "width-onboarding-narrow": "520px",
    "width-app": "1120px",
    "width-lede": "38rem",
    "width-modal": "560px",
    "width-toast": "360px",
}

CONTROL_SIZES: Final[dict[str, str]] = {
    "control-height-sm": "38px",
    "control-height": "46px",
    "control-height-lg": "52px",
    "touch-min": "44px",
    "icon-size": "24px",
    "icon-size-sm": "16px",
    "monogram-size": "40px",
}


def css_var(name: str) -> str:
    """Return a CSS custom property reference, e.g. var(--mds-pine)."""
    return f"var(--{TOKEN_PREFIX}-{name})"


def all_token_names() -> list[str]:
    """Flatten token keys for inventory / sync checks."""
    names: list[str] = []
    for group in (
        COLORS,
        SPACING,
        RADII,
        SHADOWS,
        FONTS,
        MOTION,
        LAYOUT_WIDTHS,
        CONTROL_SIZES,
    ):
        names.extend(group.keys())
    return sorted(names)
