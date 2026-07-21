"""Golden / replay tests for BenefitSignal compilers (M4)."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_OPPORTUNITY,
    REASON_VALUE_AT_RISK,
    AttentionClass,
    AttentionCtaKey,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_compiler import (
    BenefitSignal,
    benefit_fingerprint,
    benefit_is_value_at_risk,
    compile_attention_candidates,
    compile_benefit_attention,
)

USER_ID = "user-1"
PROVIDER = "amex"
FIELD = "companion_cert"
OBSERVED = "2026-07-21T10:00:00+00:00"
EXP = "2026-07-28T00:00:00"


def _signal(**overrides) -> BenefitSignal:
    payload = {
        "user_id": USER_ID,
        "provider": PROVIDER,
        "field_key": FIELD,
        "btype": "certificate",
        "urgency": "urgent",
        "days_left": 5,
        "exp_date": EXP,
        "label": "Companion Certificate",
        "value": "1",
        "kind": "expiring",
        "observed_at": OBSERVED,
        "source_item_id": "9",
    }
    payload.update(overrides)
    return BenefitSignal(**payload)


class TestBenefitCompiler:
    def test_value_at_risk_golden(self):
        item = compile_benefit_attention(_signal())
        assert item is not None
        assert item.to_dict() == {
            "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
            "attention_id": "att_user-1_value_at_risk_amex_companion_cert",
            "user_id": USER_ID,
            "attention_class": AttentionClass.VALUE_AT_RISK.value,
            "urgency": AttentionUrgency.TIME_SENSITIVE.value,
            "provider": PROVIDER,
            "fingerprint": "benefit:amex:companion_cert",
            "reason": {"code": REASON_VALUE_AT_RISK},
            "cta_key": AttentionCtaKey.OPEN_ACCOUNT_DETAIL.value,
            "source_kind": AttentionSourceKind.BENEFIT.value,
            "source_ref": "action_item:9",
            "observed_at": OBSERVED,
            "becomes_stale_at": EXP,
            "interruption_expected": False,
        }

    def test_opportunity_when_not_time_sensitive(self):
        item = compile_benefit_attention(
            _signal(urgency="info", days_left=60, exp_date=None, kind="opportunity")
        )
        assert item is not None
        assert item.attention_class is AttentionClass.OPPORTUNITY
        assert item.urgency is AttentionUrgency.OPPORTUNITY
        assert item.reason.code == REASON_OPPORTUNITY
        assert item.becomes_stale_at is None
        assert item.fingerprint == benefit_fingerprint(PROVIDER, FIELD)

    def test_days_left_threshold_makes_value_at_risk(self):
        signal = _signal(urgency="info", days_left=14)
        assert benefit_is_value_at_risk(signal) is True
        item = compile_benefit_attention(signal)
        assert item is not None
        assert item.attention_class is AttentionClass.VALUE_AT_RISK

    def test_mutual_exclusion_single_item(self):
        items = compile_attention_candidates(benefit_signals=[_signal()])
        assert len(items) == 1
        assert items[0].attention_class is AttentionClass.VALUE_AT_RISK

    def test_non_actionable_does_not_emit(self):
        assert compile_benefit_attention(_signal(btype="points_balance")) is None

    def test_payment_due_can_be_value_at_risk(self):
        item = compile_benefit_attention(
            _signal(btype="payment_due", urgency="soon", days_left=10)
        )
        assert item is not None
        assert item.attention_class is AttentionClass.VALUE_AT_RISK


def test_gather_order_benefits_before_data_gap():
    from dataclasses import dataclass

    from mighty.account_state import CONN_CONNECTED, DATA_NONE

    @dataclass
    class _Account:
        user_id: str = USER_ID
        provider: str = PROVIDER
        connection_state: str = CONN_CONNECTED
        data_status: str = DATA_NONE
        last_data_refresh: str | None = None
        updated_at: str = OBSERVED

    items = compile_attention_candidates(
        benefit_signals=[_signal(urgency="info", days_left=90, exp_date=None)],
        account_states=[_Account()],
    )
    assert [i.attention_class for i in items] == [
        AttentionClass.OPPORTUNITY,
        AttentionClass.DATA_GAP,
    ]
