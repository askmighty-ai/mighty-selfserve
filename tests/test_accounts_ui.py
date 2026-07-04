"""Tests for Accounts maintenance page presentation layer."""

from mighty.account_lifecycle import (
    CONNECTED,
    NEEDS_LOGIN as LC_NEEDS_LOGIN,
    SYNCED as LC_SYNCED,
    WAITING_FOR_EXTENSION as LC_WAITING,
    AccountLifecycle,
    resolve_account_lifecycle,
)
from mighty.accounts_ui import (
    AccountsRow,
    SECTION_NEEDS_ATTENTION,
    SECTION_NEEDS_LOGIN,
    SECTION_ORDER,
    SECTION_UP_TO_DATE,
    SECTION_WAITING,
    AccountsPortfolio,
    build_portfolio,
    group_rows_by_section,
    matches_filter,
    normalize_filter,
    portfolio_last_checked,
    portfolio_summary_line,
    render_portfolio_summary,
    resolve_accounts_section,
    sort_rows,
)


def _lifecycle(state: str, **kwargs) -> AccountLifecycle:
    base = resolve_account_lifecycle("amex", in_credentials=True, **kwargs)
    return AccountLifecycle(
        state=state,
        label=base.label,
        description=base.description,
        color=base.color,
        cta_label=base.cta_label,
        secondary_cta_label=base.secondary_cta_label,
        source_label=base.source_label,
        show_last_sync=state == LC_SYNCED,
        last_sync_at=kwargs.get("synced_at"),
        extracted_field_count=1 if state == LC_SYNCED else 0,
    )


class TestAccountsSectionResolution:
    def test_synced_maps_to_up_to_date(self):
        lc = _lifecycle(LC_SYNCED)
        assert resolve_accounts_section(lc, "ok", source="amex") == SECTION_UP_TO_DATE

    def test_needs_login_section(self):
        lc = _lifecycle(LC_NEEDS_LOGIN)
        assert resolve_accounts_section(lc, "login_required", source="amex") == SECTION_NEEDS_LOGIN

    def test_error_maps_to_needs_attention(self):
        lc = _lifecycle(LC_WAITING)
        assert resolve_accounts_section(lc, "no_data", source="amex") == SECTION_NEEDS_ATTENTION

    def test_waiting_collapses_internal_states(self):
        for state in (LC_WAITING, CONNECTED, "added"):
            lc = _lifecycle(state)
            assert resolve_accounts_section(lc, "needs_first_visit", source="amex") == SECTION_WAITING


class TestAccountsOrderingAndFilters:
    def _row(self, name: str, section: str) -> AccountsRow:
        lc = _lifecycle(LC_SYNCED if section == SECTION_UP_TO_DATE else LC_WAITING)
        return AccountsRow(
            source=name,
            display_name=name.title(),
            icon="✈️",
            color="#eee",
            section=section,
            status_label=section,
            subline="",
            source_label="Manual",
            lifecycle=lc,
            synced_fmt="",
        )

    def test_sort_order(self):
        rows = [
            self._row("zebra", SECTION_UP_TO_DATE),
            self._row("alpha", SECTION_NEEDS_LOGIN),
            self._row("beta", SECTION_WAITING),
            self._row("gamma", SECTION_NEEDS_ATTENTION),
        ]
        ordered = [r.source for r in sort_rows(rows)]
        assert ordered == ["alpha", "gamma", "beta", "zebra"]

    def test_needs_attention_filter_includes_login_and_errors(self):
        assert matches_filter(SECTION_NEEDS_LOGIN, "needs_attention")
        assert matches_filter(SECTION_NEEDS_ATTENTION, "needs_attention")
        assert not matches_filter(SECTION_WAITING, "needs_attention")

    def test_grouped_sections_preserve_order(self):
        rows = sort_rows([
            self._row("a", SECTION_WAITING),
            self._row("b", SECTION_NEEDS_LOGIN),
            self._row("c", SECTION_UP_TO_DATE),
        ])
        grouped = group_rows_by_section(rows, "all")
        assert [s for s, _ in grouped] == [
            SECTION_NEEDS_LOGIN,
            SECTION_WAITING,
            SECTION_UP_TO_DATE,
        ]


class TestAccountsPortfolio:
    def test_last_checked_precise_relative(self):
        label = portfolio_last_checked(
            ["2026-07-04T15:00:00"],
            lambda _ts: "4 minutes ago",
        )
        assert label == "Last checked 4 minutes ago"

    def test_last_checked_empty(self):
        assert portfolio_last_checked([], lambda _ts: "now") == "Not checked yet"

    def test_portfolio_summary_hides_zero_buckets(self):
        rows = [
            AccountsRow(
                source="amex",
                display_name="Amex",
                icon="💳",
                color="#eee",
                section=SECTION_UP_TO_DATE,
                status_label="Up to date",
                subline="",
                source_label="Manual",
                lifecycle=_lifecycle(LC_SYNCED),
                synced_fmt="1 hour ago",
            )
        ]
        portfolio = build_portfolio(rows, "Last checked 1 hour ago")
        summary = portfolio_summary_line(portfolio)
        assert "1 account" in summary
        assert "1 up to date" in summary
        assert "waiting" not in summary
        assert "login" not in summary

    def test_portfolio_summary_login_grammar_singular(self):
        portfolio = AccountsPortfolio(
            total=2, needs_login=1, needs_attention=0, waiting=0,
            up_to_date=1, last_checked_label="",
        )
        assert "1 needs login" in portfolio_summary_line(portfolio)

    def test_portfolio_summary_login_grammar_plural(self):
        portfolio = AccountsPortfolio(
            total=2, needs_login=2, needs_attention=0, waiting=0,
            up_to_date=0, last_checked_label="",
        )
        assert "2 need login" in portfolio_summary_line(portfolio)

    def test_portfolio_summary_attention_grammar_singular(self):
        portfolio = AccountsPortfolio(
            total=1, needs_login=0, needs_attention=1, waiting=0,
            up_to_date=0, last_checked_label="",
        )
        assert "1 needs attention" in portfolio_summary_line(portfolio)

    def test_portfolio_summary_attention_grammar_plural(self):
        portfolio = AccountsPortfolio(
            total=2, needs_login=0, needs_attention=2, waiting=0,
            up_to_date=0, last_checked_label="",
        )
        assert "2 need attention" in portfolio_summary_line(portfolio)

    def test_active_filter_chip_visible_when_count_zero(self):
        portfolio = AccountsPortfolio(
            total=1, needs_login=0, needs_attention=0, waiting=0,
            up_to_date=1, last_checked_label="Last checked 1 hour ago",
        )
        html = render_portfolio_summary(portfolio, "waiting", lambda s: s)
        assert (
            'href="/credentials?filter=waiting" '
            'class="acct-portfolio-chip acct-portfolio-chip--active"'
        ) in html


class TestAccountsFilterNormalization:
    def test_unknown_filter_defaults_to_all(self):
        assert normalize_filter("bogus") == "all"
        assert normalize_filter(None) == "all"
