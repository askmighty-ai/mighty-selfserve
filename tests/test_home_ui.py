"""Tests for Home V2 Living Calm rendering."""

import html
from datetime import datetime, timezone

from mighty.account_readiness import AccountReadiness, READY, SIGNED_OUT
from mighty.account_status import AccountStatus
from mighty.attention import (
    ATTENTION_ITEM_SCHEMA_VERSION,
    AttentionClass,
    AttentionCtaKey,
    AttentionItem,
    AttentionReason,
    AttentionSourceKind,
    AttentionUrgency,
    REASON_LOGIN,
)
from mighty.attention_state import ATTENTION_STATE_SCHEMA_VERSION, AttentionState
from mighty.attention_view import build_attention_view
from mighty.capability_state import CapabilityState
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    build_customer_account_access_view,
)
from mighty.home_state import resolve_home_state
from mighty.home_ui import render_home_page
from mighty import user_copy


def _escape(value):
    return html.escape(str(value)) if value is not None else ""


def _readiness(provider: str, state: str, **kwargs) -> AccountReadiness:
    labels = {
        READY: ("Connected", user_copy.READINESS_COPY_READY, "ready", "up_to_date"),
        SIGNED_OUT: (
            "Sign in required",
            user_copy.READINESS_COPY_SIGNED_OUT,
            "needs_sign_in",
            "needs_login",
        ),
    }
    label, copy, presentation, canonical = labels[state]
    defaults = dict(
        provider=provider,
        state=state,
        status_label=label,
        status_copy=copy,
        presentation_key=presentation,
        canonical_status=canonical,
        login_required=state == SIGNED_OUT,
        session_state="connected" if state == READY else "signed_out",
        access_cycle_id=None,
        session_evidence_at=None,
        extraction_at=None,
        extraction_ok=state == READY,
        extraction_correlated=state == READY,
        verification_id=None,
        cached_data_label=None,
        last_confirmed_ready_at=(
            datetime.now(timezone.utc).isoformat() if state == READY else None
        ),
        last_confirmed_access_cycle_id="cycle-1" if state == READY else None,
        background_verification=False,
        secondary_label=None,
    )
    defaults.update(kwargs)
    return AccountReadiness(**defaults)  # type: ignore[arg-type]


def _status_from_view(view, *, canonical: str | None = None) -> AccountStatus:
    canonical = canonical or view.canonical_status or "unverified"
    presentation_key = {
        "up_to_date": "ready",
        "needs_login": "needs_sign_in",
        "checking": "checking",
        "waiting_for_extension": "updating",
        "error": "needs_attention",
        "unverified": "unknown",
    }.get(canonical, "unknown")
    return AccountStatus(
        source=view.provider,
        display_name=view.display_name,
        status=canonical,
        presentation_key=presentation_key,
        presentation_label=view.status_label,
        last_successful_sync_at=view.last_confirmed_at,
        current_attempt_at=None,
        last_error=None,
        user_action_label=view.user_action_text,
        user_action_url=view.user_action_url,
        customer_access=view,
    )


def _attention_item(**overrides) -> AttentionItem:
    payload = {
        "schema_version": ATTENTION_ITEM_SCHEMA_VERSION,
        "attention_id": "att_user1_auth_blocker_amex_needs_human",
        "user_id": "user-1",
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
        "interruption_expected": True,
    }
    payload.update(overrides)
    return AttentionItem(**payload)


def _opportunity_item() -> AttentionItem:
    return _attention_item(
        attention_id="att_user1_opportunity_marriott",
        attention_class=AttentionClass.OPPORTUNITY,
        urgency=AttentionUrgency.OPPORTUNITY,
        provider="marriott",
        fingerprint="opportunity:marriott:cert",
        cta_key=AttentionCtaKey.OPEN_PROVIDER_SURFACE,
        source_kind=AttentionSourceKind.BENEFIT,
        source_ref="value:user-1:marriott",
        interruption_expected=False,
    )


