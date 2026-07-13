"""
mighty.accounts_ui
──────────────────
Presentation layer for the Accounts maintenance page (/credentials).

Maps backend lifecycle/status signals to user-facing sections without
changing lifecycle resolution logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mighty.account_lifecycle import (
    ADDED,
    CONNECTED,
    AccountLifecycle,
    NEEDS_LOGIN as LC_NEEDS_LOGIN,
    SYNCED as LC_SYNCED,
    WAITING_FOR_EXTENSION as LC_WAITING,
)
from mighty.account_status import (
    CHECKING,
    ERROR,
    NEEDS_LOGIN,
    UPDATING,
    UP_TO_DATE,
    UNVERIFIED,
    WAITING_FOR_EXTENSION,
    resolve_canonical_status,
)
from mighty.customer_account_access import (
    CustomerAccountAccessView,
    section_for_view,
)
from mighty import user_copy

# ── User-facing list sections (display order) ────────────────────────────────
SECTION_NEEDS_LOGIN = "needs_login"
SECTION_NEEDS_ATTENTION = "needs_attention"
SECTION_WAITING = "waiting"
SECTION_UP_TO_DATE = "up_to_date"

SECTION_ORDER = (
    SECTION_NEEDS_LOGIN,
    SECTION_NEEDS_ATTENTION,
    SECTION_WAITING,
    SECTION_UP_TO_DATE,
)

SECTION_HEADERS: dict[str, str] = {
    SECTION_NEEDS_LOGIN: "Sign in required",
    SECTION_NEEDS_ATTENTION: "Needs attention",
    SECTION_WAITING: "Still setting up",
    SECTION_UP_TO_DATE: "Connected",
}

STATUS_LABELS: dict[str, str] = {
    SECTION_NEEDS_LOGIN: "Sign in required",
    SECTION_NEEDS_ATTENTION: "Needs attention",
    SECTION_WAITING: user_copy.ACCOUNTS_STATUS_SETTING_UP,
    SECTION_UP_TO_DATE: "Connected",
}

VALID_FILTERS = frozenset({"all", "needs_attention", "waiting", "up_to_date", "needs_login"})


@dataclass
class AccountsRow:
    source: str
    display_name: str
    icon: str
    color: str
    section: str
    status_label: str
    subline: str
    source_label: str
    lifecycle: AccountLifecycle
    synced_fmt: str
    is_pending: bool = False
    access_view: CustomerAccountAccessView | None = None
    private_data_label: str | None = None
    live_access: str | None = None
    background_work: str | None = None
    meaning: str | None = None


@dataclass
class AccountsPortfolio:
    total: int
    needs_login: int
    needs_attention: int
    waiting: int
    up_to_date: int
    last_checked_label: str


def normalize_filter(filter_key: str | None) -> str:
    key = (filter_key or "all").strip().lower()
    return key if key in VALID_FILTERS else "all"


def resolve_accounts_section(
    lifecycle: AccountLifecycle,
    sync_status: str = "ok",
    *,
    updating_source: str | None = None,
    source: str = "",
    session_state: str | None = None,
    readiness: str | None = None,
) -> str:
    """Map lifecycle + session access to a maintenance list section.

    Login section comes only from session_state / readiness signed_out.
    Connected section requires readiness ready (access + correlated extraction).

    Bucket alignment with Dashboard `_health_counts`:
    - NEEDS_LOGIN → needs_login
    - ERROR → needs_attention
    - UP_TO_DATE → up_to_date (Connected)
    - UPDATING / CHECKING / UNVERIFIED / WAITING_FOR_EXTENSION / other → waiting
    """
    canonical = resolve_canonical_status(
        lifecycle,
        sync_status or "ok",
        source=source,
        updating_source=updating_source,
        session_state=session_state,
        readiness=readiness,
    )
    if canonical == NEEDS_LOGIN:
        return SECTION_NEEDS_LOGIN
    if canonical == ERROR:
        return SECTION_NEEDS_ATTENTION
    if canonical == UP_TO_DATE:
        return SECTION_UP_TO_DATE
    return SECTION_WAITING


def waiting_subline(
    lifecycle: AccountLifecycle,
    sync_status: str = "ok",
    *,
    updating_source: str | None = None,
    source: str = "",
    session_state: str | None = None,
    readiness: str | None = None,
    cached_data_label: str | None = None,
) -> str:
    """Secondary line for Still setting up rows; internal cases only."""
    if readiness == "signed_out" or session_state == "signed_out":
        return cached_data_label or user_copy.READINESS_COPY_SIGNED_OUT
    if readiness == "checking" or session_state == "checking":
        return user_copy.READINESS_COPY_CHECKING
    if readiness == "unverified" or session_state == "unknown":
        return cached_data_label or user_copy.READINESS_COPY_UNVERIFIED
    canonical = resolve_canonical_status(
        lifecycle,
        sync_status or "ok",
        source=source,
        updating_source=updating_source,
        session_state=session_state,
        readiness=readiness,
    )
    if canonical == UPDATING:
        return user_copy.ACCOUNTS_SUBLINE_UPDATING
    if canonical == CHECKING:
        return user_copy.READINESS_COPY_CHECKING
    if canonical == UNVERIFIED:
        return cached_data_label or user_copy.READINESS_COPY_UNVERIFIED
    # Lifecycle “connected” without readiness ready means session verified but
    # no successful correlated extraction yet — the only valid “awaiting data”.
    if lifecycle.state == CONNECTED and readiness not in ("ready",):
        return user_copy.ACCOUNTS_SUBLINE_CONNECTED
    return user_copy.ACCOUNTS_SUBLINE_FIRST_VISIT


def row_status_label(
    section: str,
    lifecycle: AccountLifecycle,
    *,
    session_state: str | None = None,
    sync_status: str = "ok",
    updating_source: str | None = None,
    source: str = "",
    readiness: str | None = None,
) -> str:
    if readiness == "ready" or section == SECTION_UP_TO_DATE:
        return STATUS_LABELS[SECTION_UP_TO_DATE]
    if readiness == "signed_out" or section == SECTION_NEEDS_LOGIN:
        return STATUS_LABELS[SECTION_NEEDS_LOGIN]
    if section == SECTION_NEEDS_ATTENTION:
        return STATUS_LABELS[SECTION_NEEDS_ATTENTION]
    if readiness == "unverified" or session_state == "unknown":
        return user_copy.ACCOUNTS_STATUS_NOT_VERIFIED
    if readiness == "checking" or session_state == "checking":
        return user_copy.ACCOUNTS_STATUS_CHECKING
    if session_state == "signed_out":
        return STATUS_LABELS[SECTION_NEEDS_LOGIN]
    canonical = resolve_canonical_status(
        lifecycle,
        sync_status or "ok",
        source=source,
        updating_source=updating_source,
        session_state=session_state,
        readiness=readiness,
    )
    if canonical == UNVERIFIED:
        return user_copy.ACCOUNTS_STATUS_NOT_VERIFIED
    if canonical == CHECKING:
        return user_copy.ACCOUNTS_STATUS_CHECKING
    if canonical == UPDATING:
        return user_copy.ACCOUNTS_STATUS_SETTING_UP
    if canonical == WAITING_FOR_EXTENSION:
        return user_copy.ACCOUNTS_STATUS_AWAITING_FIRST
    return STATUS_LABELS.get(section, user_copy.ACCOUNTS_STATUS_SETTING_UP)


def row_subline(
    section: str,
    lifecycle: AccountLifecycle,
    sync_status: str = "ok",
    *,
    updating_source: str | None = None,
    source: str = "",
    synced_fmt: str = "",
    failure_hint: str = "",
    session_state: str | None = None,
    readiness: str | None = None,
    cached_data_label: str | None = None,
) -> str:
    if section == SECTION_UP_TO_DATE and synced_fmt:
        return f"Updated {synced_fmt}"
    if section == SECTION_NEEDS_LOGIN and cached_data_label:
        return cached_data_label
    if section == SECTION_NEEDS_ATTENTION and failure_hint:
        return failure_hint
    if section == SECTION_WAITING:
        return waiting_subline(
            lifecycle,
            sync_status,
            updating_source=updating_source,
            source=source,
            session_state=session_state,
            readiness=readiness,
            cached_data_label=cached_data_label,
        )
    if section == SECTION_NEEDS_LOGIN:
        return cached_data_label or user_copy.NEEDS_LOGIN_EXPLAINER
    return ""


def matches_filter(section: str, filter_key: str) -> bool:
    if filter_key in ("", "all"):
        return True
    if filter_key == "needs_attention":
        return section in (SECTION_NEEDS_LOGIN, SECTION_NEEDS_ATTENTION)
    if filter_key == "needs_login":
        return section == SECTION_NEEDS_LOGIN
    return section == filter_key


def portfolio_last_checked(
    synced_at_values: list[str],
    fmt_relative: Callable[[str], str],
) -> str:
    valid = [t for t in synced_at_values if t]
    if not valid:
        return user_copy.ACCOUNTS_NOT_CHECKED_YET
    latest = max(valid)
    rel = fmt_relative(latest)
    return user_copy.accounts_last_checked(rel)


def build_portfolio(rows: list[AccountsRow], last_checked_label: str) -> AccountsPortfolio:
    counts = {s: 0 for s in SECTION_ORDER}
    for row in rows:
        counts[row.section] = counts.get(row.section, 0) + 1
    return AccountsPortfolio(
        total=len(rows),
        needs_login=counts[SECTION_NEEDS_LOGIN],
        needs_attention=counts[SECTION_NEEDS_ATTENTION],
        waiting=counts[SECTION_WAITING],
        up_to_date=counts[SECTION_UP_TO_DATE],
        last_checked_label=last_checked_label,
    )


def sort_rows(rows: list[AccountsRow]) -> list[AccountsRow]:
    order_index = {s: i for i, s in enumerate(SECTION_ORDER)}

    def _key(row: AccountsRow) -> tuple[int, str]:
        return (order_index.get(row.section, 99), row.display_name.lower())

    return sorted(rows, key=_key)


def group_rows_by_section(
    rows: list[AccountsRow],
    filter_key: str,
) -> list[tuple[str, list[AccountsRow]]]:
    filtered = [r for r in rows if matches_filter(r.section, filter_key)]
    grouped: list[tuple[str, list[AccountsRow]]] = []
    for section in SECTION_ORDER:
        section_rows = [r for r in filtered if r.section == section]
        if section_rows:
            grouped.append((section, section_rows))
    return grouped


def filter_chip_url(filter_key: str) -> str:
    if filter_key in ("", "all"):
        return "/credentials"
    return f"/credentials?filter={filter_key}"


def _count_phrase(count: int, verb: str) -> str:
    """Singular count uses third-person verb: 1 needs login, 2 need login."""
    return f"{count} {verb}{'s' if count == 1 else ''}"


def portfolio_summary_line(
    portfolio: AccountsPortfolio,
    *,
    connected_label: str | None = None,
) -> str:
    parts: list[str] = [f"{portfolio.total} account{'s' if portfolio.total != 1 else ''}"]
    if portfolio.up_to_date:
        parts.append(connected_label or f"{portfolio.up_to_date} connected")
    if portfolio.waiting:
        parts.append(f"{portfolio.waiting} still setting up")
    if portfolio.needs_login:
        parts.append(f"{_count_phrase(portfolio.needs_login, 'need')} login")
    if portfolio.needs_attention:
        parts.append(f"{_count_phrase(portfolio.needs_attention, 'need')} attention")
    return " · ".join(parts)


def apply_access_view_to_row(row: AccountsRow, view: CustomerAccountAccessView) -> AccountsRow:
    """Overwrite customer-facing labels from the shared view model."""
    row.section = section_for_view(view)
    row.status_label = view.status_label
    row.source_label = user_copy.access_discovered_from(view.discovered_from)
    row.access_view = view
    row.private_data_label = view.private_data_label
    row.live_access = view.live_access
    row.background_work = view.background_work
    row.meaning = view.meaning
    if view.readiness == "ready" and row.synced_fmt:
        row.subline = f"Private data {view.private_data_label.lower()} · Updated {row.synced_fmt}"
    elif view.user_action_required and view.cached_data_label:
        row.subline = view.cached_data_label
    else:
        row.subline = view.meaning
    return row


def render_portfolio_summary(
    portfolio: AccountsPortfolio,
    active_filter: str,
    escape: Callable[[Any], str],
    *,
    connected_label: str | None = None,
) -> str:
    chips = [
        (user_copy.ACCOUNTS_FILTER_ALL, "all"),
        (user_copy.ACCOUNTS_FILTER_NEEDS_ATTENTION, "needs_attention"),
        (user_copy.ACCOUNTS_FILTER_WAITING, "waiting"),
        (user_copy.ACCOUNTS_FILTER_UP_TO_DATE, "up_to_date"),
    ]
    chip_html = ""
    for label, key in chips:
        active = " acct-portfolio-chip--active" if active_filter == key else ""
        hide = ""
        if active_filter != key:
            if key == "needs_attention" and not (
                portfolio.needs_login or portfolio.needs_attention
            ):
                hide = ' style="display:none"'
            elif key == "waiting" and not portfolio.waiting:
                hide = ' style="display:none"'
            elif key == "up_to_date" and not portfolio.up_to_date:
                hide = ' style="display:none"'
        chip_html += (
            f'<a href="{escape(filter_chip_url(key))}" '
            f'class="acct-portfolio-chip{active}"{hide}>{escape(label)}</a>'
        )
    summary = escape(portfolio_summary_line(portfolio, connected_label=connected_label))
    freshness = escape(portfolio.last_checked_label)
    return (
        f'<section class="acct-portfolio" aria-label="Account portfolio">'
        f'<p class="acct-portfolio-counts">{summary}</p>'
        f'<div class="acct-portfolio-meta">'
        f'<span class="acct-portfolio-freshness">{freshness}</span>'
        f'<div class="acct-portfolio-filters">{chip_html}</div>'
        f"</div></section>"
    )


def render_section_header(section: str, escape: Callable[[Any], str]) -> str:
    return (
        f'<h2 class="acct-section-header">{escape(SECTION_HEADERS[section])}</h2>'
    )


def render_empty_state(escape: Callable[[Any], str]) -> str:
    return (
        f'<div class="acct-empty">'
        f'<p class="acct-empty-headline">{escape(user_copy.ACCOUNTS_EMPTY_HEADLINE)}</p>'
        f'<p class="acct-empty-body">{escape(user_copy.ACCOUNTS_EMPTY_BODY)}</p>'
        f'<div class="acct-empty-actions">'
        f'<a href="/email-scan" class="acct-maint-cta acct-maint-cta--primary">'
        f'{escape(user_copy.ACCOUNTS_EMPTY_CTA_EMAIL)}</a>'
        f'<button type="button" class="acct-maint-cta acct-maint-cta--secondary" '
        f'onclick="openModal()">{escape(user_copy.ACCOUNTS_EMPTY_CTA_MANUAL)}</button>'
        f"</div></div>"
    )


def render_filter_empty(escape: Callable[[Any], str]) -> str:
    return (
        f'<div class="acct-filter-empty">'
        f'<p>{escape(user_copy.ACCOUNTS_FILTER_EMPTY)}</p>'
        f'<a href="/credentials">{escape(user_copy.ACCOUNTS_FILTER_CLEAR)}</a>'
        f"</div>"
    )


def render_add_coverage_footer(escape: Callable[[Any], str]) -> str:
    return (
        f'<footer class="acct-add-coverage">'
        f'<p>{escape(user_copy.ACCOUNTS_ADD_COVERAGE_NOTE)}</p>'
        f'<div class="acct-add-coverage-actions">'
        f'<a href="/email-scan">{escape(user_copy.ACCOUNTS_EMPTY_CTA_EMAIL)}</a>'
        f'<button type="button" onclick="openModal()">'
        f'{escape(user_copy.ACCOUNTS_EMPTY_CTA_MANUAL)}</button>'
        f"</div></footer>"
    )

