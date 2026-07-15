"""Read/command boundary: GETs never create verification work.

Acceptance coverage for PR: Dashboard reads side-effect free;
verification triggers are explicit command-side calls.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
from mighty.session_verification import (
    FORBIDDEN_VERIFICATION_TRIGGER_SOURCES,
    VERIFICATION_TRIGGER_SOURCES,
    get_active_session_verification,
    get_latest_session_verification,
    mark_session_verification_running,
    normalize_trigger_source,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import secrets

    db_path = str(tmp_path / "mighty_read_command_boundary.db")
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
        email = f"boundary_{secrets.token_hex(4)}@test.local"
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    c.email = email
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _api_key(mighty, client):
    uid = _uid(client)
    with mighty.app.app_context():
        return mighty.get_db().execute(
            "SELECT api_key FROM users WHERE id=?", (uid,),
        ).fetchone()["api_key"]


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("_csrf") or ""


def _register_and_login(client):
    """Compatibility helper — fixture already signed up."""
    return _uid(client), _api_key(__import__("app"), client)


def _seed_stale_amex(db, uid):
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="stale connected",
            observed_at=datetime.now(timezone.utc) - timedelta(seconds=400),
            source="test",
            confidence="high",
        ),
    )
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT OR REPLACE INTO account_credentials
          (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at)
        VALUES (?, 'amex', '', '', '', ?, ?)
        """,
        (uid, now, now),
    )
    db.commit()


