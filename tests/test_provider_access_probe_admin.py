"""Route tests for provider access probe API and admin page."""

import os
import secrets
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_probe_admin.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("MIGHTY_ADMIN_TEST", raising=False)
    monkeypatch.delenv("DISABLE_AUTOMATIC_PROVIDER_PROBES", raising=False)
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
        email = f"probe_admin_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    with c.session_transaction() as sess:
        uid = sess.get("user_id")
    with mighty.app.app_context():
        row = mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()
    c.email = email
    c.api_key = row["api_key"]
    return c


@pytest.fixture()
def admin_client(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    return client


AMEX_ACCOUNT_TEXT = """
Account Home
Membership Rewards
Available Points: 99,000
Card ending in 4321
Recent Activity and account services for your American Express card
"""

DELTA_ACCOUNT_TEXT = """
My SkyMiles
SkyMiles Number: 9876543210
Available Miles 22,100
Medallion Status Gold
My Trips and wallet on delta.com
"""


def test_probe_api_requires_key(client):
    r = client.post("/api/extension/provider-access-probe", json={"provider": "amex"})
    assert r.status_code == 401


def test_probe_api_records_amex_result(client):
    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": AMEX_ACCOUNT_TEXT,
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "signed_in_data_seen"
    assert body["auth_state"] == "private_data_visible"
    assert body["private_data_detected"] is True
    assert body["evidence_snippet"]


def test_probe_api_records_delta_needs_sign_in(client):
    r = client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "url_visited": "https://www.delta.com/login",
            "dom_text": "Sign In\nUser ID\nPassword\nForgot password",
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "needs_sign_in"
    assert body["auth_state"] == "login_page"


def test_admin_page_shows_auth_state_fields(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": AMEX_ACCOUNT_TEXT,
            "page_title": "Amex Account Home",
        },
    )
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    assert b"Auth state" in r.data
    assert b"private data visible" in r.data.lower() or b"private_data_visible" in r.data
    assert b"Amex Account Home" in r.data


def test_admin_run_probe_control_renders(admin_client):
    r = admin_client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Run Probe" in text
    assert "Run Probe — Amex" in text
    assert "Manual probe runner" in text
    assert "probe-lifecycle-badge" in text


def test_manual_trigger_requires_admin(client):
    r = client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r.status_code == 403


def test_manual_trigger_accepts_one_provider(admin_client):
    r = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["provider"] == "amex"
    assert body["lifecycle"] == "running"
    assert body["manual_run_id"]


def test_manual_trigger_rejects_unsupported_provider(admin_client):
    r = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "hilton"},
    )
    assert r.status_code == 400


def test_concurrent_probe_attempt_rejected(admin_client):
    r1 = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    assert r1.status_code == 200
    r2 = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "delta"},
    )
    assert r2.status_code == 409
    assert "already running" in r2.get_json()["error"].lower()


def test_manual_probe_completes_on_extension_post(admin_client, client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "delta"},
    )
    manual_run_id = start.get_json()["manual_run_id"]

    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "manual_run_id": manual_run_id,
            "url_visited": "https://www.delta.com/myprofile/",
            "dom_text": DELTA_ACCOUNT_TEXT,
        },
    )

    status = admin_client.get("/api/admin/provider-access-probe/run-status")
    assert status.status_code == 200
    body = status.get_json()
    assert body["lifecycle"] == "done"
    assert body["probe_run_id"]


