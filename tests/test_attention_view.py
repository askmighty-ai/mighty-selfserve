"""Tests for AttentionView surface windowing + copy resolution (Milestone 3)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    REASON_LOGIN,
    REASON_PENDING_AUTHORIZATION,
    REASON_STALE,
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
from mighty.attention_view import (
    ATTENTION_VIEW_SCHEMA_VERSION,
    build_attention_view,
    resolve_attention_copy,
    resolve_attention_cta_url,
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


def _degraded() -> AttentionItem:
    return _item(
        attention_id="att_user1_access_degraded_amex",
        attention_class=AttentionClass.ACCESS_DEGRADED,
        urgency=AttentionUrgency.INFORMATIONAL,
        provider="amex",
        fingerprint="auth:amex:access_degraded",
        reason=REASON_STALE,
        cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
        source_kind=AttentionSourceKind.AUTH,
        source_ref="auth_truth:user-1:amex",
    )


class TestBuildAttentionView:
    def test_home_windows_primary_and_secondary(self):
        state = select_attention([_authorize(), _item(), _degraded()], now=FIXED_NOW)
        view = build_attention_view(
            state,
            surface="home",
            provider_open_urls={"amex": "https://example.com/amex"},
        )
        assert view.schema_version == ATTENTION_VIEW_SCHEMA_VERSION
        assert view.surface == "home"
        assert view.primary is not None
        assert view.primary.attention_class == AttentionClass.AGENT_AUTHORIZATION
        assert view.primary.cta_url == "/activity"
        assert view.primary.title == "Approval needed"
        assert len(view.secondary) == 2
        assert view.secondary[0].attention_class == AttentionClass.AUTH_BLOCKER
        assert view.secondary[0].cta_url == "https://example.com/amex"
        assert view.render_hints.interrupt is True
        assert view.silence is None
        assert view.health_counts.blockers == 2
        assert view.health_counts.informational == 1

    def test_worker_has_no_secondary(self):
        state = select_attention([_item(), _authorize()], now=FIXED_NOW)
        view = build_attention_view(state, surface="worker")
        assert view.primary is not None
        assert view.secondary == ()
        assert view.render_hints.secondary_limit == 0

    def test_activity_filters_authorize_without_rerank(self):
        state = select_attention([_item(), _authorize()], now=FIXED_NOW)
        # Global primary is authorize (rank 2 < auth 3).
        assert state.primary.attention_class == AttentionClass.AGENT_AUTHORIZATION
        view = build_attention_view(state, surface="activity")
        assert view.primary is not None
        assert view.primary.attention_class == AttentionClass.AGENT_AUTHORIZATION
        assert view.secondary == ()

    def test_activity_empty_when_no_authorize(self):
        state = select_attention([_item()], now=FIXED_NOW)
        view = build_attention_view(state, surface="activity")
        assert view.primary is None
        # Global silence still reflects ranks 1–5 present.
        assert view.silence is None

    def test_all_clear_silence(self):
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=None,
            remaining=(),
            silence=SilenceVerdict.ALL_CLEAR,
        )
        view = build_attention_view(state, surface="home")
        assert view.primary is None
        assert view.silence == SilenceVerdict.ALL_CLEAR
        assert view.render_hints.interrupt is False
        assert view.render_hints.show_silence is True

    def test_does_not_rerank_input_order(self):
        amex = _item(provider="amex", attention_id="att_a")
        delta = _item(
            provider="delta",
            attention_id="att_d",
            fingerprint="auth:delta:needs_human",
            source_ref="auth_truth:user-1:delta",
        )
        # Lex tie-break: amex before delta regardless of input order.
        state = select_attention([delta, amex], now=FIXED_NOW)
        view = build_attention_view(state, surface="home")
        assert view.primary.provider == "amex"

    def test_to_dict_roundtrip_fields(self):
        state = select_attention([_item()], now=FIXED_NOW)
        view = build_attention_view(state, surface="home")
        payload = view.to_dict()
        assert payload["surface"] == "home"
        assert payload["primary"]["attention_id"] == state.primary.attention_id
        assert payload["health_counts"]["blockers"] == 1


class TestCopyAndCta:
    def test_login_copy(self):
        title, body, cta = resolve_attention_copy(_item())
        assert "American Express" in title
        assert "Sign in to American Express." in title
        assert "only step we can't complete for you" in body
        assert "we'll take care of the rest" in body
        assert cta == "Log in to American Express"

    def test_opportunity_copy_is_benefit_concrete(self):
        item = _item(
            attention_id="att_user1_opportunity_amex_dining_credit",
            attention_class=AttentionClass.OPPORTUNITY,
            urgency=AttentionUrgency.OPPORTUNITY,
            fingerprint="benefit:amex:dining_credit",
            reason=AttentionReason(code="opportunity"),
            cta_key=AttentionCtaKey.OPEN_ACCOUNT_DETAIL,
            source_kind=AttentionSourceKind.BENEFIT,
            source_ref="action_item:1",
            interruption_expected=False,
        )
        title, body, cta = resolve_attention_copy(item)
        assert "Dining credit available on American Express" in title
        assert "Something worth a look" not in title
        assert "put real value" in body
        assert cta == "See the benefit"

    def test_cta_urls(self):
        assert resolve_attention_cta_url(
            _item(), provider_open_urls={"amex": "https://amex.test"}
        ) == "https://amex.test"
        assert resolve_attention_cta_url(_authorize()) == "/activity"
        assert resolve_attention_cta_url(_degraded()) == "/credentials?provider=amex"
