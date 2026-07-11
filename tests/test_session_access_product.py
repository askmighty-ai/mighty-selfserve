"""Regression: product login state comes only from provider_session_state."""

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_status import (
    CHECKING,
    NEEDS_LOGIN,
    UP_TO_DATE,
    load_all_account_statuses,
)
from mighty.login_truth import compute_current_account_access_rows
from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
from mighty.session_access import to_product_session_state
from mighty.session_verification import request_session_verification


def _iso_seconds_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_session_access_product.db")
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
            "email": f"sess_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _insert_amex(client, *, sync_status="login_required", connection_status="needs_login"):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        payload = {
            "items": [{"key": "mr", "label": "MR", "value": "1000"}],
            "sync_status": sync_status,
            "connection_status": connection_status,
        }
        stub = mighty.encrypt_account_data(uid, payload)
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, "
            "extra_enc, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, "
            "synced_at, connection_status, sync_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                uid, "amex", "American Express", "?", "#eee", stub, now,
                connection_status, sync_status, "complete",
            ),
        )
        db.commit()
    return uid


def test_connected_pss_never_needs_login_despite_legacy_fields(client):
    """Connected account never appears in Needs login — even with stale sync_status."""
    import app as mighty

    uid = _insert_amex(client, sync_status="login_required", connection_status="needs_login")
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="authenticated_page",
                evidence_summary="fresh connected",
                observed_at=datetime.now(timezone.utc),
                source="test",
                confidence="high",
            ),
        )
        db.commit()

        access_rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data,
        )
        amex_access = next(r for r in access_rows if r.provider == "amex")
        assert amex_access.current_access == "connected_now"
        assert to_product_session_state(amex_access.current_access) == "connected"

        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "",
        )
        by_source = {a.source: a for a in accounts}
        assert by_source["amex"].status in (UP_TO_DATE, "unverified")
        assert by_source["amex"].session_state == "connected"
        assert by_source["amex"].login_required is False
        assert by_source["amex"].presentation_key != "needs_sign_in"
        assert by_source["amex"].readiness != "signed_out"
        assert summary.needs_login_count == 0
        assert "American Express" not in summary.needs_login_accounts


def test_api_account_status_matches_current_account_access(client):
    """/api/account-status returns the same session state as compute_current_account_access_rows."""
    import app as mighty

    uid = _insert_amex(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_required",
                evidence_summary="login page",
                observed_at=datetime.now(timezone.utc),
                source="test",
                confidence="high",
            ),
        )
        db.commit()

    resp = client.get("/api/account-status")
    assert resp.status_code == 200
    data = resp.get_json()
    by_source = {a["source"]: a for a in data["accounts"]}
    assert by_source["amex"]["status"] == NEEDS_LOGIN
    assert by_source["amex"]["session_state"] == "signed_out"

    with mighty.app.app_context():
        db = mighty.get_db()
        access_rows = compute_current_account_access_rows(
            db, uid, decrypt_account_fn=mighty.decrypt_account_data,
        )
        amex_access = next(r for r in access_rows if r.provider == "amex")
        assert amex_access.current_access == "signed_out"
        assert by_source["amex"]["current_access"] == amex_access.current_access
        assert to_product_session_state(amex_access.current_access) == by_source["amex"]["session_state"]


def test_checking_while_verification_queued(client):
    import app as mighty

    uid = _insert_amex(client, sync_status="ok", connection_status="connected")
    with mighty.app.app_context():
        db = mighty.get_db()
        # Stale connected evidence → unknown unless verification active.
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="authenticated_page",
                evidence_summary="stale connected",
                observed_at=datetime.fromisoformat(_iso_minutes_ago(10)),
                source="test",
                confidence="high",
            ),
        )
        request_session_verification(db, uid, "amex")
        db.commit()

        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "",
        )
        by_source = {a.source: a for a in accounts}
        assert by_source["amex"].status == CHECKING
        assert by_source["amex"].session_state == "checking"
        assert by_source["amex"].presentation_key == "checking"
        assert summary.needs_login_count == 0
        assert "American Express" not in summary.needs_login_accounts


