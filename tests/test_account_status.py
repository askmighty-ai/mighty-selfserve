"""Tests for canonical account status shared by dashboard and extension."""

import json
import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.account_status import (
    NEEDS_LOGIN,
    UPDATING,
    UP_TO_DATE,
    WAITING_FOR_EXTENSION,
    build_account_status,
    build_status_summary,
    load_all_account_statuses,
    resolve_canonical_status,
)
from mighty.connection_state import NEEDS_LOGIN as CONN_NEEDS_LOGIN
from mighty.provider_account import ProviderAccount


def _acct(**kwargs) -> ProviderAccount:
    defaults = dict(source="delta", sync_status="ok")
    defaults.update(kwargs)
    return ProviderAccount(**defaults)


def test_needs_login_beats_updating_for_same_account():
    lc = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(source="amex", sync_status="login_required", connection_status=CONN_NEEDS_LOGIN),
    )
    status = resolve_canonical_status(
        lc, "login_required", source="amex", updating_source="amex",
    )
    assert status == NEEDS_LOGIN


def test_updating_when_syncing_and_not_login_blocked():
    lc = resolve_account_lifecycle(
        "pa_utilities",
        in_credentials=True,
        account=_acct(source="pa_utilities", sync_status="ok"),
    )
    status = resolve_canonical_status(
        lc, "ok", source="pa_utilities", updating_source="pa_utilities",
    )
    assert status == UPDATING


def test_amex_needs_login_not_overwritten_by_unrelated_sync():
    lc_amex = resolve_account_lifecycle(
        "amex",
        in_credentials=True,
        account=_acct(source="amex", connection_status=CONN_NEEDS_LOGIN),
    )
    lc_pa = resolve_account_lifecycle(
        "pa_utilities",
        in_credentials=True,
        account=_acct(source="pa_utilities", sync_status="ok"),
    )
    assert resolve_canonical_status(
        lc_amex, "ok", source="amex", updating_source="pa_utilities",
    ) == NEEDS_LOGIN
    assert resolve_canonical_status(
        lc_pa, "ok", source="pa_utilities", updating_source="pa_utilities",
    ) == UPDATING


def test_multiple_account_states_summary():
    accounts = [
        build_account_status(
            "amex", "American Express",
            resolve_account_lifecycle("amex", in_credentials=True, account=_acct(connection_status=CONN_NEEDS_LOGIN)),
            _acct(connection_status=CONN_NEEDS_LOGIN),
            sync_status="ok",
            updating_source="pa_utilities",
        ),
        build_account_status(
            "pa_utilities", "Palo Alto Utilities",
            resolve_account_lifecycle("pa_utilities", in_credentials=True, account=_acct(source="pa_utilities")),
            _acct(source="pa_utilities"),
            sync_status="ok",
            updating_source="pa_utilities",
        ),
    ]
    summary = build_status_summary(accounts)
    assert summary.is_syncing is True
    assert summary.headline == "Syncing Palo Alto Utilities"
    assert summary.subline == "American Express needs login"
    assert summary.needs_login_count == 1
    assert summary.updating_count == 1


def test_needs_login_only_headline():
    accounts = [
        build_account_status(
            "amex", "American Express",
            resolve_account_lifecycle("amex", in_credentials=True, account=_acct(connection_status=CONN_NEEDS_LOGIN)),
            _acct(connection_status=CONN_NEEDS_LOGIN),
            sync_status="ok",
            updating_source=None,
        ),
    ]
    summary = build_status_summary(accounts)
    assert summary.is_syncing is False
    assert summary.headline == "American Express needs login"
    assert summary.needs_login_count == 1


def test_multiple_needs_login_headline():
    accounts = [
        build_account_status(
            "amex", "American Express",
            resolve_account_lifecycle("amex", in_credentials=True, account=_acct(connection_status=CONN_NEEDS_LOGIN)),
            _acct(connection_status=CONN_NEEDS_LOGIN),
            sync_status="ok",
            updating_source=None,
        ),
        build_account_status(
            "united", "United Airlines",
            resolve_account_lifecycle("united", in_credentials=True, account=_acct(source="united", sync_status="login_required")),
            _acct(source="united", sync_status="login_required"),
            sync_status="login_required",
            updating_source=None,
        ),
    ]
    summary = build_status_summary(accounts)
    assert summary.headline == "2 accounts need login"
    assert summary.is_syncing is False


