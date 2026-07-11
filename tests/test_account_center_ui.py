"""Unit tests for Account Connection Center presentation layer.

Login/session presentation comes from session_access (provider_session_state).
Legacy AccountState connection_state must not independently invent Needs sign in.
"""

from datetime import datetime, timedelta, timezone

from mighty.account_center_ui import (
    ACCOUNT_CENTER_CSS,
    PRIMARY_FIX,
    PRIMARY_LOGIN,
    PRIMARY_VIEW,
    TONE_ATTENTION,
    TONE_CONNECTED,
    TONE_LOGIN,
    TONE_NEVER,
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
from mighty.account_presentation import AccountPresentation
from mighty.account_state import (
    ACCESS_BROWSER_SESSION,
    ACCESS_MIGHTY_LOGIN,
    CONN_CONNECTED,
    CONN_CONNECTING,
    CONN_NEEDS_LOGIN,
    DATA_COMPLETE,
    DATA_NONE,
    SESSION_EXPIRED,
    SESSION_HEALTHY,
    AccountState,
    Confidence,
    ConfidenceFactors,
)
from mighty.login_truth import CurrentAccountAccess
from mighty.user_copy import (
    ACCOUNT_STATE_CHECKING,
    ACCOUNT_STATE_NEEDS_ATTENTION,
    ACCOUNT_STATE_NEEDS_SIGN_IN,
    ACCOUNT_STATE_READY,
    ACCOUNT_STATE_UNKNOWN,
    ACCOUNT_STATE_UPDATING,
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


def _access(current_access: str, *, provider: str = "delta") -> CurrentAccountAccess:
    # Tests may pass product session shorthand; map to Current Access vocabulary.
    mapped = {
        "connected": "connected_now",
        "signed_out": "signed_out",
        "checking": "checking",
        "unknown": "unknown",
        "error": "error",
        "connected_now": "connected_now",
    }.get(current_access, current_access)
    return CurrentAccountAccess(
        provider=provider,
        current_access=mapped,  # type: ignore[arg-type]
        cached_data_state="none",
        last_verified=None,
        last_private_data=None,
        evidence="test",
        source="test",
        next_action_type="none",
        next_action_text="",
    )


def _pres(*, key: str, label: str, cta: str = "", disabled: bool = False) -> AccountPresentation:
    return AccountPresentation(
        key=key,
        label=label,
        cta_label=cta,
        cta_disabled=disabled,
    )


def _fmt(ts: str) -> str:
    return "3 days ago"


class TestStatusTone:
    def test_needs_sign_in_is_red_tone(self):
        s = _login_state()
        assert status_tone(s, presentation_key=ACCOUNT_STATE_NEEDS_SIGN_IN) == TONE_LOGIN
        assert status_label(s, presentation_label="Needs sign in") == "Needs sign in"

    def test_unknown_session_is_attention_tone(self):
        s = _login_state()
        assert status_tone(s, presentation_key=ACCOUNT_STATE_UNKNOWN) == TONE_NEVER
        assert status_label(s, presentation_label="Unable to verify") == "Unable to verify"

    def test_ready_is_green(self):
        s = _state()
        assert status_tone(s, presentation_key=ACCOUNT_STATE_READY) == TONE_CONNECTED
        assert status_label(s, presentation_label="Ready") == "Ready"

    def test_checking_is_attention(self):
        s = _state()
        assert status_tone(s, presentation_key=ACCOUNT_STATE_CHECKING) == TONE_ATTENTION

    def test_needs_attention_tone(self):
        s = _state()
        assert status_tone(s, presentation_key=ACCOUNT_STATE_NEEDS_ATTENTION) == TONE_ATTENTION
        assert status_label(s, presentation_label="Needs attention") == "Needs attention"


class TestPrimaryAction:
    def test_sign_in_action(self):
        s = _login_state()
        pres = _pres(key=ACCOUNT_STATE_NEEDS_SIGN_IN, label="Needs sign in", cta=CTA_SIGN_IN)
        assert primary_action(s, presentation=pres) == (CTA_SIGN_IN, PRIMARY_LOGIN, False)

    def test_unknown_disables_login(self):
        s = _login_state()
        pres = _pres(key=ACCOUNT_STATE_UNKNOWN, label="Unable to verify", disabled=True)
        label, kind, disabled = primary_action(s, presentation=pres)
        assert kind != PRIMARY_LOGIN
        assert disabled is True

    def test_updating_is_disabled(self):
        s = _state()
        pres = _pres(key=ACCOUNT_STATE_UPDATING, label="Updating", cta=CTA_UPDATING, disabled=True)
        assert primary_action(s, presentation=pres) == (CTA_UPDATING, "checking", True)

    def test_view_when_ready(self):
        s = _state(data_status=DATA_COMPLETE)
        pres = _pres(key=ACCOUNT_STATE_READY, label="Ready", cta=CTA_VIEW)
        assert primary_action(s, presentation=pres) == (CTA_VIEW, PRIMARY_VIEW, False)

    def test_fix_when_needs_attention(self):
        s = _state()
        pres = _pres(key=ACCOUNT_STATE_NEEDS_ATTENTION, label="Needs attention", cta=CTA_FIX)
        assert primary_action(s, presentation=pres) == (CTA_FIX, PRIMARY_FIX, False)


class TestCardView:
    def test_data_freshness_with_refresh(self):
        s = _state()
        assert data_freshness_label(s, _fmt) == "Fresh (3 days ago)"

    def test_access_method_cloud(self):
        s = _state(access_method=ACCESS_MIGHTY_LOGIN)
        card = build_card_view(
            s, icon="✈️", color="#eee", fmt_relative=_fmt, session_access=_access("connected"),
        )
        assert card.access_label == "Cloud"
        assert card.observation_count == 2

    def test_timestamps_are_distinct(self):
        card = build_card_view(
            _state(), fmt_relative=_fmt, session_access=_access("connected"),
        )
        assert card.session_verified_label.startswith(SESSION_VERIFIED_PREFIX)
        assert card.data_refreshed_label.startswith(DATA_REFRESHED_PREFIX)

    def test_sort_prioritizes_sign_in(self):
        login = build_card_view(
            _state(provider="a", display_name="Amex", connection_state=CONN_NEEDS_LOGIN, last_verified_at=None),
            fmt_relative=_fmt,
            session_access=_access("signed_out", provider="a"),
        )
        ok = build_card_view(
            _state(provider="b", display_name="Delta"),
            fmt_relative=_fmt,
            session_access=_access("connected", provider="b"),
        )
        ordered = sort_cards([ok, login])
        assert ordered[0].provider == "a"


class TestSummary:
    def test_summary_headline(self):
        cards = [
            build_card_view(
                _state(provider="a", display_name="A"),
                fmt_relative=_fmt,
                session_access=_access("connected", provider="a"),
            ),
            build_card_view(
                _login_state(provider="b", display_name="B"),
                fmt_relative=_fmt,
                session_access=_access("signed_out", provider="b"),
            ),
        ]
        summary = build_summary(cards)
        assert summary.total == 2
        assert summary.needs_sign_in == 1
        assert summary.ready == 0  # connected session alone is not Connected without data
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
            session_access=_access("signed_out", provider="amex"),
        )
        html = render_card(card, lambda x: str(x))
        assert 'href="https://www.americanexpress.com/login"' in html
        assert 'target="_blank"' in html
        assert ">Sign in</a>" in html
        assert "<button" not in html

    def test_render_updating_as_disabled_button(self):
        card = build_card_view(
            _state(connection_state=CONN_CONNECTING, data_status=DATA_NONE, last_verified_at=None),
            fmt_relative=_fmt,
            session_access=_access("checking"),
        )
        html = render_card(card, lambda x: str(x))
        assert "Checking" in html or "Updating" in html
        assert "disabled" in html

    def test_render_unknown_without_login_cta(self):
        card = build_card_view(
            _login_state(provider="delta"),
            fmt_relative=_fmt,
            session_access=_access("unknown"),
            provider_login_url="https://example.com/login",
        )
        html = render_card(card, lambda x: str(x))
        assert "Unable to verify" in html
        assert 'href="https://example.com/login"' not in html


class TestScrollContainer:
    def test_main_content_scroll_css_present(self):
        assert ".main-content{height:100vh;overflow-y:auto" in ACCOUNT_CENTER_CSS