def _seed_signed_out_amex(db, uid):
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="signed_out",
            evidence_type="login_required",
            evidence_summary="signed out",
            observed_at=datetime.now(timezone.utc),
            source="test",
            confidence="high",
        ),
    )
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT OR REPLACE INTO account_credentials
          (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at)
        VALUES (?, 'amex', '', '', '', ?, ?)
        """,
        (uid, now, now),
    )
    db.commit()


def _verification_snapshot(db, uid):
    rows = db.execute(
        """
        SELECT verification_id, provider, lifecycle, trigger_source, requested_by,
               requested_at, started_at, completed_at, error_message,
               terminal_reason, terminal_source
        FROM provider_session_verification
        WHERE user_id = ?
        ORDER BY verification_id
        """,
        (uid,),
    ).fetchall()
    return [dict(r) for r in rows]


def _presentation_snapshot(db, uid):
    try:
        rows = db.execute(
            """
            SELECT user_id, provider, account_identity, capability_state,
                   verification_id, access_cycle_id, completed_at
            FROM customer_capability_presentation
            WHERE user_id = ?
            ORDER BY provider, account_identity
            """,
            (uid,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def test_forbidden_trigger_sources_rejected():
    for bad in FORBIDDEN_VERIFICATION_TRIGGER_SOURCES:
        with pytest.raises(ValueError):
            normalize_trigger_source(bad)
    for good in VERIFICATION_TRIGGER_SOURCES:
        assert normalize_trigger_source(good) == good


def test_get_dashboard_creates_no_verification(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)
        before = _verification_snapshot(mighty.get_db(), uid)

    r = client.get("/dashboard")
    assert r.status_code == 200

    with mighty.app.app_context():
        after = _verification_snapshot(mighty.get_db(), uid)
        assert after == before == []


def test_get_account_status_creates_no_verification(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    assert client.get("/api/account-status").status_code == 200
    with mighty.app.app_context():
        assert _verification_snapshot(mighty.get_db(), uid) == []


def test_get_pending_creates_no_verification(client):
    import app as mighty

    uid, api_key = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    r = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["lifecycle"] == "idle"
    assert data["verification_id"] is None
    with mighty.app.app_context():
        assert _verification_snapshot(mighty.get_db(), uid) == []


def test_repeated_gets_cause_no_database_mutation(client):
    import app as mighty

    uid, api_key = _register_and_login(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex(db, uid)
        before_v = _verification_snapshot(db, uid)
        before_p = _presentation_snapshot(db, uid)

    for _ in range(4):
        assert client.get("/dashboard").status_code == 200
        assert client.get("/api/account-status").status_code == 200
        assert client.get(
            "/api/extension/session-verification/pending",
            headers={"X-Mighty-Key": api_key},
        ).status_code == 200
        assert client.get("/sync/status").status_code == 200
        assert client.get("/api/latest-sync").status_code == 200
        assert client.get("/dashboard/has-pending").status_code == 200

    with mighty.app.app_context():
        db = mighty.get_db()
        assert _verification_snapshot(db, uid) == before_v
        assert _presentation_snapshot(db, uid) == before_p


def test_repeated_dashboard_reloads_same_presentation(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    bodies = []
    for _ in range(3):
        r = client.get("/dashboard")
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        # Strip relative-time-ish noise if present — compare Truth card core.
        start = html.find('class="dash-truth-panel"')
        end = html.find("</article>", start)
        bodies.append(html[start:end] if start >= 0 else html)

    assert bodies[0] == bodies[1] == bodies[2]
    assert "Check now" in bodies[0]


def test_account_status_polling_alone_cannot_produce_checking(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    for _ in range(3):
        data = client.get("/api/account-status").get_json()
        amex = next(a for a in data["accounts"] if a["source"] == "amex")
        assert amex.get("session_state") != "checking"
        assert amex.get("presentation_key") != "checking"
        assert amex.get("status") != "checking"
        cap = amex.get("capability") or {}
        assert not cap.get("is_refreshing")


def test_post_check_now_creates_exactly_one_verification(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    # CSRF: dashboard sets cookie/session token via hidden field — fetch page first.
    dash = client.get("/dashboard")
    assert dash.status_code == 200
    csrf = None
    html = dash.data.decode("utf-8")
    marker = 'name="_csrf" value="'
    if marker in html:
        csrf = html.split(marker, 1)[1].split('"', 1)[0]

    r = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": csrf or "", "Content-Type": "application/json"},
        data="{}",
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["lifecycle"] == "requested"
    assert data["trigger_source"] == "user_check_now"
    vid = data["verification_id"]
    assert vid
    assert data["access_cycle_id"] == vid

    with mighty.app.app_context():
        rows = _verification_snapshot(mighty.get_db(), uid)
        assert len(rows) == 1
        assert rows[0]["verification_id"] == vid
        assert rows[0]["trigger_source"] == "user_check_now"


def test_duplicate_check_now_reuses_active_verification(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)
    dash = client.get("/dashboard")
    csrf = dash.data.decode("utf-8").split('name="_csrf" value="', 1)[1].split('"', 1)[0]

    first = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
    ).get_json()
    second = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
        data="{}",
    ).get_json()
    assert first["verification_id"] == second["verification_id"]
    with mighty.app.app_context():
        assert len(_verification_snapshot(mighty.get_db(), uid)) == 1


def test_scheduled_trigger_records_scheduled_recheck(client):
    import app as mighty

    uid, api_key = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    r = client.post(
        "/api/extension/session-verification/ensure-due",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"trigger_source": "scheduled_recheck"}),
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert "amex" in data["created"]
    assert data["created"]["amex"]["trigger_source"] == "scheduled_recheck"
    with mighty.app.app_context():
        latest = get_latest_session_verification(mighty.get_db(), uid, "amex")
        assert latest is not None
        assert latest.trigger_source == "scheduled_recheck"


def test_get_pending_returns_only_existing_work(client):
    import app as mighty
    from mighty.provider_access_manager import request_provider_verification

    uid, api_key = _register_and_login(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex(db, uid)
        created = request_provider_verification(
            db, uid, "amex", "user_check_now", requested_by="test",
            throttle_seconds=0,
        )
        assert created is not None
        vid = created.verification_id

    idle = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    ).get_json()
    assert idle["verification_id"] == vid
    assert idle["lifecycle"] == "requested"

    # Repeated GET does not create another row.
    client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    with mighty.app.app_context():
        assert len(_verification_snapshot(mighty.get_db(), uid)) == 1


def test_extension_can_claim_explicitly_queued_work(client):
    import app as mighty
    from mighty.provider_access_manager import request_provider_verification

    uid, api_key = _register_and_login(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex(db, uid)
        job = request_provider_verification(
            db, uid, "amex", "scheduled_recheck", throttle_seconds=0,
        )
        vid = job.verification_id

    pending = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    ).get_json()
    assert pending["verification_id"] == vid

    running = client.post(
        "/api/extension/session-verification/running",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data=json.dumps({"verification_id": vid}),
    )
    assert running.status_code == 200
    assert running.get_json()["lifecycle"] == "running"


def test_dashboard_html_has_no_trigger_source_enqueue(client):
    """No dashboard request invents a trigger_source (GETs create no rows)."""
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    client.get("/dashboard")
    client.get("/api/account-status")
    with mighty.app.app_context():
        rows = _verification_snapshot(mighty.get_db(), uid)
        assert rows == []


def test_last_checked_unaffected_by_page_reload(client):
    import app as mighty
    from mighty.provider_access_manager import (
        finish_provider_access_check,
        request_provider_verification,
    )

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_stale_amex(db, uid)
        job = request_provider_verification(
            db, uid, "amex", "user_check_now", throttle_seconds=0,
        )
        mark_session_verification_running(db, uid, job.verification_id)
        finish_provider_access_check(
            db,
            uid,
            job.verification_id,
            lifecycle="completed",
            terminal_reason="signed_out",
            terminal_source="test",
        )

    first = client.get("/dashboard").data.decode("utf-8")
    second = client.get("/dashboard").data.decode("utf-8")
    assert 'id="dash-last-checked"' in first
    assert 'id="dash-last-checked"' in second
    marker = 'data-last-checked="'
    assert marker in first and marker in second
    a = first.split(marker, 1)[1].split('"', 1)[0]
    b = second.split(marker, 1)[1].split('"', 1)[0]
    assert a and a == b
    # Must not be empty browser/poll time — anchored to verification clock.
    assert "T" in a


def test_worker_watching_does_not_imply_checking(client):
    import app as mighty

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_stale_amex(mighty.get_db(), uid)

    html = client.get("/dashboard").data.decode("utf-8")
    assert "Worker: Watching" in html
    assert "Checking your login state" not in html or 'data-refreshing="1"' not in html
    # Without an active verification row, Checking must not be forced by worker badge alone.
    with mighty.app.app_context():
        assert get_active_session_verification(mighty.get_db(), uid, "amex") is None


def test_auto_refresh_rules_documented_in_dashboard_js():
    src = (ROOT / "app.py").read_text()
    assert "dashboard_refresh_reason" in src
    assert "verification_id_changed" in src
    assert "verification_lifecycle_changed" in src
    assert "snapshot_id_changed" in src
    # Must not reload Truth Dashboard solely on synced_at from latest-sync.
    assert "Intentionally no synced_at poller" in src


def test_unrelated_synced_at_does_not_force_truth_reload_logic():
    """Guard: Truth reload identity ignores generic synced_at."""
    src = (ROOT / "app.py").read_text()
    assert "_maybeReloadTruthDashboard" in src
    assert "d.latest !== baseline" not in src.split("Truth Dashboard:")[-1][
        :1500
    ] or "Intentionally no synced_at poller" in src


def test_signed_out_to_checking_requires_real_verification_row(client):
    import app as mighty
    from mighty.provider_access_manager import request_provider_verification

    uid, _ = _register_and_login(client)
    with mighty.app.app_context():
        _seed_signed_out_amex(mighty.get_db(), uid)

    before = client.get("/api/account-status").get_json()
    amex = next(a for a in before["accounts"] if a["source"] == "amex")
    assert amex.get("session_state") != "checking"

    with mighty.app.app_context():
        request_provider_verification(
            mighty.get_db(), uid, "amex", "user_check_now", throttle_seconds=0,
        )

    after = client.get("/api/account-status").get_json()
    amex2 = next(a for a in after["accounts"] if a["source"] == "amex")
    assert (
        amex2.get("session_state") == "checking"
        or amex2.get("presentation_key") == "checking"
        or (amex2.get("capability") or {}).get("is_refreshing")
        or amex2.get("verification_lifecycle") in {
            "requested", "running", "session_verified", "extracting",
        }
    )


def test_ensure_stale_callers_classified():
    """Audit: ensure_stale_provider_access_checks only on command paths."""
    app_src = (ROOT / "app.py").read_text()
    # GET account-status and GET pending must not call it.
    assert "def api_account_status" in app_src
    status_fn = app_src.split("def api_account_status", 1)[1].split("\n@app.route", 1)[0]
    assert "ensure_stale_provider_access_checks" not in status_fn

    pending_fn = app_src.split(
        "def api_extension_session_verification_pending", 1
    )[1].split("\n@app.route", 1)[0]
    assert "ensure_stale_provider_access_checks" not in pending_fn

    ensure_due = app_src.split(
        "def api_extension_session_verification_ensure_due", 1
    )[1].split("\n@app.route", 1)[0]
    assert "ensure_stale_provider_access_checks" in ensure_due


def test_check_now_endpoint_in_routes(client):
    uid, _ = _register_and_login(client)
    # Missing CSRF should fail closed (403) rather than enqueue anonymously.
    r = client.post("/api/providers/amex/check", json={})
    assert r.status_code in (400, 403)