def test_no_global_syncing_without_updating_accounts():
    accounts = [
        build_account_status(
            "amex", "American Express",
            resolve_account_lifecycle("amex", in_credentials=True, account=_acct(connection_status=CONN_NEEDS_LOGIN)),
            _acct(connection_status=CONN_NEEDS_LOGIN),
            sync_status="ok",
            updating_source=None,
        ),
    ]
    summary = build_status_summary(accounts)
    assert summary.is_syncing is False
    assert "Syncing" not in summary.headline


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_account_status.db")
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
            "email": f"status_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _user_api(client):
    import app as mighty

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT id, api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def _insert_account(client, source, display_name, *, sync_status="ok", connection_status=None):
    import app as mighty

    uid, _ = _user_api(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        payload = {"items": [], "sync_status": sync_status}
        if connection_status:
            payload["connection_status"] = connection_status
        stub = mighty.encrypt_account_data(uid, payload)
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, connection_status, sync_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, source, display_name, "?", "#eee", stub, now, connection_status or "", sync_status),
        )
        db.commit()
    return uid


def test_api_account_status_dashboard_and_extension_consistent(client):
    import app as mighty
    from mighty.connection_state import NEEDS_LOGIN as CONN_NEEDS_LOGIN

    uid, api_key = _user_api(client)
    _insert_account(client, "amex", "American Express", connection_status=CONN_NEEDS_LOGIN)
    _insert_account(client, "pa_utilities", "Palo Alto Utilities", sync_status="ok")

    with mighty.app.app_context():
        db = mighty.get_db()
        db.execute(
            "UPDATE users SET sync_running=1, sync_started_at=?, sync_current_source=? WHERE id=?",
            (mighty.iso(), "pa_utilities", uid),
        )
        db.commit()

    ext_resp = client.get("/api/account-status", headers={"X-Mighty-Key": api_key})
    assert ext_resp.status_code == 200
    ext_data = ext_resp.get_json()
    assert ext_data["ok"] is True

    dash_resp = client.get("/api/account-status")
    assert dash_resp.status_code == 200
    dash_data = dash_resp.get_json()

    assert ext_data["accounts"] == dash_data["accounts"]
    assert ext_data["summary"] == dash_data["summary"]

    by_source = {a["source"]: a for a in ext_data["accounts"]}
    assert by_source["amex"]["status"] == NEEDS_LOGIN
    assert by_source["pa_utilities"]["status"] == UPDATING
    assert ext_data["summary"]["headline"] == "Syncing Palo Alto Utilities"
    assert ext_data["summary"]["subline"] == "American Express needs login"


def test_api_sync_progress_updates_updating_account(client):
    import app as mighty

    uid, api_key = _user_api(client)
    _insert_account(client, "marriott", "Marriott Bonvoy")

    client.post("/api/sync/start", headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"})
    client.post(
        "/api/sync/progress",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"source": "marriott"}),
    )

    resp = client.get("/api/account-status", headers={"X-Mighty-Key": api_key})
    data = resp.get_json()
    by_source = {a["source"]: a for a in data["accounts"]}
    assert by_source["marriott"]["status"] == UPDATING
    assert data["summary"]["is_syncing"] is True


def test_synced_account_is_up_to_date(client):
    import app as mighty

    uid, api_key = _user_api(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        now = mighty.iso()
        payload = {
            "items": [{"key": "points", "label": "Points", "value": "50000"}],
            "sync_status": "ok",
        }
        stub = mighty.encrypt_account_data(uid, payload)
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "delta", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at, sync_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, "delta", "Delta", "?", "#eee", stub, now, "ok", "complete"),
        )
        db.commit()

    resp = client.get("/api/account-status", headers={"X-Mighty-Key": api_key})
    by_source = {a["source"]: a for a in resp.get_json()["accounts"]}
    assert by_source["delta"]["status"] == UP_TO_DATE


def test_waiting_for_extension_status(client):
    _insert_account(client, "hilton", "Hilton Honors", sync_status="needs_first_visit")
    _, api_key = _user_api(client)
    resp = client.get("/api/account-status", headers={"X-Mighty-Key": api_key})
    by_source = {a["source"]: a for a in resp.get_json()["accounts"]}
    assert by_source["hilton"]["status"] == WAITING_FOR_EXTENSION
