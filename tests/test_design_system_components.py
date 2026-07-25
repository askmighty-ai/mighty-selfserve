"""Unit tests for Mighty design-system component renderers + a11y contracts."""

import pytest

from mighty.design_system import (
    ICONS,
    render_account_row,
    render_banner,
    render_button,
    render_card,
    render_checkbox,
    render_empty_state,
    render_hero,
    render_icon,
    render_modal,
    render_navigation,
    render_permission_card,
    render_progress_stepper,
    render_status_badge,
    render_switch,
    render_text_field,
    render_timeline,
    render_toast,
    render_trust_card,
)


def test_button_primary_is_button_element_by_default():
    html = render_button("Continue", variant="primary")
    assert html.startswith("<button")
    assert 'type="button"' in html
    assert "mds-btn--primary" in html
    assert "Continue" in html


def test_button_link_uses_anchor():
    html = render_button("Get started", href="/signup", variant="primary")
    assert html.startswith("<a")
    assert 'href="/signup"' in html


def test_button_loading_sets_aria_busy_and_keeps_label():
    html = render_button("Saving", loading=True)
    assert 'aria-busy="true"' in html
    assert "disabled" in html
    assert "Saving" in html
    assert "mds-btn__spinner" in html


def test_button_disabled_anchor_is_not_tabbable():
    html = render_button("Nope", href="/x", disabled=True)
    assert 'aria-disabled="true"' in html
    assert 'tabindex="-1"' in html


def test_button_rejects_unknown_variant():
    with pytest.raises(ValueError):
        render_button("X", variant="neon")


def test_status_badge_includes_text_not_color_only():
    html = render_status_badge("Needs Chrome", variant="waiting")
    assert "Needs Chrome" in html
    assert "mds-badge--waiting" in html
    assert "mds-badge__dot" in html


def test_trust_card_is_complementary_aside():
    html = render_trust_card("Nothing is connected yet.", "Access comes later.", variant="reassure")
    assert html.startswith("<aside")
    assert 'aria-label="Reassurance"' in html
    assert "mds-trust" in html


def test_permission_card_marks_limits_row():
    html = render_permission_card(
        [
            {"title": "Why", "body": "Discover accounts"},
            {"title": "What is not accessed", "body": "No sending mail", "limits": True},
        ],
        title="Connect Gmail",
    )
    assert "<ul" in html
    assert "mds-permission__row--limits" in html
    assert "What is not accessed" in html


def test_timeline_requires_kind_labels_and_time_element():
    html = render_timeline(
        [
            {
                "kind": "authorized",
                "title": "Gmail connected",
                "body": "You authorized discovery.",
                "time": "9:12 AM",
                "datetime": "2026-07-25T09:12:00",
            }
        ]
    )
    assert "You authorized" in html
    assert "<time" in html
    assert 'datetime="2026-07-25T09:12:00"' in html


def test_account_row_selectable_names_checkbox():
    html = render_account_row(
        name="American Express",
        monogram="AX",
        selectable=True,
        checkbox_name="watch",
        selected=True,
    )
    assert 'aria-label="Watch American Express"' in html
    assert "checked" in html
    assert "AX" in html


def test_empty_state_error_uses_alert_role():
    html = render_empty_state(
        title="Unavailable",
        body="Try again shortly.",
        variant="error",
    )
    assert 'role="alert"' in html


def test_empty_state_default_is_region_not_error():
    html = render_empty_state(
        title="Home stays quiet",
        body="Silence means success.",
        variant="first-use",
    )
    assert 'role="region"' in html


def test_modal_has_dialog_semantics():
    html = render_modal(
        title="Disconnect Gmail?",
        body="You can reconnect later.",
        actions_html=render_button("Disconnect Gmail", variant="destructive"),
        open=True,
        modal_id="disconnect",
    )
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="disconnect-title"' in html
    assert "hidden" not in html.split(">", 1)[0]


def test_progress_stepper_exposes_current_step():
    html = render_progress_stepper(
        [
            {"label": "Done step", "state": "done"},
            {"label": "Live step", "state": "live"},
            {"label": "Next", "state": "upcoming"},
        ],
        live=True,
    )
    assert 'aria-current="step"' in html
    assert 'aria-live="polite"' in html
    assert "is-live" in html


def test_navigation_marks_current_page():
    html = render_navigation(
        [
            {"label": "Home", "href": "/dashboard", "current": True},
            {"label": "Accounts", "href": "/credentials"},
        ],
        variant="app",
    )
    assert "<nav" in html
    assert 'aria-current="page"' in html
    assert "Home" in html


def test_text_field_wires_label_and_describedby():
    html = render_text_field(
        label="Email",
        name="email",
        helper="Used only to sign into Mighty.",
        error="Enter a valid email.",
        required=True,
    )
    assert '<label class="mds-field__label" for="mds-field-email">' in html
    assert 'aria-invalid="true"' in html
    assert "aria-describedby=" in html
    assert 'role="alert"' in html
    assert "mds-field-email-helper" in html
    assert "mds-field-email-error" in html


def test_switch_uses_role_switch():
    html = render_switch(label="Watch Amex", name="amex", checked=True)
    assert 'role="switch"' in html
    assert 'aria-checked="true"' in html


def test_checkbox_has_visible_label():
    html = render_checkbox(label="I agree", name="tos")
    assert "I agree" in html
    assert 'type="checkbox"' in html


def test_toast_roles_by_variant():
    assert 'role="status"' in render_toast("Saved", variant="success")
    assert 'role="alert"' in render_toast("Failed", variant="error")


def test_banner_is_labelled_region():
    html = render_banner("Setup still open", variant="waiting", dismissible=True)
    assert 'role="region"' in html
    assert 'aria-label="Notification"' in html
    assert 'aria-label="Dismiss notification"' in html


def test_hero_uses_single_h1():
    html = render_hero(title="You’re good.", lede="Nothing needs you.", variant="home")
    assert html.count("<h1") == 1
    assert "mds-display-lg" in html


def test_card_interactive_can_take_tabindex():
    html = render_card("Content", variant="interactive", tabindex=0, role="button")
    assert 'tabindex="0"' in html
    assert 'role="button"' in html


def test_icon_decorative_is_aria_hidden():
    html = render_icon("mail")
    assert 'aria-hidden="true"' in html


def test_icon_meaningful_requires_label():
    with pytest.raises(ValueError):
        render_icon("warning", decorative=False)


def test_icon_meaningful_with_label():
    html = render_icon("warning", decorative=False, label="Attention needed")
    assert 'aria-label="Attention needed"' in html
    assert 'role="img"' in html


def test_core_icon_set_matches_iconography_doc():
    expected = {
        "check",
        "minus",
        "info",
        "mail",
        "window",
        "accounts",
        "activity",
        "plus",
        "close",
        "chevron-right",
        "warning",
        "horizon-points",
    }
    assert expected == set(ICONS)


def test_html_escaping_in_button_label():
    html = render_button('<script>alert(1)</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