def test_signed_out_evidence_updates_dashboard_immediately(client):
    import app as mighty

    uid = _insert_amex(client, sync_status="ok", connection_status="connected")
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="signed_out",
                evidence_type="login_required",
                evidence_summary="explicit login page",
                observed_at=datetime.now(timezone.utc),
                source="test",
                confidence="high",
            ),
        )
        db.commit()

    resp = client.get("/api/account-status")
    by_source = {a["source"]: a for a in resp.get_json()["accounts"]}
    assert by_source["amex"]["status"] == NEEDS_LOGIN
    assert by_source["amex"]["session_state"] == "signed_out"
    assert resp.get_json()["summary"]["needs_login_count"] == 1


def test_unknown_when_no_fresh_verification(client):
    import app as mighty

    uid = _insert_amex(client, sync_status="login_required", connection_status="needs_login")
    with mighty.app.app_context():
        db = mighty.get_db()
        # No PSS row → unknown. Legacy login_required must not force needs_login.
        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "",
        )
        by_source = {a.source: a for a in accounts}
        assert by_source["amex"].session_state == "unknown"
        assert by_source["amex"].status != NEEDS_LOGIN
        assert summary.needs_login_count == 0

        # Stale connected evidence without active verification → unknown
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="authenticated_page",
                evidence_summary="stale",
                observed_at=datetime.fromisoformat(_iso_seconds_ago(500)),
                source="test",
                confidence="high",
            ),
        )
        db.commit()
        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "",
        )
        by_source = {a.source: a for a in accounts}
        assert by_source["amex"].session_state == "unknown"
        assert by_source["amex"].status != NEEDS_LOGIN
        assert summary.needs_login_count == 0


def test_dashboard_and_current_access_agree(client):
    """Dashboard account statuses and Current Access always agree on login."""
    import app as mighty

    uid = _insert_amex(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="authenticated_page",
                evidence_summary="connected",
                observed_at=datetime.now(timezone.utc),
                source="test",
                confidence="high",
            ),
        )
        db.commit()

        access_rows = {
            r.provider: r
            for r in compute_current_account_access_rows(
                db, uid, decrypt_account_fn=mighty.decrypt_account_data,
            )
        }
        accounts, _summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "",
        )
        for acct in accounts:
            if acct.source not in access_rows:
                continue
            access = access_rows[acct.source]
            assert acct.current_access == access.current_access
            assert acct.session_state == to_product_session_state(access.current_access)
            if access.current_access == "connected_now":
                assert acct.status != NEEDS_LOGIN
            if access.current_access == "signed_out":
                assert acct.status == NEEDS_LOGIN
            if access.current_access == "checking":
                assert acct.status == CHECKING
                assert acct.status != NEEDS_LOGIN


def _access(current_access: str, *, cached: str = "none"):
    from mighty.login_truth import CurrentAccountAccess

    return CurrentAccountAccess(
        provider="amex",
        current_access=current_access,  # type: ignore[arg-type]
        cached_data_state=cached,  # type: ignore[arg-type]
        last_verified=None,
        last_private_data=None,
        evidence="test",
        source="test",
        next_action_type="none",
        next_action_text="",
    )


def _legacy_needs_login_state():
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
        provider="amex",
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


def test_account_center_unknown_never_login_cta():
    """unknown PSS + legacy needs_login → Unable to verify, no login CTA."""
    from mighty.account_center_ui import PRIMARY_LOGIN, build_card_view

    card = build_card_view(
        _legacy_needs_login_state(),
        fmt_relative=lambda _x: "now",
        session_access=_access("unknown"),
        provider_login_url="https://example.com/login",
    )
    assert card.status_label == "Unable to verify"
    assert card.primary_action_kind != PRIMARY_LOGIN
    assert card.primary_action_disabled is True
    assert "sign in" not in card.status_label.lower()
    assert card.primary_action_href is None


def test_account_center_signed_out_login_cta():
    """signed_out PSS → Needs sign in + login CTA."""
    from mighty.account_center_ui import PRIMARY_LOGIN, build_card_view

    card = build_card_view(
        _legacy_needs_login_state(),
        fmt_relative=lambda _x: "now",
        session_access=_access("signed_out"),
        provider_login_url="https://example.com/login",
    )
    assert card.status_label == "Sign in required"
    assert card.primary_action_kind == PRIMARY_LOGIN
    assert card.primary_action_disabled is False
    assert card.primary_action_href == "https://example.com/login"


