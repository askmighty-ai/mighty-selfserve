"""Independent command-side verification maintenance heartbeat.

Guarantees every Check now cycle terminalizes without Dashboard GET mutation.
Covers PR hotfix: stuck "Checking" cannot persist past phase deadline + one
maintenance interval.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_access_manager import (
    request_provider_verification,
    run_all_verification_maintenance,
    run_verification_maintenance,
)
from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
from mighty.session_verification import (
    VERIFICATION_EXTRACTION_TIMEOUT_SECONDS,
    VERIFICATION_MAINTENANCE_INTERVAL_SECONDS,
    VERIFICATION_MAX_DURATION_SECONDS,
    complete_session_verification,
    expire_timed_out_verifications,
    get_active_session_verification,
    get_latest_session_verification,
    mark_session_verification_running,
    session_verification_to_json,
    verification_timeout_deadline_at,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import secrets

    db_path = str(tmp_path / "mighty_maint_heartbeat.db")
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
        email = f"maint_{secrets.token_hex(4)}@test.local"
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


def _seed_amex_cred(db, uid):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT OR REPLACE INTO account_credentials
          (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at)
        VALUES (?, 'amex', '', '', '', ?, ?)
        """,
        (uid, now, now),
    )
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="seed",
            observed_at=datetime.now(timezone.utc) - timedelta(seconds=400),
            source="test",
            confidence="high",
        ),
    )
    db.commit()


def _seed_active(
    db,
    uid,
    *,
    lifecycle: str,
    age_seconds: int,
    verification_id: str,
    terminal_reason: str | None = None,
    terminal_source: str | None = None,
):
    requested = (
        datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    ).isoformat()
    started = requested if lifecycle != "requested" else None
    completed = None
    db.execute(
        """
        INSERT INTO provider_session_verification (
          verification_id, user_id, provider, lifecycle, entry_url,
          error_message, requested_at, started_at, completed_at,
          terminal_reason, terminal_source, trigger_source, requested_by
        ) VALUES (?, ?, 'amex', ?, ?, NULL, ?, ?, ?, ?, ?, 'user_check_now', 'test')
        """,
        (
            verification_id,
            uid,
            lifecycle,
            "https://global.americanexpress.com/overview",
            requested,
            started,
            completed,
            terminal_reason,
            terminal_source,
        ),
    )
    db.commit()
    return verification_id


def _row(db, vid):
    return dict(
        db.execute(
            "SELECT * FROM provider_session_verification WHERE verification_id=?",
            (vid,),
        ).fetchone()
    )


# ── 1. Check now creates/reuses one requested verification ───────────────────


def test_check_now_creates_or_reuses_one_requested(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)

    r1 = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": _csrf(client), "Content-Type": "application/json"},
        data="{}",
    )
    assert r1.status_code == 200
    body1 = r1.get_json()
    assert body1["ok"] is True
    assert body1["lifecycle"] == "requested"
    assert body1["verification_id"]
    assert body1["access_cycle_id"] == body1["verification_id"]
    assert body1["trigger_source"] == "user_check_now"
    assert body1["requested_at"]
    assert body1["timeout_deadline_at"]
    assert body1["last_transition_at"]

    r2 = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": _csrf(client), "Content-Type": "application/json"},
        data="{}",
    )
    body2 = r2.get_json()
    assert body2["verification_id"] == body1["verification_id"]

    with mighty.app.app_context():
        active = get_active_session_verification(mighty.get_db(), uid, "amex")
        assert active is not None
        assert active.lifecycle == "requested"


# ── 2–5. Phase timeouts without Dashboard traffic ────────────────────────────


