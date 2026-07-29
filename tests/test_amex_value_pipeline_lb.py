"""Founder Session 2 — Amex Extracting Learning Blocker repairs."""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_readiness import UNVERIFIED
from mighty.capability_state import CapabilityState
from mighty.connection_state import (
    advance_amex_to_waiting,
    amex_extension_connected,
    start_amex_connect,
)
from mighty.customer_account_access import (
    BG_EXTRACTING,
    BG_UNSUPPORTED_DATA,
    build_customer_account_access_view,
    resolve_background_work,
)
from mighty.product_account_lifecycle import (
    UNSUPPORTED_DATA,
    resolve_product_account_lifecycle,
)
from mighty.provider_access_manager import (
    complete_provider_access_check,
    request_provider_access_check,
)
from mighty.provider_access_probe import AUTH_AUTHENTICATED_NO_PRIVATE_DATA
from mighty.provider_account import EXTRACTION_NO_ACCOUNT_DATA, EXTRACTION_PENDING
from mighty.session_verification import get_latest_session_verification


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_amex_lb.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_lb_{secrets.token_hex(4)}@test.local"
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


def _uid(client) -> str:
    with client.session_transaction() as sess:
        return sess["user_id"]


def _seed_amex(mighty, uid: str) -> None:
    from mighty.provider_access_manager import record_amex_extension_connected

    db = mighty.get_db()
    start_amex_connect(db, uid, **_ctx(mighty))
    advance_amex_to_waiting(db, uid, **_ctx(mighty))
    amex_extension_connected(db, uid, session_verified=True, **_ctx(mighty))
    record_amex_extension_connected(db, uid, observed_at=mighty.iso())


def _probe() -> dict:
    return {
        "provider": "amex",
        "status": "ok",
        "auth_state": AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
        "url_visited": "https://global.americanexpress.com/overview",
        "final_url": None,
        "signed_in_detected": True,
        "private_data_detected": False,
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": None,
        "login_form_present": False,
        "probed_at": datetime.now(timezone.utc).isoformat(),
    }


def test_resolve_background_work_ignores_stale_pending_on_terminal():
    assert (
        resolve_background_work(
            readiness=UNVERIFIED,
            verification_lifecycle="completed",
            extraction_status=EXTRACTION_PENDING,
            private_data_state="not_yet_seen",
        )
        == BG_UNSUPPORTED_DATA
    )
    assert (
        resolve_background_work(
            readiness=UNVERIFIED,
            verification_lifecycle="completed",
            extraction_status=EXTRACTION_NO_ACCOUNT_DATA,
            private_data_state="unsupported",
        )
        == BG_UNSUPPORTED_DATA
    )
    assert (
        resolve_background_work(
            readiness=UNVERIFIED,
            verification_lifecycle="extracting",
            extraction_status=EXTRACTION_PENDING,
        )
        == BG_EXTRACTING
    )


def test_product_lifecycle_unsupported_has_next_action():
    lc = resolve_product_account_lifecycle(
        capability_state=CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA,
        verification_lifecycle="completed",
        last_confirmed_at="2026-07-28T12:00:00Z",
    )
    assert lc.state == UNSUPPORTED_DATA
    assert lc.timestamp
    assert lc.next_action


def test_no_qualifying_clears_pending_and_not_extracting(client):
    import app as mighty
    from mighty.account_readiness import AccountReadiness

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex(mighty, uid)
        db.execute(
            "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
            (EXTRACTION_PENDING, uid, "amex"),
        )
        db.commit()
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = db.execute(
            "SELECT api_key FROM users WHERE id=?", (uid,)
        ).fetchone()["api_key"]

    r = client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    )
    assert r.status_code == 200, r.get_json()

    with mighty.app.app_context():
        db = mighty.get_db()
        row = db.execute(
            "SELECT extraction_status FROM account_data WHERE user_id=? AND source=?",
            (uid, "amex"),
        ).fetchone()
        assert row is not None
        assert row["extraction_status"] == EXTRACTION_NO_ACCOUNT_DATA
        latest = get_latest_session_verification(db, uid, "amex")
        assert latest is not None
        assert latest.lifecycle == "completed"

        readiness = AccountReadiness(
            provider="amex",
            state=UNVERIFIED,
            status_label="Unable to verify",
            status_copy="",
            presentation_key="unknown",
            canonical_status="unverified",
            login_required=False,
            session_state="connected",
            access_cycle_id=vid,
            session_evidence_at=None,
            extraction_at=None,
            extraction_ok=False,
            extraction_correlated=False,
            verification_id=vid,
        )
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=readiness,
            discovered_from="Manual add",
            verification_lifecycle="completed",
            extraction_status=row["extraction_status"],
        )
        assert view.background_work != BG_EXTRACTING
        assert view.background_work == BG_UNSUPPORTED_DATA
        assert view.private_data_state == "unsupported"


def test_login_preserves_next_on_failed_auth(client):
    with client.session_transaction() as sess:
        sess.clear()
    client.get("/login?next=/enable-monitoring")
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
    r = client.post(
        "/login",
        data={
            "email": "nobody@example.com",
            "password": "wrong-password",
            "_csrf": csrf,
            "next": "/enable-monitoring",
        },
        follow_redirects=False,
    )
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert 'name="next"' in body
    assert 'value="/enable-monitoring"' in body


def test_value_pipeline_diagnostics_endpoint(client):
    r = client.get("/api/amex/value-pipeline-diagnostics")
    assert r.status_code == 200
    body = r.get_json()
    assert "stages" in body
    assert len(body["stages"]) >= 8


def test_account_status_next_action_honest_after_no_qualifying(client):
    """F1 audit: top-level next_action must not say 'Nothing… monitor automatically'."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex(mighty, uid)
        db.execute(
            "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
            (EXTRACTION_PENDING, uid, "amex"),
        )
        db.commit()
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = db.execute(
            "SELECT api_key FROM users WHERE id=?", (uid,)
        ).fetchone()["api_key"]

    assert client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    ).status_code == 200

    r = client.get("/api/account-status")
    assert r.status_code == 200
    body = r.get_json()
    accounts = body.get("accounts") or body.get("account_statuses") or []
    if isinstance(body.get("accounts"), dict):
        accounts = list(body["accounts"].values())
    amex = next(
        (
            a
            for a in accounts
            if isinstance(a, dict) and a.get("source") == "amex"
        ),
        None,
    )
    assert amex is not None, body.keys()
    nxt = (amex.get("next_action_text") or "").lower()
    assert "nothing" not in nxt
    assert "monitor this account automatically" not in nxt
    plc = amex.get("product_lifecycle") or {}
    assert plc.get("state") == UNSUPPORTED_DATA
    assert plc.get("next_action")
    assert amex.get("next_action_text") == plc.get("next_action")
    access = amex.get("customer_access") or {}
    assert access.get("background_work") != "Extracting"
    # Complete Amex Experience AT-05/N1: nested label matches unsupported-data.
    assert access.get("status_label") == "Logged in — no account data"
    assert access.get("status_label") != "Unable to verify"
    diag = client.get("/api/amex/value-pipeline-diagnostics").get_json()
    assert int(diag.get("events_sampled") or 0) > 0
