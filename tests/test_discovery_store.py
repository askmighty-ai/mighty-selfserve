"""Discovery store reconcile / dismiss tests (Milestone 7)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.discovery_policy import decide_discovery
from mighty.discovery_store import (
    ensure_discovery_tables,
    get_discovery_fact,
    mark_dismissed,
    reconcile_discovery_hits,
)

FIXED_NOW = datetime(2026, 7, 22, 15, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "discovery.db"))
    conn.row_factory = sqlite3.Row
    ensure_discovery_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            site_key TEXT NOT NULL,
            display_name TEXT NOT NULL,
            category TEXT NOT NULL,
            email_count INTEGER DEFAULT 0,
            sender_domain TEXT,
            dismissed INTEGER DEFAULT 0,
            added INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, site_key)
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _decision(domain="americanexpress.com", count=3, **kwargs):
    return decide_discovery(
        domain=domain,
        email_count=count,
        is_enrolled=kwargs.get("is_enrolled", False),
        is_dismissed=kwargs.get("is_dismissed", False),
        auto_enroll_providers=frozenset({"amex"}),
    )


class TestReconcile:
    def test_insert_and_update_preserves_dismiss(self, db):
        d1 = _decision()
        reconcile_discovery_hits(
            db,
            USER_ID,
            [d1],
            source_type="gmail_sender",
            source_ref="gmail",
            now=FIXED_NOW,
        )
        fact = get_discovery_fact(db, USER_ID, "amex")
        assert fact is not None
        assert fact.disposition == "eligible"

        mark_dismissed(db, USER_ID, "amex", now=FIXED_NOW)
        d2 = _decision(count=9)
        reconcile_discovery_hits(
            db,
            USER_ID,
            [d2],
            source_type="gmail_sender",
            source_ref="gmail",
            now=FIXED_NOW,
        )
        fact = get_discovery_fact(db, USER_ID, "amex")
        assert fact.disposition == "dismissed"
        assert fact.email_count == 9

    def test_absent_becomes_ignored_not_deleted(self, db):
        amex = _decision()
        delta = decide_discovery(
            domain="delta.com",
            email_count=2,
            is_enrolled=False,
            is_dismissed=False,
            auto_enroll_providers=frozenset({"amex"}),
        )
        reconcile_discovery_hits(
            db,
            USER_ID,
            [amex, delta],
            source_type="gmail_sender",
            source_ref="gmail",
            now=FIXED_NOW,
        )
        reconcile_discovery_hits(
            db,
            USER_ID,
            [amex],
            source_type="gmail_sender",
            source_ref="gmail",
            now=FIXED_NOW,
        )
        delta_fact = get_discovery_fact(db, USER_ID, "delta")
        assert delta_fact is not None
        assert delta_fact.disposition == "ignored"
