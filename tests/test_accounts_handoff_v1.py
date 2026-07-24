"""Accounts + First-Data Handoff V1 — focused acceptance tests."""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_status import AccountStatus
from mighty.home_projection import project_home
from mighty.home_state import HomeState, resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy


def _escape(s: object) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _waiting_acct(name: str = "American Express", source: str = "amex") -> AccountStatus:
    return AccountStatus(
        source=source,
        display_name=name,
        status="waiting_for_extension",
        presentation_key="updating",
        presentation_label="Waiting",
        last_successful_sync_at=None,
        current_attempt_at=None,
        last_error=None,
        user_action_label=None,
        user_action_url=f"https://example.test/{source}",
    )


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_handoff.db")
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
        email = f"hand_{os.urandom(4).hex()}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c


def test_empty_home_is_gmail_first_not_chrome_setup():
    result = resolve_home_state(accounts=[])
    assert result.state == HomeState.EMPTY
    assert result.featured.cta_label == user_copy.HOME_EMPTY_CTA
    assert result.featured.cta_url == "/email-scan"
    assert "extension-setup" not in (result.featured.cta_url or "")


def test_handoff_confirmation_names_account_and_next_step():
    result = resolve_home_state(
        accounts=[_waiting_acct()],
        provider_open_urls={"amex": "https://amex.test/"},
    )
    projection = project_home(result, first_name="Pat", today_label="Thu")
    assert projection.story_kind == "handoff"
    assert "American Express" in (projection.featured.title or "")
    assert projection.featured.cta_label == "Visit American Express"
    assert projection.featured.cta_url == "https://amex.test/"
    assert projection.answer == user_copy.HOME_BRIEFING_ANSWER_HANDOFF


def test_handoff_needs_mighty_in_chrome_when_flagged():
    result = resolve_home_state(
        accounts=[_waiting_acct()],
        worker_setup_needed=True,
    )
    assert result.featured.cta_label == user_copy.CTA_SET_UP_WORKER
    assert result.featured.cta_url == "/extension-setup"
    assert "Mighty in Chrome" in (result.featured.body or "")


def test_handoff_verifying_has_no_primary_cta():
    result = resolve_home_state(
        accounts=[
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="checking",
                presentation_key="checking",
                presentation_label="Checking",
                last_successful_sync_at=None,
                current_attempt_at="2026-07-23T12:00:00+00:00",
                last_error=None,
                user_action_label=None,
                user_action_url=None,
            )
        ],
        sync_running=True,
        updating_display_name="American Express",
    )
    assert result.state == HomeState.WAITING
    assert result.featured.cta_label is None
    assert "do not need to do anything" in (result.featured.body or "").lower()


def test_ready_home_says_youre_good_without_primary_cta():
    result = resolve_home_state(
        accounts=[
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="up_to_date",
                presentation_key="ready",
                presentation_label="Connected",
                last_successful_sync_at="2026-07-23T12:00:00+00:00",
                current_attempt_at=None,
                last_error=None,
                user_action_label=None,
                user_action_url=None,
            )
        ],
    )
    projection = project_home(result, first_name="Pat", today_label="Thu")
    assert projection.story_kind == "all_clear"
    assert projection.answer == "You're good."
    assert projection.featured.cta_label is None


def test_customer_copy_avoids_worker_jargon():
    assert user_copy.ROLE_EXTENSION == "Mighty in Chrome"
    assert user_copy.WORKER_OPEN_ACCOUNT_CENTER == "Open Accounts"
    assert "worker" not in user_copy.CTA_SET_UP_WORKER.lower()
    assert "worker" not in user_copy.EXT_SETUP_TITLE.lower()
    html = render_home_page(
        resolve_home_state(accounts=[_waiting_acct()]),
        first_name="Pat",
        today_label="Thu",
        escape=_escape,
    )
    assert "Mighty is beginning to manage" in html
    assert "Set up worker" not in html
    assert "Account Center" not in html


def test_account_center_redirect(client):
    r = client.get("/account-center", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/credentials")


def test_credentials_is_accounts_destination(client):
    r = client.get("/credentials")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Accounts" in body


def test_home_requests_mighty_in_chrome_when_enrolled_without_extension(client):
    import app as mighty
    from mighty.account_state import ensure_account_state_tables, recompute_account_state

    with client.session_transaction() as sess:
        uid = sess["user_id"]
    with mighty.app.app_context():
        db = mighty.get_db()
        ensure_account_state_tables(db)
        now = mighty.iso()
        stub = mighty.encrypt_account_data(
            uid, {"items": [], "sync_status": "needs_first_visit"},
        )
        db.execute(
            "INSERT INTO account_credentials "
            "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, "amex", "", "", "", now, now),
        )
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "connection_status, sync_status) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                uid, "amex", "American Express", "💳", "#e8f0fe", stub, "",
                "waiting_for_extension", "needs_first_visit",
            ),
        )
        recompute_account_state(db, uid, "amex")
        db.execute(
            "UPDATE users SET extension_version=NULL, extension_last_seen_at=NULL, "
            "onboarded=1 WHERE id=?",
            (uid,),
        )
        db.commit()
    r = client.get("/dashboard")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "Set up Mighty in Chrome" in body
    assert "You're good." not in body and "You&#x27;re good." not in body
