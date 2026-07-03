"""Tests for persisted recommendation lifecycle."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from mighty.decision_engine import Recommendation
from mighty.recommendation_lifecycle import (
    CLICKED,
    COMPLETED,
    DISMISSED,
    EXPIRED,
    GENERATED,
    SHOWN,
    RecommendationLifecycleState,
    attach_lifecycle_ids,
    expire_stale_recommendations,
    filter_visible_recommendations,
    load_lifecycle_by_key,
    mark_recommendations_shown,
    recommendation_key_for,
    sync_generated_recommendations,
    transition_recommendation,
)


@pytest.fixture()
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE recommendation_lifecycle (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             TEXT NOT NULL,
            recommendation_key  TEXT NOT NULL,
            title               TEXT NOT NULL,
            recommendation_type TEXT NOT NULL,
            source              TEXT NOT NULL DEFAULT 'dashboard',
            state               TEXT NOT NULL DEFAULT 'generated',
            generated_at        TEXT NOT NULL,
            shown_at            TEXT,
            clicked_at          TEXT,
            dismissed_at        TEXT,
            completed_at        TEXT,
            expired_at          TEXT,
            UNIQUE(user_id, recommendation_key)
        )
        """
    )
    yield conn
    conn.close()


def _rec(**kwargs):
    defaults = {
        "id": "email_hyatt",
        "title": "Review your Hyatt emails",
        "summary": "Recent Hyatt messages may include points offers.",
        "recommendation_type": "hotel",
        "rationale": "A recent email subject mentioned Hyatt.",
    }
    defaults.update(kwargs)
    return Recommendation(**defaults)


def test_lifecycle_state_enum_values():
    assert RecommendationLifecycleState.GENERATED.value == GENERATED
    assert RecommendationLifecycleState.SHOWN.value == SHOWN
    assert RecommendationLifecycleState.CLICKED.value == CLICKED
    assert RecommendationLifecycleState.DISMISSED.value == DISMISSED
    assert RecommendationLifecycleState.COMPLETED.value == COMPLETED
    assert RecommendationLifecycleState.EXPIRED.value == EXPIRED


def test_recommendation_key_uses_explicit_id():
    rec = _rec(id="email_hyatt")
    assert recommendation_key_for(rec) == "email_hyatt"


def test_recommendation_key_falls_back_to_hash():
    rec = Recommendation(title="Book hotel", summary="Use benefits", recommendation_type="hotel")
    key = recommendation_key_for(rec, source="dashboard")
    assert key.startswith("hotel_")


def test_sync_generated_creates_rows(db):
    recs = [_rec()]
    lifecycle = sync_generated_recommendations(db, "user-1", recs)
    assert "email_hyatt" in lifecycle
    assert lifecycle["email_hyatt"].state == GENERATED


def test_mark_shown_advances_state(db):
    sync_generated_recommendations(db, "user-1", [_rec()])
    mark_recommendations_shown(db, "user-1", ["email_hyatt"])
    row = load_lifecycle_by_key(db, "user-1")["email_hyatt"]
    assert row.state == SHOWN
    assert row.shown_at


def test_happy_path_transitions(db):
    sync_generated_recommendations(db, "user-1", [_rec()])
    mark_recommendations_shown(db, "user-1", ["email_hyatt"])
    clicked = transition_recommendation(db, "user-1", "email_hyatt", CLICKED)
    completed = transition_recommendation(db, "user-1", "email_hyatt", COMPLETED)
    assert clicked.state == CLICKED
    assert completed.state == COMPLETED
    assert completed.completed_at


def test_dismissed_is_terminal(db):
    sync_generated_recommendations(db, "user-1", [_rec()])
    mark_recommendations_shown(db, "user-1", ["email_hyatt"])
    dismissed = transition_recommendation(db, "user-1", "email_hyatt", DISMISSED)
    blocked = transition_recommendation(db, "user-1", "email_hyatt", CLICKED)
    assert dismissed.state == DISMISSED
    assert blocked.state == DISMISSED


def test_filter_visible_hides_terminal_states(db):
    recs = [_rec(), _rec(id="email_marriott", title="Review your Marriott emails")]
    sync_generated_recommendations(db, "user-1", recs)
    transition_recommendation(db, "user-1", "email_hyatt", DISMISSED)
    lifecycle = load_lifecycle_by_key(db, "user-1")
    visible = filter_visible_recommendations(recs, lifecycle)
    assert len(visible) == 1
    assert visible[0].id == "email_marriott"


def test_expire_stale_recommendations_marks_missing_keys(db):
    sync_generated_recommendations(db, "user-1", [_rec()])
    lifecycle = load_lifecycle_by_key(db, "user-1")
    expired_count = expire_stale_recommendations(
        db,
        "user-1",
        active_keys=set(),
        existing=lifecycle,
    )
    db.commit()
    row = load_lifecycle_by_key(db, "user-1")["email_hyatt"]
    assert expired_count == 1
    assert row.state == EXPIRED


def test_expire_stale_recommendations_respects_ttl(db):
    old = (datetime.utcnow() - timedelta(days=31)).isoformat()
    db.execute(
        """
        INSERT INTO recommendation_lifecycle (
            user_id, recommendation_key, title, recommendation_type, source,
            state, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("user-1", "email_hyatt", "Review your Hyatt emails", "hotel", "dashboard", GENERATED, old),
    )
    db.commit()
    lifecycle = load_lifecycle_by_key(db, "user-1")
    expire_stale_recommendations(
        db,
        "user-1",
        active_keys={"email_hyatt"},
        existing=lifecycle,
    )
    db.commit()
    row = load_lifecycle_by_key(db, "user-1")["email_hyatt"]
    assert row.state == EXPIRED


def test_attach_lifecycle_ids_sets_missing_id():
    rec = Recommendation(title="Book hotel", summary="Use benefits", recommendation_type="hotel")
    attach_lifecycle_ids([rec], source="dashboard")
    assert rec.id