class TestHomeV2Ui:
    def test_empty_shows_product_onboarding(self):
        result = resolve_home_state(accounts=[])
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "Your accounts, watched quietly." in rendered
        assert "Connect Gmail" in rendered
        assert "home-v2" in rendered
        assert 'data-state="empty"' in rendered
        assert "mds-quiet-field" in rendered
        assert "mds-btn--primary" in rendered

    def test_healthy_state_calm_earned_evidence(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(accounts=[_status_from_view(view)])
        rendered = render_home_page(
            result,
            first_name="Ryan",
            today_label="Friday, July 3",
            last_checked="2 minutes ago",
            escape=_escape,
            gmail_connected=True,
            chrome_active=True,
        )
        assert 'data-state="healthy"' in rendered
        assert "You&#x27;re good." in rendered or "You're good." in rendered
        assert "watch quietly" in rendered.lower()
        assert "Watching 1 account" in rendered
        assert "Last verified 2 minutes ago" in rendered or "Updated 2 minutes ago" in rendered
        assert "Gmail connected" in rendered
        assert "Chrome active" in rendered
        assert "Evidence" in rendered
        assert "Activity" in rendered
        assert "mds-btn--primary" not in rendered
        assert "home-v2__field" in rendered
        assert "home-v2__message" in rendered
        assert "<table" not in rendered.lower()

    def test_attention_required_owns_primary_action(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(accounts=[_status_from_view(view)])
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=_attention_item(),
            remaining=(),
            silence=None,
        )
        attention = build_attention_view(
            state,
            surface="home",
            provider_open_urls={"amex": "https://amex.test/login"},
        )
        rendered = render_home_page(
            result,
            first_name="Ryan",
            today_label="Friday, July 3",
            escape=_escape,
            attention=attention,
            use_attention=True,
            gmail_connected=True,
            chrome_active=True,
        )
        assert 'data-state="attention"' in rendered
        assert attention.primary is not None
        assert attention.primary.title in rendered
        assert "only step we can&#x27;t complete for you" in rendered or (
            "only step we can't complete for you" in rendered
        )
        assert "https://amex.test/login" in rendered
        assert "mds-btn--primary" in rendered
        assert 'mds-field-point is-signal' in rendered or "is-signal" in rendered

    def test_opportunity_available_state(self):
        view = build_customer_account_access_view(
            provider="marriott",
            display_name="Marriott Bonvoy",
            readiness=_readiness("marriott", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(accounts=[_status_from_view(view)])
        state = AttentionState(
            schema_version=ATTENTION_STATE_SCHEMA_VERSION,
            primary=_opportunity_item(),
            remaining=(),
            silence=None,
        )
        attention = build_attention_view(
            state,
            surface="home",
            provider_open_urls={"marriott": "https://marriott.test/cert"},
        )
        rendered = render_home_page(
            result,
            first_name="Ryan",
            today_label="Friday, July 3",
            escape=_escape,
            attention=attention,
            use_attention=True,
        )
        assert 'data-state="opportunity"' in rendered
        assert attention.primary is not None
        assert attention.primary.title in rendered
        assert "mds-btn--primary" in rendered
        assert "Value waiting" in rendered

    def test_waiting_handoff_confirmation_not_healthy(self):
        accounts = [
            AccountStatus(
                source="amex",
                display_name="American Express",
                status="waiting_for_extension",
                presentation_key="updating",
                presentation_label="Waiting",
                last_successful_sync_at=None,
                current_attempt_at=None,
                last_error=None,
                user_action_label=None,
                user_action_url=None,
            )
        ]
        result = resolve_home_state(accounts=accounts)
        rendered = render_home_page(
            result, first_name="Ryan", today_label="Friday, July 3", escape=_escape,
        )
        assert "You're good." not in rendered and "You&#x27;re good." not in rendered
        assert 'data-state="handoff"' in rendered
        assert "beginning to manage" in rendered
        assert "Visit American Express" in rendered
        assert "mds-btn--primary" in rendered
        assert "Getting ready" in rendered

    def test_activity_preview_from_recent_wins(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(accounts=[_status_from_view(view)])
        rendered = render_home_page(
            result,
            first_name="Ryan",
            today_label="Friday, July 3",
            escape=_escape,
            recent_wins=[{"message": "Membership Rewards increased", "source": "amex"}],
            gmail_connected=True,
            chrome_active=True,
        )
        assert "Membership Rewards increased" in rendered
        assert "home-v2__activity" in rendered

    def test_truth_debug_only_when_flagged(self):
        view = build_customer_account_access_view(
            provider="amex",
            display_name="American Express",
            readiness=_readiness("amex", READY),
            discovered_from=DISCOVERED_MANUAL,
        )
        result = resolve_home_state(
            accounts=[_status_from_view(view)],
            show_access_debug=True,
            extracted_items=[{"label": "Membership Rewards", "value": "125,000"}],
            session_confidence="high",
        )
        rendered = render_home_page(
            result,
            first_name="Alex",
            today_label="Friday, July 3",
            escape=_escape,
        )
        assert "Capability debug" in rendered
        assert result.capability is not None
        assert result.capability.state == CapabilityState.EXTRACTION_SUCCESS
