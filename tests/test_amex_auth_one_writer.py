"""Phase 1 Fix 3: one Amex auth writer + auth-only diagnostic console."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mighty.amex_login_state_console import (
    AmexLoginStateDiagnostic,
    resolve_amex_login_state_diagnostic,
)
from mighty.authentication_state import AuthenticationState
from mighty.provider_access_manager import (
    complete_provider_access_check,
    finish_provider_access_check,
    request_provider_access_check,
)
from mighty.provider_access_probe import AUTH_LOGIN_PAGE, ensure_probe_tables
from mighty.provider_session_state import (
    ensure_provider_session_state_tables,
    get_provider_session_state,
)
from mighty.session_verification import ensure_session_verification_tables


BG = (Path(__file__).resolve().parents[1] / "extension" / "background.js").read_text()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import secrets

    import app as mighty

    db_path = str(tmp_path / "amex_auth_one_writer.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
        email = f"auth_one_writer_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


def _db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_probe_tables(db)
    ensure_provider_session_state_tables(db)
    ensure_session_verification_tables(db)
    return db


def _probe_result(**overrides):
    base = {
        "provider": "amex",
        "status": "ok",
        "auth_state": "unknown",
        "url_visited": "https://global.americanexpress.com/overview",
        "final_url": "https://global.americanexpress.com/overview",
        "login_form_present": False,
        "signed_in_detected": False,
        "private_data_detected": False,
        "failure_reason": "",
    }
    base.update(overrides)
    return base


def _session_api_inspect(status_code: int = 200):
    return {
        "auth_network_trace": {
            "auth_session_requests": [
                {
                    "url": (
                        "https://global.americanexpress.com/api/servicing/v1/"
                        "ReadUserSession.v1"
                    ),
                    "status_code": status_code,
                    "start_time_ms": 50,
                }
            ]
        }
    }


# ── Extension one-writer contracts ─────────────────────────────────────────────


def test_probe_amex_connection_state_is_noop_stub():
    assert "LEGACY ACCESS PATH — DO NOT EXTEND / DISABLED ON PRODUCT PATH" in BG
    stub = BG.split("async function probeAmexConnectionState")[1].split(
        "// ── Provider Access Probe"
    )[0]
    assert "_probeAmexLoggedIn()" not in stub
    assert "_postAmexNeedsLogin" not in stub
    assert "_postAmexConnected" not in stub
    assert "canonical auth path only" in stub
    assert "session-verification/ensure-due" in stub


def test_is_amex_access_cycle_authenticated_requires_server_decision():
    fn = BG.split("function _isAmexAccessCycleAuthenticated")[1].split(
        "function _isAmexSafeToAttemptExtraction"
    )[0]
    assert "verification_decision" in fn
    assert "decision === 'connected'" in fn
    assert "payload.signed_in_detected" not in fn
    assert "authenticated_no_private_data" not in fn
    assert "extraction_required" not in fn
    assert "missing server verification_decision" in fn


# ── Passive endpoints must not write PSS ─────────────────────────────────────────


def test_passive_amex_needs_login_does_not_write_pss(client):
    import app as mighty
    from tests.test_login_truth_admin import _prepare_amex_waiting, _uid

    api_key = _prepare_amex_waiting(client, mighty)
    r = client.post(
        "/api/extension/amex/needs-login",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("pss_written") is False
    assert body.get("verification_enqueued") is True

    uid = _uid(client)
    with mighty.app.app_context():
        assert get_provider_session_state(mighty.get_db(), uid, "amex") is None


def test_passive_amex_connected_does_not_write_pss(client):
    import app as mighty
    from tests.test_login_truth_admin import _prepare_amex_waiting, _uid

    api_key = _prepare_amex_waiting(client, mighty)
    r = client.post(
        "/api/extension/amex/connected",
        headers={"X-Mighty-Key": api_key},
        json={"session_verified": True},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body.get("pss_written") is False
    assert body.get("verification_enqueued") is True

    uid = _uid(client)
    with mighty.app.app_context():
        assert get_provider_session_state(mighty.get_db(), uid, "amex") is None


def test_canonical_verification_still_writes_pss_via_decide_amex():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(deep_inspect=_session_api_inspect(200)),
        verification_id=verification.verification_id,
    )
    assert result["authentication_state"] == AuthenticationState.SIGNED_IN.value
    assert result["verification_decision"] == "connected"
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"


def test_canonical_login_page_writes_signed_out_via_decide_amex():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            url_visited="https://www.americanexpress.com/en-us/account/log-in",
            final_url="https://www.americanexpress.com/en-us/account/log-in",
        ),
        verification_id=verification.verification_id,
    )
    assert result["authentication_state"] == AuthenticationState.SIGNED_OUT.value
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "signed_out"


# ── Diagnostic console ─────────────────────────────────────────────────────────


def test_amex_login_state_diagnostic_from_terminal_cycle():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    complete_provider_access_check(
        db,
        uid,
        _probe_result(deep_inspect=_session_api_inspect(200)),
        verification_id=verification.verification_id,
    )
    finish_provider_access_check(
        db,
        uid,
        verification.verification_id,
        lifecycle="completed",
        terminal_reason="authenticated",
        terminal_source="test",
    )
    diag = resolve_amex_login_state_diagnostic(db, uid)
    assert diag.authentication_state == AuthenticationState.SIGNED_IN.value
    assert diag.verification_id == verification.verification_id
    assert diag.access_cycle_id == verification.verification_id
    blob = str(diag.to_dict()).lower()
    assert "cookie" not in blob
    assert "password" not in blob
    assert "token" not in blob
    assert "balance" not in blob


def test_admin_amex_login_state_page_requires_admin(client):
    r = client.get("/admin/amex-login-state")
    assert r.status_code in {302, 401, 403}


def test_admin_amex_login_state_in_tools_nav():
    from mighty.admin_debug import ADMIN_TOOLS

    slugs = {slug for slug, _label, _desc in ADMIN_TOOLS}
    assert "amex-login-state" in slugs


def test_render_amex_login_state_page_has_run_button_no_secrets():
    import app as mighty
    from mighty.admin_debug import render_amex_login_state_page

    diag = AmexLoginStateDiagnostic(
        authentication_state="login_unknown",
        confidence="n/a",
        terminal_reason="timeout",
        terminal_source="test",
        verification_id="v-1",
        access_cycle_id="v-1",
        lifecycle="timed_out",
        duration_ms=1200,
        started_at=None,
        completed_at=None,
        requested_at=None,
        trigger_source="admin_debug",
        evidence_summary=None,
        evidence_type=None,
        pss_state=None,
        pss_source=None,
        extension_version="1.2.3",
        deployment_sha="abc123",
        evidence_flags=("timeout",),
    )
    with mighty.app.app_context():
        html = render_amex_login_state_page(diag)
    assert "Run login-state check" in html
    assert "login_unknown" in html
    assert "/api/admin/amex-login-state/run" in html
    # Disclaimer may mention cookies; payload must not include secret values.
    assert "session=" not in html.lower()
    assert "password" not in html.lower()
    assert "membership rewards" not in html.lower()
    assert "account number" not in html.lower()
    assert "No balances, cookies, tokens" in html or "cookies, tokens" in html.lower()
