"""Agreement / disagreement metrics for Attention shadow comparison (M3)."""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_LOGIN,
    REASON_PENDING_AUTHORIZATION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compare import (
    AttentionAgreement,
    LegacyAttentionSignal,
    compare_attention,
    legacy_signal_from_home,
    legacy_signal_from_worker,
    load_attention_compare,
    record_attention_compare,
)
from mighty.attention_state import (
    ATTENTION_STATE_SCHEMA_VERSION,
    AttentionState,
    SilenceVerdict,
    select_attention,
)
from mighty.attention_shadow import record_attention_shadow

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


def _authorize() -> AttentionItem:
    return _item(
        attention_id="att_user1_agent_authorization_42",
        attention_class=AttentionClass.AGENT_AUTHORIZATION,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint="authorize:42",
        reason=REASON_PENDING_AUTHORIZATION,
        cta_key=AttentionCtaKey.OPEN_ACTIVITY_APPROVAL,
        source_kind=AttentionSourceKind.AUTHORIZE,
        source_ref="authorize:42",
    )


class TestCompareTaxonomy:
    def test_both_silent_exact(self):
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=None,
            remaining=(),
            silence=SilenceVerdict.ALL_CLEAR,
        )
        result = compare_attention(LegacyAttentionSignal(active=False), state)
        assert result.agreement == AttentionAgreement.EXACT_AGREEMENT

    def test_exact_active_match(self):
        state = select_attention([_item()], now=FIXED_NOW)
        legacy = LegacyAttentionSignal(
            active=True,
            attention_class="auth_blocker",
            provider="amex",
            reason="login",
        )
        result = compare_attention(legacy, state)
        assert result.agreement == AttentionAgreement.EXACT_AGREEMENT

    def test_old_silent_new_active(self):
        state = select_attention([_item()], now=FIXED_NOW)
        result = compare_attention(LegacyAttentionSignal(active=False), state)
        assert result.agreement == AttentionAgreement.OLD_SILENT_NEW_ACTIVE
        assert result.detail == "false_interruption_candidate"

    def test_old_active_new_silent(self):
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=None,
            remaining=(),
            silence=SilenceVerdict.ALL_CLEAR,
        )
        legacy = LegacyAttentionSignal(
            active=True, attention_class="auth_blocker", provider="amex", reason="login"
        )
        result = compare_attention(legacy, state)
        assert result.agreement == AttentionAgreement.OLD_ACTIVE_NEW_SILENT
        assert result.detail == "false_silence_candidate"

    def test_same_class_diff_primary(self):
        state = select_attention(
            [
                _item(provider="delta", attention_id="att_delta", fingerprint="auth:delta:needs_human", source_ref="auth_truth:user-1:delta"),
            ],
            now=FIXED_NOW,
        )
        legacy = LegacyAttentionSignal(
            active=True,
            attention_class="auth_blocker",
            provider="amex",
            reason="login",
        )
        result = compare_attention(legacy, state)
        assert result.agreement == AttentionAgreement.SAME_CLASS_DIFF_PRIMARY

    def test_both_active_diff_class(self):
        state = select_attention([_authorize(), _item()], now=FIXED_NOW)
        legacy = LegacyAttentionSignal(
            active=True,
            attention_class="auth_blocker",
            provider="amex",
            reason="login",
        )
        result = compare_attention(legacy, state)
        assert result.agreement == AttentionAgreement.BOTH_ACTIVE_DIFF_PROVIDER_OR_REASON

    def test_platform_failure(self):
        result = compare_attention(
            LegacyAttentionSignal(active=True),
            None,
            platform_failed=True,
        )
        assert result.agreement == AttentionAgreement.PLATFORM_FAILURE


class TestLegacyProbes:
    def test_home_login(self):
        sig = legacy_signal_from_home(home_state="login", provider="amex")
        assert sig.active is True
        assert sig.attention_class == "auth_blocker"

    def test_home_capability_action(self):
        sig = legacy_signal_from_home(
            home_state="all_clear", action_required=True, provider="amex"
        )
        assert sig.active is True

    def test_worker_counts(self):
        assert legacy_signal_from_worker(needs_login_count=1).active is True
        assert legacy_signal_from_worker(needs_sign_in=2).active is True
        assert legacy_signal_from_worker().active is False


class TestPersistCompare:
    @pytest.fixture
    def db(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "compare.db"))
        conn.row_factory = sqlite3.Row
        yield conn
        conn.close()

    def test_record_and_load(self, db):
        state = select_attention([_item()], now=FIXED_NOW)
        legacy = LegacyAttentionSignal(
            active=True,
            attention_class="auth_blocker",
            provider="amex",
            reason="login",
        )
        result = record_attention_compare(
            db,
            USER_ID,
            "home",
            legacy=legacy,
            state=state,
            generated_at=FIXED_NOW.isoformat(),
        )
        assert result is not None
        loaded = load_attention_compare(db, USER_ID, "home")
        assert loaded is not None
        assert loaded["agreement"] == AttentionAgreement.EXACT_AGREEMENT.value
        assert loaded["new_primary_id"] == state.primary.attention_id

    def test_shadow_with_legacy_writes_compare(self, db):
        # Empty DB → engine returns all_clear; active legacy → false silence.
        legacy = LegacyAttentionSignal(active=True, attention_class="auth_blocker")
        snap = record_attention_shadow(
            db, USER_ID, "worker", now=FIXED_NOW, legacy=legacy
        )
        assert snap is not None
        assert snap.state.silence == SilenceVerdict.ALL_CLEAR
        loaded = load_attention_compare(db, USER_ID, "worker")
        assert loaded is not None
        assert loaded["agreement"] == AttentionAgreement.OLD_ACTIVE_NEW_SILENT.value
