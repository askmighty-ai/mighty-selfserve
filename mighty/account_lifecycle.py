"""
mighty.account_lifecycle
────────────────────────
Unified account lifecycle for discovery → add → connect → sync.

States (in priority order when resolving):
  discovered            — found from email scan, not yet added
  added                 — added to Mighty, not connected
  waiting_for_extension — user started connect; awaiting extension
  needs_login           — extension saw provider but session not verified
  connected             — extension verified login; no extracted fields yet
  synced                — at least one meaningful normalized field exists
"""

from __future__ import annotations

from dataclasses import dataclass

from mighty.connection_state import (
    AMEX_CONNECTION_STATES,
    CONNECTED as CONN_CONNECTED,
    CONNECTING,
    NEEDS_LOGIN as CONN_NEEDS_LOGIN,
    WAITING_FOR_EXTENSION as CONN_WAITING,
)
from mighty.provider_account import ProviderAccount, is_synced
from mighty.user_copy import (
    LIFECYCLE_CTAS,
    LIFECYCLE_DESCRIPTIONS,
    LIFECYCLE_LABELS,
    SECONDARY_CTA_EXTENSION_RETRY,
    SOURCE_EXTENSION,
    SOURCE_FOUND_FROM_GMAIL,
    SOURCE_MANUALLY_ADDED,
)

# ── Lifecycle state ids ───────────────────────────────────────────────────────
DISCOVERED = "discovered"
ADDED = "added"
WAITING_FOR_EXTENSION = "waiting_for_extension"
NEEDS_LOGIN = "needs_login"
CONNECTED = "connected"
SYNCED = "synced"

ALL_STATES = (
    DISCOVERED,
    ADDED,
    WAITING_FOR_EXTENSION,
    NEEDS_LOGIN,
    CONNECTED,
    SYNCED,
)

STATE_LABELS: dict[str, str] = {
    DISCOVERED: LIFECYCLE_LABELS["discovered"],
    ADDED: LIFECYCLE_LABELS["added"],
    WAITING_FOR_EXTENSION: LIFECYCLE_LABELS["waiting_for_extension"],
    NEEDS_LOGIN: LIFECYCLE_LABELS["needs_login"],
    CONNECTED: LIFECYCLE_LABELS["connected"],
    SYNCED: LIFECYCLE_LABELS["synced"],
}

STATE_COLORS: dict[str, str] = {
    DISCOVERED: "#6b7280",
    ADDED: "#6366f1",
    WAITING_FOR_EXTENSION: "#6366f1",
    NEEDS_LOGIN: "#dc2626",
    CONNECTED: "#22c55e",
    SYNCED: "#16a34a",
}

STATE_DESCRIPTIONS: dict[str, str] = {
    DISCOVERED: LIFECYCLE_DESCRIPTIONS["discovered"],
    ADDED: LIFECYCLE_DESCRIPTIONS["added"],
    WAITING_FOR_EXTENSION: LIFECYCLE_DESCRIPTIONS["waiting_for_extension"],
    NEEDS_LOGIN: LIFECYCLE_DESCRIPTIONS["needs_login"],
    CONNECTED: LIFECYCLE_DESCRIPTIONS["connected"],
    SYNCED: LIFECYCLE_DESCRIPTIONS["synced"],
}

CTA_LABELS: dict[str, str] = {
    DISCOVERED: LIFECYCLE_CTAS["discovered"],
    ADDED: LIFECYCLE_CTAS["added"],
    WAITING_FOR_EXTENSION: LIFECYCLE_CTAS["waiting_for_extension"],
    NEEDS_LOGIN: LIFECYCLE_CTAS["needs_login"],
    CONNECTED: LIFECYCLE_CTAS["connected"],
    SYNCED: LIFECYCLE_CTAS["synced"],
}

SECONDARY_CTA: dict[str, str | None] = {
    WAITING_FOR_EXTENSION: SECONDARY_CTA_EXTENSION_RETRY,
}


@dataclass
class AccountLifecycle:
    state: str
    label: str
    description: str
    color: str
    cta_label: str | None
    secondary_cta_label: str | None
    source_label: str
    show_last_sync: bool
    last_sync_at: str | None
    extracted_field_count: int

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "label": self.label,
            "description": self.description,
            "color": self.color,
            "cta_label": self.cta_label,
            "secondary_cta_label": self.secondary_cta_label,
            "source_label": self.source_label,
            "show_last_sync": self.show_last_sync,
            "last_sync_at": self.last_sync_at,
            "extracted_field_count": self.extracted_field_count,
        }


def _source_label(
    *,
    from_email: bool,
    data_source: str | None,
    in_credentials: bool,
) -> str:
    if from_email:
        return SOURCE_FOUND_FROM_GMAIL
    if data_source == "extension":
        return SOURCE_EXTENSION
    if in_credentials:
        return SOURCE_MANUALLY_ADDED
    return SOURCE_MANUALLY_ADDED


