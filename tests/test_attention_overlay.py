"""Golden / replay tests for Attention overlays + compose (PR 2D)."""

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
    REASON_LOGIN,
    REASON_OPPORTUNITY,
    REASON_PENDING_AUTHORIZATION,
    REASON_STALE,
    REASON_VALUE_AT_RISK,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
)
from mighty.attention_overlay import (
    IN_FLIGHT_TIMEOUT_SECONDS,
    AttentionOverlay,
    AttentionOverlayValidationError,
    OverlayStatus,
    apply_overlays,
    compose_attention,
)
from mighty.attention_state import SilenceVerdict, select_attention

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


def _auth_blocker(*, provider: str = "amex") -> AttentionItem:
    return _item(
        attention_id=f"att_{USER_ID}_auth_blocker_{provider}_needs_human",
        attention_class=AttentionClass.AUTH_BLOCKER,
        urgency=AttentionUrgency.BLOCKER,
        provider=provider,
        fingerprint=f"auth:{provider}:needs_human",
        reason=REASON_LOGIN,
        cta_key=AttentionCtaKey.START_PROVIDER_LOGIN,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=f"auth_truth:{USER_ID}:{provider}",
    )


def _opportunity(*, attention_id: str = "att_user1_opportunity_1") -> AttentionItem:
    return _item(
        attention_id=attention_id,
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        provider="amex",
        fingerprint=f"benefit:opportunity:{attention_id}",
        reason=REASON_OPPORTUNITY,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref=f"benefit:{attention_id}",
    )


def _value_at_risk() -> AttentionItem:
    return _item(
        attention_id="att_user1_var_1",
        attention_class=AttentionClass.VALUE_AT_RISK,
        urgency=AttentionUrgency.TIME_SENSITIVE,
        provider="amex",
        fingerprint="benefit:var:1",
        reason=REASON_VALUE_AT_RISK,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref="benefit:var:1",
        becomes_stale_at="2026-07-22T00:00:00+00:00",
    )


def _access_degraded() -> AttentionItem:
    return _item(
        attention_id="att_user1_access_degraded_amex_stale",
        attention_class=AttentionClass.ACCESS_DEGRADED,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider="amex",
        fingerprint="auth:amex:stale",
        reason=REASON_STALE,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.AUTH,
        source_ref=f"auth_truth:{USER_ID}:amex",
    )


def _agent() -> AttentionItem:
    return _item(
        attention_id="att_user1_agent_authorization_1",
        attention_class=AttentionClass.AGENT_AUTHORIZATION,
        urgency=AttentionUrgency.BLOCKER,
        provider=None,
        fingerprint="authorize:pending:1",
        reason=REASON_PENDING_AUTHORIZATION,
        cta_key=AttentionCtaKey.OPEN_ACTIVITY_APPROVAL,
        source_kind=AttentionSourceKind.AUTHORIZE,
        source_ref="authorize:1",
    )


def _overlay(
    attention_id: str,
    status: OverlayStatus,
    *,
    until: str | None = None,
    started_at: str | None = None,
    updated_at: str = "2026-07-21T11:30:00+00:00",
) -> AttentionOverlay:
    return AttentionOverlay(
        attention_id=attention_id,
        status=status,
        until=until,
        started_at=started_at,
        updated_at=updated_at,
    )


class TestOverlayContract:
    def test_round_trip(self):
        overlay = _overlay(
            "att_1",
            OverlayStatus.SNOOZED,
            until="2026-07-21T13:00:00+00:00",
        )
        restored = AttentionOverlay.from_dict(overlay.to_dict())
        assert restored == overlay

    def test_snoozed_requires_until(self):
        with pytest.raises(AttentionOverlayValidationError):
            AttentionOverlay(
                attention_id="att_1",
                status=OverlayStatus.SNOOZED,
                until=None,
                started_at=None,
                updated_at="2026-07-21T11:30:00+00:00",
            )

    def test_in_flight_requires_started_at(self):
        with pytest.raises(AttentionOverlayValidationError):
            AttentionOverlay(
                attention_id="att_1",
                status=OverlayStatus.IN_FLIGHT,
                until=None,
                started_at=None,
                updated_at="2026-07-21T11:30:00+00:00",
            )

    def test_in_flight_timeout_constant(self):
        assert IN_FLIGHT_TIMEOUT_SECONDS == 30 * 60


