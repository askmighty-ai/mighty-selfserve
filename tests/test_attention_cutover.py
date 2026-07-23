"""Cutover flags + Home/Worker Attention consumer (Milestone 3)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    ACCOUNT_STATE_VERSION,
    CONN_CONNECTED,
    DATA_NONE,
    AccountState,
    Confidence,
    ConfidenceFactors,
    ensure_account_state_tables,
    persist_account_state,
)
from mighty.attention_compare import (
    AttentionAgreement,
    legacy_signal_from_home,
    legacy_signal_from_worker,
    load_attention_compare,
)
from mighty.attention_consumer import (
    attention_api_payload,
    consume_attention_for_surface,
)
from mighty.attention_cutover import (
    attention_cutover_enabled,
    attention_cutover_mode,
    attention_shadow_compare_enabled,
)
from mighty.attention_store import ensure_attention_overlay_tables
from mighty.home_ui import render_attention_panel, render_home_page
from mighty.home_state import resolve_home_state
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    upsert_provider_session_state,
)
from tests.recovery_test_helpers import escalate_recovery

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "cutover.db"))
    conn.row_factory = sqlite3.Row
    ensure_account_state_tables(conn)
    ensure_provider_session_state_tables(conn)
    ensure_attention_overlay_tables(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            label TEXT NOT NULL,
            fields TEXT,
            status TEXT NOT NULL,
            outcome TEXT,
            approval_token TEXT UNIQUE,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            expires_at TEXT
        )
        """
    )
    conn.commit()
    yield conn
    conn.close()


def _persist_signed_out(db):
    state = AccountState(
        user_id=USER_ID,
        provider="amex",
        display_name="American Express",
        category="financial",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,
        session_health="healthy",
        last_verified_at=None,
        data_status=DATA_NONE,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="",
        is_actionable=False,
        updated_at=FIXED_NOW.isoformat(),
        version=ACCOUNT_STATE_VERSION,
    )
    persist_account_state(db, state)
    upsert_provider_session_state(
        db,
        USER_ID,
        SessionEvidence(
            provider="amex",
            state="signed_out",
            evidence_type="login_form",
            evidence_summary="login form",
            observed_at=FIXED_NOW - timedelta(minutes=5),
            source="access_manager",
            confidence="high",
        ),
    )
    escalate_recovery(db, USER_ID, "amex", root_cause="login", now=FIXED_NOW)


class TestCutoverFlags:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("ATTENTION_CUTOVER", raising=False)
        monkeypatch.delenv("ATTENTION_CUTOVER_HOME", raising=False)
        assert attention_cutover_mode("home") == "on"
        assert attention_cutover_enabled("home") is True

    def test_surface_override(self, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER", "on")
        monkeypatch.setenv("ATTENTION_CUTOVER_WORKER", "shadow")
        assert attention_cutover_mode("worker") == "shadow"
        assert attention_cutover_mode("home") == "on"

    def test_shadow_compare_opt_in(self, monkeypatch):
        monkeypatch.delenv("ATTENTION_SHADOW_COMPARE", raising=False)
        monkeypatch.setenv("ATTENTION_CUTOVER", "on")
        assert attention_shadow_compare_enabled("home") is False
        monkeypatch.setenv("ATTENTION_SHADOW_COMPARE", "1")
        assert attention_shadow_compare_enabled("home") is True
        monkeypatch.delenv("ATTENTION_SHADOW_COMPARE", raising=False)
        monkeypatch.setenv("ATTENTION_CUTOVER_WORKER", "shadow")
        assert attention_shadow_compare_enabled("worker") is True


class TestConsumerCutover:
    def test_on_uses_attention_view_without_legacy_probe(self, db, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER_HOME", "on")
        monkeypatch.delenv("ATTENTION_SHADOW_COMPARE", raising=False)
        _persist_signed_out(db)
        result = consume_attention_for_surface(
            db,
            USER_ID,
            "home",
            legacy=None,
            now=FIXED_NOW,
            provider_open_urls={"amex": "https://amex.test"},
        )
        assert result.used_attention is True
        assert result.view is not None
        assert result.view.primary is not None
        assert result.view.primary.attention_class.value == "auth_blocker"
        assert result.view.primary.cta_url == "https://amex.test"
        # Without a legacy probe, compare metrics are not written.
        assert load_attention_compare(db, USER_ID, "home") is None

    def test_shadow_does_not_use_for_ui(self, db, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER_WORKER", "shadow")
        _persist_signed_out(db)
        result = consume_attention_for_surface(
            db,
            USER_ID,
            "worker",
            now=FIXED_NOW,
            legacy=legacy_signal_from_worker(needs_login_count=1, provider="amex"),
        )
        assert result.mode == "shadow"
        assert result.used_attention is False
        assert result.view is not None  # exposed for observability
        payload = attention_api_payload(result)
        assert payload["used_attention"] is False
        assert payload["view"]["primary"]["attention_class"] == "auth_blocker"

    def test_off_hides_payload(self, db, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER_WORKER", "off")
        result = consume_attention_for_surface(db, USER_ID, "worker", now=FIXED_NOW)
        assert attention_api_payload(result) is None


class TestHomeAttentionRender:
    def test_render_attention_panel_primary(self, db, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER_HOME", "on")
        _persist_signed_out(db)
        result = consume_attention_for_surface(
            db, USER_ID, "home", now=FIXED_NOW,
            provider_open_urls={"amex": "https://amex.test"},
        )
        html = render_attention_panel(result.view, escape=lambda s: str(s))
        assert "dash-attention" in html
        assert "American Express" in html
        assert "https://amex.test" in html

    def test_render_home_page_includes_attention_when_enabled(self, db, monkeypatch):
        monkeypatch.setenv("ATTENTION_CUTOVER_HOME", "on")
        _persist_signed_out(db)
        home = resolve_home_state(accounts=[])
        attn = consume_attention_for_surface(db, USER_ID, "home", now=FIXED_NOW)
        html = render_home_page(
            home,
            first_name="Pat",
            today_label="Tue",
            escape=lambda s: str(s),
            attention=attn.view,
            use_attention=True,
        )
        assert "home-briefing" in html
        # Empty enrollment still owns hero when no accounts.
        assert "watched quietly" in html.lower()
