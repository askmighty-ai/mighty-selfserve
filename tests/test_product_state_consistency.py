"""Cross-surface consistency: admin and customer agree on product account state.

For identical Current Access / provider_session_state evidence, Admin Current
Access, Dashboard, Accounts, Account Center, extension popup payload, and
/api/account-status must agree on:

  - session_state: connected | checking | signed_out | unknown
  - login_required
  - user_attention_required (session-level)
  - next_action_type / next_action_text

Admin may expose more diagnostic detail, but must never disagree on those fields.
Provider-independent: same contract for every provider source key.
"""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_center_ui import PRIMARY_LOGIN, build_card_view
from mighty.account_status import NEEDS_LOGIN, load_all_account_statuses
from mighty.accounts_ui import SECTION_NEEDS_LOGIN, resolve_accounts_section
from mighty.home_state import resolve_home_state
from mighty.login_truth import compute_current_account_access_rows
from mighty.provider_account import ProviderAccount
from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
from mighty.session_access import (
    PRODUCT_NEXT_ACTION,
    resolve_product_account_state,
    to_product_session_state,
)

# Probe + non-probe sources — state model must not depend on how auth is proven.
CROSS_SURFACE_PROVIDERS = (
    "amex",
    "delta",
    "hilton",
    "marriott",
    "united",
    "southwest",
    "xfinity",
    "pa_utilities",
)

DISPLAY_NAMES = {
    "amex": "American Express",
    "delta": "Delta",
    "hilton": "Hilton",
    "marriott": "Marriott",
    "united": "United",
    "southwest": "Southwest",
    "xfinity": "Xfinity",
    "pa_utilities": "Palo Alto Utilities",
}

PSS_STATES = ("connected", "checking", "signed_out", "unknown")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_product_state_consistency.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post(
        "/signup",
        data={
            "email": f"psc_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _insert_provider(client, source: str, *, sync_status="login_required", connection_status="needs_login"):
    """Insert credentials + account_data with hostile legacy login fields."""
    import app as mighty

    uid = _uid(client)
    display = DISPLAY_NAMES.get(source, source)
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        payload = {
            "items": [{"key": "balance", "label": "Balance", "value": "100"}],
            "sync_status": sync_status,
            "connection_status": connection_status,
        }
        stub = mighty.encrypt_account_data(uid, payload)
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, "
            "extra_enc, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, "
            "synced_at, connection_status, sync_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                uid, source, display, "?", "#eee", stub, now,
                connection_status, sync_status, "complete",
            ),
        )
        db.commit()
    return uid


