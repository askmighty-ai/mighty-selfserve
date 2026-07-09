"""Tests for Login Truth dashboard logic and admin page."""

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.adapters.amex_extraction import apply_amex_membership_rewards_extraction
from mighty.connection_state import advance_amex_to_waiting, amex_extension_connected, start_amex_connect
from mighty.login_truth import (
    TruthObservation,
    compute_login_truth_rows,
    gather_provider_observations,
    resolve_login_truth,
)
from mighty.provider_access_probe import AUTH_LOGIN_PAGE, record_probe_run


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _iso_minutes_ago(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_login_truth.db")
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
        email = f"login_truth_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


@pytest.fixture()
def admin_client(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    return client


def _ctx(mighty):
    return dict(
        iso_fn=mighty.iso,
        encrypt_fn=mighty.encrypt_account_data,
        decrypt_fn=mighty.decrypt_account_data,
    )


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def test_private_amex_mr_observation_yes(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(db, uid, "142,500", **ctx)

        rows = compute_login_truth_rows(db, uid, decrypt_account_fn=mighty.decrypt_account_data)
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.login_known == "YES"
        assert amex.evidence == "saw Membership Rewards balance"
        assert amex.source == "account_data.items"


def test_stale_login_probe_does_not_override_private_data(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        ctx = _ctx(mighty)
        start_amex_connect(db, uid, **ctx)
        advance_amex_to_waiting(db, uid, **ctx)
        amex_extension_connected(db, uid, session_verified=True, **ctx)
        apply_amex_membership_rewards_extraction(db, uid, "99,000", **ctx)

        record_probe_run(
            db,
            uid,
            {
                "provider": "amex",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_minutes_ago(5),
            },
        )

        rows = compute_login_truth_rows(db, uid, decrypt_account_fn=mighty.decrypt_account_data)
        amex = next(r for r in rows if r.provider == "amex")
        assert amex.login_known == "YES"
        assert amex.evidence == "saw Membership Rewards balance"
        assert amex.source == "account_data.items"


def test_login_page_with_no_private_data_no(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        record_probe_run(
            db,
            uid,
            {
                "provider": "delta",
                "status": "needs_sign_in",
                "auth_state": AUTH_LOGIN_PAGE,
                "private_data_detected": False,
                "signed_in_detected": False,
                "failure_reason": "login_required",
                "probed_at": _iso_minutes_ago(10),
            },
        )

        rows = compute_login_truth_rows(db, uid, decrypt_account_fn=mighty.decrypt_account_data)
        delta = next(r for r in rows if r.provider == "delta")
        assert delta.login_known == "NO"
        assert delta.evidence == "login page detected"
        assert delta.source == "provider_access_probe"


def test_no_observations_unknown(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        rows = compute_login_truth_rows(db, uid, decrypt_account_fn=mighty.decrypt_account_data)
        assert all(r.login_known == "UNKNOWN" for r in rows)
        assert all(r.evidence == "—" for r in rows)


def test_resolve_login_truth_private_beats_login():
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    observations = [
        TruthObservation(
            observed_at=now - timedelta(hours=2),
            kind="private",
            evidence="saw Membership Rewards balance",
            source="account_data.items",
        ),
        TruthObservation(
            observed_at=now - timedelta(minutes=30),
            kind="login",
            evidence="login page detected",
            source="provider_access_probe",
        ),
    ]
    result = resolve_login_truth(observations, now=now)
    assert result.login_known == "YES"
    assert result.evidence == "saw Membership Rewards balance"


def test_admin_login_truth_page_forbidden(client):
    assert client.get("/admin/login-truth").status_code == 403


def test_admin_login_truth_page_loads(admin_client):
    r = admin_client.get("/admin/login-truth")
    assert r.status_code == 200
    assert b"Login Truth" in r.data
    assert b"Login known?" in r.data
