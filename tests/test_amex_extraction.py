"""Tests for Amex Membership Rewards extraction storage."""

import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.adapters.amex_extraction import (
    apply_amex_membership_rewards_extraction,
    build_amex_mr_item,
    normalize_points_value,
)
from mighty.connection_state import CONNECTED, advance_amex_to_waiting, amex_extension_connected, start_amex_connect
from mighty.provider_access_manager import (
    complete_provider_access_check,
    request_provider_access_check,
)
from mighty.provider_access_probe import AUTH_AUTHENTICATED_NO_PRIVATE_DATA
from mighty.provider_account import EXTRACTION_COMPLETE, load_provider_account
from mighty.provider_session_state import ensure_provider_session_state_tables
from mighty.session_verification import ensure_session_verification_tables


def test_normalize_points_value():
    assert normalize_points_value("123,456") == "123,456"
    assert normalize_points_value("123456") == "123,456"
    assert normalize_points_value("") is None
    assert normalize_points_value("0") is None


def test_build_amex_mr_item():
    item = build_amex_mr_item("85000")
    assert item["key"] == "points_balance"
    assert item["label"] == "Membership Rewards Points"
    assert item["value"] == "85,000"
    assert item["_type"] == "points_balance"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty
    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def test_apply_extraction_marks_synced(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        start_amex_connect(db, uid, **_ctx(mighty))
        advance_amex_to_waiting(db, uid, **_ctx(mighty))
        amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))
        result = apply_amex_membership_rewards_extraction(
            db,
            uid,
            "142,500",
            access_cycle_id="cycle-1",
            verification_id="cycle-1",
            **_ctx(mighty),
        )
        assert result["is_synced"] is True
        assert result["extraction_status"] == EXTRACTION_COMPLETE
        assert result["access_cycle_id"] == "cycle-1"
        assert result["verification_id"] == "cycle-1"
        row = db.execute(
            "SELECT * FROM account_data WHERE user_id=? AND source='amex'", (uid,),
        ).fetchone()
        acct = load_provider_account(uid, dict(row), decrypt_fn=mighty.decrypt_account_data)
        assert acct.is_synced
        assert acct.normalized_fields[0]["value"] == "142,500"
        assert row["connection_status"] == CONNECTED


def test_apply_extraction_rejects_missing_cycle(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        start_amex_connect(db, uid, **_ctx(mighty))
        advance_amex_to_waiting(db, uid, **_ctx(mighty))
        amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))
        with pytest.raises(ValueError, match="active_verification_required"):
            apply_amex_membership_rewards_extraction(db, uid, "142,500", **_ctx(mighty))


def test_http_extract_without_cycle_rejected(client):
    """Passive / uncorrelated HTTP extract must not persist."""
    import app as mighty

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
        uid = sess["user_id"]
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/connect/amex", headers=headers)
    client.post("/api/connect/amex/waiting", headers=headers)
    with mighty.app.app_context():
        api_key = mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()["api_key"]
    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True, "value": "99,000"},
    )
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "active_verification_required"
    assert body["ok"] is False


def test_http_extract_with_active_cycle(client):
    import app as mighty
    from datetime import datetime, timezone

    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
        uid = sess["user_id"]
    headers = {"X-CSRF-Token": csrf}
    client.post("/api/connect/amex", headers=headers)
    client.post("/api/connect/amex/waiting", headers=headers)
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_provider_session_state_tables(db)
        ensure_session_verification_tables(db)
        from mighty.provider_access_manager import record_amex_extension_connected

        record_amex_extension_connected(db, uid, observed_at=mighty.iso())
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(
            db,
            uid,
            {
                "provider": "amex",
                "status": "ok",
                "auth_state": AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
                "url_visited": "https://global.americanexpress.com/overview",
                "signed_in_detected": True,
                "private_data_detected": False,
                "evidence_type": "page",
                "evidence_snippet": "test",
                "probed_at": datetime.now(timezone.utc).isoformat(),
            },
            verification_id=vid,
        )
        api_key = db.execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()["api_key"]

    r = client.post(
        "/api/extension/amex/extract",
        headers={"X-Mighty-Key": api_key},
        json={
            "session_verified": True,
            "value": "99,000",
            "verification_id": vid,
            "access_cycle_id": vid,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["is_synced"] is True
    assert body["field"]["value"] == "99,000"
    assert body["access_cycle_id"] == vid
    assert body["verification_id"] == vid
