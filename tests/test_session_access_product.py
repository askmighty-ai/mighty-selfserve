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
        assert by_source["amex"].status == UP_TO_DATE
        assert by_source["amex"].session_state == "connected"
        assert by_source["amex"].presentation_key != "needs_sign_in"
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
