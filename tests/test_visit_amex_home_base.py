"""Visit Amex home-base interaction (system of engagement)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_status import AccountStatus
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy


def _escape(value):
    import html

    return html.escape(str(value)) if value is not None else ""


def test_visit_cta_opens_new_tab_with_orientation():
    accounts = [
        AccountStatus(
            source="amex",
            display_name="American Express",
            status="waiting_for_extension",
            presentation_key="updating",
            presentation_label="Waiting",
            last_successful_sync_at=None,
            current_attempt_at=None,
            last_error=None,
            user_action_label=None,
            user_action_url=None,
        )
    ]
    result = resolve_home_state(
        accounts=accounts,
        provider_open_urls={"amex": "https://www.americanexpress.com/"},
        worker_setup_needed=False,
    )
    assert result.featured is not None
    assert result.featured.cta_url == "https://www.americanexpress.com/"
    body = (result.featured.body or "").lower()
    assert "new tab" in body
    assert "keep this mighty tab open" in body

    rendered = render_home_page(
        result, first_name="Alex", today_label="Tuesday, July 28", escape=_escape,
    )
    assert 'target="_blank"' in rendered
    assert 'rel="noopener noreferrer"' in rendered
    assert 'data-provider-visit="1"' in rendered
    assert "home-visit-stay-note" in rendered
    assert user_copy.HOME_PROVIDER_VISIT_HELPER in rendered
    assert 'data-opened-text="' in rendered


def test_dashboard_js_polls_on_visibility_and_provider_visit():
    import app as mighty

    js_src = mighty.__dict__.get("DASHBOARD_JS") or ""
    # Script is embedded in HTML templates — search app.py source file.
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "visibilitychange" in src
    assert "_pollAccountStatus()" in src
    assert "window.addEventListener('focus'" in src or 'window.addEventListener("focus"' in src
    assert "data-provider-visit" in src
    assert "product_lifecycle_changed" in src or "capability_state_changed" in src
    assert "HOME_PROVIDER_VISIT" not in src  # copy lives in user_copy
    del js_src


def test_login_body_teaches_home_base():
    text = user_copy.home_login_body("American Express").lower()
    assert "new tab" in text
    assert "keep this mighty tab open" in text
    assert "return here" in text