class TestApplyOverlays:
    def test_missing_overlay_is_visible(self):
        item = _auth_blocker()
        result = apply_overlays([item], [], now=FIXED_NOW)
        assert result.visible == (item,)
        assert result.snoozed_rank_1_to_4 is False

    def test_active_snooze_hides(self):
        item = _auth_blocker()
        overlays = [
            _overlay(
                item.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        result = apply_overlays([item], overlays, now=FIXED_NOW)
        assert result.visible == ()
        assert result.snoozed_rank_1_to_4 is True

    def test_expired_snooze_is_visible(self):
        item = _auth_blocker()
        overlays = [
            _overlay(
                item.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T11:00:00+00:00",
            )
        ]
        result = apply_overlays([item], overlays, now=FIXED_NOW)
        assert result.visible == (item,)
        assert result.snoozed_rank_1_to_4 is False

    def test_exact_snooze_until_is_visible(self):
        item = _auth_blocker()
        overlays = [
            _overlay(
                item.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T12:00:00+00:00",
            )
        ]
        result = apply_overlays([item], overlays, now=FIXED_NOW)
        assert result.visible == (item,)

    def test_in_flight_remains_visible(self):
        item = _auth_blocker()
        overlays = [
            _overlay(
                item.attention_id,
                OverlayStatus.IN_FLIGHT,
                started_at="2026-07-21T11:45:00+00:00",
            )
        ]
        result = apply_overlays([item], overlays, now=FIXED_NOW)
        assert result.visible == (item,)

    def test_durable_dismiss_hides_opportunity_only(self):
        opp = _opportunity()
        auth = _auth_blocker()
        overlays = [
            _overlay(opp.attention_id, OverlayStatus.DURABLE_DISMISSED),
            _overlay(auth.attention_id, OverlayStatus.DURABLE_DISMISSED),
        ]
        result = apply_overlays([opp, auth], overlays, now=FIXED_NOW)
        assert result.visible == (auth,)

    def test_mapping_overlays_accepted(self):
        item = _auth_blocker()
        overlays = {
            item.attention_id: _overlay(
                item.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        }
        result = apply_overlays([item], overlays, now=FIXED_NOW)
        assert result.visible == ()


class TestComposeSuppressed:
    def test_scenario_4_snooze_blocker_suppressed(self):
        """Part XIV #4 — snooze blocker → silence=suppressed."""
        auth = _auth_blocker()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        state = compose_attention([auth], overlays, now=FIXED_NOW)
        assert state.primary is None
        assert state.remaining == ()
        assert state.silence == SilenceVerdict.SUPPRESSED

    def test_scenario_10_dismiss_opportunity_snooze_login(self):
        """Part XIV #10 — dismissed opp must not become primary under suppressed."""
        auth = _auth_blocker()
        opp = _opportunity()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            ),
            _overlay(opp.attention_id, OverlayStatus.DURABLE_DISMISSED),
        ]
        state = compose_attention([auth, opp], overlays, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.SUPPRESSED
        assert state.primary is None
        assert state.remaining == ()

    def test_snoozed_blocker_opportunity_not_primary(self):
        auth = _auth_blocker()
        opp = _opportunity()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        state = compose_attention([auth, opp], overlays, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.SUPPRESSED
        assert state.primary is None
        assert state.remaining == (opp,)

    def test_snoozed_blocker_with_visible_value_at_risk_not_suppressed(self):
        auth = _auth_blocker()
        var = _value_at_risk()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        state = compose_attention([auth, var], overlays, now=FIXED_NOW)
        assert state.silence is None
        assert state.primary == var
        assert state.remaining == ()

    def test_snoozed_opportunity_alone_is_all_clear_not_suppressed(self):
        opp = _opportunity()
        overlays = [
            _overlay(
                opp.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        state = compose_attention([opp], overlays, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.ALL_CLEAR
        assert state.primary is None

    def test_no_overlays_matches_select_attention(self):
        items = [_auth_blocker(provider="chase"), _auth_blocker(provider="amex"), _opportunity()]
        composed = compose_attention(items, [], now=FIXED_NOW)
        ranked = select_attention(items, now=FIXED_NOW)
        assert composed.to_dict() == ranked.to_dict()


class TestComposeOther:
    def test_in_flight_auth_still_primary(self):
        auth = _auth_blocker()
        opp = _opportunity()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.IN_FLIGHT,
                started_at="2026-07-21T11:50:00+00:00",
            )
        ]
        state = compose_attention([opp, auth], overlays, now=FIXED_NOW)
        assert state.primary == auth
        assert state.silence is None

    def test_agent_beats_auth_after_overlays(self):
        auth = _auth_blocker()
        agent = _agent()
        state = compose_attention([auth, agent], [], now=FIXED_NOW)
        assert state.primary == agent
        assert state.remaining == (auth,)

    def test_input_order_and_mutation_safety(self):
        auth = _auth_blocker(provider="amex")
        chase = _auth_blocker(provider="chase")
        opp = _opportunity()
        items = [opp, chase, auth]
        before = [copy.deepcopy(item.to_dict()) for item in items]
        overlays = [
            _overlay(
                chase.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        first = compose_attention(items, overlays, now=FIXED_NOW)
        second = compose_attention(list(reversed(items)), overlays, now=FIXED_NOW)
        assert first.to_dict() == second.to_dict()
        assert first.primary == auth
        assert [item.to_dict() for item in items] == before

    def test_json_replay(self):
        auth = _auth_blocker()
        degraded = _access_degraded()
        overlays = [
            _overlay(
                auth.attention_id,
                OverlayStatus.SNOOZED,
                until="2026-07-21T13:00:00+00:00",
            )
        ]
        state = compose_attention([auth, degraded], overlays, now=FIXED_NOW)
        assert state.silence == SilenceVerdict.SUPPRESSED
        assert state.primary is None
        assert state.remaining == (degraded,)
        encoded = json.dumps(state.to_dict(), sort_keys=True)
        # AttentionState round-trip covered in 2C; ensure payload is stable.
        assert json.loads(encoded)["silence"] == "suppressed"
