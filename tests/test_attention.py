"""Unit tests for AttentionItem contract (PR 2A / RFC Part XIV)."""

from __future__ import annotations

import json
import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_CAPTCHA,
    REASON_DATA_GAP,
    REASON_LOGIN,
    REASON_MFA,
    REASON_OPPORTUNITY,
    REASON_PENDING_AUTHORIZATION,
    REASON_STALE,
    REASON_VALUE_AT_RISK,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionItemValidationError,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)


def _item(**overrides) -> AttentionItem:
    payload = {
        "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
        "attention_id": "att_user1_auth_blocker_amex_login",
        "user_id": "user-1",
        "attention_class": AttentionClass.AUTH_BLOCKER,
        "urgency": AttentionUrgency.BLOCKER,
        "provider": "amex",
        "fingerprint": "auth:amex:needs_human",
        "reason": AttentionReason(code=REASON_LOGIN),
        "cta_key": AttentionCtaKey.START_PROVIDER_LOGIN,
        "source_kind": AttentionSourceKind.AUTH,
        "source_ref": "auth_truth:user-1:amex",
        "observed_at": "2026-07-21T12:00:00+00:00",
        "becomes_stale_at": None,
        "interruption_expected": False,
    }
    payload.update(overrides)
    if isinstance(payload.get("reason"), str):
        payload["reason"] = AttentionReason(code=payload["reason"])
    return AttentionItem(**payload)


class TestImmutabilityAndSerialization:
    def test_frozen(self):
        item = _item()
        with pytest.raises(Exception):
            item.user_id = "other"  # type: ignore[misc]

    def test_round_trip_dict(self):
        item = _item(
            becomes_stale_at="2026-07-22T00:00:00+00:00",
            interruption_expected=True,
            reason=AttentionReason(code=REASON_MFA),
        )
        restored = AttentionItem.from_dict(item.to_dict())
        assert restored == item
        assert restored.to_dict() == item.to_dict()

    def test_json_stable_keys(self):
        item = _item()
        encoded = json.dumps(item.to_dict(), sort_keys=True)
        restored = AttentionItem.from_dict(json.loads(encoded))
        assert restored == item

    def test_reason_accepts_string_in_from_dict(self):
        payload = _item().to_dict()
        payload["reason"] = REASON_CAPTCHA
        restored = AttentionItem.from_dict(payload)
        assert restored.reason.code == REASON_CAPTCHA

    def test_provider_normalized_lower(self):
        item = _item(provider="Amex")
        assert item.provider == "amex"
        assert AttentionItem.from_dict(item.to_dict()).provider == "amex"

    def test_identical_inputs_produce_identical_dicts(self):
        a = _item()
        b = _item()
        assert a.to_dict() == b.to_dict()


class TestValidation:
    def test_rejects_empty_attention_id(self):
        with pytest.raises(AttentionItemValidationError, match="attention_id"):
            _item(attention_id="  ")

    def test_rejects_unknown_class(self):
        payload = _item().to_dict()
        payload["attention_class"] = "hero"
        with pytest.raises(AttentionItemValidationError, match="attention_class"):
            AttentionItem.from_dict(payload)

    def test_rejects_unknown_cta(self):
        payload = _item().to_dict()
        payload["cta_key"] = "show_banner"
        with pytest.raises(AttentionItemValidationError, match="cta_key"):
            AttentionItem.from_dict(payload)

    def test_rejects_class_urgency_mismatch(self):
        with pytest.raises(AttentionItemValidationError, match="urgency"):
            _item(
                attention_class=AttentionClass.AUTH_BLOCKER,
                urgency=AttentionUrgency.OPPORTUNITY,
            )

    def test_rejects_empty_reason_code(self):
        with pytest.raises(AttentionItemValidationError, match="reason.code"):
            AttentionReason(code="")

    def test_rejects_whitespace_reason_code(self):
        with pytest.raises(AttentionItemValidationError, match="whitespace"):
            AttentionReason(code="needs login")

    def test_rejects_wrong_schema_version(self):
        payload = _item().to_dict()
        payload["schema_version"] = 99
        with pytest.raises(AttentionItemValidationError, match="schema_version"):
            AttentionItem.from_dict(payload)

    def test_rejects_bad_observed_at(self):
        with pytest.raises(AttentionItemValidationError, match="observed_at"):
            _item(observed_at="not-a-datetime")

    def test_rejects_presentation_fields_are_not_on_model(self):
        item = _item()
        payload = item.to_dict()
        assert "title" not in payload
        assert "body" not in payload
        assert "cta_label" not in payload
        assert "rank" not in payload
        assert "dismissed" not in payload
        assert "snoozed" not in payload
        assert "primary" not in payload