@pytest.mark.parametrize(
    "lifecycle,age,expected_source",
    [
        ("requested", VERIFICATION_MAX_DURATION_SECONDS + 1, "server_timeout_queue"),
        ("running", VERIFICATION_MAX_DURATION_SECONDS + 1, "server_timeout"),
        (
            "session_verified",
            VERIFICATION_EXTRACTION_TIMEOUT_SECONDS + 1,
            "server_timeout_extraction",
        ),
        (
            "extracting",
            VERIFICATION_EXTRACTION_TIMEOUT_SECONDS + 1,
            "server_timeout_extraction",
        ),
    ],
)
def test_active_phases_timeout_without_dashboard(client, lifecycle, age, expected_source):
    import app as mighty

    uid = _uid(client)
    vid = f"stuck-{lifecycle}"
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(), uid, lifecycle=lifecycle, age_seconds=age, verification_id=vid,
        )
        n = run_verification_maintenance(mighty.get_db(), uid)
        assert n == 1
        row = _row(mighty.get_db(), vid)
        assert row["lifecycle"] == "timed_out"
        assert row["terminal_reason"] == "timeout"
        assert row["terminal_source"] == expected_source
        assert row["completed_at"]


# ── 6. Timeout cadence meets documented bound ────────────────────────────────


def test_timeout_cadence_meets_documented_bound():
    assert VERIFICATION_MAINTENANCE_INTERVAL_SECONDS == 60
    assert VERIFICATION_MAX_DURATION_SECONDS == 20
    assert VERIFICATION_EXTRACTION_TIMEOUT_SECONDS == 90
    # Worst case = phase deadline + one maintenance interval.
    probe_worst = VERIFICATION_MAX_DURATION_SECONDS + VERIFICATION_MAINTENANCE_INTERVAL_SECONDS
    extract_worst = (
        VERIFICATION_EXTRACTION_TIMEOUT_SECONDS + VERIFICATION_MAINTENANCE_INTERVAL_SECONDS
    )
    assert probe_worst == 80
    assert extract_worst == 150
    assert probe_worst < 20 * 60  # prior keepalive-only ceiling
    assert extract_worst < 20 * 60


# ── 7. Dashboard GET does not cause the timeout ──────────────────────────────


def test_dashboard_get_does_not_timeout(client):
    import app as mighty

    uid = _uid(client)
    vid = "dash-get-no-expire"
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="running",
            age_seconds=VERIFICATION_MAX_DURATION_SECONDS + 30,
            verification_id=vid,
        )

    assert client.get("/dashboard").status_code == 200
    assert client.get("/api/account-status").status_code == 200

    with mighty.app.app_context():
        row = _row(mighty.get_db(), vid)
        assert row["lifecycle"] == "running"
        assert row["completed_at"] is None


# ── 8. GET /pending remains read-only ────────────────────────────────────────


def test_pending_remains_read_only_with_overdue(client):
    import app as mighty

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    vid = "pending-ro"
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="requested",
            age_seconds=120,
            verification_id=vid,
        )
        before = _row(mighty.get_db(), vid)

    r = client.get(
        "/api/extension/session-verification/pending",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200

    with mighty.app.app_context():
        after = _row(mighty.get_db(), vid)
        assert after["lifecycle"] == "requested"
        assert after["completed_at"] == before["completed_at"]


# ── 9–12. Extension failure paths terminalize or are swept ───────────────────


def test_tab_close_and_nav_paths_covered_in_extension_source():
    src = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
    assert "extension_tab_closed" in src
    assert "extension_navigation_exception" in src
    assert "extension_extraction_post_rejected" in src
    assert "extension_extraction_no_result" in src
    assert "runVerificationMaintenanceHeartbeat" in src
    assert "VERIFICATION_MAINT_ALARM" in src
    assert "/api/extension/session-verification/maintain" in src


def test_maintain_endpoint_sweeps_after_extension_style_abandon(client):
    """Tab close / SW loss mid-cycle → maintain expires within bound."""
    import app as mighty

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    vid = "abandoned-midcycle"
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="extracting",
            age_seconds=VERIFICATION_EXTRACTION_TIMEOUT_SECONDS + 5,
            verification_id=vid,
        )

    r = client.post(
        "/api/extension/session-verification/maintain",
        headers={"X-Mighty-Key": api_key, "Content-Type": "application/json"},
        data="{}",
    )
    assert r.status_code == 200
    assert r.get_json()["expired"] == 1
    with mighty.app.app_context():
        assert _row(mighty.get_db(), vid)["lifecycle"] == "timed_out"


# ── 13–14. Extension reload / SW loss cannot leave permanent active ──────────