def test_automatic_probe_disabled_in_development(client, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    from mighty.provider_access_probe import is_automatic_probe_disabled

    assert is_automatic_probe_disabled() is True

    r = client.get(
        "/api/extension/provider-access-probe/config",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert r.status_code == 200
    assert r.get_json()["automatic_probes_enabled"] is False


def test_automatic_probe_disabled_via_admin_test_flag(client, monkeypatch):
    monkeypatch.setenv("MIGHTY_ADMIN_TEST", "true")
    from mighty.provider_access_probe import is_automatic_probe_disabled

    assert is_automatic_probe_disabled() is True


def test_extension_manual_trigger_endpoint(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/run",
        json={"provider": "amex"},
    )
    manual_run_id = start.get_json()["manual_run_id"]

    r = client.get(
        "/api/extension/provider-access-probe/manual",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["lifecycle"] == "running"
    assert body["provider"] == "amex"
    assert body["manual_run_id"] == manual_run_id


def test_extension_manual_trigger_requires_key(client):
    r = client.get("/api/extension/provider-access-probe/manual")
    assert r.status_code == 401


def test_admin_page_shows_page_diagnostics_for_unknown(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": "",
            "page_diagnostics": {
                "ready_state": "complete",
                "body_exists": True,
                "body_text_length": 0,
                "visible_text_preview": "",
                "iframe_count": 0,
                "input_count": 0,
                "final_url": "https://www.americanexpress.com/en-us/account/",
            },
        },
    )
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Page diagnostics" in text
    assert "body_text_length=0" in text
    assert "blank_or_unloaded_page" in text or "blank or unloaded" in text.lower()


def test_admin_page_shows_deep_inspect_for_amex(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "amex",
            "url_visited": "https://www.americanexpress.com/en-us/account/",
            "dom_text": "",
            "page_diagnostics": {
                "ready_state": "complete",
                "body_text_length": 13,
                "visible_text_preview": "Give Feedback",
            },
            "deep_inspect": {
                "outer_html_length": 45000,
                "outer_html_preview": "<html><head><title>One App</title></head><body></body></html>",
                "iframe_count": 2,
                "iframes": [
                    {"index": 0, "src": "https://www.americanexpress.com/auth", "id": "sso", "name": "", "sandbox": ""},
                ],
                "shadow_root_count": 3,
                "script_count": 12,
                "script_srcs": ["https://www.americanexpress.com/app.js"],
                "cookie_names": ["sessionId"],
                "local_storage_keys": ["prefs"],
                "session_storage_keys": [],
                "content_script_injection_succeeded": True,
                "final_url": "https://www.americanexpress.com/en-us/account/",
                "page_title": "One App",
                "ready_state": "complete",
                "visible_text_preview": "Give Feedback",
                "spa_roots": [
                    {"key": "root", "exists": True, "child_element_count": 1, "inner_html_length": 42000, "text_length": 13},
                ],
                "mutation_timeline": {
                    "total_count": 5,
                    "first_mutation_ms": 200,
                    "last_mutation_ms": 2500,
                    "mutation_activity": "stopped_early",
                },
                "console_diagnostics": [
                    {"level": "warn", "message": "locale/account title unresolved"},
                ],
                "resource_diagnostics": {
                    "js_count": 42,
                    "css_count": 4,
                    "fetch_xhr_count": 6,
                    "failed_loads": [],
                    "slow_loads": [],
                },
                "framework_detection": ["React"],
                "observation_window": {
                    "observation_ms": 15000,
                    "start_dom_size": 43000,
                    "end_dom_size": 43000,
                    "visible_text_length_delta": 0,
                },
                "auth_network_trace": {
                    "request_count": 3,
                    "status_counts": {"401": 2, "200": 1},
                    "diagnostic_summary": "ReadUserSession.v1 returned 401; UpdateUserSession.v1 returned 401; cookies present at document level",
                    "highlighted_requests": [
                        {
                            "url": "https://functions.americanexpress.com/ReadUserSession.v1",
                            "method": "POST",
                            "status_code": 401,
                            "duration_ms": 95,
                            "response_header_names": ["content-type"],
                        },
                    ],
                    "status_401_requests": [
                        {"url": "https://functions.americanexpress.com/ReadUserSession.v1", "status_code": 401},
                        {"url": "https://functions.americanexpress.com/UpdateUserSession.v1", "status_code": 401},
                    ],
                },
            },
        },
    )
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Amex deep inspect" in text
    assert "Amex authentication network trace" in text
    assert "ReadUserSession.v1" in text
    assert "401" in text
    assert "Diagnostic:" in text
    assert "outer_html_length" in text
    assert "45000" in text
    assert "sessionId" in text
    assert "names/keys only" in text
    assert "SPA roots" in text
    assert "mutation_timeline" in text
    assert "framework_detection" in text
    assert "observation_window" in text


def test_admin_page_forbidden_for_non_admin(client):
    assert client.get("/admin/provider-access-probe").status_code == 403


def test_admin_page_loads_for_admin(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    assert b"Provider Access Probe" in r.data
    assert b"amex" in r.data


def test_admin_json_endpoint(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    client.post(
        "/api/extension/provider-access-probe",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "provider": "delta",
            "url_visited": "https://www.delta.com/myprofile/",
            "dom_text": DELTA_ACCOUNT_TEXT,
        },
    )
    r = client.get("/api/admin/provider-access-probe")
    assert r.status_code == 200
    providers = {p["provider"]: p for p in r.get_json()["providers"]}
    assert providers["delta"]["status"] == "signed_in_data_seen"
    assert providers["delta"]["auth_state"] == "private_data_visible"
    assert providers["amex"]["status"] == "not_started"


def test_admin_page_shows_bootstrap_trace_section(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Amex Bootstrap Trace" in text
    assert "bootstrap-trace-btn" in text
    assert "https://www.americanexpress.com/en-us/account/" in text


def test_admin_bootstrap_trace_start_and_extension_poll(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/bootstrap-trace",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/"},
    )
    assert start.status_code == 200
    body = start.get_json()
    trace_run_id = body["trace_run_id"]
    assert body["lifecycle"] == "running"

    pending = client.get(
        "/api/extension/provider-access-probe/bootstrap-trace",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert pending.status_code == 200
    assert pending.get_json()["trace_run_id"] == trace_run_id


def test_extension_bootstrap_trace_submit(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/bootstrap-trace",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/login"},
    )
    trace_run_id = start.get_json()["trace_run_id"]

    submit = client.post(
        "/api/extension/provider-access-probe/bootstrap-trace",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "trace_run_id": trace_run_id,
            "entry_url": "https://www.americanexpress.com/en-us/account/login",
            "navigation_timeline": {
                "initial_url": "https://www.americanexpress.com/en-us/account/login",
                "final_url": "https://www.americanexpress.com/en-us/account/login",
                "events": [
                    {"observed_at_ms": 0, "url": "https://www.americanexpress.com/en-us/account/login", "source": "initial"},
                ],
            },
            "bootstrap_requests": [
                {
                    "url": "https://functions.americanexpress.com/ReadUserSession.v1?token=secret",
                    "status_code": 401,
                    "start_time_ms": 500,
                    "response_header_names": ["content-type"],
                },
            ],
            "first_401_at_ms": 500,
            "first_401_url": "https://functions.americanexpress.com/ReadUserSession.v1?token=secret",
        },
    )
    assert submit.status_code == 200
    data = submit.get_json()
    assert data["lifecycle"] == "done"
    assert "401" in (data.get("diagnostic_summary") or "")
    assert "secret" not in str(data.get("trace"))

    status = admin_client.get("/api/admin/provider-access-probe/bootstrap-trace-status")
    assert status.status_code == 200
    assert status.get_json()["lifecycle"] == "done"


def test_bootstrap_trace_concurrent_rejected(admin_client):
    admin_client.post(
        "/api/admin/provider-access-probe/bootstrap-trace",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/"},
    )
    conflict = admin_client.post(
        "/api/admin/provider-access-probe/bootstrap-trace",
        json={"entry_url": "https://global.americanexpress.com/login"},
    )
    assert conflict.status_code == 409


def test_admin_page_shows_live_session_comparator_section(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Amex Live Session Comparator" in text
    assert "live-session-comparison-btn" in text
    assert "Compare — global.americanexpress.com/overview" in text
    assert "Differences only" in text


def test_admin_live_session_comparison_accepts_global_overview_entry(admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/live-session-comparison",
        json={"entry_url": "https://global.americanexpress.com/overview"},
    )
    assert start.status_code == 200
    body = start.get_json()
    assert body["entry_url"] == "https://global.americanexpress.com/overview"
    assert body["lifecycle"] == "running"


def test_admin_live_session_comparison_start_and_extension_poll(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/live-session-comparison",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/"},
    )
    assert start.status_code == 200
    body = start.get_json()
    comparison_run_id = body["comparison_run_id"]
    assert body["lifecycle"] == "running"

    pending = client.get(
        "/api/extension/provider-access-probe/live-session-comparison",
        headers={"X-Mighty-Key": client.api_key},
    )
    assert pending.status_code == 200
    assert pending.get_json()["comparison_run_id"] == comparison_run_id


def test_extension_live_session_comparison_submit(client, admin_client):
    start = admin_client.post(
        "/api/admin/provider-access-probe/live-session-comparison",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/"},
    )
    comparison_run_id = start.get_json()["comparison_run_id"]

    submit = client.post(
        "/api/extension/provider-access-probe/live-session-comparison",
        headers={"X-Mighty-Key": client.api_key},
        json={
            "comparison_run_id": comparison_run_id,
            "entry_url": "https://www.americanexpress.com/en-us/account/",
            "logged_in_tab": {
                "found": True,
                "final_url": "https://www.americanexpress.com/en-us/account/",
                "page_title": "Account Home",
                "navigator_user_agent": "Mozilla/5.0",
                "auth_session_requests": [
                    {
                        "url": "https://functions.americanexpress.com/ReadUserSession.v1",
                        "status_code": 200,
                        "with_credentials": True,
                        "request_header_names": ["accept"],
                        "response_header_names": ["content-type"],
                    },
                ],
            },
            "bootstrap_probe_tab": {
                "found": True,
                "final_url": "https://www.americanexpress.com/en-us/account/login",
                "page_title": "Login",
                "navigator_user_agent": "Mozilla/5.0",
                "auth_session_requests": [
                    {
                        "url": "https://functions.americanexpress.com/ReadUserSession.v1",
                        "status_code": 400,
                        "with_credentials": True,
                        "request_header_names": ["accept"],
                        "response_header_names": ["content-type"],
                    },
                    {
                        "url": "https://functions.americanexpress.com/UpdateUserSession.v1",
                        "status_code": 401,
                        "with_credentials": False,
                    },
                ],
            },
        },
    )
    assert submit.status_code == 200
    data = submit.get_json()
    assert data["lifecycle"] == "done"
    comparison = data.get("comparison") or {}
    assert comparison.get("field_diffs")
    assert "400" in (data.get("diagnostic_summary") or "")

    status = admin_client.get("/api/admin/provider-access-probe/live-session-comparison-status")
    assert status.status_code == 200
    assert status.get_json()["lifecycle"] == "done"


def test_live_session_comparison_concurrent_rejected(admin_client):
    admin_client.post(
        "/api/admin/provider-access-probe/live-session-comparison",
        json={"entry_url": "https://www.americanexpress.com/en-us/account/"},
    )
    conflict = admin_client.post(
        "/api/admin/provider-access-probe/live-session-comparison",
        json={"entry_url": "https://global.americanexpress.com/login"},
    )
    assert conflict.status_code == 409


def test_admin_live_session_page_shows_logged_in_tab_found(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", client.email)
    import app as mighty
    from mighty.provider_access_probe import (
        complete_live_session_comparison,
        build_amex_live_session_comparison,
        start_live_session_comparison,
        PROBE_LIFECYCLE_DONE,
    )

    with mighty.app.app_context():
        db = mighty.get_db()
        with client.session_transaction() as sess:
            user_id = sess["user_id"]
        state = start_live_session_comparison(db, user_id, "https://www.americanexpress.com/en-us/account/")
        comparison = build_amex_live_session_comparison({
            "entry_url": "https://www.americanexpress.com/en-us/account/",
            "logged_in_tab": {
                "found": True,
                "final_url": "https://global.americanexpress.com/overview",
                "page_title": "Overview",
            },
            "bootstrap_probe_tab": {"found": True, "final_url": "https://www.americanexpress.com/en-us/account/login"},
        })
        complete_live_session_comparison(
            db,
            user_id,
            state["comparison_run_id"],
            lifecycle=PROBE_LIFECYCLE_DONE,
            comparison=comparison,
        )

    r = client.get("/admin/provider-access-probe")
    assert r.status_code == 200
    text = r.data.decode("utf-8")
    assert "Logged-in tab:" in text
    assert "found —" in text
    assert "global.americanexpress.com/overview" in text
    assert "Logged-in tab:</strong> not found" not in text
