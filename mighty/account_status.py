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
from mighty.provider_account import ProviderAccount, infer_extraction_status, load_provider_account
from mighty.user_copy import (
    FAILURE_HINTS,
    LIFECYCLE_CTAS,
    STATUS_LABELS,
    summary_needs_login,
    summary_needs_login_plural,
    summary_updating,
    summary_updating_plural,
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
            "status_label": STATUS_LABELS.get(self.status, self.status),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "subline": self.subline,
            "is_syncing": self.is_syncing,
            "needs_login_count": self.needs_login_count,
            "updating_count": self.updating_count,
            "needs_login_accounts": self.needs_login_accounts,
            "updating_accounts": self.updating_accounts,
        }


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
) -> str:
    """Map lifecycle + sync signals to a canonical status.

    needs_login always wins over updating for the same account so a login wall
    is never masked by an in-progress sync queue.
    """
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
) -> tuple[str | None, str | None]:
    if status == NEEDS_LOGIN:
        return lifecycle.cta_label or LIFECYCLE_CTAS["needs_login"], login_url or None
    if status == WAITING_FOR_EXTENSION:
        return lifecycle.cta_label or LIFECYCLE_CTAS["waiting_for_extension"], connect_url
    if status == ERROR:
        return "Retry sync", connect_url
    return None, None


def build_account_status(
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
) -> AccountStatus:
    canonical = resolve_canonical_status(
        lifecycle,
        sync_status,
        source=source,
        updating_source=updating_source,
    )
    synced_at = account.synced_at if account else None
    last_error = None
    if canonical == ERROR:
        reason = failure_reason or sync_status
        last_error = _FAILURE_MESSAGES.get(reason, reason or "Sync failed")
    elif canonical == NEEDS_LOGIN:
        last_error = _FAILURE_MESSAGES.get("login_required")

    action_label, action_url = _user_action_for_status(
        canonical,
        lifecycle,
        login_url=login_url,
        connect_url=connect_url,
    )

    return AccountStatus(
        source=source,
        display_name=display_name,
        status=canonical,
        last_successful_sync_at=synced_at if lifecycle.state == LC_SYNCED else None,
        current_attempt_at=sync_started_at if canonical == UPDATING else None,
        last_error=last_error,
        user_action_label=action_label,
        user_action_url=action_url,
    )


def build_status_summary(accounts: list[AccountStatus]) -> AccountStatusSummary:
    """Headline/subline for extension popup and dashboard sync header."""
    needs_login = [a for a in accounts if a.status == NEEDS_LOGIN]
    updating = [a for a in accounts if a.status == UPDATING]

    headline = ""
    subline = ""

    if updating:
        headline = summary_updating(updating[0].display_name)
        if len(updating) > 1:
            subline = summary_updating_plural(len(updating))
    elif needs_login:
        if len(needs_login) == 1:
            headline = summary_needs_login(needs_login[0].display_name)
        else:
            headline = summary_needs_login_plural(len(needs_login))

    if updating and needs_login:
        if len(needs_login) == 1:
            subline = summary_needs_login(needs_login[0].display_name)
        else:
            subline = summary_needs_login_plural(len(needs_login))

    return AccountStatusSummary(
        headline=headline,
        subline=subline,
        is_syncing=bool(updating),
        needs_login_count=len(needs_login),
        updating_count=len(updating),
        needs_login_accounts=[a.display_name for a in needs_login],
        updating_accounts=[a.display_name for a in updating],
    )


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
) -> tuple[list[AccountStatus], AccountStatusSummary]:
    """Load canonical status for every connected account."""
    if lifecycle_signals is None:
        lifecycle_signals = _load_lifecycle_signals(uid, db)

    cred_rows = db.execute(
        "SELECT source FROM account_credentials WHERE user_id=? AND source != '_email'",
        (uid,),
    ).fetchall()

    effective_updating = updating_source if sync_running else None

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

        accounts.append(
            build_account_status(
                source,
                display_name,
                lifecycle,
                provider_acct,
                sync_status=sync_status,
                updating_source=effective_updating,
                sync_started_at=sync_started_at,
                login_url=login_url,
                connect_url=connect_url,
                failure_reason=failure_reason,
            )
        )

    accounts.sort(key=lambda a: a.display_name.lower())
    return accounts, build_status_summary(accounts)


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
