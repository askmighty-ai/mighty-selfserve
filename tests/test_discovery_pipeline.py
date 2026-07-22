"""Discovery pipeline enrollment + isolation tests (Milestone 7)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.discovery_enrollment import enroll_from_discovery, is_provider_enrolled
from mighty.discovery_pipeline import process_email_scan
from mighty.discovery_store import (
    ensure_discovery_tables,
    get_discovery_fact,
    mark_dismissed,
)

FIXED_NOW = datetime(2026, 7, 22, 15, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "discovery_pipeline.db"))
    conn.row_factory = sqlite3.Row
    ensure_discovery_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_credentials (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            username_enc TEXT,
            password_enc TEXT,
            extra_enc TEXT,
            created_at TEXT,
            updated_at TEXT,
            PRIMARY KEY (user_id, source)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_data (
            user_id TEXT NOT NULL,
            source TEXT NOT NULL,
            display_name TEXT,
            icon TEXT,
            color TEXT,
            data_enc TEXT,
            synced_at TEXT,
            connection_status TEXT,
            PRIMARY KEY (user_id, source)
        )
        """
    )
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


def _register(uid, source, db):
    now = FIXED_NOW.isoformat()
    db.execute(
        "INSERT OR IGNORE INTO account_credentials "
        "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
        "VALUES (?, ?, '', '', '', ?, ?)",
        (uid, source, now, now),
    )
    db.execute(
        "INSERT OR IGNORE INTO account_data "
        "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
        "VALUES (?, ?, ?, '🔗', '#eee', '', '', 'waiting_for_extension')",
        (uid, source, source.title()),
    )
    db.commit()


def _suggestions():
    return [
        {
            "site_key": "amex",
            "display_name": "American Express",
            "category": "credit_card",
            "email_count": 4,
            "sender": "americanexpress.com",
        },
        {
            "site_key": "delta",
            "display_name": "Delta SkyMiles",
            "category": "airline",
            "email_count": 8,
            "sender": "delta.com",
        },
    ]


class TestDiscoveryPipeline:
    def test_auto_enrolls_amex_not_delta(self, db):
        result = process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=_register,
            now=FIXED_NOW,
            auto_enroll=True,
        )
        assert "amex" in result.auto_enrolled
        assert "delta" not in result.auto_enrolled
        assert "delta" in result.ambiguous
        assert is_provider_enrolled(db, USER_ID, "amex")
        assert not is_provider_enrolled(db, USER_ID, "delta")
        amex = get_discovery_fact(db, USER_ID, "amex")
        assert amex.disposition == "enrolled"
        # No fake extracted data
        row = db.execute(
            "SELECT connection_status, data_enc FROM account_data "
            "WHERE user_id=? AND source='amex'",
            (USER_ID,),
        ).fetchone()
        assert row["connection_status"] == "waiting_for_extension"
        assert row["data_enc"] == ""

    def test_idempotent_rescan(self, db):
        process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=_register,
            now=FIXED_NOW,
        )
        second = process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=_register,
            now=FIXED_NOW,
        )
        assert second.auto_enrolled == []
        assert "amex" in second.already_enrolled
        rows = db.execute(
            "SELECT COUNT(*) AS c FROM account_credentials WHERE user_id=?",
            (USER_ID,),
        ).fetchone()
        assert int(rows["c"]) == 1

    def test_manual_enroll_coexists(self, db):
        _register(USER_ID, "amex", db)
        result = process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=_register,
            now=FIXED_NOW,
        )
        assert "amex" in result.already_enrolled
        assert result.auto_enrolled == []

    def test_dismiss_blocks_auto_enroll(self, db):
        mark_dismissed(db, USER_ID, "amex", now=FIXED_NOW)
        result = process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=_register,
            now=FIXED_NOW,
        )
        assert "amex" not in result.auto_enrolled
        assert not is_provider_enrolled(db, USER_ID, "amex")

    def test_register_failure_isolated(self, db):
        def boom(*a, **k):
            raise RuntimeError("register down")

        result = process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=boom,
            now=FIXED_NOW,
        )
        assert result.errors == 0
        assert not is_provider_enrolled(db, USER_ID, "amex")

    def test_enroll_from_discovery_helper(self, db):
        process_email_scan(
            db,
            USER_ID,
            _suggestions(),
            source_type="gmail_sender",
            source_ref="gmail",
            auto_enroll_providers=frozenset({"amex"}),
            register_fn=None,
            now=FIXED_NOW,
            auto_enroll=False,
        )
        result = enroll_from_discovery(
            db,
            USER_ID,
            "amex",
            register_fn=_register,
            now=FIXED_NOW,
            require_eligible=True,
        )
        assert result.enrolled is True
        assert is_provider_enrolled(db, USER_ID, "amex")
