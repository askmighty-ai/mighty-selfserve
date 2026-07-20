"""Tests for Railway AccessState ingest, ordering, stale presentation, and card UI."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

import pytest

from mighty.access_state_publication import SCHEMA_VERSION, serialize_access_state
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    ACCESS_HEALTH_RECOVERING,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RECOVERY_STATUS_RECOVERING,
    RUNTIME_STATUS_RUNNING,
    AccessState,
)
from mighty.runtime_access_state import (
    STATUS_AWAITING_USER,
    STATUS_HEALTHY,
    STATUS_NEVER_REPORTED,
    STATUS_RECOVERING,
    STATUS_RUNTIME_OFFLINE,
    STATUS_STALE,
    build_runtime_access_presentation,
    compute_presentation_status,
    ensure_runtime_access_state_tables,
    get_runtime_access_state,
    load_runtime_access_presentation,
    render_runtime_access_card,
    upsert_runtime_access_state,
    validate_ingest_payload,
)


def _payload(**overrides):
    state = AccessState(
        provider="amex",
        runtime_status=RUNTIME_STATUS_RUNNING,
        browser_status=BROWSER_STATUS_HEALTHY,
        recovery_planner_status=RECOVERY_STATUS_IDLE,
        authentication_state="SIGNED_IN",
        access_health=ACCESS_HEALTH_HEALTHY,
        session_started_at="2026-07-20T10:00:00+00:00",
        last_verification_at="2026-07-20T11:00:00+00:00",
        last_keepalive_at="2026-07-20T11:05:00+00:00",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:06:00+00:00",
    )
    payload = serialize_access_state(state, runtime_instance_id="inst-amex-1")
    payload.update(overrides)
    return payload


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_runtime_access.db")
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
            "email": f"runtime_{secrets.token_hex(4)}@test.local",
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
            "SELECT id, api_key FROM users WHERE id=?",
            (uid,),
        ).fetchone()
        return row["id"], row["api_key"]


def test_validate_ingest_payload_ok_and_sensitive_rejected():
    ok, err = validate_ingest_payload(_payload())
    assert err is None
    assert ok["schema_version"] == SCHEMA_VERSION
    bad, err = validate_ingest_payload({**_payload(), "cookies": "x"})
    assert bad is None
    assert "sensitive" in (err or "")


def test_latest_state_replacement_and_out_of_order(tmp_path, monkeypatch):
    import sqlite3

    db = sqlite3.connect(str(tmp_path / "ras.db"))
    db.row_factory = sqlite3.Row
    ensure_runtime_access_state_tables(db)

    first = _payload(updated_at="2026-07-20T12:00:00+00:00", access_health=ACCESS_HEALTH_HEALTHY)
    r1 = upsert_runtime_access_state(db, "user-1", first)
    assert r1["accepted"] is True

    second = _payload(
        updated_at="2026-07-20T12:05:00+00:00",
        access_health=ACCESS_HEALTH_RECOVERING,
        recovery_state=RECOVERY_STATUS_RECOVERING,
    )
    r2 = upsert_runtime_access_state(db, "user-1", second)
    assert r2["accepted"] is True
    stored = get_runtime_access_state(db, "user-1", "amex")
    assert stored["payload"]["access_health"] == ACCESS_HEALTH_RECOVERING

    stale = _payload(
        updated_at="2026-07-20T11:00:00+00:00",
        access_health=ACCESS_HEALTH_HEALTHY,
    )
    r3 = upsert_runtime_access_state(db, "user-1", stale)
    assert r3["accepted"] is False
    assert r3["reason"] == "out_of_order"
    stored2 = get_runtime_access_state(db, "user-1", "amex")
    assert stored2["payload"]["access_health"] == ACCESS_HEALTH_RECOVERING


def test_stale_and_offline_calculation():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    fresh = _payload(updated_at=(now - timedelta(seconds=30)).isoformat())
    assert compute_presentation_status(fresh, now=now, stale_after_seconds=180) == STATUS_HEALTHY

    stale_ts = (now - timedelta(seconds=200)).isoformat()
    stale = _payload(updated_at=stale_ts, access_health=ACCESS_HEALTH_HEALTHY)
    assert compute_presentation_status(stale, now=now, stale_after_seconds=180) == STATUS_STALE

    offline = _payload(
        updated_at=(now - timedelta(seconds=500)).isoformat(),
        access_health=ACCESS_HEALTH_HEALTHY,
        runtime_state="stopped",
    )
    assert (
        compute_presentation_status(offline, now=now, stale_after_seconds=180)
        == STATUS_RUNTIME_OFFLINE
    )
    assert compute_presentation_status(None, now=now) == STATUS_NEVER_REPORTED


def test_presentation_variants():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    never = build_runtime_access_presentation(None, now=now)
    assert never.status == STATUS_NEVER_REPORTED
    assert "No local AccessState" in never.headline

    healthy_row = {
        "payload": _payload(updated_at=now.isoformat()),
        "updated_at": now.isoformat(),
        "runtime_instance_id": "inst-1",
    }
    healthy = build_runtime_access_presentation(healthy_row, now=now)
    assert healthy.status == STATUS_HEALTHY
    assert healthy.ready_for_extraction is True

    recovering_row = {
        "payload": _payload(
            updated_at=now.isoformat(),
            access_health=ACCESS_HEALTH_RECOVERING,
            recovery_state=RECOVERY_STATUS_RECOVERING,
        ),
        "updated_at": now.isoformat(),
    }
    recovering = build_runtime_access_presentation(recovering_row, now=now)
    assert recovering.status == STATUS_RECOVERING

    awaiting_row = {
        "payload": _payload(
            updated_at=now.isoformat(),
            recovery_state=RECOVERY_STATUS_AWAITING_USER,
            escalation_reason="mfa_required",
        ),
        "updated_at": now.isoformat(),
    }
    awaiting = build_runtime_access_presentation(awaiting_row, now=now)
    assert awaiting.status == STATUS_AWAITING_USER
    assert awaiting.user_action_required is True


def test_render_runtime_access_card_distinguishes_statuses():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    for status, payload in [
        (STATUS_HEALTHY, _payload(updated_at=now.isoformat())),
        (
            STATUS_RECOVERING,
            _payload(
                updated_at=now.isoformat(),
                access_health=ACCESS_HEALTH_RECOVERING,
                recovery_state=RECOVERY_STATUS_RECOVERING,
            ),
        ),
        (
            STATUS_AWAITING_USER,
            _payload(
                updated_at=now.isoformat(),
                recovery_state=RECOVERY_STATUS_AWAITING_USER,
            ),
        ),
        (STATUS_NEVER_REPORTED, None),
    ]:
        if payload is None:
            presentation = build_runtime_access_presentation(None, now=now)
        else:
            presentation = build_runtime_access_presentation(
                {"payload": payload, "updated_at": now.isoformat()},
                now=now,
            )
        html = render_runtime_access_card(presentation, escape=html_escape)
        assert 'data-runtime-access="1"' in html
        assert f'data-access-status="{status}"' in html
        assert "American Express access" in html
        assert presentation.status_label in html
        assert "View details" in html
        assert 'data-access-details="1"' in html


def test_api_ingest_requires_auth(client):
    # Drop the fixture session so only an API key would authenticate.
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.post("/api/runtime/access-state", json=_payload())
    # Unauthenticated: redirect to login or 401 depending on path
    assert resp.status_code in (301, 302, 401, 403)


def test_api_ingest_and_read_with_api_key(client):
    import app as mighty

    uid, api_key = _user_api(client)
    headers = {"X-Mighty-Key": api_key, "Content-Type": "application/json"}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = _payload(updated_at=now)

    resp = client.post("/api/runtime/access-state", json=payload, headers=headers)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["accepted"] is True

    # Out of order rejected safely
    older = _payload(updated_at="2020-01-01T00:00:00+00:00")
    resp2 = client.post("/api/runtime/access-state", json=older, headers=headers)
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    assert body2["accepted"] is False
    assert body2["reason"] == "out_of_order"

    read = client.get("/api/runtime/access-state?provider=amex", headers=headers)
    assert read.status_code == 200
    data = read.get_json()
    assert data["ok"] is True
    assert data["access"]["status"] == STATUS_HEALTHY
    assert data["access"]["provider"] == "amex"
    assert "operations" in data
    assert "timeline" in data["operations"]
    assert data["operations"]["provider"] == "amex"

    with mighty.app.app_context():
        presentation = load_runtime_access_presentation(mighty.get_db(), uid, "amex")
        assert presentation.status == STATUS_HEALTHY


def test_api_ingest_invalid_key(client):
    resp = client.post(
        "/api/runtime/access-state",
        json=_payload(),
        headers={"X-Mighty-Key": "mk_invalid", "Content-Type": "application/json"},
    )
    assert resp.status_code == 401


def test_dashboard_includes_access_card(client):
    _, api_key = _user_api(client)
    headers = {"X-Mighty-Key": api_key, "Content-Type": "application/json"}
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    client.post("/api/runtime/access-state", json=_payload(updated_at=now), headers=headers)

    page = client.get("/dashboard")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'data-runtime-access="1"' in html
    assert "American Express access" in html
    assert "View details" in html
    assert "_pollRuntimeAccessState" in html
    assert "_applyRuntimeAccessOperations" in html


def test_never_reported_on_fresh_user(client):
    _, api_key = _user_api(client)
    resp = client.get(
        "/api/runtime/access-state?provider=amex",
        headers={"X-Mighty-Key": api_key},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["access"]["status"] == STATUS_NEVER_REPORTED
