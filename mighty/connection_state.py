"""
mighty.connection_state
───────────────────────
Amex connection state machine (connection_status on provider accounts).

Flow:
  connecting → waiting_for_extension → needs_login | connected
  needs_login → connected (after verified login)
  connected → needs_login (session lost)

Connection is separate from extraction. Synced means normalized fields exist
(from any adapter) — see mighty.provider_account.
"""

from __future__ import annotations

from mighty.provider_account import (
    EXTRACTION_NOT_STARTED,
    EXTRACTION_PENDING,
    has_normalized_data,
    is_synced,
    load_provider_account,
    persist_provider_state,
)

AMEX_SOURCE = "amex"

CONNECTING = "connecting"
WAITING_FOR_EXTENSION = "waiting_for_extension"
NEEDS_LOGIN = "needs_login"
CONNECTED = "connected"

AMEX_CONNECTION_STATES = (
    CONNECTING,
    WAITING_FOR_EXTENSION,
    NEEDS_LOGIN,
    CONNECTED,
)

STATE_LABELS: dict[str, str] = {
    CONNECTING: "Connecting",
    WAITING_FOR_EXTENSION: "Waiting for verification",
    NEEDS_LOGIN: "Sign in to Amex",
    CONNECTED: "Connected",
}

STATE_STATUS_LABELS: dict[str, str] = {
    CONNECTING: "Setting up…",
    WAITING_FOR_EXTENSION: "Awaiting verification",
    NEEDS_LOGIN: "Sign in required",
    CONNECTED: "Connected — awaiting data",
}

_CONNECTION_EXTRACTION: dict[str, str] = {
    CONNECTING: EXTRACTION_NOT_STARTED,
    WAITING_FOR_EXTENSION: EXTRACTION_NOT_STARTED,
    NEEDS_LOGIN: EXTRACTION_NOT_STARTED,
    CONNECTED: EXTRACTION_PENDING,
}

_TRANSITIONS: dict[str | None, set[str]] = {
    None: {CONNECTING},
    CONNECTING: {WAITING_FOR_EXTENSION},
    WAITING_FOR_EXTENSION: {NEEDS_LOGIN, CONNECTED},
    NEEDS_LOGIN: {CONNECTED},
    CONNECTED: {NEEDS_LOGIN},
}


class InvalidAmexConnectionTransition(Exception):
    """Raised when a connection_status transition is not allowed."""

    def __init__(self, current: str | None, target: str):
        self.current = current
        self.target = target
        super().__init__(f"cannot transition amex connection from {current!r} to {target!r}")


def state_label(status: str | None) -> str:
    if not status:
        return ""
    return STATE_LABELS.get(status, status.replace("_", " ").title())


def status_line_label(status: str | None) -> str:
    if not status:
        return ""
    return STATE_STATUS_LABELS.get(status, state_label(status))


def amex_has_meaningful_items(items: list | None) -> bool:
    return has_normalized_data(items)


def amex_show_as_synced(items: list | None) -> bool:
    return is_synced(items)


def _can_transition(current: str | None, target: str) -> bool:
    allowed = _TRANSITIONS.get(current if current else None, set())
    return target in allowed


def _read_current_status(row: dict | None, ad_data: dict) -> str | None:
    col = (row or {}).get("connection_status") or ""
    if col:
        return col
    return ad_data.get("connection_status") or None


def _ensure_amex_account(db, uid: str, iso_fn, encrypt_fn) -> None:
    """Create credential + account_data rows for Amex if missing."""
    display, icon, color = "American Express", "💳", "#e8f0fe"
    now = iso_fn()

    existing = db.execute(
        "SELECT created_at FROM account_credentials WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO account_credentials "
            "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, AMEX_SOURCE, "", "", "", now, now),
        )

    ad_row = db.execute(
        "SELECT user_id FROM account_data WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not ad_row:
        stub = encrypt_fn(uid, {
            "sync_status": "needs_first_visit",
            "extraction_status": EXTRACTION_NOT_STARTED,
            "items": [],
            "raw_text": "",
        })
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, "
            "connection_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (uid, AMEX_SOURCE, display, icon, color, stub, "",
             None, EXTRACTION_NOT_STARTED),
        )


def set_amex_connection_status(
    db,
    uid: str,
    target: str,
    *,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
    force: bool = False,
) -> str:
    """Persist connection_status on the provider account."""
    if target not in AMEX_CONNECTION_STATES:
        raise ValueError(f"unknown amex connection status: {target}")

    _ensure_amex_account(db, uid, iso_fn, encrypt_fn)

    row = db.execute(
        "SELECT data_enc, connection_status, extraction_status FROM account_data "
        "WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    ad_data = decrypt_fn(uid, row["data_enc"] or "") if row else {}
    current = _read_current_status(dict(row) if row else None, ad_data)

    if not force and not _can_transition(current, target):
        raise InvalidAmexConnectionTransition(current, target)

    extraction = _CONNECTION_EXTRACTION.get(target, EXTRACTION_NOT_STARTED)
    if has_normalized_data(ad_data.get("items") or ad_data.get("ai_items")):
        from mighty.provider_account import EXTRACTION_COMPLETE
        extraction = EXTRACTION_COMPLETE

    persist_provider_state(
        db, uid, AMEX_SOURCE, ad_data,
        encrypt_fn=encrypt_fn,
        connection_status=target,
        extraction_status=extraction,
        iso_fn=iso_fn,
    )
    db.commit()
    return target


def start_amex_connect(db, uid: str, *, iso_fn, encrypt_fn, decrypt_fn) -> str:
    """Begin connect flow: email added + account registered → connecting."""
    db.execute(
        "UPDATE email_suggestions SET added=1 WHERE user_id=? AND site_key=?",
        (uid, AMEX_SOURCE),
    )
    db.commit()
    return set_amex_connection_status(
        db, uid, CONNECTING, iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
        force=True,
    )


def advance_amex_to_waiting(db, uid: str, *, iso_fn, encrypt_fn, decrypt_fn) -> str:
    return set_amex_connection_status(
        db, uid, WAITING_FOR_EXTENSION,
        iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
    )


def amex_extension_needs_login(db, uid: str, *, iso_fn, encrypt_fn, decrypt_fn) -> str:
    return set_amex_connection_status(
        db, uid, NEEDS_LOGIN,
        iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
    )


def amex_extension_connected(
    db,
    uid: str,
    *,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
    session_verified: bool = False,
) -> str:
    if not session_verified:
        raise ValueError("session_verified required to mark Amex connected")
    return set_amex_connection_status(
        db, uid, CONNECTED,
        iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
    )


def get_amex_connection_status(db, uid: str, *, decrypt_fn) -> dict:
    row = db.execute(
        "SELECT * FROM account_data WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not row:
        return {
            "source": AMEX_SOURCE,
            "connection_status": None,
            "extraction_status": EXTRACTION_NOT_STARTED,
            "label": "",
            "status_line": "",
            "is_synced": False,
            "show_synced": False,
        }

    account = load_provider_account(uid, dict(row), decrypt_fn=decrypt_fn)
    status = account.connection_status if account else None
    return {
        "source": AMEX_SOURCE,
        "connection_status": status,
        "extraction_status": account.extraction_status if account else EXTRACTION_NOT_STARTED,
        "data_source": account.data_source if account else None,
        "label": state_label(status),
        "status_line": status_line_label(status),
        "synced_at": account.synced_at if account else None,
        "is_synced": account.is_synced if account else False,
        "show_synced": account.is_synced if account else False,
    }
