"""
mighty.account_status
─────────────────────
Canonical per-account update state shared by the dashboard and Chrome extension.

Login / session status comes only from provider_session_state via
mighty.session_access (Current Access resolver). Positive “Connected” /
ready status comes from mighty.account_readiness (fresh session + correlated
private-data extraction). Legacy sync_status and connection_status remain
written for compatibility but must not decide needs-login banners, counts,
Connected, or messaging.

Statuses (priority when resolving):
  needs_login           — readiness signed_out
  checking              — readiness checking (verification/extraction in flight)
  updating              — extension is actively syncing this account right now
  up_to_date            — readiness ready (access + correlated private data)
  unverified            — readiness unverified (incomplete/stale/failed evidence)
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
    AccountPresentation,
    build_access_loop_summary,
    resolve_presentation_from_status_signals,
)
from mighty.account_readiness import (
    CHECKING as READINESS_CHECKING,
    READY as READINESS_READY,
    SIGNED_OUT as READINESS_SIGNED_OUT,
    UNVERIFIED as READINESS_UNVERIFIED,
    AccountReadiness,
    resolve_account_readiness,
)
from mighty.customer_account_access import (
    CustomerAccountAccessView,
    build_customer_account_access_view,
    resolve_discovered_from,
)
from mighty.capability_state import CapabilityView, build_capability_view
from mighty.provider_account import ProviderAccount, infer_extraction_status, has_normalized_data
from mighty.session_access import (
    CHECKING,
    load_session_access_by_provider,
    product_state_for_session,
    resolve_product_account_state,
    resolve_session_access_presentation,
)
from mighty.user_copy import (
    ACCOUNT_STATE_CTAS,
    CTA_SIGN_IN,
    FAILURE_HINTS,
    LIFECYCLE_CTAS,
    SOURCE_EXTENSION,
    SOURCE_FOUND_FROM_GMAIL,
    STATUS_LABELS,
)

UP_TO_DATE = "up_to_date"
UPDATING = "updating"
NEEDS_LOGIN = "needs_login"
WAITING_FOR_EXTENSION = "waiting_for_extension"
ERROR = "error"
UNVERIFIED = "unverified"

ALL_CANONICAL = (
    UP_TO_DATE,
    UPDATING,
    CHECKING,
    NEEDS_LOGIN,
    WAITING_FOR_EXTENSION,
    ERROR,
    UNVERIFIED,
)

_STATUS_COLORS: dict[str, str] = {
    UP_TO_DATE: "#16a34a",
    UPDATING: "#6366f1",
    CHECKING: "#6366f1",
    NEEDS_LOGIN: "#dc2626",
    WAITING_FOR_EXTENSION: "#6366f1",
    ERROR: "#dc2626",
    UNVERIFIED: "#6b7280",
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
    session_state: str | None = None
    current_access: str | None = None
    verification_message: str | None = None
    login_required: bool | None = None
    user_attention_required: bool | None = None
    next_action_type: str | None = None
    next_action_text: str | None = None
    readiness: str | None = None
    readiness_copy: str | None = None
    access_cycle_id: str | None = None
    cached_data_label: str | None = None
    last_confirmed_ready_at: str | None = None
    last_confirmed_access_cycle_id: str | None = None
    background_verification: bool = False
    secondary_label: str | None = None
    verification_lifecycle: str | None = None
    discovered_from: str | None = None
    evidence_source: str | None = None
    customer_access: CustomerAccountAccessView | None = None
    snapshot_id: str | None = None
    snapshot_verified_at: str | None = None
    snapshot_schema_version: int | None = None
    capability: CapabilityView | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
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
        if self.session_state is not None:
            payload["session_state"] = self.session_state
        if self.current_access is not None:
            payload["current_access"] = self.current_access
        if self.verification_message is not None:
            payload["verification_message"] = self.verification_message
        if self.login_required is not None:
            payload["login_required"] = self.login_required
        if self.user_attention_required is not None:
            payload["user_attention_required"] = self.user_attention_required
        if self.next_action_type is not None:
            payload["next_action_type"] = self.next_action_type
        if self.next_action_text is not None:
            payload["next_action_text"] = self.next_action_text
        if self.readiness is not None:
            payload["readiness"] = self.readiness
            payload["readiness_copy"] = self.readiness_copy
            payload["status_copy"] = self.readiness_copy
        if self.access_cycle_id is not None:
            payload["access_cycle_id"] = self.access_cycle_id
        if self.cached_data_label is not None:
            payload["cached_data_label"] = self.cached_data_label
        if self.last_confirmed_ready_at is not None:
            payload["last_confirmed_ready_at"] = self.last_confirmed_ready_at
        if self.last_confirmed_access_cycle_id is not None:
            payload["last_confirmed_access_cycle_id"] = (
                self.last_confirmed_access_cycle_id
            )
        if self.background_verification:
            payload["background_verification"] = True
        if self.secondary_label is not None:
            payload["secondary_label"] = self.secondary_label
        if self.verification_lifecycle is not None:
            payload["verification_lifecycle"] = self.verification_lifecycle
        if self.discovered_from is not None:
            payload["discovered_from"] = self.discovered_from
        if self.evidence_source is not None:
            payload["evidence_source"] = self.evidence_source
        if self.customer_access is not None:
            payload["customer_access"] = self.customer_access.to_dict()
        if self.snapshot_id is not None:
            payload["snapshot_id"] = self.snapshot_id
        if self.snapshot_verified_at is not None:
            payload["snapshot_verified_at"] = self.snapshot_verified_at
        if self.snapshot_schema_version is not None:
            payload["snapshot_schema_version"] = self.snapshot_schema_version
        if self.capability is not None:
            cap = self.capability.to_dict()
            payload["capability"] = cap
            payload["capability_state"] = cap["capability_state"]
            if cap.get("truth_validation") is not None:
                payload["truth_validation"] = cap["truth_validation"]
            elif self.capability.truth_validation is not None:
                payload["truth_validation"] = self.capability.truth_validation.to_dict()
        elif self.customer_access is not None:
            payload["capability_state"] = self.customer_access.to_dict().get(
                "capability_state",
            )
        return payload


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
    session_state: str | None = None,
    readiness: str | None = None,
) -> str:
    """Map lifecycle + sync signals to a canonical status.

    When readiness is provided, it alone decides ready / checking / signed_out /
    unverified for customer surfaces. When only session_state is provided,
    it decides needs_login / checking; connected alone never implies up_to_date
    without readiness. Legacy sync_status and connection_status never decide login.
    """
    del connection_status  # Legacy — never decides product status.
    if readiness == READINESS_SIGNED_OUT:
        return NEEDS_LOGIN
    if readiness == READINESS_CHECKING:
        return CHECKING
    if readiness == READINESS_READY:
        if updating_source and updating_source == source:
            return UPDATING
        return UP_TO_DATE
    if readiness == READINESS_UNVERIFIED:
        if updating_source and updating_source == source:
            return UPDATING
        # Setup / first-visit posture stays waiting — not a positive Connected claim.
        if lifecycle.state == LC_WAITING or sync_status in ("needs_first_visit",):
            return WAITING_FOR_EXTENSION
        if session_state in (None, "unknown") and lifecycle.state != LC_SYNCED:
            return WAITING_FOR_EXTENSION
        return UNVERIFIED

    if session_state == "signed_out":
        return NEEDS_LOGIN
    if session_state == "checking":
        return CHECKING
    if session_state == "connected":
        # Session alone is not Connected — require readiness ready.
        if updating_source and updating_source == source:
            return UPDATING
        if sync_status == "no_data" and lifecycle.state != LC_SYNCED:
            return ERROR
        return UNVERIFIED
    if session_state == "unknown":
        # No fresh session evidence — never treat legacy login_required as needs_login.
        if updating_source and updating_source == source:
            return UPDATING
        if lifecycle.state == LC_SYNCED:
            return UNVERIFIED
        if sync_status == "no_data":
            return ERROR
        if lifecycle.state == LC_WAITING or sync_status in ("needs_first_visit",):
            return WAITING_FOR_EXTENSION
        return WAITING_FOR_EXTENSION

    # Non-probe providers: resolve without login signals from legacy fields.
    if updating_source and updating_source == source:
        return UPDATING
    if lifecycle.state == LC_SYNCED:
        return UNVERIFIED
    if sync_status == "no_data":
        return ERROR
    if lifecycle.state == LC_WAITING or sync_status in ("needs_first_visit",):
        return WAITING_FOR_EXTENSION
    # Ignore legacy login_required / connection needs_login for product status.
    if lifecycle.state == LC_NEEDS_LOGIN:
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
    # Login CTA only when status is needs_login (from signed_out session_state).
    if status == NEEDS_LOGIN:
        return presentation_cta or lifecycle.cta_label or CTA_SIGN_IN, login_url or None
    if presentation_cta == CTA_SIGN_IN:
        # Never promote a Sign in CTA unless status is needs_login.
        return None, None
    if status == WAITING_FOR_EXTENSION:
        return lifecycle.cta_label or LIFECYCLE_CTAS["waiting_for_extension"], connect_url
    if status == ERROR:
        return "Retry sync", connect_url
    return None, None


def _presentation_from_readiness(readiness: AccountReadiness) -> AccountPresentation:
    cta = ACCOUNT_STATE_CTAS.get(readiness.presentation_key, "")
    return AccountPresentation(
        key=readiness.presentation_key,
        label=readiness.status_label,
        cta_label=cta,
        cta_disabled=readiness.state in (READINESS_CHECKING, READINESS_UNVERIFIED),
        extension_hint=readiness.status_copy,
    )


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
    connection_status: str | None = None,
    last_verified_at: str | None = None,
    extraction_status: str | None = None,
    last_data_refresh: str | None = None,
    session_access=None,
    extraction_access_cycle_id: str | None = None,
    snapshot_id: str | None = None,
    snapshot_verified_at: str | None = None,
    snapshot_schema_version: int | None = None,
) -> AccountStatus:
    session_state = None
    current_access = None
    verification_message = None
    session_presentation = None
    login_required = None
    user_attention_required = None
    next_action_type = None
    next_action_text = None
    product = None
    if session_access is not None:
        session_presentation = resolve_session_access_presentation(
            session_access, display_name=display_name,
        )
        product = resolve_product_account_state(session_access)
        session_state = product.session_state
        current_access = product.current_access
        verification_message = session_presentation.verification_message
        login_required = product.login_required
        user_attention_required = product.user_attention_required
        next_action_type = product.next_action_type
        next_action_text = product.next_action_text
    else:
        # Non-probe / no Current Access row → unknown product session.
        product = product_state_for_session("unknown", provider=source)
        session_state = product.session_state
        current_access = product.current_access
        login_required = product.login_required
        user_attention_required = product.user_attention_required
        next_action_type = product.next_action_type
        next_action_text = product.next_action_text

    updating_this = bool(updating_source and updating_source == source)
    readiness = resolve_account_readiness(
        provider=source,
        product=product,
        session_evidence_at=(
            getattr(session_access, "last_verified", None) if session_access else None
        ),
        verification_id=(
            getattr(session_access, "verification_id", None) if session_access else None
        ),
        verification_lifecycle=(
            getattr(session_access, "verification_lifecycle", None)
            if session_access
            else None
        ),
        account=account,
        extraction_status=extraction_status or (account.extraction_status if account else None),
        extraction_at=last_data_refresh or (account.synced_at if account else None),
        extraction_access_cycle_id=extraction_access_cycle_id,
        last_private_data_at=(
            getattr(session_access, "last_private_data", None)
            if session_access
            else (last_data_refresh or (account.synced_at if account else None))
        ),
        updating_this_source=updating_this,
    )

    # Customer login CTA follows readiness, not product error→signed_out mapping.
    login_required = readiness.login_required
    user_attention_required = readiness.login_required

    canonical = resolve_canonical_status(
        lifecycle,
        sync_status,
        source=source,
        updating_source=updating_source,
        connection_status=connection_status,
        session_state=session_state,
        readiness=readiness.state,
    )
    # Active sync still shows updating unless readiness says signed_out/checking.
    if (
        readiness.state not in (READINESS_SIGNED_OUT, READINESS_CHECKING)
        and updating_this
    ):
        canonical = UPDATING

    synced_at = account.synced_at if account else None
    last_error = None
    if canonical == ERROR:
        reason = failure_reason or sync_status
        last_error = _FAILURE_MESSAGES.get(reason, reason or "Sync failed")
    elif canonical == NEEDS_LOGIN:
        last_error = _FAILURE_MESSAGES.get("login_required")

    if readiness.state == READINESS_SIGNED_OUT:
        presentation = _presentation_from_readiness(readiness)
        canonical = NEEDS_LOGIN
        verification_message = readiness.status_copy
    elif canonical == UPDATING:
        presentation = AccountPresentation(
            key="updating",
            label=STATUS_LABELS["updating"],
            cta_label=ACCOUNT_STATE_CTAS["updating"],
            cta_disabled=True,
            extension_hint=readiness.status_copy,
        )
        verification_message = readiness.status_copy
    elif readiness.state == READINESS_CHECKING:
        presentation = _presentation_from_readiness(readiness)
        canonical = CHECKING
        verification_message = readiness.status_copy
    elif readiness.state == READINESS_READY:
        presentation = _presentation_from_readiness(readiness)
        verification_message = readiness.status_copy
        if readiness.secondary_label:
            verification_message = (
                f"{readiness.status_copy} {readiness.secondary_label}"
            )
    elif readiness.state == READINESS_UNVERIFIED:
        if canonical == WAITING_FOR_EXTENSION:
            presentation = AccountPresentation(
                key="updating",
                label=STATUS_LABELS["waiting_for_extension"],
                cta_label=ACCOUNT_STATE_CTAS["updating"],
                cta_disabled=True,
                extension_hint=readiness.status_copy,
            )
        else:
            presentation = _presentation_from_readiness(readiness)
            canonical = UNVERIFIED
        verification_message = readiness.status_copy
    elif session_presentation is not None and session_state is not None:
        # Fallback path — should be rare once readiness always resolves.
        if canonical == WAITING_FOR_EXTENSION:
            presentation = AccountPresentation(
                key="updating",
                label=STATUS_LABELS["waiting_for_extension"],
                cta_label=ACCOUNT_STATE_CTAS["updating"],
                cta_disabled=True,
            )
        elif canonical == ERROR:
            presentation = AccountPresentation(
                key="needs_attention",
                label=STATUS_LABELS["error"],
                cta_label=ACCOUNT_STATE_CTAS["needs_attention"],
                cta_disabled=False,
                extension_hint=last_error,
            )
        else:
            presentation = _presentation_from_readiness(readiness)
    else:
        has_meaningful = has_normalized_data(account.normalized_fields if account else None)
        # Strip legacy login signals so presentation never invents needs_sign_in.
        safe_sync = sync_status if sync_status != "login_required" else "ok"
        safe_conn = (
            None
            if (connection_status or "") in ("needs_login", "login_required")
            else connection_status
        )
        safe_lifecycle = lifecycle.state if lifecycle.state != LC_NEEDS_LOGIN else LC_WAITING
        presentation = resolve_presentation_from_status_signals(
            provider=source,
            connection_status=safe_conn,
            sync_status=safe_sync,
            lifecycle_state=safe_lifecycle,
            has_meaningful_data=has_meaningful,
            last_verified_at=last_verified_at,
            is_updating=canonical == UPDATING,
            sync_status_error=failure_reason if failure_reason != "login_required" else None,
            extraction_status=extraction_status,
            last_data_refresh=last_data_refresh,
        )
        # Legacy path must still not invent Connected without readiness ready.
        if presentation.key == "ready" and readiness.state != READINESS_READY:
            presentation = _presentation_from_readiness(readiness)
            canonical = UNVERIFIED if readiness.state == READINESS_UNVERIFIED else canonical

    action_label, action_url = _user_action_for_status(
        canonical,
        lifecycle,
        login_url=login_url,
        connect_url=connect_url,
        presentation_cta=presentation.cta_label,
    )

    verification_lifecycle = (
        getattr(session_access, "verification_lifecycle", None)
        if session_access is not None
        else None
    )
    evidence_source = (
        getattr(session_access, "source", None) if session_access is not None else None
    )
    if lifecycle.source_label == SOURCE_FOUND_FROM_GMAIL:
        discovered = resolve_discovered_from(from_email=True)
    elif lifecycle.source_label == SOURCE_EXTENSION:
        discovered = resolve_discovered_from(data_source="extension")
    else:
        discovered = resolve_discovered_from(
            data_source=account.data_source if account else None,
        )

    customer_access = build_customer_account_access_view(
        provider=source,
        display_name=display_name,
        readiness=readiness,
        discovered_from=discovered,
        verification_lifecycle=verification_lifecycle,
        extraction_status=extraction_status or (account.extraction_status if account else None),
        user_action_text=action_label,
        user_action_url=action_url,
        evidence_source=evidence_source,
        cached_snapshot_at=last_data_refresh or synced_at,
        canonical_status=canonical,
    )
    ext_status = extraction_status or (account.extraction_status if account else None)
    capability = build_capability_view(
        customer_access,
        display_name=display_name,
        provider=source,
        extracted_items=account.normalized_fields if account else None,
        extraction_status=ext_status,
        login_url=login_url or None,
        verification_id=(
            getattr(session_access, "verification_id", None)
            if session_access is not None
            else None
        ),
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
        session_state=session_state,
        current_access=current_access,
        verification_message=verification_message,
        login_required=login_required,
        user_attention_required=user_attention_required,
        next_action_type=next_action_type,
        next_action_text=next_action_text,
        readiness=readiness.state,
        readiness_copy=readiness.status_copy,
        access_cycle_id=readiness.access_cycle_id,
        cached_data_label=readiness.cached_data_label,
        last_confirmed_ready_at=readiness.last_confirmed_ready_at,
        last_confirmed_access_cycle_id=readiness.last_confirmed_access_cycle_id,
        background_verification=readiness.background_verification,
        secondary_label=readiness.secondary_label,
        verification_lifecycle=verification_lifecycle,
        discovered_from=discovered,
        evidence_source=evidence_source,
        customer_access=customer_access,
        snapshot_id=snapshot_id,
        snapshot_verified_at=snapshot_verified_at,
        snapshot_schema_version=snapshot_schema_version,
        capability=capability,
    )


def build_status_summary(
    accounts: list[AccountStatus],
    *,
    access_loop_presentations=None,
) -> AccountStatusSummary:
    """Headline/subline for extension popup and dashboard sync header."""
    if access_loop_presentations is not None:
        loop = build_access_loop_summary(access_loop_presentations)
        needs_sign_in_accounts = [
            a.display_name
            for a in accounts
            if a.presentation_key == "needs_sign_in"
        ]
        updating_accounts = [
            a.display_name
            for a in accounts
            if a.presentation_key in ("updating", "checking")
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
            cta_disabled=a.presentation_key in ("updating", "checking"),
        )
        for a in accounts
    ]
    loop = build_access_loop_summary(presentations)
    needs_sign_in_accounts = [
        a.display_name for a in accounts if a.presentation_key == "needs_sign_in"
    ]
    updating_accounts = [
        a.display_name for a in accounts if a.presentation_key in ("updating", "checking")
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
    """Load canonical status for every connected account.

    Login/session fields come from provider_session_state Current Access.
    """
    if lifecycle_signals is None:
        lifecycle_signals = _load_lifecycle_signals(uid, db)

    cred_rows = db.execute(
        "SELECT source FROM account_credentials WHERE user_id=? AND source != '_email'",
        (uid,),
    ).fetchall()
    # Provider-independent: load Current Access for every credentialed source
    # (not only probe providers). Probe providers stay included for completeness.
    from mighty.provider_access_probe import PROBE_PROVIDERS

    session_providers = sorted({row["source"] for row in cred_rows} | set(PROBE_PROVIDERS))
    session_by_provider = load_session_access_by_provider(
        db, uid, decrypt_fn=decrypt_fn, providers=session_providers,
    )

    from mighty.account_snapshot import load_latest_snapshots_by_provider

    snapshots_by_provider = load_latest_snapshots_by_provider(
        db, uid, providers=session_providers,
    )

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

        snapshot = snapshots_by_provider.get(source)
        # Customer field source of truth: latest successful snapshot.
        # Fall back to account_data only when no snapshot exists yet (migration).
        if snapshot is not None:
            items = snapshot.display_items()
        else:
            items = data.get("items", [])
        extraction_st = (ad_row["extraction_status"] if ad_row else "") or ""
        extraction_access_cycle_id = data.get("access_cycle_id") or data.get("extraction_access_cycle_id")
        if snapshot is not None and snapshot.access_cycle_id:
            extraction_access_cycle_id = snapshot.access_cycle_id
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
            synced_at=(
                snapshot.verified_at if snapshot is not None
                else (ad_row["synced_at"] if ad_row else None)
            ),
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

        session_access = session_by_provider.get(source)
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
                connection_status=connection_status or None,
                last_verified_at=_latest_connection_verification(db, uid, source),
                extraction_status=extraction_st or None,
                last_data_refresh=(
                    snapshot.verified_at if snapshot is not None
                    else (ad_row["synced_at"] if ad_row else None)
                ),
                session_access=session_access,
                extraction_access_cycle_id=(
                    str(extraction_access_cycle_id) if extraction_access_cycle_id else None
                ),
                snapshot_id=snapshot.snapshot_id if snapshot else None,
                snapshot_verified_at=snapshot.verified_at if snapshot else None,
                snapshot_schema_version=snapshot.schema_version if snapshot else None,
            )
        )

    _apply_stable_customer_capability(accounts, db=db, user_id=uid)

    accounts.sort(key=lambda a: a.display_name.lower())
    summary_presentations = [
        AccountPresentation(
            key=a.presentation_key,
            label=a.presentation_label,
            cta_label=ACCOUNT_STATE_CTAS.get(a.presentation_key, ""),
            cta_disabled=a.presentation_key in ("updating", "checking"),
        )
        for a in accounts
    ]
    return accounts, build_status_summary(accounts, access_loop_presentations=summary_presentations)


def _apply_stable_customer_capability(
    accounts: list[AccountStatus],
    *,
    db,
    user_id: str,
) -> None:
    """Hold prior Truth card while verification is in flight (customer providers)."""
    from mighty.capability_state import is_customer_visible_provider
    from mighty.customer_capability_presentation import (
        load_stable_capability,
        present_customer_capability,
        save_stable_capability,
    )

    for acct in accounts:
        if not is_customer_visible_provider(acct.source) or acct.capability is None:
            continue
        previous = load_stable_capability(db, user_id, acct.source)
        presented = present_customer_capability(
            acct.capability,
            previous_stable=previous,
            access_view=acct.customer_access,
            verification_lifecycle=acct.verification_lifecycle,
            background_verification=acct.background_verification,
        )
        acct.capability = presented
        if not presented.is_refreshing:
            save_stable_capability(db, user_id, presented)


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
