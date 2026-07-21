"""Tests for AttentionStore commands + persistence (PR 2E)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_LOGIN,
    REASON_OPPORTUNITY,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_overlay import OverlayStatus, compose_attention
from mighty.attention_state import SilenceVerdict
from mighty.attention_store import (
    MAX_SNOOZE,
    AttentionStoreCommandError,
    build_dismiss_overlay,
    build_in_flight_overlay,
    build_snooze_overlay,
    clear_attention_overlay,
    dismiss_attention,
    ensure_attention_overlay_tables,
    get_attention_overlay,
    list_attention_overlays,
    snooze_attention,
    start_attention_cta,
)

FIXED_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
USER_ID = "user-1"


def _item(**overrides) -> AttentionItem:
    payload = {
        "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
        "attention_id": "att_user1_auth_blocker_amex_needs_human",
        "user_id": USER_ID,
        "attention_class": AttentionClass.AUTH_BLOCKER,
        "urgency": AttentionUrgency.BLOCKER,
        "provider": "amex",
        "fingerprint": "auth:amex:needs_human",
        "reason": AttentionReason(code=REASON_LOGIN),
        "cta_key": AttentionCtaKey.START_PROVIDER_LOGIN,
        "source_kind": AttentionSourceKind.AUTH,
        "source_ref": "auth_truth:user-1:amex",
        "observed_at": "2026-07-21T11:00:00+00:00",
        "becomes_stale_at": None,
        "interruption_expected": False,
    }
    payload.update(overrides)
    if isinstance(payload.get("reason"), str):
        payload["reason"] = AttentionReason(code=payload["reason"])
    return AttentionItem(**payload)


def _auth() -> AttentionItem:
    return _item()


def _opportunity() -> AttentionItem:
    return _item(
        attention_id="att_user1_opportunity_1",
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        fingerprint="benefit:opportunity:1",
        reason=REASON_OPPORTUNITY,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref="benefit:1",
    )


@pytest.fixture
def db(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "attention.db"))
    ensure_attention_overlay_tables(conn)
    yield conn
    conn.close()


class TestCommandBuilders:
    def test_snooze_sets_until(self):
        overlay = build_snooze_overlay(
            _auth(),
            now=FIXED_NOW,
            duration=timedelta(minutes=30),
        )
        assert overlay.status is OverlayStatus.SNOOZED
        assert overlay.until == "2026-07-21T12:30:00+00:00"
        assert overlay.started_at is None
        assert overlay.updated_at == "2026-07-21T12:00:00+00:00"

    def test_snooze_rejects_over_max(self):
        with pytest.raises(AttentionStoreCommandError):
            build_snooze_overlay(
                _auth(),
                now=FIXED_NOW,
                duration=MAX_SNOOZE + timedelta(seconds=1),
            )

    def test_snooze_rejects_non_positive(self):
        with pytest.raises(AttentionStoreCommandError):
            build_snooze_overlay(_auth(), now=FIXED_NOW, duration=timedelta(0))

    def test_snooze_allows_exact_max(self):
        overlay = build_snooze_overlay(
            _auth(),
            now=FIXED_NOW,
            duration=MAX_SNOOZE,
        )
        assert overlay.until == "2026-07-21T13:00:00+00:00"

    def test_dismiss_opportunity_ok(self):
        overlay = build_dismiss_overlay(_opportunity(), now=FIXED_NOW)
        assert overlay.status is OverlayStatus.DURABLE_DISMISSED
        assert overlay.until is None

    def test_dismiss_blocker_rejected(self):
        with pytest.raises(AttentionStoreCommandError):
            build_dismiss_overlay(_auth(), now=FIXED_NOW)

    def test_in_flight_sets_started_at(self):
        overlay = build_in_flight_overlay(_auth(), now=FIXED_NOW)
        assert overlay.status is OverlayStatus.IN_FLIGHT
        assert overlay.started_at == "2026-07-21T12:00:00+00:00"


class TestPersistence:
    def test_snooze_round_trip(self, db):
        item = _auth()
        overlay = snooze_attention(
            db,
            item,
            now=FIXED_NOW,
            duration=timedelta(minutes=15),
        )
        loaded = get_attention_overlay(db, USER_ID, item.attention_id)
        assert loaded == overlay
        listed = list_attention_overlays(db, USER_ID)
        assert listed == [overlay]

    def test_dismiss_and_list_ordered(self, db):
        auth = _auth()
        opp = _opportunity()
        snooze_attention(db, auth, now=FIXED_NOW, duration=timedelta(minutes=10))
        dismiss_attention(db, opp, now=FIXED_NOW)
        ids = [row.attention_id for row in list_attention_overlays(db, USER_ID)]
        assert ids == sorted(ids)

    def test_upsert_replaces(self, db):
        item = _auth()
        snooze_attention(db, item, now=FIXED_NOW, duration=timedelta(minutes=10))
        later = FIXED_NOW + timedelta(minutes=5)
        start_attention_cta(db, item, now=later)
        loaded = get_attention_overlay(db, USER_ID, item.attention_id)
        assert loaded is not None
        assert loaded.status is OverlayStatus.IN_FLIGHT
        assert loaded.started_at == later.isoformat()

    def test_clear_removes_row(self, db):
        item = _auth()
        snooze_attention(db, item, now=FIXED_NOW, duration=timedelta(minutes=10))
        clear_attention_overlay(db, USER_ID, item.attention_id)
        assert get_attention_overlay(db, USER_ID, item.attention_id) is None

    def test_user_isolation(self, db):
        item = _auth()
        snooze_attention(db, item, now=FIXED_NOW, duration=timedelta(minutes=10))
        assert list_attention_overlays(db, "other-user") == []
        assert get_attention_overlay(db, "other-user", item.attention_id) is None


class TestComposeIntegration:
    def test_store_overlays_feed_compose_suppressed(self, db):
        auth = _auth()
        opp = _opportunity()
        snooze_attention(db, auth, now=FIXED_NOW, duration=timedelta(minutes=30))
        dismiss_attention(db, opp, now=FIXED_NOW)
        overlays = list_attention_overlays(db, USER_ID)
        state = compose_attention([auth, opp], overlays, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.SUPPRESSED
        assert state.primary is None
        assert state.remaining == ()
