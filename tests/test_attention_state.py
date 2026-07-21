"""Golden / replay tests for AttentionState ranking (PR 2C / RFC §7)."""

from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_DATA_GAP,
    REASON_LOGIN,
    REASON_OPPORTUNITY,
    REASON_PENDING_AUTHORIZATION,
    REASON_STALE,
    REASON_SYSTEM,
    REASON_TRUST,
    REASON_VALUE_AT_RISK,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_state import (
    ATTENTION_STATE_SCHEMA_VERSION,
    AttentionState,
    SilenceVerdict,
    select_attention,
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


def _auth_blocker(*, provider: str, attention_id: str | None = None) -> AttentionItem:
    return _item(
        attention_id=attention_id or f"att_{USER_ID}_auth_blocker_{provider}_needs_human",
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider=provider,
        fingerprint=f"auth:{provider}:needs_human",
        reason=REASON_LOGIN,
        cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=f"auth_truth:{USER_ID}:{provider}",
    )


def _agent_authorization(*, attention_id: str = "att_user1_agent_authorization_1") -> AttentionItem:
    return _item(
        attention_id=attention_id,
        attention_class=AttentionClass.AGENT_AUTHORIZATION,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint="authorize:pending:1",
        reason=REASON_PENDING_AUTHORIZATION,
        cta_key=AttentionCtaKey.OPEN_ACTIVITY_APPROVAL,
        source_kind=AttentionSourceKind.AUTHORIZE,
        source_ref="authorize:1",
    )


def _opportunity(
    *,
    attention_id: str = "att_user1_opportunity_1",
    provider: str | None = "amex",
) -> AttentionItem:
    return _item(
        attention_id=attention_id,
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        provider=provider,
        fingerprint=f"benefit:opportunity:{attention_id}",
        reason=REASON_OPPORTUNITY,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref=f"benefit:{attention_id}",
    )


def _value_at_risk(
    *,
    attention_id: str,
    becomes_stale_at: str | None,
    provider: str = "amex",
) -> AttentionItem:
    return _item(
        attention_id=attention_id,
        attention_class=AttentionClass.VALUE_AT_RISK,
        urgency=AttentionUrgency.TIME_SENSITIVE,
        provider=provider,
        fingerprint=f"benefit:var:{attention_id}",
        reason=REASON_VALUE_AT_RISK,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref=f"benefit:{attention_id}",
        becomes_stale_at=becomes_stale_at,
    )


def _access_degraded(*, provider: str = "amex") -> AttentionItem:
    return _item(
        attention_id=f"att_{USER_ID}_access_degraded_{provider}_stale",
        attention_class=AttentionClass.ACCESS_DEGRADED,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider=provider,
        fingerprint=f"auth:{provider}:stale",
        reason=REASON_STALE,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=f"auth_truth:{USER_ID}:{provider}",
    )


def _data_gap(*, attention_id: str = "att_user1_data_gap_1") -> AttentionItem:
    return _item(
        attention_id=attention_id,
        attention_class=AttentionClass.DATA_GAP,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider=None,
        fingerprint=f"account_data:gap:{attention_id}",
        reason=REASON_DATA_GAP,
        cta_key=AttentionCtaKey.CONNECT_GMAIL,
        source_kind=AttentionSourceKind.ACCOUNT_DATA,
        source_ref=f"account_data:{attention_id}",
    )


def _trust() -> AttentionItem:
    return _item(
        attention_id="att_user1_trust_1",
        attention_class=AttentionClass.TRUST,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint="trust:1",
        reason=REASON_TRUST,
        cta_key=AttentionCtaKey.NOOP,
        source_kind=AttentionSourceKind.TRUST,
        source_ref="trust:1",
    )


def _system() -> AttentionItem:
    return _item(
        attention_id="att_user1_system_1",
        attention_class=AttentionClass.SYSTEM,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint="worker:system:1",
        reason=REASON_SYSTEM,
        cta_key=AttentionCtaKey.INSTALL_WORKER,
        source_kind=AttentionSourceKind.WORKER,
        source_ref="worker:1",
    )


class TestEmptyAndSilence:
    def test_empty_input_all_clear(self):
        state = select_attention([], now=FIXED_NOW)
        assert state.primary is None
        assert state.remaining == ()
        assert state.silence == SilenceVerdict.ALL_CLEAR
        assert state.to_dict() == {
            "schema_version": ATTENTION_STATE_SCHEMA_VERSION,
            "primary": None,
            "remaining": [],
            "silence": "all_clear",
        }

    def test_opportunity_only_primary_with_all_clear(self):
        opp = _opportunity()
        state = select_attention([opp], now=FIXED_NOW)
        assert state.primary == opp
        assert state.remaining == ()
        assert state.silence == SilenceVerdict.ALL_CLEAR

    def test_awaiting_data_when_only_ranks_7_8(self):
        degraded = _access_degraded()
        gap = _data_gap()
        state = select_attention([gap, degraded], now=FIXED_NOW)
        assert state.silence == SilenceVerdict.AWAITING_DATA
        assert state.primary == degraded
        assert state.remaining == (gap,)

    def test_awaiting_data_wins_over_all_clear_with_opportunity(self):
        """Ranks 7–8 present → awaiting_data even if opportunity also present."""
        opp = _opportunity()
        degraded = _access_degraded()
        state = select_attention([opp, degraded], now=FIXED_NOW)
        assert state.silence == SilenceVerdict.AWAITING_DATA
        assert state.primary == opp
        assert state.remaining == (degraded,)

    def test_suppressed_in_contract_but_not_produced(self):
        assert SilenceVerdict.SUPPRESSED.value == "suppressed"
        state = select_attention([_auth_blocker(provider="amex")], now=FIXED_NOW)
        assert state.silence is None
        assert state.silence != SilenceVerdict.SUPPRESSED


class TestPrimarySelection:
    def test_one_auth_blocker_is_primary(self):
        item = _auth_blocker(provider="amex")
        state = select_attention([item], now=FIXED_NOW)
        assert state.primary == item
        assert state.remaining == ()
        assert state.silence is None

    def test_agent_authorization_beats_auth_blocker(self):
        auth = _auth_blocker(provider="amex")
        agent = _agent_authorization()
        state = select_attention([auth, agent], now=FIXED_NOW)
        assert state.primary == agent
        assert state.remaining == (auth,)
        assert state.silence is None

    def test_auth_blocker_beats_opportunity(self):
        auth = _auth_blocker(provider="amex")
        opp = _opportunity()
        state = select_attention([opp, auth], now=FIXED_NOW)
        assert state.primary == auth
        assert state.remaining == (opp,)
        assert state.silence is None

    def test_canonical_class_order(self):
        items = [
            _data_gap(),
            _access_degraded(),
            _opportunity(),
            _value_at_risk(
                attention_id="att_var_soon",
                becomes_stale_at="2026-07-22T00:00:00+00:00",
            ),
            _system(),
            _auth_blocker(provider="amex"),
            _agent_authorization(),
            _trust(),
        ]
        state = select_attention(items, now=FIXED_NOW)
        ordered_classes = [state.primary.attention_class] + [
            item.attention_class for item in state.remaining
        ]
        assert ordered_classes == [
            AttentionClass.TRUST,
            AttentionClass.AGENT_AUTHORIZATION,
            AttentionClass.AUTH_BLOCKER,
            AttentionClass.SYSTEM,
            AttentionClass.VALUE_AT_RISK,
            AttentionClass.OPPORTUNITY,
            AttentionClass.ACCESS_DEGRADED,
            AttentionClass.DATA_GAP,
        ]
        assert state.silence is None


class TestLexicalOrdering:
    def test_same_class_lex_provider_then_attention_id(self):
        chase = _auth_blocker(provider="chase")
        amex = _auth_blocker(provider="amex")
        state = select_attention([chase, amex], now=FIXED_NOW)
        assert state.primary == amex
        assert state.remaining == (chase,)

    def test_provider_none_sorts_as_empty_string(self):
        with_provider = _opportunity(
            attention_id="att_opp_amex",
            provider="amex",
        )
        no_provider = _opportunity(
            attention_id="att_opp_none",
            provider=None,
        )
        state = select_attention([with_provider, no_provider], now=FIXED_NOW)
        assert state.primary == no_provider
        assert state.remaining == (with_provider,)

    def test_identical_semantics_different_input_order(self):
        a = _auth_blocker(provider="amex")
        b = _auth_blocker(provider="chase")
        c = _opportunity()
        first = select_attention([c, b, a], now=FIXED_NOW)
        second = select_attention([a, c, b], now=FIXED_NOW)
        third = select_attention([b, a, c], now=FIXED_NOW)
        assert first.to_dict() == second.to_dict() == third.to_dict()
        assert first.primary == a
        assert first.remaining == (b, c)


class TestEffectiveness:
    def test_stale_vs_effective_at_fixed_clock(self):
        expired = _value_at_risk(
            attention_id="att_var_expired",
            becomes_stale_at="2026-07-21T11:00:00+00:00",
        )
        live = _value_at_risk(
            attention_id="att_var_live",
            becomes_stale_at="2026-07-21T13:00:00+00:00",
        )
        state = select_attention([expired, live], now=FIXED_NOW)
        assert state.primary == live
        assert state.remaining == ()
        assert state.silence is None

    def test_exact_deadline_is_ineffective(self):
        at_deadline = _value_at_risk(
            attention_id="att_var_exact",
            becomes_stale_at="2026-07-21T12:00:00+00:00",
        )
        state = select_attention([at_deadline], now=FIXED_NOW)
        assert state.primary is None
        assert state.silence == SilenceVerdict.ALL_CLEAR

    def test_rank5_earlier_deadline_wins_none_last(self):
        soon = _value_at_risk(
            attention_id="att_var_soon",
            becomes_stale_at="2026-07-22T00:00:00+00:00",
            provider="amex",
        )
        later = _value_at_risk(
            attention_id="att_var_later",
            becomes_stale_at="2026-07-23T00:00:00+00:00",
            provider="amex",
        )
        no_deadline = _value_at_risk(
            attention_id="att_var_none",
            becomes_stale_at=None,
            provider="amex",
        )
        state = select_attention([no_deadline, later, soon], now=FIXED_NOW)
        assert state.primary == soon
        assert state.remaining == (later, no_deadline)


class TestPrimaryAndRemaining:
    def test_primary_plus_ordered_remaining(self):
        agent = _agent_authorization()
        amex = _auth_blocker(provider="amex")
        chase = _auth_blocker(provider="chase")
        opp = _opportunity()
        state = select_attention([opp, chase, amex, agent], now=FIXED_NOW)
        assert state.primary == agent
        assert state.remaining == (amex, chase, opp)


class TestSerializationAndReplay:
    def test_serialization_round_trip(self):
        state = select_attention(
            [
                _agent_authorization(),
                _auth_blocker(provider="amex"),
                _opportunity(),
            ],
            now=FIXED_NOW,
        )
        restored = AttentionState.from_dict(state.to_dict())
        assert restored == state
        assert restored.to_dict() == state.to_dict()

    def test_json_round_trip(self):
        state = select_attention(
            [_auth_blocker(provider="amex"), _access_degraded()],
            now=FIXED_NOW,
        )
        encoded = json.dumps(state.to_dict(), sort_keys=True)
        restored = AttentionState.from_dict(json.loads(encoded))
        assert restored.to_dict() == state.to_dict()

    def test_replay_determinism(self):
        items = [
            _auth_blocker(provider="chase"),
            _auth_blocker(provider="amex"),
            _opportunity(attention_id="att_opp_b", provider="boa"),
            _value_at_risk(
                attention_id="att_var_a",
                becomes_stale_at="2026-07-25T00:00:00+00:00",
            ),
        ]
        snapshots = [select_attention(items, now=FIXED_NOW).to_dict() for _ in range(5)]
        assert all(snap == snapshots[0] for snap in snapshots)


class TestBoundaries:
    def test_no_provider_category_preference(self):
        """Lex provider ASC only — financial/loyalty categories must not matter."""
        # "united" (loyalty-ish) before "amex" (financial-ish) would win under a
        # category rule; lex requires amex first.
        united = _auth_blocker(provider="united")
        amex = _auth_blocker(provider="amex")
        state = select_attention([united, amex], now=FIXED_NOW)
        assert state.primary.provider == "amex"
        assert state.remaining[0].provider == "united"

    def test_no_mutation_of_input_items(self):
        items = [
            _opportunity(),
            _auth_blocker(provider="amex"),
            _auth_blocker(provider="chase"),
        ]
        before = [copy.deepcopy(item.to_dict()) for item in items]
        ids_before = [id(item) for item in items]
        state = select_attention(items, now=FIXED_NOW)
        assert [item.to_dict() for item in items] == before
        assert [id(item) for item in items] == ids_before
        # Outputs are the same immutable instances (no copies required).
        assert state.primary is items[1]
        assert state.remaining[0] is items[2]
        assert state.remaining[1] is items[0]

    def test_frozen_state(self):
        state = select_attention([], now=FIXED_NOW)
        with pytest.raises(Exception):
            state.silence = SilenceVerdict.AWAITING_DATA  # type: ignore[misc]
