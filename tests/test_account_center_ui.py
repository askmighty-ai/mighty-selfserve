"""Unit tests for Account Connection Center presentation layer."""

from datetime import datetime, timedelta, timezone

from mighty.account_center_ui import (
    ACCOUNT_CENTER_CSS,
    PRIMARY_FIX,
    PRIMARY_LOGIN,
    PRIMARY_VIEW,
    TONE_ATTENTION,
    TONE_CONNECTED,
    TONE_LOGIN,
    build_card_view,
    build_summary,
    data_freshness_label,
    primary_action,
    render_card,
    resolve_primary_action_href,
    sort_cards,
    status_label,
    status_tone,
    summary_headline,
)
from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    ACCESS_MIGHTY_LOGIN,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    CONN_NOT_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
    SESSION_EXPIRED,
    SESSION_HEALTHY,
    SESSION_UNKNOWN,
    AccountState,
    Confidence,
    ConfidenceFactors,
)
from mighty.user_copy import (
    CTA_FIX,
    CTA_SIGN_IN,
    CTA_UPDATING,
    CTA_VIEW,
    DATA_REFRESHED_PREFIX,
    SESSION_VERIFIED_PREFIX,
)


def _state(**kwargs) -> AccountState:
    now = datetime.now(timezone.utc)
    defaults = dict(
        user_id="u1",
        provider="delta",
        display_name="Delta",
        category="travel_loyalty",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,
        session_health=SESSION_HEALTHY,
        last_verified_at=(now - timedelta(hours=1)).isoformat(),
        data_status=DATA_COMPLETE,
        last_data_refresh=(now - timedelta(minutes=30)).isoformat(),
        observations_available=["miles_balance", "tier_status"],
        field_count=2,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="Up to date · Updated today",
        is_actionable=False,
        updated_at=(now - timedelta(minutes=5)).isoformat(),
    )
    defaults.update(kwargs)
    return AccountState(**defaults)


def _login_state(**kwargs) -> AccountState:
    return _state(
        connection_state=CONN_NEEDS_LOGIN,
        session_health=SESSION_EXPIRED,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        sync_status="login_required",
        **kwargs,
    )


def _not_connected_state(**kwargs) -> AccountState:
    return _state(
        connection_state=CONN_NOT_CONNECTED,
        session_health=SESSION_UNKNOWN,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        **kwargs,
    )


def _connecting_state(**kwargs) -> AccountState:
    return _state(
        connection_state=CONN_CONNECTING,
        session_health=SESSION_UNKNOWN,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
        **kwargs,
    )


def _attention_state(**kwargs) -> AccountState:
    return _state(
        session_health=SESSION_EXPIRED,
        connection_state=CONN_CONNECTED,
        last_verified_at=None,
        last_data_refresh=None,
        observations_available=[],
        field_count=0,
        data_status=DATA_NONE,
        **kwargs,
    )


def _fmt(ts: str) -> str:
    return "3 days ago"


class TestStatusTone:
    def test_needs_sign_in_is_red_tone(self):
        s = _login_state()
        assert status_tone(s) == TONE_LOGIN
        assert status_label(s) == "Needs sign in"

    def test_not_connected_is_needs_sign_in_tone(self):
        s = _not_connected_state()
        assert status_tone(s) == TONE_LOGIN
        assert status_label(s) == "Needs sign in"

    def test_ready_is_green(self):
        s = _state()
        assert status_tone(s) == TONE_CONNECTED
        assert status_label(s) == "Ready"

    def test_connecting_is_updating(self):
        s = _connecting_state()
        assert status_tone(s) == TONE_ATTENTION
        assert status_label(s) == "Updating"

    def test_expired_session_on_connected_is_attention(self):
        s = _attention_state()
        assert status_tone(s) == TONE_ATTENTION
        assert status_label(s) == "Needs attention"


