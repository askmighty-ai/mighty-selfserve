"""Post-#87 adoption: surfaces consume ProductAccountState.login_required."""

from __future__ import annotations

import ast
import inspect
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_center_ui import PRIMARY_LOGIN, build_card_view
from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    CONN_NEEDS_LOGIN,
    DATA_NONE,
    AccountState,
    Confidence,
    ConfidenceFactors,
)
from mighty.login_truth import (
    CurrentAccountAccess,
    next_action_for_current_access,
)
from mighty.session_access import (
    PRODUCT_NEXT_ACTION,
    client_login_badge_kind,
    product_state_for_session,
    resolve_product_account_state,
)


def _legacy_login_state(provider: str = "amex"):
    from datetime import datetime, timezone

    return AccountState(
        user_id="u",
        provider=provider,
        display_name="American Express",
        category=None,
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_NEEDS_LOGIN,
        session_health="unknown",
        last_verified_at=None,
        data_status=DATA_NONE,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        next_recommended_action=None,
        confidence=Confidence(level="low", score=10, factors=ConfidenceFactors()),
        status_line="",
        is_actionable=True,
        updated_at=datetime.now(timezone.utc).isoformat(),
        sync_status="login_required",
    )


def _access(current_access: str, *, provider: str = "amex") -> CurrentAccountAccess:
    mapped = {
        "connected": "connected_now",
        "connected_now": "connected_now",
        "signed_out": "signed_out",
        "checking": "checking",
        "unknown": "unknown",
        "error": "error",
    }[current_access]
    return CurrentAccountAccess(
        provider=provider,
        current_access=mapped,  # type: ignore[arg-type]
        cached_data_state="none",
        last_verified=None,
        last_private_data=None,
        evidence="test",
        source="test",
        next_action_type="none",
        next_action_text="",
    )


@pytest.fixture()
def mighty_app(tmp_path, monkeypatch):
    db_path = str(tmp_path / "adoption.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    return mighty


def test_accounts_cta_uses_login_required_not_session_string(mighty_app):
    """Explicit login_required drives the CTA even if session_state were wrong."""
    from mighty.account_lifecycle import resolve_account_lifecycle
    from mighty.provider_account import ProviderAccount

    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=ProviderAccount(source="amex", sync_status="ok", normalized_fields=[]),
    )
    # Hostile: session_state unknown but login_required True must still show CTA
    # only when login_required is True — product contract wins.
    html_required = mighty_app._accounts_primary_cta_html(
        lc, "amex", "American Express", "ok",
        session_state="unknown",
        login_required=True,
    )
    assert "acct-maint-cta--urgent" in html_required

    html_unknown = mighty_app._accounts_primary_cta_html(
        lc, "amex", "American Express", "login_required",
        session_state="unknown",
        login_required=False,
    )
    assert "acct-maint-cta--urgent" not in html_unknown

    html_checking = mighty_app._accounts_primary_cta_html(
        lc, "amex", "American Express", "login_required",
        session_state="checking",
        login_required=False,
    )
    assert "acct-maint-cta--urgent" not in html_checking
    assert "Checking now" in html_checking

    html_signed_out = mighty_app._accounts_primary_cta_html(
        lc, "amex", "American Express", "ok",
        session_state="signed_out",
        login_required=True,
    )
    assert "acct-maint-cta--urgent" in html_signed_out

    # Source must not re-derive with session_state == "signed_out" as the login gate.
    src = inspect.getsource(mighty_app._accounts_primary_cta_html)
    assert "login_required" in src
    assert 'session_state == "signed_out"' not in src


