"""UBE journey narrator — event-based Home continuity + R1."""

from __future__ import annotations

import os
import secrets
import sqlite3
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.home_projection import HomeCard, project_home
from mighty.home_state import (
    AccountHealthCounts,
    HomeFeatured,
    HomeState,
    HomeStateResult,
)
from mighty.home_ui import render_home_page
from mighty.journey_narrative import (
    ACTION_PROVIDER_VISIT,
    KIND_SYSTEM_OBSERVATION,
    KIND_USER_ACTION,
    OBS_STILL_NEEDS_LOGIN,
    apply_journey_narrative_to_projection,
    compose_narrative_for_provider_ask,
    ensure_journey_narrative_table,
    recent_narrative_events,
    record_system_observation,
    record_user_action,
)
from mighty import user_copy


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "journey.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    ensure_journey_narrative_table(conn)
    yield conn
    conn.close()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "journey_app.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    monkeypatch.delenv("HOME_OS_ENABLED", raising=False)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setenv("MIGHTY_ENV", "production")

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
            "email": f"jn_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _escape(value):
    import html

    return html.escape(str(value)) if value is not None else ""


def test_record_user_action_and_observation_separate(db):
    a = record_user_action(
        db, "u1", event_type=ACTION_PROVIDER_VISIT, provider="amex"
    )
    o = record_system_observation(
        db, "u1", event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
    )
    assert a.kind == KIND_USER_ACTION
    assert o.kind == KIND_SYSTEM_OBSERVATION
    assert a.id != o.id
    events = recent_narrative_events(db, "u1", provider="amex")
    kinds = {e.kind for e in events}
    assert KIND_USER_ACTION in kinds and KIND_SYSTEM_OBSERVATION in kinds


def test_compose_r1_repeat_ask_explains_previous_failure(db):
    action = record_user_action(
        db, "u1", event_type=ACTION_PROVIDER_VISIT, provider="amex"
    )
    record_system_observation(
        db,
        "u1",
        event_type=OBS_STILL_NEEDS_LOGIN,
        provider="amex",
        detail={"after_user_action_id": action.id},
    )
    events = recent_narrative_events(db, "u1", provider="amex")
    compose = compose_narrative_for_provider_ask(
        provider_key="amex",
        provider_display="American Express",
        events=events,
        repeating_user_action=True,
    )
    assert compose is not None
    assert compose.beat == "repeat_ask"
    assert action.id in compose.event_ids
    body = (compose.body or "").lower()
    assert "asking again" in body or "that is why" in body
    assert "already" in body


def test_reload_preserves_visit_acknowledgment(db):
    """I1: Visit then recompose still acknowledges Visit (not cold amnesia)."""
    record_user_action(db, "u1", event_type=ACTION_PROVIDER_VISIT, provider="amex")
    events = recent_narrative_events(db, "u1", provider="amex")
    compose = compose_narrative_for_provider_ask(
        provider_key="amex",
        provider_display="American Express",
        events=events,
        repeating_user_action=True,
    )
    assert compose is not None
    assert compose.beat in ("waiting", "repeat_ask", "non_progress")
    assert any(r.startswith("user_action:") for r in compose.event_refs)
    cold = user_copy.home_login_body("American Express")
    assert compose.body != cold


def test_projection_overlay_binds_events_and_avoids_cold_body(db):
    record_user_action(db, "u1", event_type=ACTION_PROVIDER_VISIT, provider="amex")
    record_system_observation(
        db, "u1", event_type=OBS_STILL_NEEDS_LOGIN, provider="amex"
    )
    result = HomeStateResult(
        state=HomeState.WAITING,
        priority_summary="Visit American Express",
        featured=HomeFeatured(
            headline="Visit American Express",
            body=user_copy.home_handoff_needs_visit_body("American Express"),
            cta_label=user_copy.home_visit_provider_cta("American Express"),
            cta_url="https://www.americanexpress.com/",
        ),
        health=AccountHealthCounts(waiting=1, needs_login=1),
    )
    projection = project_home(result, first_name="Alex", today_label="Tue")
    # Simulate AUTH-like http CTA card with provider
    cold_card = HomeCard(
        kind="story",
        title=user_copy.home_login_headline("American Express"),
        body=user_copy.home_login_body("American Express"),
        tone="interrupt",
        cta_label=user_copy.home_login_cta("American Express"),
        cta_url="https://www.americanexpress.com/",
        provider="amex",
    )
    from dataclasses import replace

    projection = replace(
        projection, featured=cold_card, story_kind="attention", visual_state="attention"
    )
    projection = apply_journey_narrative_to_projection(
        projection,
        db,
        "u1",
        still_needs_user=True,
        provider_key="amex",
        provider_display="American Express",
    )
    assert projection.narrative_event_ids
    assert projection.narrative_beat == "repeat_ask"
    assert projection.featured is not None
    assert projection.featured.body != user_copy.home_login_body("American Express")
    assert "asking again" in projection.featured.body.lower()

    html = render_home_page(
        result,
        first_name="Alex",
        today_label="Tue",
        escape=_escape,
        db=db,
        user_id="u1",
    )
    # render_home_page re-projects; after visit + sync should still bind events
    assert "data-narrative-events=" in html or "data-narrative-beat=" in html


def test_api_records_user_action(client):
    with client.session_transaction() as sess:
        csrf = sess["_csrf"]
        uid = sess["user_id"]
    r = client.post(
        "/api/journey/user-action",
        json={"provider": "amex", "action": "provider_visit"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["event"]["kind"] == "user_action"
    assert data["event"]["event_type"] == "provider_visit"

    import app as mighty

    with mighty.app.app_context():
        events = recent_narrative_events(mighty.get_db(), uid, provider="amex")
    assert any(e.event_type == ACTION_PROVIDER_VISIT for e in events)
