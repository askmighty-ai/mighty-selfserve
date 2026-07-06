"""Unit tests for Account Connection Center presentation layer."""

from mighty.account_center_ui import (
    PRIMARY_CONNECT,
    PRIMARY_LOGIN,
    PRIMARY_REFRESH,
    PRIMARY_VIEW_BENEFITS,
    TONE_ATTENTION,
    TONE_CONNECTED,
    TONE_LOGIN,
    TONE_NEVER,
    build_card_view,
    build_summary,
    data_freshness_label,
    primary_action,
    sort_cards,
    status_label,
    status_tone,
    summary_headline,
)
from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    ACCESS_MIGHTY_LOGIN,
    ACTION_LOGIN,
    ACTION_NONE,
    ACTION_REVIEW,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    CONN_NOT_CONNECTED,
    DATA_COMPLETE,
    DATA_NONE,
    DATA_PARTIAL,
    SESSION_EXPIRED,
    SESSION_HEALTHY,
    AccountState,
    Confidence,
    ConfidenceFactors,
    RecommendedAction,
)


def _state(**kwargs) -> AccountState:
    defaults = dict(
        user_id="u1",
        provider="delta",
        display_name="Delta",
        category="travel_loyalty",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,
        session_health=SESSION_HEALTHY,
        last_verified_at="2026-07-01T00:00:00+00:00",
        data_status=DATA_COMPLETE,
        last_data_refresh="2026-07-05T00:00:00+00:00",
        observations_available=["miles_balance", "tier_status"],
        field_count=2,
        next_recommended_action=None,
        confidence=Confidence(level=CONFIDENCE_HIGH, score=90, factors=ConfidenceFactors()),
        status_line="Up to date · Updated today",
        is_actionable=False,
        updated_at="2026-07-06T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return AccountState(**defaults)


def _fmt(ts: str) -> str:
    return "3 days ago"


class TestStatusTone:
    def test_needs_login_is_red_tone(self):
        s = _state(connection_state=CONN_NEEDS_LOGIN, session_health=SESSION_EXPIRED)
        assert status_tone(s) == TONE_LOGIN
        assert status_label(s) == "Needs login"

    def test_not_connected_is_gray_tone(self):
        s = _state(connection_state=CONN_NOT_CONNECTED, data_status=DATA_NONE)
        assert status_tone(s) == TONE_NEVER
        assert status_label(s) == "Not connected"

    def test_connected_healthy_is_green(self):
        s = _state()
        assert status_tone(s) == TONE_CONNECTED
        assert status_label(s) == "Connected"

    def test_connecting_is_attention(self):
        s = _state(connection_state=CONN_CONNECTING, data_status=DATA_NONE)
        assert status_tone(s) == TONE_ATTENTION
        assert status_label(s) == "Connecting"

    def test_expired_session_on_connected_is_attention(self):
        s = _state(session_health=SESSION_EXPIRED, connection_state=CONN_CONNECTED)
        assert status_tone(s) == TONE_ATTENTION


class TestPrimaryAction:
    def test_login_action(self):
        s = _state(
            connection_state=CONN_NEEDS_LOGIN,
            next_recommended_action=RecommendedAction(
                kind=ACTION_LOGIN, label="Log in", url="/credentials?connect=delta",
            ),
        )
        assert primary_action(s) == ("Login", PRIMARY_LOGIN)

    def test_connect_when_not_connected(self):
        s = _state(connection_state=CONN_NOT_CONNECTED, data_status=DATA_NONE)
        assert primary_action(s) == ("Connect", PRIMARY_CONNECT)

    def test_refresh_when_healthy(self):
        s = _state(data_status=DATA_PARTIAL)
        assert primary_action(s) == ("Refresh", PRIMARY_REFRESH)

    def test_view_benefits_on_review(self):
        s = _state(
            data_status=DATA_PARTIAL,
            confidence=Confidence(level=CONFIDENCE_LOW, score=40, factors=ConfidenceFactors()),
            next_recommended_action=RecommendedAction(
                kind=ACTION_REVIEW, label="Review", url="/account/delta",
            ),
        )
        assert primary_action(s) == ("View Benefits", PRIMARY_VIEW_BENEFITS)

    def test_view_benefits_when_complete(self):
        s = _state(
            data_status=DATA_COMPLETE,
            next_recommended_action=RecommendedAction(kind=ACTION_NONE, label=""),
        )
        assert primary_action(s) == ("View Benefits", PRIMARY_VIEW_BENEFITS)


class TestCardView:
    def test_data_freshness_with_refresh(self):
        s = _state()
        assert data_freshness_label(s, _fmt) == "Fresh (3 days ago)"

    def test_access_method_cloud(self):
        s = _state(access_method=ACCESS_MIGHTY_LOGIN)
        card = build_card_view(s, icon="✈️", color="#eee", fmt_relative=_fmt)
        assert card.access_label == "Cloud"
        assert card.observation_count == 2

    def test_sort_prioritizes_login(self):
        login = build_card_view(
            _state(provider="a", display_name="Amex", connection_state=CONN_NEEDS_LOGIN),
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
            build_card_view(
                _state(provider="b", display_name="B", connection_state=CONN_NEEDS_LOGIN),
                fmt_relative=_fmt,
            ),
        ]
        summary = build_summary(cards)
        assert summary.total == 2
        assert summary.needs_login == 1
        assert "2 accounts" in summary_headline(summary)