def test_accounts_cta_unknown_no_login(client):
    """unknown PSS + LC_NEEDS_LOGIN → no login CTA."""
    import app as mighty
    from mighty.account_lifecycle import NEEDS_LOGIN as LC_NEEDS_LOGIN, resolve_account_lifecycle
    from mighty.provider_account import ProviderAccount

    acct = ProviderAccount(
        source="amex",
        sync_status="login_required",
        connection_status="needs_login",
        normalized_fields=[],
    )
    lc = resolve_account_lifecycle("amex", in_credentials=True, account=acct)
    assert lc.state == LC_NEEDS_LOGIN
    html = mighty._accounts_primary_cta_html(
        lc, "amex", "American Express", "login_required",
        session_state="unknown", login_required=False,
    )
    assert html == ""
    assert "acct-maint-cta--urgent" not in html


def test_accounts_cta_checking_no_login(client):
    """checking PSS + LC_NEEDS_LOGIN → no login CTA."""
    import app as mighty
    from mighty.account_lifecycle import NEEDS_LOGIN as LC_NEEDS_LOGIN, resolve_account_lifecycle
    from mighty.provider_account import ProviderAccount

    acct = ProviderAccount(
        source="amex",
        sync_status="login_required",
        connection_status="needs_login",
        normalized_fields=[],
    )
    lc = resolve_account_lifecycle("amex", in_credentials=True, account=acct)
    assert lc.state == LC_NEEDS_LOGIN
    html = mighty._accounts_primary_cta_html(
        lc, "amex", "American Express", "login_required",
        session_state="checking", login_required=False,
    )
    assert "acct-maint-cta--urgent" not in html
    assert "Checking" in html


def test_accounts_cta_signed_out_login(client):
    """signed_out PSS → login CTA."""
    import app as mighty
    from mighty.account_lifecycle import resolve_account_lifecycle
    from mighty.provider_account import ProviderAccount

    acct = ProviderAccount(source="amex", sync_status="ok", normalized_fields=[])
    lc = resolve_account_lifecycle("amex", in_credentials=True, account=acct)
    html = mighty._accounts_primary_cta_html(
        lc, "amex", "American Express", "ok",
        session_state="signed_out", login_required=True,
    )
    assert "acct-maint-cta--urgent" in html
    assert "Log in" in html or "Sign in" in html or "login" in html.lower()


def test_unknown_consistent_across_product_surfaces(client):
    """unknown PSS + legacy needs_login → never needs-login across surfaces."""
    import app as mighty
    from mighty.account_center_ui import PRIMARY_LOGIN, build_card_view
    from mighty.accounts_ui import SECTION_NEEDS_LOGIN, resolve_accounts_section
    from mighty.account_lifecycle import resolve_account_lifecycle
    from mighty.provider_account import ProviderAccount

    uid = _insert_amex(client, sync_status="login_required", connection_status="needs_login")
    with mighty.app.app_context():
        db = mighty.get_db()
        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "American Express"},
            login_url_fn=lambda _s: "https://example.com/login",
        )
        by_source = {a.source: a for a in accounts}
        amex = by_source["amex"]
        assert amex.session_state == "unknown"
        assert amex.status != NEEDS_LOGIN
        assert amex.presentation_key != "needs_sign_in"
        assert summary.needs_login_count == 0
        assert "American Express" not in summary.needs_login_accounts

        # API payload
        resp = client.get("/api/account-status")
        payload = resp.get_json()
        api_amex = {a["source"]: a for a in payload["accounts"]}["amex"]
        assert api_amex["session_state"] == "unknown"
        assert api_amex["status"] != NEEDS_LOGIN
        assert payload["summary"]["needs_login_count"] == 0

        # Accounts section
        acct = ProviderAccount(
            source="amex",
            sync_status="login_required",
            connection_status="needs_login",
            normalized_fields=[],
        )
        lc = resolve_account_lifecycle("amex", in_credentials=True, account=acct)
        section = resolve_accounts_section(
            lc, "login_required", source="amex", session_state="unknown",
        )
        assert section != SECTION_NEEDS_LOGIN
        cta = mighty._accounts_primary_cta_html(
            lc, "amex", "American Express", "login_required",
            session_state="unknown", login_required=False,
        )
        assert "acct-maint-cta--urgent" not in cta

        # Account Center
        card = build_card_view(
            _legacy_needs_login_state(),
            fmt_relative=lambda _x: "now",
            session_access=_access("unknown"),
            provider_login_url="https://example.com/login",
        )
        assert card.primary_action_kind != PRIMARY_LOGIN
        assert card.status_label == "Unable to verify"

        # Popup payload uses same summary
        assert payload["summary"]["access_loop"]["needs_sign_in"] == 0
