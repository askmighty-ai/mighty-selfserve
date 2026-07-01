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
from mighty.provider_account import EXTRACTION_COMPLETE, load_provider_account


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
        result = apply_amex_membership_rewards_extraction(db, uid, "142,500", **_ctx(mighty))
        assert result["is_synced"] is True
        assert result["extraction_status"] == EXTRACTION_COMPLETE
        row = db.execute(
            "SELECT * FROM account_data WHERE user_id=? AND source='amex'", (uid,),
        ).fetchone()
        acct = load_provider_account(uid, dict(row), decrypt_fn=mighty.decrypt_account_data)
        assert acct.is_synced
        assert acct.normalized_fields[0]["value"] == "142,500"
        assert row["connection_status"] == CONNECTED


def test_http_extract_endpoint(client):
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
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["is_synced"] is True
    assert body["field"]["value"] == "99,000"