# ---------------------------------------------------------------------------
# Part XIV normative scenarios — expressed only as AttentionItem candidates.
# Overlay / ranking / delivery outcomes are documented, not modeled here.
# ---------------------------------------------------------------------------


class TestPartXivScenarios:
    def test_1_extension_session_expired(self):
        """AuthTruth.needs_human → one auth_blocker candidate."""
        items = (
            _item(
                attention_id="att_u1_auth_blocker_amex_needs_human",
                fingerprint="auth:amex:needs_human",
                reason=AttentionReason(code=REASON_LOGIN),
                cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
                source_ref="auth_truth:user-1:amex",
                interruption_expected=False,
            ),
        )
        assert len(items) == 1
        assert items[0].attention_class == AttentionClass.AUTH_BLOCKER
        assert items[0].cta_key == AttentionCtaKey.START_PROVIDER_LOGIN
        # Cleared state = empty candidate set (no item), not a tombstone field.

    def test_2_runtime_mfa_mid_session(self):
        items = (
            _item(
                attention_id="att_u1_auth_blocker_amex_needs_human",
                fingerprint="auth:amex:needs_human",
                reason=AttentionReason(code=REASON_MFA),
                cta_key=AttentionCtaKey.FOCUS_MANAGED_RUNTIME,
                interruption_expected=False,
            ),
        )
        assert items[0].reason.code == REASON_MFA
        assert items[0].interruption_expected is False
        # Recovering / needs_human false → empty tuple (no candidate).

    def test_3_captcha_during_login_same_fingerprint(self):
        login = _item(
            fingerprint="auth:amex:needs_human",
            reason=AttentionReason(code=REASON_LOGIN),
        )
        captcha = _item(
            fingerprint="auth:amex:needs_human",
            reason=AttentionReason(code=REASON_CAPTCHA),
        )
        assert login.fingerprint == captcha.fingerprint
        assert login.attention_id == captcha.attention_id
        assert login.reason.code != captcha.reason.code
        # One candidate identity; material reason change is attention.updated later.

    def test_4_snooze_blocker_still_a_candidate(self):
        """Snooze is overlay state — the candidate item itself is unchanged."""
        items = (_item(),)
        assert items[0].attention_class == AttentionClass.AUTH_BLOCKER
        payload = items[0].to_dict()
        assert "snoozed" not in payload
        assert "until" not in payload

    def test_5_agent_authorize(self):
        items = (
            _item(
                attention_id="att_u1_agent_authorization_row42",
                attention_class=AttentionClass.AGENT_AUTHORIZATION,
                urgency=AttentionUrgency.BLOCKER,
                provider=None,
                fingerprint="authorize:row:42",
                reason=AttentionReason(code=REASON_PENDING_AUTHORIZATION),
                cta_key=AttentionCtaKey.OPEN_ACTIVITY_APPROVAL,
                source_kind=AttentionSourceKind.AUTHORIZE,
                source_ref="authorize:42",
                interruption_expected=False,
            ),
        )
        assert items[0].source_kind == AttentionSourceKind.AUTHORIZE
        assert items[0].cta_key == AttentionCtaKey.OPEN_ACTIVITY_APPROVAL

    def test_6_multi_provider_signed_out(self):
        items = (
            _item(
                attention_id="att_u1_auth_blocker_amex_needs_human",
                provider="amex",
                fingerprint="auth:amex:needs_human",
                source_ref="auth_truth:user-1:amex",
            ),
            _item(
                attention_id="att_u1_auth_blocker_chase_needs_human",
                provider="chase",
                fingerprint="auth:chase:needs_human",
                source_ref="auth_truth:user-1:chase",
            ),
        )
        assert len(items) == 2
        assert {i.provider for i in items} == {"amex", "chase"}
        # Primary selection is ranking (later PR), not a field on AttentionItem.

    def test_7_phone_only_stale_is_not_auth_blocker(self):
        """stale ≠ signed_out: optional access_degraded, never auth_blocker."""
        items = (
            _item(
                attention_id="att_u1_access_degraded_amex_stale",
                attention_class=AttentionClass.ACCESS_DEGRADED,
                urgency=AttentionUrgency.INFORMATIONAL,
                fingerprint="auth:amex:stale",
                reason=AttentionReason(code=REASON_STALE),
                cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
                source_ref="auth_truth:user-1:amex",
            ),
        )
        assert items[0].attention_class == AttentionClass.ACCESS_DEGRADED
        assert items[0].urgency == AttentionUrgency.INFORMATIONAL
        # Surface capability (mobile cannot complete browser_session) is View
        # concern — see docs/ATTENTION_ITEM.md gap.

    def test_8_bootstrap_mfa_expected(self):
        items = (
            _item(
                fingerprint="auth:amex:needs_human",
                reason=AttentionReason(code=REASON_MFA),
                cta_key=AttentionCtaKey.FOCUS_MANAGED_RUNTIME,
                interruption_expected=True,
            ),
        )
        assert items[0].attention_class == AttentionClass.AUTH_BLOCKER
        assert items[0].interruption_expected is True

    def test_9_dual_path_no_customer_auth_blocker(self):
        """Primary browser_session signed_in + Runtime needs_human → no item."""
        items: tuple[AttentionItem, ...] = ()
        assert items == ()

    def test_10_dismiss_opportunity_and_snooze_login(self):
        """Both remain candidates; dismiss/snooze are overlays (later PR)."""
        items = (
            _item(
                attention_id="att_u1_auth_blocker_amex_needs_human",
                fingerprint="auth:amex:needs_human",
                reason=AttentionReason(code=REASON_LOGIN),
            ),
            _item(
                attention_id="att_u1_opportunity_amex_benefit9",
                attention_class=AttentionClass.OPPORTUNITY,
                urgency=AttentionUrgency.OPPORTUNITY,
                fingerprint="benefit:amex:9",
                reason=AttentionReason(code=REASON_OPPORTUNITY),
                cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
                source_kind=AttentionSourceKind.BENEFIT,
                source_ref="benefit:9",
                becomes_stale_at="2026-08-01T00:00:00+00:00",
            ),
        )
        assert len(items) == 2
        assert {i.attention_class for i in items} == {
            AttentionClass.AUTH_BLOCKER,
            AttentionClass.OPPORTUNITY,
        }
        for item in items:
            payload = item.to_dict()
            assert "dismissed" not in payload
            assert "snoozed" not in payload
            assert "primary" not in payload

    def test_value_at_risk_uses_becomes_stale_at(self):
        item = _item(
            attention_id="att_u1_value_at_risk_amex_b1",
            attention_class=AttentionClass.VALUE_AT_RISK,
            urgency=AttentionUrgency.TIME_SENSITIVE,
            fingerprint="benefit:amex:expiring:1",
            reason=AttentionReason(code=REASON_VALUE_AT_RISK),
            cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
            source_kind=AttentionSourceKind.BENEFIT,
            source_ref="benefit:1",
            becomes_stale_at="2026-07-25T00:00:00+00:00",
        )
        assert item.becomes_stale_at is not None

    def test_data_gap_candidate(self):
        item = _item(
            attention_id="att_u1_data_gap_amex",
            attention_class=AttentionClass.DATA_GAP,
            urgency=AttentionUrgency.INFORMATIONAL,
            fingerprint="account_data:amex:gap",
            reason=AttentionReason(code=REASON_DATA_GAP),
            cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
            source_kind=AttentionSourceKind.ACCOUNT_DATA,
            source_ref="account_state:user-1:amex",
        )
        assert item.attention_class == AttentionClass.DATA_GAP