def test_reset_on_reload_clears_active_and_allows_new_check(client):
    import app as mighty

    uid = _uid(client)
    api_key = _api_key(mighty, client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="running",
            age_seconds=5,
            verification_id="reload-active",
        )

    r = client.post(
        "/api/extension/session-verification/reset-on-reload",
        headers={"X-Mighty-Key": api_key},
    )
    assert r.status_code == 200
    assert r.get_json()["count"] >= 1

    with mighty.app.app_context():
        assert _row(mighty.get_db(), "reload-active")["lifecycle"] != "running"
        active = get_active_session_verification(mighty.get_db(), uid, "amex")
        assert active is None

    # Fresh Check now may create a new verification.
    r2 = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": _csrf(client), "Content-Type": "application/json"},
        data="{}",
    )
    assert r2.get_json()["lifecycle"] == "requested"
    assert r2.get_json()["verification_id"] != "reload-active"


def test_service_worker_loss_cannot_leave_permanent_active(client):
    """SW death mid-cycle leaves row until maintenance — then it terminalizes."""
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="session_verified",
            age_seconds=VERIFICATION_EXTRACTION_TIMEOUT_SECONDS + 2,
            verification_id="sw-loss",
        )
        assert run_all_verification_maintenance(mighty.get_db()) == 1
        assert _row(mighty.get_db(), "sw-loss")["lifecycle"] == "timed_out"


# ── 15. Startup maintenance clears pre-existing overdue rows ─────────────────


def test_startup_maintenance_clears_preexisting_overdue(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="requested",
            age_seconds=300,
            verification_id="startup-overdue",
        )
        n = mighty._run_verification_maintenance_once(label="startup-test")
        assert n == 1
        assert _row(mighty.get_db(), "startup-overdue")["lifecycle"] == "timed_out"


# ── 16. Duplicate maintenance calls are idempotent ───────────────────────────


def test_duplicate_maintenance_idempotent(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="running",
            age_seconds=60,
            verification_id="idem",
        )
        assert run_verification_maintenance(mighty.get_db(), uid) == 1
        completed = _row(mighty.get_db(), "idem")["completed_at"]
        assert run_verification_maintenance(mighty.get_db(), uid) == 0
        assert run_all_verification_maintenance(mighty.get_db()) == 0
        assert _row(mighty.get_db(), "idem")["completed_at"] == completed


# ── 17. Timeout cannot overwrite terminal SIGNED_IN / SIGNED_OUT ─────────────


