"""Unit tests for Mighty design-system tokens."""

from pathlib import Path

from mighty.design_system.tokens import (
    COLORS,
    CONTROL_SIZES,
    FONTS,
    LAYOUT_WIDTHS,
    MOTION,
    RADII,
    SHADOWS,
    SPACING,
    TYPE_RAMP,
    all_token_names,
    css_var,
)

ROOT = Path(__file__).resolve().parents[1]
TOKENS_CSS = ROOT / "static" / "design-system" / "tokens.css"


def test_core_palette_matches_visual_system():
    assert COLORS["bg"].upper() == "#F3EEE6"
    assert COLORS["pine"].upper() == "#1F5C4F"
    assert COLORS["attention"].upper() == "#9B4A2E"
    assert COLORS["field"].upper() == "#243B36"


def test_spacing_scale_is_complete():
    assert SPACING == {
        "space-1": "4px",
        "space-2": "8px",
        "space-3": "12px",
        "space-4": "16px",
        "space-5": "24px",
        "space-6": "32px",
        "space-7": "48px",
        "space-8": "64px",
    }


def test_motion_tokens_honor_contract():
    assert MOTION["ease"] == "cubic-bezier(0.22, 1, 0.36, 1)"
    assert MOTION["duration-fast"] == "180ms"
    assert MOTION["duration-slow"] == "420ms"


def test_type_ramp_includes_display_and_ui_roles():
    for key in ("display-xl", "display-lg", "display-md", "body", "button", "label"):
        assert key in TYPE_RAMP


def test_css_var_helper():
    assert css_var("pine") == "var(--mds-pine)"


def test_tokens_css_declares_python_registry_keys():
    css = TOKENS_CSS.read_text(encoding="utf-8")
    for name in all_token_names():
        assert f"--mds-{name}" in css, f"missing CSS token --mds-{name}"


def test_fonts_are_not_generic_saas_defaults():
    assert "Fraunces" in FONTS["font-display"]
    assert "Plus Jakarta Sans" in FONTS["font-ui"]
    assert "Inter" not in FONTS["font-ui"]
    assert "Roboto" not in FONTS["font-ui"]


def test_layout_and_control_inventories_present():
    assert "width-app" in LAYOUT_WIDTHS
    assert "touch-min" in CONTROL_SIZES
    assert RADII["radius"] == "14px"
    assert "shadow-sm" in SHADOWS