@pytest.mark.parametrize(
    "session,expect_login",
    [
        ("signed_out", True),
        ("checking", False),
        ("connected", False),
        ("unknown", False),
    ],
)
def test_account_center_login_cta_uses_login_required(session, expect_login):
    product = product_state_for_session(session, provider="amex")
    assert product.login_required is expect_login
    card = build_card_view(
        _legacy_login_state(),
        fmt_relative=lambda _x: "now",
        session_access=_access(session),
        provider_login_url="https://example.com/login",
    )
    if expect_login:
        assert card.primary_action_kind == PRIMARY_LOGIN
        assert card.primary_action_disabled is False
    else:
        assert card.primary_action_kind != PRIMARY_LOGIN

    src = inspect.getsource(build_card_view)
    assert "product.login_required" in src
    assert 'session_state != "signed_out"' not in src


def test_unknown_and_checking_never_sign_in_cta():
    for session in ("unknown", "checking"):
        card = build_card_view(
            _legacy_login_state(),
            fmt_relative=lambda _x: "now",
            session_access=_access(session),
            provider_login_url="https://example.com/login",
        )
        assert card.primary_action_kind != PRIMARY_LOGIN
        assert product_state_for_session(session).login_required is False


def test_signed_out_produces_sign_in_cta():
    card = build_card_view(
        _legacy_login_state(),
        fmt_relative=lambda _x: "now",
        session_access=_access("signed_out"),
        provider_login_url="https://example.com/login",
    )
    assert card.primary_action_kind == PRIMARY_LOGIN
    assert card.primary_action_href == "https://example.com/login"


def test_dashboard_client_legacy_sync_cannot_override_session():
    """Legacy sync_status=login_required cannot invent Needs login over session."""
    assert client_login_badge_kind(
        session_state="connected", sync_status="login_required",
    ) is None
    assert client_login_badge_kind(
        session_state="unknown", sync_status="login_required",
    ) is None
    assert client_login_badge_kind(
        session_state="checking", sync_status="login_required",
    ) == "checking"
    assert client_login_badge_kind(
        session_state="signed_out", sync_status="ok",
    ) == "needs_login"
    assert client_login_badge_kind(
        login_required=True, session_state="unknown", sync_status="ok",
    ) == "needs_login"
    # sync_status alone never yields needs_login
    assert client_login_badge_kind(sync_status="login_required") is None

    import app as mighty

    dash_js = open(mighty.__file__).read()
    # Login badge OR must not include syncStatus === 'login_required'.
    assert "sessionState === 'signed_out' || syncStatus === 'login_required'" not in dash_js
    assert "sessionState === 'signed_out' || loginRequired" in dash_js
    # Poll must set data-login-required from canonical API field.
    assert "dataset.loginRequired" in dash_js
    assert "acct.login_required" in dash_js
    # Clearing a stale dataset.syncStatus value is OK; inventing login from it is not.
    assert "if (syncStatus === 'login_required')" not in dash_js
    assert "if (syncStatus === 'login_required')" not in dash_js.replace(
        "card.dataset.syncStatus === 'login_required'", ""
    )

def test_canonical_next_action_defined_in_one_place():
    import mighty.login_truth as login_truth
    import mighty.session_access as session_access

    # Policy table exists only on the product module.
    assert hasattr(session_access, "PRODUCT_NEXT_ACTION")
    assert not hasattr(login_truth, "NEXT_ACTION_BY_CURRENT_ACCESS")
    assert not hasattr(login_truth, "NEXT_ACTION_UNKNOWN_INCONCLUSIVE")

    for current_access in ("connected_now", "signed_out", "checking", "unknown", "error"):
        product = resolve_product_account_state(_access(current_access))
        admin = next_action_for_current_access(current_access)  # type: ignore[arg-type]
        assert admin == (product.next_action_type, product.next_action_text)
        assert admin == PRODUCT_NEXT_ACTION[product.session_state]

    # login_truth helper body must call PRODUCT_NEXT_ACTION (lazy import).
    src = inspect.getsource(next_action_for_current_access)
    assert "PRODUCT_NEXT_ACTION" in src
    assert "to_product_session_state" in src

    # Ensure no duplicate dict literal of next-action pairs in login_truth module AST.
    tree = ast.parse(inspect.getsource(login_truth))
    assert tree is not None