def _meaningful_field_count(account: ProviderAccount | None) -> int:
    if not account or not account.is_synced:
        return 0
    count = 0
    for item in account.normalized_fields or []:
        if not isinstance(item, dict):
            continue
        val = str(item.get("value", "")).strip().lower()
        if val and val not in ("", "—", "–", "-", "n/a", "none", "0", "no data"):
            count += 1
    return count


def resolve_account_lifecycle(
    source: str,
    *,
    in_credentials: bool = False,
    email_added: bool = False,
    from_email: bool = False,
    account: ProviderAccount | None = None,
) -> AccountLifecycle:
    """Derive the unified lifecycle state from persisted account signals."""
    data_source = account.data_source if account else None
    src_label = _source_label(
        from_email=from_email,
        data_source=data_source,
        in_credentials=in_credentials,
    )
    field_count = _meaningful_field_count(account)
    synced_at = account.synced_at if account else None

    # 1. Synced — only when real normalized fields exist
    if account and is_synced(account.normalized_fields, extraction_status=account.extraction_status):
        return AccountLifecycle(
            state=SYNCED,
            label=STATE_LABELS[SYNCED],
            description=STATE_DESCRIPTIONS[SYNCED],
            color=STATE_COLORS[SYNCED],
            cta_label=CTA_LABELS[SYNCED],
            secondary_cta_label=None,
            source_label=src_label,
            show_last_sync=True,
            last_sync_at=synced_at,
            extracted_field_count=field_count,
        )

    conn = (account.connection_status or "") if account else ""
    sync_status = (account.sync_status or "ok") if account else ""

    if in_credentials or account:
        # 2. Needs login — but not when extension already verified a session
        if conn == CONN_NEEDS_LOGIN:
            return _lifecycle(
                NEEDS_LOGIN, src_label, synced_at, field_count,
            )
        if sync_status == "login_required" and conn != CONN_CONNECTED:
            return _lifecycle(
                NEEDS_LOGIN, src_label, synced_at, field_count,
            )

        # 3. Waiting for extension (persisted connection or first-visit stub)
        if conn in (CONNECTING, CONN_WAITING) or sync_status == "needs_first_visit":
            return _lifecycle(
                WAITING_FOR_EXTENSION, src_label, synced_at, field_count,
            )

        # 4. Connected — session verified via extension, no extracted fields
        if conn == CONN_CONNECTED:
            return _lifecycle(CONNECTED, src_label, synced_at, field_count)

        # Registered but indeterminate — treat as waiting, not connected
        if in_credentials:
            return _lifecycle(
                WAITING_FOR_EXTENSION, src_label, synced_at, field_count,
            )

    # 5. Added (email) but not registered
    if email_added:
        return _lifecycle(ADDED, src_label, synced_at, field_count)

    # 6. Discovered from email
    if from_email:
        return _lifecycle(DISCOVERED, src_label, synced_at, field_count)

    # Fallback for manual registration without email context
    if in_credentials:
        return _lifecycle(WAITING_FOR_EXTENSION, src_label, synced_at, field_count)

    return _lifecycle(DISCOVERED, src_label, synced_at, field_count)


def _lifecycle(
    state: str,
    source_label: str,
    synced_at: str | None,
    field_count: int,
) -> AccountLifecycle:
    return AccountLifecycle(
        state=state,
        label=STATE_LABELS[state],
        description=STATE_DESCRIPTIONS[state],
        color=STATE_COLORS[state],
        cta_label=CTA_LABELS.get(state),
        secondary_cta_label=SECONDARY_CTA.get(state),
        source_label=source_label,
        show_last_sync=state == SYNCED,
        last_sync_at=synced_at if state == SYNCED else None,
        extracted_field_count=field_count,
    )


def lifecycle_status_line(lifecycle: AccountLifecycle) -> str:
    """Exact lifecycle label for display on cards."""
    return lifecycle.label


def lifecycle_is_actionable(lifecycle: AccountLifecycle) -> bool:
    return lifecycle.state in (
        DISCOVERED,
        ADDED,
        WAITING_FOR_EXTENSION,
        NEEDS_LOGIN,
        CONNECTED,
    )


def connection_status_for_connect_start() -> str:
    """Persisted status when user clicks Connect (extension-first flow)."""
    return CONN_WAITING


def map_connection_status_to_lifecycle_state(connection_status: str | None) -> str | None:
    """Map raw connection_status column to lifecycle state (non-synced accounts)."""
    if not connection_status:
        return None
    mapping = {
        CONNECTING: WAITING_FOR_EXTENSION,
        CONN_WAITING: WAITING_FOR_EXTENSION,
        CONN_NEEDS_LOGIN: NEEDS_LOGIN,
        CONN_CONNECTED: CONNECTED,
    }
    return mapping.get(connection_status)


def amex_connection_states() -> tuple:
    return AMEX_CONNECTION_STATES
