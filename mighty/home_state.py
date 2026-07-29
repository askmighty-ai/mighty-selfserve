"""
mighty.home_state
─────────────────
Prepare Home enrollment / operational context for rendering.

Attention ranking and interrupt hero selection are owned by the Attention
Platform (AttentionState → AttentionView). This module must not re-rank
attention. Home V1 composes this result with AttentionView in
``home_projection``.

See docs/HOME_V1.md, docs/HOME_EXPERIENCE.md, and
docs/ATTENTION_PLATFORM_ADOPTION.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from mighty.account_status import (
    CHECKING,
    ERROR,
    NEEDS_LOGIN,
    UPDATING,
    UP_TO_DATE,
    WAITING_FOR_EXTENSION,
    AccountStatus,
)
from mighty.action import Action
from mighty.capability_state import (
    CapabilityView,
    TRUTH_PROVIDER,
    TRUTH_PROVIDER_DISPLAY,
    filter_customer_accounts,
)
from mighty.customer_capability_presentation import (
    build_presented_capability_view,
    load_valid_stable_capability,
    resolve_account_identity,
)
from mighty.customer_account_access import (
    DISCOVERED_MANUAL,
    CustomerAccountAccessView,
    connected_summary_label,
)
from mighty.control_tower import ControlTowerSummary, build_control_tower_from_statuses
from mighty import user_copy


class HomeState(str, Enum):
    """Enrollment / operational Home context — not attention ranking.

    ``LOGIN`` and ``RECOMMENDATION`` remain for deserialization compatibility
    but are no longer produced; attention interrupts come from AttentionView.
    """

    UPDATE = "update"
    LOGIN = "login"  # deprecated — not produced; use AttentionView
    EMPTY = "empty"
    WAITING = "waiting"
    RECOMMENDATION = "recommendation"  # deprecated — not produced
    ALL_CLEAR = "all_clear"


@dataclass
class HomeFeatured:
    headline: str
    body: str
    cta_label: str | None = None
    cta_url: str | None = None
    disabled_cta_label: str | None = None
    secondary_label: str | None = None
    secondary_url: str | None = None


@dataclass
class AccountHealthCounts:
    up_to_date: int = 0
    waiting: int = 0  # still setting up / verifying (not user-actionable)
    needs_login: int = 0
    needs_attention: int = 0  # ERROR and other non-login user-actionable issues
    connected_names: list[str] = field(default_factory=list)
    connected_label: str | None = None

    @property
    def attention_required(self) -> int:
        """Accounts that need customer action — same total as the health chip."""
        return self.needs_login + self.needs_attention


# Canonical statuses that mean Mighty is still setting up / verifying.
# Aligned with Accounts SECTION_WAITING (see resolve_accounts_section).
# Exception documented: ERROR maps to needs_attention on both surfaces
# (Accounts SECTION_NEEDS_ATTENTION), not still-setting-up.
_STILL_SETTING_UP = frozenset({
    WAITING_FOR_EXTENSION,
    UPDATING,
    CHECKING,
    "unverified",
})


@dataclass
class WaitingRow:
    display_name: str
    status_label: str


@dataclass
class HomeStateResult:
    state: HomeState
    priority_summary: str
    featured: HomeFeatured
    health: AccountHealthCounts
    waiting_rows: list[WaitingRow] = field(default_factory=list)
    secondary_recommendations: list[Action] = field(default_factory=list)
    show_health: bool = True
    show_metrics: bool = False
    metrics_accounts: int = 0
    metrics_benefits: int = 0
    metrics_value: str = ""
    activity_pending_count: int = 0
    freshness_label: str = ""
    updating_display_name: str | None = None
    access_views: list[CustomerAccountAccessView] = field(default_factory=list)
    show_access_debug: bool = False
    tower: ControlTowerSummary = field(default_factory=ControlTowerSummary)
    # Truth Dashboard (single-provider capability instrument)
    capability: CapabilityView | None = None
    extracted_items: list[dict] = field(default_factory=list)
    session_confidence: str | None = None
    provider_open_url: str | None = None


def _access_views_from_accounts(
    accounts: Sequence[AccountStatus],
) -> list[CustomerAccountAccessView]:
    return [a.customer_access for a in accounts if a.customer_access is not None]


def _health_counts(
    accounts: Sequence[AccountStatus],
    *,
    access_views: Sequence[CustomerAccountAccessView] | None = None,
) -> AccountHealthCounts:
    """Bucket accounts for Dashboard health chips.

    Same product buckets as Accounts portfolio:
    - UP_TO_DATE → Connected (ready)
    - NEEDS_LOGIN → Sign in required
    - ERROR → needs attention
    - UPDATING / CHECKING / UNVERIFIED / WAITING_FOR_EXTENSION → still setting up
    """
    from mighty.account_status import UNVERIFIED

    counts = AccountHealthCounts()
    for acct in accounts:
        if acct.status == UP_TO_DATE:
            counts.up_to_date += 1
            counts.connected_names.append(acct.display_name)
        elif acct.status == NEEDS_LOGIN:
            counts.needs_login += 1
        elif acct.status == ERROR:
            counts.needs_attention += 1
        elif acct.status in _STILL_SETTING_UP or acct.status == UNVERIFIED:
            counts.waiting += 1
        else:
            counts.waiting += 1
    views = list(access_views) if access_views is not None else _access_views_from_accounts(accounts)
    counts.connected_label = connected_summary_label(views)
    if not counts.connected_label and counts.connected_names:
        if len(counts.connected_names) == 1:
            counts.connected_label = user_copy.access_connected_named(
                counts.connected_names[0],
            )
        else:
            counts.connected_label = f"{counts.up_to_date} connected"
    return counts


def _pick_waiting_account(accounts: Sequence[AccountStatus]) -> AccountStatus | None:
    waiting = [a for a in accounts if a.status in (WAITING_FOR_EXTENSION, ERROR)]
    if waiting:
        return waiting[0]
    without_data = [a for a in accounts if a.status != UP_TO_DATE and a.status != NEEDS_LOGIN]
    return without_data[0] if without_data else None


def _waiting_row_label(acct: AccountStatus) -> str:
    # Prefer top-level presentation (canonical lifecycle) so Home chips
    # cannot contradict Accounts / product_lifecycle (Complete Amex AT-08).
    if acct.presentation_label:
        return acct.presentation_label
    if acct.customer_access is not None:
        return acct.customer_access.status_label
    if acct.status == NEEDS_LOGIN:
        return user_copy.STATUS_LABEL_NEEDS_LOGIN
    if acct.readiness == "ready" or acct.status == UP_TO_DATE:
        return user_copy.ACCOUNT_STATE_LABELS[user_copy.ACCOUNT_STATE_READY]
    if acct.status == CHECKING:
        return user_copy.ACCOUNTS_STATUS_CHECKING
    if acct.status == UPDATING:
        return user_copy.STATUS_LABEL_UPDATING
    if acct.last_successful_sync_at and acct.readiness not in (
        "ready", "checking", "signed_out", "unverified",
    ):
        return user_copy.CONNECTION_STATUS_LINES.get("connected", "Connected — awaiting data")
    return user_copy.ACCOUNTS_STATUS_AWAITING_FIRST


def _resolve_updating_name(
    accounts: Sequence[AccountStatus],
    *,
    sync_running: bool,
    updating_source: str | None,
    updating_display_name: str | None,
) -> str | None:
    if not sync_running and not any(a.status == UPDATING for a in accounts):
        return None
    name = updating_display_name
    if not name and updating_source:
        for acct in accounts:
            if acct.source == updating_source:
                name = acct.display_name
                break
    if not name:
        updating_accts = [a for a in accounts if a.status == UPDATING]
        name = updating_accts[0].display_name if updating_accts else "your account"
    return name


def _attach_update_context(result: HomeStateResult, updating_name: str | None) -> HomeStateResult:
    if updating_name:
        result.updating_display_name = updating_name
    return result


def resolve_home_state(
    *,
    accounts: Sequence[AccountStatus],
    actions: Sequence[Action] | None = None,
    sync_running: bool = False,
    updating_source: str | None = None,
    updating_display_name: str | None = None,
    pending_activity_count: int = 0,
    benefit_count: int = 0,
    tracked_value_label: str = "",
    freshness_label: str = "",
    worker_setup_needed: bool = False,
    provider_open_urls: dict[str, str] | None = None,
    show_access_debug: bool = False,
    extracted_items: list[dict] | None = None,
    session_confidence: str | None = None,
    extraction_status: str | None = None,
    previous_stable_capability: CapabilityView | None = None,
    persist_db: object | None = None,
    persist_user_id: str | None = None,
    force_unknown: bool = False,
    write_persist: bool = False,
) -> HomeStateResult:
    """Prepare capability + enrollment/operational context for Home.

    Does **not** rank attention or select a cross-account hero CTA — that is
    AttentionView's job. ``actions`` is accepted for API compatibility only.

    CapabilityView is always Amex-first (Truth Dashboard). Customer-facing GETs
    must keep ``write_persist=False`` so presentation rows are not written on
    read. Command paths may opt in.
    """
    _ = actions  # Accepted for compatibility; Attention Platform owns ranking.
    access_views = _access_views_from_accounts(accounts)
    health = _health_counts(accounts, access_views=access_views)
    enrolled = len(accounts)
    updating_name = _resolve_updating_name(
        accounts,
        sync_running=sync_running,
        updating_source=updating_source,
        updating_display_name=updating_display_name,
    )
    tower = build_control_tower_from_statuses(
        accounts,
        updating_display_name=updating_name,
    )
    open_urls = provider_open_urls or {}
    provider_open_url = open_urls.get(TRUTH_PROVIDER)
    truth_accounts = filter_customer_accounts(list(accounts))
    truth_views = _access_views_from_accounts(truth_accounts)
    truth_view = truth_views[0] if truth_views else None
    truth_acct = truth_accounts[0] if truth_accounts else None
    if truth_view is None and truth_acct is not None and (
        truth_acct.status == NEEDS_LOGIN
        or truth_acct.readiness == "signed_out"
        or truth_acct.login_required
    ):
        # Fixtures / edge rows without customer_access still need SIGNED_OUT.
        from mighty.customer_account_access import build_customer_account_access_view
        from mighty.account_readiness import AccountReadiness, SIGNED_OUT as R_SIGNED_OUT
        from mighty import user_copy as _uc

        synthetic = AccountReadiness(
            provider=truth_acct.source,
            state=R_SIGNED_OUT,
            status_label="Sign in required",
            status_copy=_uc.READINESS_COPY_SIGNED_OUT,
            presentation_key="needs_sign_in",
            canonical_status="needs_login",
            login_required=True,
            session_state="signed_out",
            access_cycle_id=None,
            session_evidence_at=None,
            extraction_at=None,
            extraction_ok=False,
            extraction_correlated=False,
            verification_id=None,
            cached_data_label=truth_acct.cached_data_label,
            last_confirmed_ready_at=None,
            last_confirmed_access_cycle_id=None,
            background_verification=False,
            secondary_label=None,
        )
        truth_view = build_customer_account_access_view(
            provider=truth_acct.source,
            display_name=truth_acct.display_name,
            readiness=synthetic,
            discovered_from=DISCOVERED_MANUAL,
            user_action_text=truth_acct.user_action_label,
            user_action_url=truth_acct.user_action_url or provider_open_url,
        )

    provider_key = (
        truth_view.provider if truth_view else (
            truth_acct.source if truth_acct else TRUTH_PROVIDER
        )
    )
    account_identity = None
    if persist_db is not None and persist_user_id:
        account_identity = resolve_account_identity(
            persist_db, persist_user_id, provider_key,
        )
    previous = previous_stable_capability
    if previous is None and persist_db is not None and persist_user_id:
        previous = load_valid_stable_capability(
            persist_db,
            persist_user_id,
            provider_key,
            account_identity=account_identity,
        )

    capability = build_presented_capability_view(
        truth_view,
        previous_stable=previous,
        force_unknown=force_unknown,
        persist_db=persist_db,
        persist_user_id=persist_user_id,
        account_identity=account_identity,
        write_persist=write_persist,
        display_name=(
            truth_view.display_name if truth_view else (
                truth_acct.display_name if truth_acct else TRUTH_PROVIDER_DISPLAY
            )
        ),
        provider=provider_key,
        extracted_items=extracted_items,
        session_confidence=session_confidence,
        extraction_status=extraction_status,
        login_url=(
            (truth_view.user_action_url if truth_view else None)
            or (truth_acct.user_action_url if truth_acct else None)
            or provider_open_url
        ),
    )

    def _result(**kwargs) -> HomeStateResult:
        kwargs.setdefault("access_views", truth_views if truth_views else access_views)
        kwargs.setdefault("show_access_debug", show_access_debug)
        kwargs.setdefault("tower", tower)
        kwargs.setdefault("capability", capability)
        kwargs.setdefault("extracted_items", list(extracted_items or []))
        kwargs.setdefault("session_confidence", session_confidence)
        kwargs.setdefault("provider_open_url", provider_open_url)
        # Attention owns opportunity ranking — do not project Actions as Home recs.
        kwargs.setdefault("secondary_recommendations", [])
        return HomeStateResult(**kwargs)

    if enrolled == 0:
        body = user_copy.HOME_EMPTY_BODY
        if worker_setup_needed:
            body = f"{body} {user_copy.HOME_EMPTY_WORKER_NOTE}"
        return _result(
            state=HomeState.EMPTY,
            priority_summary="",
            featured=HomeFeatured(
                headline=user_copy.HOME_EMPTY_HEADLINE,
                body=body,
                cta_label=user_copy.HOME_EMPTY_CTA,
                cta_url="/email-scan",
                secondary_label=user_copy.HOME_EMPTY_SECONDARY,
                secondary_url="/credentials",
            ),
            health=health,
            show_health=False,
            activity_pending_count=pending_activity_count,
            freshness_label=freshness_label,
        )

    has_fresh_data = health.up_to_date > 0
    if not has_fresh_data:
        wait_acct = _pick_waiting_account(accounts)
        n = enrolled
        headline = user_copy.home_waiting_headline(
            n, wait_acct.display_name if n == 1 and wait_acct else None,
        )
        verifying = bool(
            updating_name
            or any(a.status in (UPDATING, CHECKING) for a in accounts)
        )
        body = user_copy.home_handoff_body(
            needs_chrome=worker_setup_needed,
            verifying=verifying and not worker_setup_needed,
            provider_name=wait_acct.display_name if wait_acct else None,
        )
        if worker_setup_needed:
            cta_label = user_copy.CTA_SET_UP_WORKER
            cta_url = "/extension-setup"
        elif verifying:
            cta_label = None
            cta_url = None
        elif wait_acct:
            cta_label = user_copy.home_visit_provider_cta(wait_acct.display_name)
            open_urls = provider_open_urls or {}
            cta_url = (
                open_urls.get(wait_acct.source)
                or wait_acct.user_action_url
                or "/credentials"
            )
        else:
            cta_label = user_copy.HOME_VIEW_ACCOUNTS_LABEL
            cta_url = "/credentials"
        rows = [
            WaitingRow(display_name=a.display_name, status_label=_waiting_row_label(a))
            for a in accounts
            if a.status != UP_TO_DATE
        ][:5]
        return _attach_update_context(
            _result(
                state=HomeState.WAITING,
                priority_summary=user_copy.HOME_PRIORITY_WAITING,
                featured=HomeFeatured(
                    headline=headline,
                    body=body,
                    cta_label=cta_label,
                    cta_url=cta_url,
                    secondary_label=user_copy.HOME_VIEW_WAITING_LABEL,
                    secondary_url="/credentials?filter=waiting",
                ),
                health=health,
                waiting_rows=rows,
                show_health=True,
                activity_pending_count=pending_activity_count,
                freshness_label=freshness_label,
            ),
            updating_name,
        )

    if updating_name:
        headline = user_copy.home_update_headline(updating_name)
        return _result(
            state=HomeState.UPDATE,
            priority_summary=user_copy.HOME_PRIORITY_UPDATE,
            featured=HomeFeatured(
                headline=headline,
                body=user_copy.HOME_UPDATE_BODY,
                disabled_cta_label=user_copy.HOME_UPDATING_CTA,
                secondary_label=user_copy.HOME_VIEW_ACCOUNTS_LABEL,
                secondary_url="/credentials",
            ),
            health=health,
            show_health=True,
            activity_pending_count=pending_activity_count,
            freshness_label=freshness_label,
            updating_display_name=updating_name,
        )

    # Attention interrupts (login, authorize, …) are rendered from AttentionView.
    # HomeState here is only enrollment/operational context + capability.
    cta_label = user_copy.HOME_VIEW_ACCOUNTS_LABEL
    cta_url = "/credentials"
    if enrolled == 1 and accounts[0].status == UP_TO_DATE:
        cta_label = user_copy.home_view_provider_cta(accounts[0].display_name)
        cta_url = (
            open_urls.get(accounts[0].source)
            or accounts[0].user_action_url
            or "/credentials"
        )
    return _attach_update_context(
        _result(
            state=HomeState.ALL_CLEAR,
            priority_summary=user_copy.HOME_PRIORITY_ALL_CLEAR,
            featured=HomeFeatured(
                headline=user_copy.HOME_ALL_CLEAR_HEADLINE,
                body=user_copy.home_all_clear_body(enrolled),
                cta_label=cta_label,
                cta_url=cta_url,
            ),
            health=health,
            show_health=True,
            show_metrics=bool(benefit_count or tracked_value_label),
            metrics_accounts=enrolled,
            metrics_benefits=benefit_count,
            metrics_value=tracked_value_label,
            activity_pending_count=pending_activity_count,
            freshness_label=freshness_label,
        ),
        updating_name,
    )