def _set_pss(db, uid: str, provider: str, state: str, *, fresh: bool = True):
    import uuid

    from mighty.session_verification import ensure_session_verification_tables

    observed = datetime.now(timezone.utc)
    if not fresh:
        observed = observed - timedelta(minutes=30)
    evidence_type = {
        "connected": "authenticated_page",
        "signed_out": "login_required",
        "checking": "authenticated_page",
        "unknown": "unknown",
    }.get(state, "unknown")
    # checking = stale connected evidence + active verification row (read-path).
    # Insert verification directly so tests are provider-independent of entry URLs.
    pss_state = "connected" if state == "checking" else state
    pss_observed = (
        observed - timedelta(minutes=10) if state == "checking" else observed
    )
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider=provider,
            state=pss_state,
            evidence_type=evidence_type if state != "checking" else "authenticated_page",
            evidence_summary=f"test {state}",
            observed_at=pss_observed,
            source="test",
            confidence="high",
        ),
    )
    if state == "checking":
        ensure_session_verification_tables(db)
        db.execute(
            """
            INSERT INTO provider_session_verification (
                verification_id, user_id, provider, lifecycle, entry_url, requested_at
            ) VALUES (?, ?, ?, 'requested', ?, ?)
            """,
            (
                str(uuid.uuid4()),
                uid,
                provider,
                f"https://example.com/{provider}",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    db.commit()


def _legacy_account_state(provider: str):
    from mighty.account_state import (
        ACCESS_BROWSER_SESSION,
        CONN_NEEDS_LOGIN,
        DATA_NONE,
        AccountState,
        Confidence,
        ConfidenceFactors,
    )

    return AccountState(
        user_id="u",
        provider=provider,
        display_name=DISPLAY_NAMES.get(provider, provider),
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


def _access_row(provider: str, current_access: str):
    from mighty.login_truth import CurrentAccountAccess

    return CurrentAccountAccess(
        provider=provider,
        current_access=current_access,  # type: ignore[arg-type]
        cached_data_state="none",
        last_verified=None,
        last_private_data=None,
        evidence="test",
        source="test",
        next_action_type="none",
        next_action_text="",
    )


@pytest.mark.parametrize("provider", CROSS_SURFACE_PROVIDERS)
@pytest.mark.parametrize("pss_state", PSS_STATES)
def test_surfaces_agree_on_product_session_contract(client, provider, pss_state):
    """Admin + customer surfaces share the same product session contract."""
    import app as mighty
    from mighty.account_lifecycle import resolve_account_lifecycle

    uid = _insert_provider(client, provider)
    with mighty.app.app_context():
        db = mighty.get_db()
        _set_pss(db, uid, provider, pss_state)

        access_rows = {
            r.provider: r
            for r in compute_current_account_access_rows(
                db,
                uid,
                decrypt_account_fn=mighty.decrypt_account_data,
                providers=[provider],
            )
        }
        assert provider in access_rows
        access = access_rows[provider]
        product = resolve_product_account_state(access)

        expected_session = to_product_session_state(access.current_access)
        if pss_state == "checking":
            assert expected_session == "checking"
        elif pss_state == "connected":
            assert expected_session == "connected"
        elif pss_state == "signed_out":
            assert expected_session == "signed_out"
        else:
            assert expected_session == "unknown"

        assert product.session_state == expected_session
        assert product.login_required == (expected_session == "signed_out")
        assert product.user_attention_required == product.login_required
        assert product.next_action_type == PRODUCT_NEXT_ACTION[expected_session][0]
        assert product.next_action_text == PRODUCT_NEXT_ACTION[expected_session][1]

        # Admin Current Access next_action must agree with product contract.
        assert access.next_action_type == product.next_action_type
        assert access.next_action_text == product.next_action_text
        assert to_product_session_state(access.current_access) == product.session_state

        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names=DISPLAY_NAMES,
            login_url_fn=lambda _s: "https://example.com/login",
        )
        by_source = {a.source: a for a in accounts}
        assert provider in by_source
        acct = by_source[provider]

        assert acct.session_state == product.session_state
        assert acct.login_required == product.login_required
        assert acct.user_attention_required == product.user_attention_required
        assert acct.next_action_type == product.next_action_type
        assert acct.next_action_text == product.next_action_text
        if product.login_required:
            assert acct.status == NEEDS_LOGIN
            assert summary.needs_login_count >= 1
        else:
            assert acct.status != NEEDS_LOGIN

        # Dashboard health / hero buckets
        home = resolve_home_state(accounts=accounts)
        if product.login_required:
            assert home.health.needs_login >= 1
            assert home.health.attention_required >= 1
        else:
            # This provider must not inflate needs_login from legacy fields.
            login_sources = [a.source for a in accounts if a.status == NEEDS_LOGIN]
            assert provider not in login_sources

        # Accounts section + CTA
        lc = resolve_account_lifecycle(
            provider,
            in_credentials=True,
            account=ProviderAccount(
                source=provider,
                sync_status="login_required",
                connection_status="needs_login",
                normalized_fields=[],
            ),
        )
        section = resolve_accounts_section(
            lc, "login_required", source=provider, session_state=product.session_state,
        )
        if product.login_required:
            assert section == SECTION_NEEDS_LOGIN
        else:
            assert section != SECTION_NEEDS_LOGIN
        cta = mighty._accounts_primary_cta_html(
            lc,
            provider,
            DISPLAY_NAMES[provider],
            "login_required",
            session_state=product.session_state,
            login_required=product.login_required,
        )
        if product.login_required:
            assert "acct-maint-cta--urgent" in cta
        else:
            assert "acct-maint-cta--urgent" not in cta

        # Account Center card
        card = build_card_view(
            _legacy_account_state(provider),
            fmt_relative=lambda _x: "now",
            session_access=access,
            provider_login_url="https://example.com/login",
        )
        if product.login_required:
            assert card.primary_action_kind == PRIMARY_LOGIN
        else:
            assert card.primary_action_kind != PRIMARY_LOGIN

        # /api/account-status (+ popup payload)
        resp = client.get("/api/account-status")
        assert resp.status_code == 200
        payload = resp.get_json()
        api_acct = {a["source"]: a for a in payload["accounts"]}[provider]
        assert api_acct["session_state"] == product.session_state
        assert api_acct["login_required"] == product.login_required
        assert api_acct["user_attention_required"] == product.user_attention_required
        assert api_acct["next_action_type"] == product.next_action_type
        assert api_acct["next_action_text"] == product.next_action_text
        if product.login_required:
            assert payload["summary"]["access_loop"]["needs_sign_in"] >= 1
        else:
            # Provider must not contribute to needs_sign_in via legacy fields.
            if all(not a.get("login_required") for a in payload["accounts"]):
                assert payload["summary"]["access_loop"]["needs_sign_in"] == 0


@pytest.mark.parametrize("provider", CROSS_SURFACE_PROVIDERS)
def test_no_pss_never_invents_login_required(client, provider):
    """Missing session evidence → unknown everywhere; legacy login fields ignored."""
    import app as mighty

    uid = _insert_provider(
        client, provider, sync_status="login_required", connection_status="needs_login",
    )
    with mighty.app.app_context():
        db = mighty.get_db()
        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names=DISPLAY_NAMES,
            login_url_fn=lambda _s: "https://example.com/login",
        )
        by_source = {a.source: a for a in accounts}
        acct = by_source[provider]
        assert acct.session_state == "unknown"
        assert acct.login_required is False
        assert acct.user_attention_required is False
        assert acct.status != NEEDS_LOGIN
        assert summary.needs_login_count == 0

        resp = client.get("/api/account-status")
        api_acct = {a["source"]: a for a in resp.get_json()["accounts"]}[provider]
        assert api_acct["session_state"] == "unknown"
        assert api_acct["login_required"] is False

        card = build_card_view(
            _legacy_account_state(provider),
            fmt_relative=lambda _x: "now",
            session_access=_access_row(provider, "unknown"),
            provider_login_url="https://example.com/login",
        )
        assert card.primary_action_kind != PRIMARY_LOGIN


def test_product_next_action_table_covers_all_session_states():
    assert set(PRODUCT_NEXT_ACTION) == {"connected", "checking", "signed_out", "unknown"}
    assert PRODUCT_NEXT_ACTION["signed_out"][0] == "reauthenticate"
    assert PRODUCT_NEXT_ACTION["unknown"][0] == "none"
    assert PRODUCT_NEXT_ACTION["connected"][0] == "none"
    assert PRODUCT_NEXT_ACTION["checking"][0] == "verifying"


def test_connect_modal_poll_uses_session_not_lifecycle_login(client):
    """/api/extension/poll exposes product session; legacy needs_login is ignored."""
    import app as mighty

    uid = _insert_provider(client, "amex")
    with mighty.app.app_context():
        db = mighty.get_db()
        _set_pss(db, uid, "amex", "connected")

    resp = client.get("/api/extension/poll/amex")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["session_state"] == "connected"
    assert data["login_required"] is False
    assert data["product"]["session_state"] == "connected"
    assert data["product"]["login_required"] is False
    # Hostile legacy fields may still be present on the row / lifecycle.
    assert data.get("connection_status") == "needs_login"
