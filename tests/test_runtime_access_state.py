"""Tests for thin runtime_access_state store (M5)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.runtime_access_state import (
    STATUS_AWAITING_USER,
    STATUS_HEALTHY,
    STATUS_NEVER_REPORTED,
    STATUS_STALE,
    compute_presentation_status,
    ensure_runtime_access_state_tables,
    get_runtime_access_state,
    upsert_runtime_access_state,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "runtime.db"))
    conn.row_factory = sqlite3.Row
    ensure_runtime_access_state_tables(conn)
    yield conn
    conn.close()


def test_upsert_and_get(db):
    payload = {
        "schema_version": 2,
        "provider": "amex",
        "runtime_instance_id": "rt-1",
        "updated_at": FIXED_NOW.isoformat(),
        "authentication_state": "SIGNED_IN",
        "access_health": "healthy",
        "runtime_state": "running",
        "browser_state": "healthy",
        "recovery_state": "idle",
    }
    assert upsert_runtime_access_state(db, "user-1", payload) == "created"
    row = get_runtime_access_state(db, "user-1", "amex")
    assert row is not None
    assert row["payload"]["authentication_state"] == "SIGNED_IN"
    assert compute_presentation_status(row, now=FIXED_NOW) == STATUS_HEALTHY


def test_awaiting_user_status(db):
    upsert_runtime_access_state(
        db,
        "user-1",
        {
            "schema_version": 2,
            "provider": "amex",
            "runtime_instance_id": "rt-1",
            "updated_at": FIXED_NOW.isoformat(),
            "authentication_state": "SIGNED_IN",
            "access_health": "degraded",
            "runtime_state": "running",
            "browser_state": "healthy",
            "recovery_state": "awaiting_user",
            "escalation_reason": "mfa",
        },
    )
    row = get_runtime_access_state(db, "user-1", "amex")
    assert compute_presentation_status(row, now=FIXED_NOW) == STATUS_AWAITING_USER


def test_stale_and_never_reported(db):
    assert compute_presentation_status(None, now=FIXED_NOW) == STATUS_NEVER_REPORTED
    old = FIXED_NOW - timedelta(hours=2)
    upsert_runtime_access_state(
        db,
        "user-1",
        {
            "schema_version": 2,
            "provider": "amex",
            "runtime_instance_id": "rt-1",
            "updated_at": old.isoformat(),
            "authentication_state": "SIGNED_IN",
            "access_health": "healthy",
            "runtime_state": "running",
            "browser_state": "healthy",
            "recovery_state": "idle",
        },
    )
    row = get_runtime_access_state(db, "user-1", "amex")
    assert compute_presentation_status(row, now=FIXED_NOW) == STATUS_STALE
