"""
mighty.account_status
─────────────────────
Canonical per-account update state shared by the dashboard and Chrome extension.

Statuses (priority when resolving):
  needs_login           — session expired or provider login wall detected
  updating              — extension is actively syncing this account right now
  up_to_date            — meaningful fields synced recently
  waiting_for_extension — registered but not yet connected / first visit pending
  error                 — sync failed for a non-login reason
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mighty.account_lifecycle import (
    NEEDS_LOGIN as LC_NEEDS_LOGIN,
    SYNCED as LC_SYNCED,
    WAITING_FOR_EXTENSION as LC_WAITING,
    AccountLifecycle,
    resolve_account_lifecycle,
)
from mighty.account_presentation import (
    build_access_loop_summary,
    resolve_account_presentation,
    resolve_presentation_from_status_signals,
)
from mighty.provider_account import ProviderAccount, infer_extraction_status, load_provider_account, has_normalized_data
from mighty.user_copy import (
    ACCOUNT_STATE_CTAS,
    CTA_SIGN_IN,
    FAILURE_HINTS,
    LIFECYCLE_CTAS,
    STATUS_LABELS,
)

UP_TO_DATE = "up_to_date"
UPDATING = "updating"
NEEDS_LOGIN = "needs_login"
WAITING_FOR_EXTENSION = "waiting_for_extension"
ERROR = "error"

ALL_CANONICAL = (
    UP_TO_DATE,
    UPDATING,
    NEEDS_LOGIN,
    WAITING_FOR_EXTENSION,
    ERROR,
)

# STATUS_LABELS imported from user_copy (shared with dashboard + worker popup)

_STATUS_COLORS: dict[str, str] = {
    UP_TO_DATE: "#16a34a",
    UPDATING: "#6366f1",
    NEEDS_LOGIN: "#dc2626",
    WAITING_FOR_EXTENSION: "#6366f1",
    ERROR: "#dc2626",
}

_SYNC_SEVERITY = {"": 0, "ok": 0, "needs_first_visit": 1, "no_data": 1, "login_required": 2}

_FAILURE_MESSAGES: dict[str, str] = dict(FAILURE_HINTS)


@dataclass
class AccountStatus:
    source: str
    display_name: str
    status: str
    presentation_key: str
    presentation_label: str
    last_successful_sync_at: str | None
    current_attempt_at: str | None
    last_error: str | None
    user_action_label: str | None
    user_action_url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "display_name": self.display_name,
            "status": self.status,
            "status_label": self.presentation_label,
            "presentation_key": self.presentation_key,
            "presentation_label": self.presentation_label,
            "status_color": _STATUS_COLORS.get(self.status, "#6b7280"),
            "last_successful_sync_at": self.last_successful_sync_at,
            "current_attempt_at": self.current_attempt_at,
            "last_error": self.last_error,
            "user_action_label": self.user_action_label,
            "user_action_url": self.user_action_url,
        }


@dataclass
class AccountStatusSummary:
    headline: str
    subline: str
    is_syncing: bool
    needs_login_count: int
    updating_count: int
    needs_login_accounts: list[str]
    updating_accounts: list[str]
    access_loop: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "headline": self.headline,
            "subline": self.subline,
            "is_syncing": self.is_syncing,
            "needs_login_count": self.needs_login_count,
            "updating_count": self.updating_count,
            "needs_login_accounts": self.needs_login_accounts,
            "updating_accounts": self.updating_accounts,
        }
        if self.access_loop is not None:
            payload["access_loop"] = self.access_loop
            payload["detail_lines"] = self.access_loop.get("detail_lines", [])
            payload["open_account_center_label"] = self.access_loop.get(
                "open_account_center_label", "",
            )
        return payload


def merge_sync_status(blob_status: str, column_status: str) -> str:
    """Prefer the more severe persisted sync_status (column vs encrypted blob)."""
    blob_status = blob_status or "ok"
    column_status = column_status or ""
    if _SYNC_SEVERITY.get(column_status, 0) > _SYNC_SEVERITY.get(blob_status, 0):
        return column_status
    return blob_status


def resolve_canonical_status(
    lifecycle: AccountLifecycle,
    sync_status: str,
    *,
    source: str,
    updating_source: str | None,
    connection_status: str | None = None,
) -> str:
    """Map lifecycle + sync signals to a canonical status.

    needs_login always wins over updating for the same account so a login wall
    is never masked by an in-progress sync queue — unless the extension already
    verified an active browser session.
    """
    conn = connection_status or ""
    if conn == "connected":
        if lifecycle.state == LC_SYNCED:
            return UP_TO_DATE
        if updating_source and updating_source == source:
            return UPDATING
        if sync_status == "no_data":
            return ERROR
        return UP_TO_DATE
    if lifecycle.state == LC_NEEDS_LOGIN or sync_status == "login_required":
        return NEEDS_LOGIN
    if updating_source and updating_source == source:
        return UPDATING
    if lifecycle.state == LC_SYNCED:
        return UP_TO_DATE
    if sync_status == "no_data":
        return ERROR
    if lifecycle.state == LC_WAITING or sync_status in ("needs_first_visit",):
        return WAITING_FOR_EXTENSION
    return WAITING_FOR_EXTENSION


def _user_action_for_status(
    status: str,
    lifecycle: AccountLifecycle,
    *,
    login_url: str,
    connect_url: str,
    presentation_cta: str | None = None,
) -> tuple[str | None, str | None]:
    if presentation_cta == CTA_SIGN_IN or status == NEEDS_LOGIN:
        return presentation_cta or lifecycle.cta_label or CTA_SIGN_IN, login_url or None
    if status == WAITING_FOR_EXTENSION:
        return lifecycle.cta_label or LIFECYCLE_CTAS["waiting_for_extension"], connect_url
    if status == ERROR:
        return "Retry sync", connect_url
    return None, None


def     build_account_status(
        source: str,
        display_name: str,
        lifecycle: AccountLifecycle,
        account: ProviderAccount | None,
        *,
        sync_status: str,
        updating_source: str | None,
        sync_started_at: str | None = None,
        login_url: str = "",
        connect_url: str = "",
        failure_reason: str | None = None,
        connection_status: str | None = None,
        last_verified_at: str | None = None,
        extraction_status: str | None = None,
        last_data_refresh: str | None = None,
    ) -> AccountStatus:
    canonical = resolve_canonical_status(
        lifecycle,
        sync_status,
        source=source,
        updating_source=updating_source,
        connection_status=connection_status,
    )
    synced_at = account.synced_at if account else None
    last_error = None
    if canonical == ERROR:
        reason = failure_reason or sync_status
        last_error = _FAILURE_MESSAGES.get(reason, reason or "Sync failed")
    elif canonical == NEEDS_LOGIN:
        last_error = _FAILURE_MESSAGES.get("login_required")

    has_meaningful = has_normalized_data(account.normalized_fields if account else None)
    presentation = resolve_presentation_from_status_signals(
        provider=source,
        connection_status=connection_status,
        sync_status=sync_status,
        lifecycle_state=lifecycle.state,
        has_meaningful_data=has_meaningful,
        last_verified_at=last_verified_at,
        is_updating=canonical == UPDATING,
        sync_status_error=failure_reason,
        extraction_status=extraction_status,
        last_data_refresh=last_data_refresh,
    )

    action_label, action_url = _user_action_for_status(
        canonical,
        lifecycle,
        login_url=login_url,
        connect_url=connect_url,
        presentation_cta=presentation.cta_label,
    )

    return AccountStatus(
        source=source,
        display_name=display_name,
        status=canonical,
        presentation_key=presentation.key,
        presentation_label=presentation.label,
        last_successful_sync_at=synced_at if lifecycle.state == LC_SYNCED else None,
        current_attempt_at=sync_started_at if canonical == UPDATING else None,
        last_error=last_error or presentation.extension_hint,
        user_action_label=action_label,
        user_action_url=action_url,
    )


def build_status_summary(
    accounts: list[AccountStatus],
    *,
    access_loop_presentations=None,
) -> AccountStatusSummary:
    """Headline/subline for extension popup and dashboard sync header."""
    from mighty.account_presentation import AccountPresentation

    if access_loop_presentations is not None:
        loop = build_access_loop_summary(access_loop_presentations)
        needs_sign_in_accounts = [
            a.display_name
            for a in accounts
            if a.presentation_key == "needs_sign_in"
        ]
        updating_accounts = [
            a.display_name for a in accounts if a.presentation_key == "updating"
        ]
        return AccountStatusSummary(
            headline=loop.headline,
            subline=" · ".join(loop.detail_lines),
            is_syncing=loop.is_updating,
            needs_login_count=loop.needs_sign_in,
            updating_count=loop.updating,
            needs_login_accounts=needs_sign_in_accounts,
            updating_accounts=updating_accounts,
            access_loop=loop.to_dict(),
        )

    presentations = [
        AccountPresentation(
            key=a.presentation_key,
            label=a.presentation_label,
            cta_label=ACCOUNT_STATE_CTAS.get(a.presentation_key, ""),
            cta_disabled=a.presentation_key == "updating",
        )
        for a in accounts
    ]
    loop = build_access_loop_summary(presentations)
    needs_sign_in_accounts = [
        a.display_name for a in accounts if a.presentation_key == "needs_sign_in"
    ]
    updating_accounts = [
        a.display_name for a in accounts if a.presentation_key == "updating"
    ]
    return AccountStatusSummary(
        headline=loop.headline,
        subline=" · ".join(loop.detail_lines),
        is_syncing=loop.is_updating,
        needs_login_count=loop.needs_sign_in,
        updating_count=loop.updating,
        needs_login_accounts=needs_sign_in_accounts,
        updating_accounts=updating_accounts,
        access_loop=loop.to_dict(),
    )


def _latest_connection_verification(db, uid: str, source: str) -> str | None:
    from mighty.account_state import _latest_successful_connection_verification

    return _latest_successful_connection_verification(db, uid, source)


def load_all_account_statuses(
    uid: str,
    db,
    *,
    decrypt_fn: Callable,
    display_names: dict[str, str],
    login_url_fn: Callable[[str], str],
    lifecycle_signals: dict[str, tuple[bool, bool, bool]] | None = None,
    sync_running: bool = False,
    sync_started_at: str | None = None,
    updating_source: str | None = None,
    account_states: list | None = None,
) -> tuple[list[AccountStatus], AccountStatusSummary]:
    """Load canonical status for every connected account."""
    if lifecycle_signals is None:
        lifecycle_signals = _load_lifecycle_signals(uid, db)

    cred_rows = db.execute(
        "SELECT source FROM account_credentials WHERE user_id=? AND source != '_email'",
        (uid,),
    ).fetchall()

    effective_updating = updating_source if sync_running else None
    states_by_provider = {}
    if account_states:
        states_by_provider = {s.provider: s for s in account_states}

    accounts: list[AccountStatus] = []
    for row in cred_rows:
        source = row["source"]
        display_name = display_names.get(source) or source.replace("_", " ").title()

        ad_row = db.execute(
            "SELECT source, display_name, synced_at, connection_status, extraction_status, "
            "data_enc, sync_status, sync_failure_reason, entry_url "
            "FROM account_data WHERE user_id=? AND source=?",
            (uid, source),
        ).fetchone()

        data: dict = {}
        if ad_row:
            data = decrypt_fn(uid, ad_row["data_enc"] or "") or {}
            if ad_row["display_name"]:
                display_name = ad_row["display_name"]

        sync_status = merge_sync_status(
            data.get("sync_status", "ok"),
            (ad_row["sync_status"] if ad_row and ad_row["sync_status"] else "") or "",
        )
        connection_status = ""
        if ad_row:
            connection_status = (ad_row["connection_status"] or "") or data.get("connection_status", "")

        items = data.get("items", [])
        extraction_st = (ad_row["extraction_status"] if ad_row else "") or ""
        provider_acct = ProviderAccount(
            source=source,
            connection_status=connection_status or None,
            extraction_status=infer_extraction_status(
                items,
                explicit=extraction_st or None,
                sync_status=sync_status,
            ),
            normalized_fields=items,
            data_source=data.get("data_source") or data.get("sync_source"),
            synced_at=ad_row["synced_at"] if ad_row else None,
            sync_status=sync_status,
        ) if ad_row else None

        in_cred, from_email, email_added = lifecycle_signals.get(source, (True, False, False))
        lifecycle = resolve_account_lifecycle(
            source,
            in_credentials=in_cred,
            email_added=email_added,
            from_email=from_email,
            account=provider_acct,
        )

        entry_url = ""
        if ad_row and ad_row["entry_url"]:
            entry_url = ad_row["entry_url"]
        login_url = login_url_fn(source) or entry_url
        connect_url = f"/credentials?connect={source}"

        failure_reason = None
        if ad_row:
            try:
                failure_reason = ad_row["sync_failure_reason"]
            except (KeyError, IndexError):
                failure_reason = data.get("sync_failure_reason")

        canonical = resolve_canonical_status(
            lifecycle,
            sync_status,
            source=source,
            updating_source=effective_updating,
            connection_status=connection_status or None,
        )

        state = states_by_provider.get(source)
        if state is not None:
            presentation = resolve_account_presentation(
                state,
                sync_running=sync_running,
                updating_source=effective_updating,
            )
        else:
            presentation = resolve_presentation_from_status_signals(
                provider=source,
                connection_status=connection_status,
                sync_status=sync_status,
                lifecycle_state=lifecycle.state,
                has_meaningful_data=has_normalized_data(provider_acct.normalized_fields if provider_acct else None),
                last_verified_at=_latest_connection_verification(db, uid, source),
                is_updating=canonical == UPDATING,
                sync_status_error=failure_reason,
                extraction_status=extraction_st or None,
                last_data_refresh=ad_row["synced_at"] if ad_row else None,
            )

        synced_at = provider_acct.synced_at if provider_acct else None
        last_error = None
        if canonical == ERROR:
            reason = failure_reason or sync_status
            last_error = _FAILURE_MESSAGES.get(reason, reason or "Sync failed")
        elif presentation.key == "needs_sign_in":
            last_error = _FAILURE_MESSAGES.get("login_required")

        action_label, action_url = _user_action_for_status(
            canonical,
            lifecycle,
            login_url=login_url,
            connect_url=connect_url,
            presentation_cta=presentation.cta_label,
        )

        accounts.append(
            AccountStatus(
                source=source,
                display_name=display_name,
                status=canonical,
                presentation_key=presentation.key,
                presentation_label=presentation.label,
                last_successful_sync_at=synced_at if lifecycle.state == LC_SYNCED else None,
                current_attempt_at=sync_started_at if canonical == UPDATING else None,
                last_error=last_error or presentation.extension_hint,
                user_action_label=action_label,
                user_action_url=action_url,
            )
        )

    accounts.sort(key=lambda a: a.display_name.lower())
    from mighty.account_presentation import AccountPresentation

    summary_presentations = [
        AccountPresentation(
            key=a.presentation_key,
            label=a.presentation_label,
            cta_label=ACCOUNT_STATE_CTAS.get(a.presentation_key, ""),
            cta_disabled=a.presentation_key == "updating",
        )
        for a in accounts
    ]
    return accounts, build_status_summary(accounts, access_loop_presentations=summary_presentations)


def _load_lifecycle_signals(uid: str, db) -> dict[str, tuple[bool, bool, bool]]:
    cred_sources = {
        r["source"]
        for r in db.execute(
            "SELECT source FROM account_credentials WHERE user_id=?", (uid,),
        ).fetchall()
    }
    email_by_source: dict[str, tuple[bool, bool]] = {}
    for r in db.execute(
        "SELECT site_key, added FROM email_suggestions WHERE user_id=? AND dismissed=0",
        (uid,),
    ).fetchall():
        email_by_source[r["site_key"]] = (True, bool(r["added"]))
    signals: dict[str, tuple[bool, bool, bool]] = {}
    for src in cred_sources | set(email_by_source.keys()):
        from_email, email_added = email_by_source.get(src, (False, False))
        signals[src] = (src in cred_sources, from_email, email_added)
    return signals