class TestPrimaryAction:
    def test_sign_in_action(self):
        s = _login_state()
        assert primary_action(s) == (CTA_SIGN_IN, PRIMARY_LOGIN, False)

    def test_sign_in_when_not_connected(self):
        s = _not_connected_state()
        assert primary_action(s) == (CTA_SIGN_IN, PRIMARY_LOGIN, False)

    def test_updating_is_disabled(self):
        s = _connecting_state()
        assert primary_action(s) == (CTA_UPDATING, "checking", True)

    def test_view_when_ready(self):
        s = _state(data_status=DATA_COMPLETE)
        assert primary_action(s) == (CTA_VIEW, PRIMARY_VIEW, False)

    def test_fix_when_needs_attention(self):
        s = _attention_state()
        assert primary_action(s) == (CTA_FIX, PRIMARY_FIX, False)


class TestCardView:
    def test_data_freshness_with_refresh(self):
        s = _state()
        assert data_freshness_label(s, _fmt) == "Fresh (3 days ago)"

    def test_access_method_cloud(self):
        s = _state(access_method=ACCESS_MIGHTY_LOGIN)
        card = build_card_view(s, icon="✈️", color="#eee", fmt_relative=_fmt)
        assert card.access_label == "Cloud"
        assert card.observation_count == 2

    def test_timestamps_are_distinct(self):
        card = build_card_view(_state(), fmt_relative=_fmt)
        assert card.session_verified_label.startswith(SESSION_VERIFIED_PREFIX)
        assert card.data_refreshed_label.startswith(DATA_REFRESHED_PREFIX)

    def test_sort_prioritizes_sign_in(self):
        login = build_card_view(
            _state(provider="a", display_name="Amex", connection_state=CONN_NEEDS_LOGIN, last_verified_at=None),
            fmt_relative=_fmt,
        )
        ok = build_card_view(
            _state(provider="b", display_name="Delta"),
            fmt_relative=_fmt,
        )
        ordered = sort_cards([ok, login])
        assert ordered[0].provider == "a"


class TestSummary:
    def test_summary_headline(self):
        cards = [
            build_card_view(_state(provider="a", display_name="A"), fmt_relative=_fmt),
            build_card_view(_login_state(provider="b", display_name="B"), fmt_relative=_fmt),
        ]
        summary = build_summary(cards)
        assert summary.total == 2
        assert summary.needs_sign_in == 1
        assert "needs sign in" in summary_headline(summary)


class TestActionLinks:
    def test_login_uses_provider_url_in_new_tab(self):
        href, external = resolve_primary_action_href(
            PRIMARY_LOGIN,
            "delta",
            provider_login_url="https://www.delta.com/login",
        )
        assert href == "https://www.delta.com/login"
        assert external is True

    def test_render_sign_in_as_anchor(self):
        card = build_card_view(
            _state(
                provider="amex",
                connection_state=CONN_NEEDS_LOGIN,
                data_status=DATA_NONE,
                last_data_refresh=None,
                last_verified_at=None,
            ),
            fmt_relative=_fmt,
            provider_login_url="https://www.americanexpress.com/login",
        )
        html = render_card(card, lambda x: str(x))
        assert 'href="https://www.americanexpress.com/login"' in html
        assert 'target="_blank"' in html
        assert ">Sign in</a>" in html
        assert "<button" not in html

    def test_render_updating_as_disabled_button(self):
        card = build_card_view(_connecting_state(), fmt_relative=_fmt)
        html = render_card(card, lambda x: str(x))
        assert ">Updating…</button>" in html
        assert "disabled" in html

    def test_render_fix_as_button(self):
        card = build_card_view(_attention_state(), fmt_relative=_fmt)
        html = render_card(card, lambda x: str(x))
        assert ">Fix</button>" in html


class TestScrollContainer:
    def test_main_content_scroll_css_present(self):
        assert ".main-content{height:100vh;overflow-y:auto" in ACCOUNT_CENTER_CSS
