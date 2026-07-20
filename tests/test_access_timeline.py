"""Tests for AccessTimeline persistence, ordering, retention, and rendering."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from html import escape as html_escape

from mighty.access_state_publication import serialize_access_state
from mighty.access_timeline import (
    DEFAULT_TIMELINE_LIMIT,
    EVENT_AUTHENTICATION_CHANGED,
    EVENT_AWAITING_USER,
    EVENT_KEEPALIVE_FAILED,
    EVENT_RECOVERY_COMPLETED,
    EVENT_RECOVERY_STARTED,
    EVENT_RUNTIME_STARTED,
    EVENT_RUNTIME_STOPPED,
    EVENT_SNAPSHOT_REFRESHED,
    EVENT_VERIFICATION_FAILED,
    EVENT_VERIFICATION_SUCCEEDED,
    AccessTimeline,
    append_access_timeline_events,
    build_provider_operations_details,
    derive_access_timeline_events,
    ensure_access_timeline_tables,
    list_access_timeline_events,
    make_timeline_event,
    render_provider_operations_details_html,
    render_timeline_events_html,
)
from mighty.provider_runtime_control_center import (
    ACCESS_HEALTH_HEALTHY,
    BROWSER_STATUS_HEALTHY,
    RECOVERY_STATUS_AWAITING_USER,
    RECOVERY_STATUS_IDLE,
    RECOVERY_STATUS_RECOVERING,
    RUNTIME_STATUS_RUNNING,
    RUNTIME_STATUS_STOPPED,
    AccessState,
)
from mighty.runtime_access_state import (
    ensure_runtime_access_state_tables,
    render_runtime_access_card,
    build_runtime_access_presentation,
    upsert_runtime_access_state,
)


def _db(tmp_path):
    db = sqlite3.connect(str(tmp_path / "timeline.db"))
    db.row_factory = sqlite3.Row
    ensure_runtime_access_state_tables(db)
    return db


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
        last_verification_result="SIGNED_IN",
        last_keepalive_at="2026-07-20T11:05:00+00:00",
        last_keepalive_result="ok",
        ready_for_extraction=True,
        ready_for_connector=True,
        updated_at="2026-07-20T11:06:00+00:00",
    )
    payload = serialize_access_state(state, runtime_instance_id="inst-amex-1")
    payload.update(overrides)
    return payload


def test_access_timeline_append_only_and_timestamped():
    timeline = AccessTimeline(limit=10)
    first = timeline.record(
        EVENT_RUNTIME_STARTED,
        "started",
        observed_at="2026-07-20T10:00:00+00:00",
    )
    second = timeline.record(
        EVENT_VERIFICATION_SUCCEEDED,
        "verified",
        observed_at="2026-07-20T10:01:00+00:00",
    )
    events = timeline.list_events()
    assert len(events) == 2
    assert events[0].event_id == first.event_id
    assert events[1].event_id == second.event_id
    assert events[0].observed_at < events[1].observed_at
    newest = timeline.list_events(newest_first=True)
    assert newest[0].event_type == EVENT_VERIFICATION_SUCCEEDED


def test_access_timeline_retention_bound():
    timeline = AccessTimeline(limit=3)
    for idx in range(5):
        timeline.record(
            EVENT_SNAPSHOT_REFRESHED,
            f"snap-{idx}",
            observed_at=f"2026-07-20T10:0{idx}:00+00:00",
        )
    events = timeline.list_events()
    assert len(events) == 3
    assert events[0].message == "snap-2"
    assert events[-1].message == "snap-4"


def test_derive_lifecycle_events_and_quiet_heartbeat():
    first = derive_access_timeline_events(None, _payload())
    types = [e.event_type for e in first]
    assert EVENT_RUNTIME_STARTED in types
    assert EVENT_SNAPSHOT_REFRESHED in types
    # First ingest does not invent verification/keepalive history.
    assert EVENT_VERIFICATION_SUCCEEDED not in types

    prev = _payload()
    same_heartbeat = _payload(updated_at="2026-07-20T11:07:00+00:00", published_at="2026-07-20T11:07:00+00:00")
    assert derive_access_timeline_events(prev, same_heartbeat) == []

    verified = _payload(
        last_verified_at="2026-07-20T11:10:00+00:00",
        last_verification_result="SIGNED_IN",
        updated_at="2026-07-20T11:10:00+00:00",
    )
    v_events = derive_access_timeline_events(prev, verified)
    assert [e.event_type for e in v_events] == [EVENT_VERIFICATION_SUCCEEDED]

    failed = _payload(
        last_verified_at="2026-07-20T11:11:00+00:00",
        last_verification_result="SIGNED_OUT",
        authentication_state="SIGNED_OUT",
        access_health="unavailable",
        ready_for_extraction=False,
        ready_for_connector=False,
        updated_at="2026-07-20T11:11:00+00:00",
    )
    f_types = [e.event_type for e in derive_access_timeline_events(verified, failed)]
    assert EVENT_AUTHENTICATION_CHANGED in f_types
    assert EVENT_VERIFICATION_FAILED in f_types
    assert EVENT_SNAPSHOT_REFRESHED in f_types


def test_derive_recovery_keepalive_runtime_and_escalation():
    base = _payload()
    recovering = _payload(
        recovery_state=RECOVERY_STATUS_RECOVERING,
        access_health="recovering",
        updated_at="2026-07-20T12:00:00+00:00",
    )
    started = derive_access_timeline_events(base, recovering)
    assert EVENT_RECOVERY_STARTED in {e.event_type for e in started}

    completed = _payload(
        recovery_state=RECOVERY_STATUS_IDLE,
        last_recovery_result="succeeded",
        updated_at="2026-07-20T12:01:00+00:00",
    )
    done = derive_access_timeline_events(recovering, completed)
    assert EVENT_RECOVERY_COMPLETED in {e.event_type for e in done}

    awaiting = _payload(
        recovery_state=RECOVERY_STATUS_AWAITING_USER,
        escalation_reason="mfa_required",
        updated_at="2026-07-20T12:02:00+00:00",
    )
    esc = derive_access_timeline_events(recovering, awaiting)
    assert EVENT_AWAITING_USER in {e.event_type for e in esc}

    keepalive_fail = _payload(
        last_keepalive_at="2026-07-20T12:03:00+00:00",
        last_keepalive_result="failed",
        updated_at="2026-07-20T12:03:00+00:00",
    )
    ka = derive_access_timeline_events(base, keepalive_fail)
    assert [e.event_type for e in ka] == [EVENT_KEEPALIVE_FAILED]

    stopped = _payload(
        runtime_state=RUNTIME_STATUS_STOPPED,
        access_health="unavailable",
        ready_for_extraction=False,
        ready_for_connector=False,
        updated_at="2026-07-20T12:04:00+00:00",
    )
    stop_types = {e.event_type for e in derive_access_timeline_events(base, stopped)}
    assert EVENT_RUNTIME_STOPPED in stop_types


def test_timeline_persistence_ordering_and_retention(tmp_path):
    db = _db(tmp_path)
    events = [
        make_timeline_event(
            EVENT_RUNTIME_STARTED,
            f"e-{idx}",
            observed_at=f"2026-07-20T10:{idx:02d}:00+00:00",
        )
        for idx in range(5)
    ]
    append_access_timeline_events(db, "user-1", "amex", events, limit=3)
    newest_first = list_access_timeline_events(db, "user-1", "amex", limit=10, newest_first=True)
    assert len(newest_first) == 3
    assert newest_first[0].message == "e-4"
    assert newest_first[-1].message == "e-2"
    oldest_first = list_access_timeline_events(db, "user-1", "amex", limit=10, newest_first=False)
    assert oldest_first[0].message == "e-2"
    assert oldest_first[-1].message == "e-4"


def test_upsert_records_timeline_and_skips_out_of_order(tmp_path):
    db = _db(tmp_path)
    first = _payload(updated_at="2026-07-20T12:00:00+00:00")
    r1 = upsert_runtime_access_state(db, "user-1", first)
    assert r1["accepted"] is True
    assert r1["timeline_events_recorded"] >= 1

    second = _payload(
        updated_at="2026-07-20T12:05:00+00:00",
        authentication_state="SIGNED_OUT",
        access_health="unavailable",
        ready_for_extraction=False,
        ready_for_connector=False,
        last_verified_at="2026-07-20T12:05:00+00:00",
        last_verification_result="SIGNED_OUT",
    )
    r2 = upsert_runtime_access_state(db, "user-1", second)
    assert r2["accepted"] is True
    assert r2["timeline_events_recorded"] >= 1

    events = list_access_timeline_events(db, "user-1", "amex", limit=50)
    types = {e.event_type for e in events}
    assert EVENT_AUTHENTICATION_CHANGED in types
    assert EVENT_VERIFICATION_FAILED in types

    # Out-of-order must not append.
    before = len(events)
    older = _payload(updated_at="2026-07-20T11:00:00+00:00")
    r3 = upsert_runtime_access_state(db, "user-1", older)
    assert r3["accepted"] is False
    after = list_access_timeline_events(db, "user-1", "amex", limit=50)
    assert len(after) == before


def test_default_retention_matches_limit_constant(tmp_path):
    db = _db(tmp_path)
    ensure_access_timeline_tables(db)
    bulk = [
        make_timeline_event(EVENT_SNAPSHOT_REFRESHED, f"n-{i}", observed_at=f"2026-07-20T00:00:{i:02d}+00:00")
        for i in range(DEFAULT_TIMELINE_LIMIT + 25)
    ]
    append_access_timeline_events(db, "user-1", "amex", bulk)
    stored = list_access_timeline_events(
        db, "user-1", "amex", limit=DEFAULT_TIMELINE_LIMIT + 10
    )
    assert len(stored) == DEFAULT_TIMELINE_LIMIT


def test_operations_details_and_render_view_details():
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    payload = _payload(updated_at=now.isoformat())
    timeline = [
        make_timeline_event(
            EVENT_AWAITING_USER,
            "needs login",
            observed_at="2026-07-20T11:30:00+00:00",
            ok=False,
        ),
        make_timeline_event(
            EVENT_KEEPALIVE_SUCCEEDED,
            "keepalive ok",
            observed_at="2026-07-20T11:45:00+00:00",
        ),
    ]
    details = build_provider_operations_details(payload, timeline, now=now)
    assert details.autonomous_uptime_label != "—"
    assert details.last_user_intervention_at == "2026-07-20T11:30:00+00:00"
    assert details.verification_last_result == "SIGNED_IN"
    assert details.keepalive_last_result == "ok"
    assert len(details.timeline) == 2

    ops_html = render_provider_operations_details_html(details, escape=html_escape)
    assert "Provider Operations" in ops_html
    assert "Autonomous uptime" in ops_html
    assert "awaiting_user" in ops_html
    assert 'data-access-timeline="1"' in ops_html

    presentation = build_runtime_access_presentation(
        {"payload": payload, "updated_at": now.isoformat()},
        now=now,
    )
    card = render_runtime_access_card(
        presentation, escape=html_escape, operations=details
    )
    assert "View details" in card
    assert 'data-access-details="1"' in card
    assert 'data-access-ops="1"' in card
    assert "American Express access" in card
    # Compact summary still present; details are behind <details>.
    assert "<details" in card
    assert "Recent timeline" in card


def test_timeline_html_empty_state():
    html = render_timeline_events_html([], escape=html_escape)
    assert "No timeline events yet" in html