def test_timeout_cannot_overwrite_terminal_signed_result(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex_cred(db, uid)
        now = datetime.now(timezone.utc)
        requested = (now - timedelta(seconds=5)).isoformat()
        db.execute(
            """
            INSERT INTO provider_session_verification (
              verification_id, user_id, provider, lifecycle, entry_url,
              error_message, requested_at, started_at, completed_at,
              terminal_reason, terminal_source, trigger_source, requested_by
            ) VALUES (
              'already-signed-in', ?, 'amex', 'completed',
              'https://global.americanexpress.com/overview', NULL,
              ?, ?, ?, 'authenticated', 'extraction_success',
              'user_check_now', 'test'
            )
            """,
            (uid, requested, requested, now.isoformat()),
        )
        db.commit()
        assert expire_timed_out_verifications(db, uid) == 0
        row = _row(db, "already-signed-in")
        assert row["lifecycle"] == "completed"
        assert row["terminal_reason"] == "authenticated"


# ── 18. Timed-out row no longer blocks a new Check now ───────────────────────


def test_timed_out_row_does_not_block_new_check_now(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        _seed_active(
            mighty.get_db(),
            uid,
            lifecycle="requested",
            age_seconds=120,
            verification_id="old-timeout",
        )
        run_verification_maintenance(mighty.get_db(), uid)
        assert _row(mighty.get_db(), "old-timeout")["lifecycle"] == "timed_out"

    r = client.post(
        "/api/providers/amex/check",
        headers={"X-CSRF-Token": _csrf(client), "Content-Type": "application/json"},
        data="{}",
    )
    body = r.get_json()
    assert body["ok"] is True
    assert body["lifecycle"] == "requested"
    assert body["verification_id"] != "old-timeout"


# ── 19. Dashboard leaves Checking after committed timeout ────────────────────


def test_dashboard_leaves_checking_after_committed_timeout(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        ver = request_provider_verification(
            mighty.get_db(),
            uid,
            "amex",
            trigger_source="user_check_now",
            requested_by="test",
            throttle_seconds=0,
        )
        assert ver is not None
        # Force overdue timestamps then maintain.
        db = mighty.get_db()
        old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
        db.execute(
            """
            UPDATE provider_session_verification
            SET requested_at=?, started_at=?
            WHERE verification_id=?
            """,
            (old, old, ver.verification_id),
        )
        db.commit()
        run_verification_maintenance(db, uid)

    data = client.get("/api/account-status").get_json()
    amex = next(a for a in data["accounts"] if a["source"] == "amex")
    assert amex.get("verification_lifecycle") == "timed_out"
    # Not stuck in active checking lifecycle.
    assert amex.get("verification_lifecycle") not in {
        "requested", "running", "session_verified", "extracting",
    }


# ── 20. No active past phase deadline + one maintenance interval ─────────────


def test_no_active_past_deadline_plus_one_interval(client):
    import app as mighty

    uid = _uid(client)
    phases = [
        ("requested", VERIFICATION_MAX_DURATION_SECONDS),
        ("running", VERIFICATION_MAX_DURATION_SECONDS),
        ("session_verified", VERIFICATION_EXTRACTION_TIMEOUT_SECONDS),
        ("extracting", VERIFICATION_EXTRACTION_TIMEOUT_SECONDS),
    ]
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        for i, (lifecycle, phase) in enumerate(phases):
            age = phase + VERIFICATION_MAINTENANCE_INTERVAL_SECONDS
            _seed_active(
                mighty.get_db(),
                uid,
                lifecycle=lifecycle,
                age_seconds=age,
                verification_id=f"bound-{i}-{lifecycle}",
            )
            # Only one active allowed — collapse by maintaining between seeds.
            run_all_verification_maintenance(mighty.get_db())
            row = _row(mighty.get_db(), f"bound-{i}-{lifecycle}")
            assert row["lifecycle"] == "timed_out", lifecycle


def test_self_describing_json_exposes_deadline_fields(client):
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        _seed_amex_cred(mighty.get_db(), uid)
        ver = request_provider_verification(
            mighty.get_db(),
            uid,
            "amex",
            trigger_source="user_check_now",
            requested_by="test",
            throttle_seconds=0,
        )
        payload = session_verification_to_json(ver)
        assert payload["access_cycle_id"] == ver.verification_id
        assert payload["timeout_deadline_at"]
        assert payload["last_transition_at"]
        deadline = verification_timeout_deadline_at(ver)
        assert deadline == payload["timeout_deadline_at"]


def test_scheduler_and_maintain_wired_in_app():
    app_src = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "def _start_verification_maintenance_scheduler" in app_src
    assert "def _run_verification_maintenance_once" in app_src
    assert "def api_extension_session_verification_maintain" in app_src
    assert "_start_verification_maintenance_scheduler()" in app_src
    assert "ENABLE_VERIFICATION_MAINTENANCE" in app_src

    # GET handlers must still not call maintenance.
    pending_fn = app_src.split(
        "def api_extension_session_verification_pending", 1
    )[1].split("\n@app.route", 1)[0]
    status_fn = app_src.split("def api_account_status", 1)[1].split("\n@app.route", 1)[0]
    for name, src in (("pending", pending_fn), ("account_status", status_fn)):
        assert "run_verification_maintenance" not in src, name
        assert "expire_timed_out_verifications" not in src, name
        assert "run_all_verification_maintenance" not in src, name


def test_determining_copy_is_finite():
    from mighty.customer_capability_presentation import (
        DETERMINING_BODY,
        DETERMINING_FINITE_NOTE,
    )

    assert "stop automatically" in DETERMINING_BODY
    assert DETERMINING_FINITE_NOTE in DETERMINING_BODY


def test_maintain_requires_auth(client):
    r = client.post(
        "/api/extension/session-verification/maintain",
        headers={"Content-Type": "application/json"},
        data="{}",
    )
    assert r.status_code == 401
