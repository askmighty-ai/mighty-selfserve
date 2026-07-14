"""Admin-only Amex verification timeline debug endpoint (investigation)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_timeline_admin.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("MIGHTY_AMEX_TIMELINE_DEBUG", raising=False)
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
        email = f"admin_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c, email, mighty


def _seed_amex_timeline(mighty, uid: str) -> None:
    from mighty.customer_capability_presentation import (
        ensure_customer_capability_presentation_tables,
        save_stable_capability,
    )
    from mighty.capability_state import build_capability_view
    from mighty.customer_account_access import CustomerAccountAccessView
    from mighty.customer_capability_presentation import present_customer_capability
    from mighty.session_verification import (
        complete_session_verification,
        ensure_session_verification_tables,
        mark_session_verification_running,
        request_session_verification,
    )

    db = mighty.get_db()
    ensure_session_verification_tables(db)
    ensure_customer_capability_presentation_tables(db)

    def run(ts: datetime) -> str:
        ver = request_session_verification(db, uid, "amex", now=ts)
        mark_session_verification_running(db, uid, ver.verification_id, now=ts)
        complete_session_verification(
            db,
            uid,
            ver.verification_id,
            terminal_reason="signed_out",
            terminal_source="test",
            now=ts,
        )
        return ver.verification_id

    vid_a = run(datetime(2026, 7, 14, 18, 29, tzinfo=timezone.utc))
    run(datetime(2026, 7, 14, 21, 23, tzinfo=timezone.utc))
    run(datetime(2026, 7, 14, 22, 36, tzinfo=timezone.utc))

    access = CustomerAccountAccessView(
        provider="amex",
        display_name="American Express",
        readiness="signed_out",
        session_state="signed_out",
        private_data_state="not_seen",
        last_confirmed_at="2026-07-14T18:29:00+00:00",
        active_verification_lifecycle="completed",
        discovered_from="manual",
        user_action_required=True,
        user_action_text="Sign in",
        user_action_url="https://www.americanexpress.com/",
        live_access="Sign in required",
        private_data_label="Not seen",
        background_work="None",
        meaning="Signed out",
        status_label="Sign in required",
        access_cycle_id=vid_a,
    )
    presented = present_customer_capability(
        build_capability_view(access, verification_id=vid_a),
        access_view=access,
        now=datetime(2026, 7, 14, 18, 30, tzinfo=timezone.utc),
    )
    save_stable_capability(db, uid, presented, access_view=access)


def test_endpoint_disabled_without_flag(client):
    c, email, _ = client
    import app as mighty

    mighty.app.config["TESTING"] = True
    # Non-admin still forbidden; admin with flag off → 404.
    os.environ["ADMIN_EMAIL"] = email
    r = c.get("/api/admin/debug/amex-verification-timeline")
    assert r.status_code == 404
    assert r.get_json()["error"] == "debug_endpoint_disabled"


def test_endpoint_forbidden_for_non_admin(client, monkeypatch):
    c, _email, _ = client
    monkeypatch.setenv("MIGHTY_AMEX_TIMELINE_DEBUG", "1")
    monkeypatch.setenv("ADMIN_EMAIL", "other@example.com")
    r = c.get("/api/admin/debug/amex-verification-timeline")
    assert r.status_code == 403


def test_endpoint_returns_sanitized_timeline(client, monkeypatch):
    c, email, mighty = client
    monkeypatch.setenv("MIGHTY_AMEX_TIMELINE_DEBUG", "1")
    monkeypatch.setenv("ADMIN_EMAIL", email)

    with c.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_amex_timeline(mighty, uid)

    r = c.get("/api/admin/debug/amex-verification-timeline")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["sanitized"] is True
    assert body["user_id"] == uid
    assert body["provider"] == "amex"
    assert "deployment_sha" in body
    assert body["datastore"]["engine"] == "sqlite"
    assert body["verifications"]["window_row_count"] >= 3
    assert body["persisted_presentation"] is not None
    assert body["persisted_presentation"]["payload_summary_kind"] in {
        None,
        "signed_out_last_confirmed",
        "other_customer_copy",
    }
    dash = body["presentation_selection"]["dashboard"]
    api = body["presentation_selection"]["api_account_status"]
    assert body["presentation_selection"]["dashboard_and_api_select_same_records"] is True
    assert dash["selected_terminal_verification_id"]
    assert api["selected_terminal_verification_id"] == dash["selected_terminal_verification_id"]
    assert "clock_comparison" in body
    assert body["clock_comparison"]["verifications_after_1129_count"] >= 1

    blob = json.dumps(body)
    assert "password" not in blob.lower()
    assert "cookie" not in blob.lower()
    assert "@test.local" not in blob


def test_report_builder_direct(client, monkeypatch):
    from mighty.verification_timeline_diagnostics import (
        build_amex_verification_timeline_report,
        error_message_code_only,
    )

    assert error_message_code_only("signed_out: user left") == "signed_out"
    assert error_message_code_only("probe_navigation_error") == "probe_navigation_error"
    assert error_message_code_only("Secret token abc123") == "redacted_non_code"

    c, email, mighty = client
    monkeypatch.setenv("ADMIN_EMAIL", email)
    with c.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        _seed_amex_timeline(mighty, uid)
        report = build_amex_verification_timeline_report(mighty.get_db(), uid)
    assert report["verifications"]["all_row_count"] == 3
    assert report["clock_comparison"]["primary_hypothesis"] in {
        "C_stale_persisted_presentation_timestamp",
        "B_older_verification_incorrectly_selected",
        "A_true_completion_of_newest_verification",
        "E_no_newer_verification_created",
        None,
    }


def test_deployment_sha_env_order_no_git(monkeypatch):
    from mighty.verification_timeline_diagnostics import deployment_sha

    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("SOURCE_VERSION", raising=False)
    monkeypatch.delenv("COMMIT_SHA", raising=False)
    assert deployment_sha() == "unknown"

    monkeypatch.setenv("COMMIT_SHA", "commitsha123456")
    assert deployment_sha() == "commitsha123"

    monkeypatch.setenv("SOURCE_VERSION", "sourceverzzzzzz")
    assert deployment_sha() == "sourceverzzz"

    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abcdef1234567890")
    assert deployment_sha() == "abcdef123456"


def test_health_exposes_deployment_sha_from_env(client, monkeypatch):
    c, _email, _ = client
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "deadbeefcafebabe")
    r = c.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["deployment_sha"] == "deadbeefcafe"
    assert body["git_sha"] == body["deployment_sha"]


def test_endpoint_not_in_admin_navigation():
    from mighty.admin_debug import ADMIN_TOOLS

    slugs = [slug for slug, *_ in ADMIN_TOOLS]
    assert "amex-verification-timeline" not in slugs
    joined = " ".join(slugs)
    assert "amex-verification" not in joined


def test_endpoint_accepts_explicit_user_id(client, monkeypatch):
    c, email, mighty = client
    monkeypatch.setenv("MIGHTY_AMEX_TIMELINE_DEBUG", "1")
    monkeypatch.setenv("ADMIN_EMAIL", email)
    with c.session_transaction() as sess:
        admin_uid = sess["user_id"]
    other_uid = "other-user-id-for-diag"
    with mighty.app.app_context():
        _seed_amex_timeline(mighty, other_uid)

    r = c.get(f"/api/admin/debug/amex-verification-timeline?user_id={other_uid}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["user_id"] == other_uid
    assert body["user_id"] != admin_uid
    assert body["verifications"]["all_row_count"] == 3
