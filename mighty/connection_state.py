"""
mighty.connection_state
───────────────────────
Amex-only connection state machine. States persist on account_data.connection_status.

Flow: connecting → waiting_for_extension → connected
"""

from __future__ import annotations

AMEX_SOURCE = "amex"

CONNECTING = "connecting"
WAITING_FOR_EXTENSION = "waiting_for_extension"
CONNECTED = "connected"

AMEX_CONNECTION_STATES = (CONNECTING, WAITING_FOR_EXTENSION, CONNECTED)

STATE_LABELS: dict[str, str] = {
    CONNECTING: "Connecting",
    WAITING_FOR_EXTENSION: "Waiting for Browser Extension",
    CONNECTED: "Connected",
}

# allowed transitions: current → {next states}
_TRANSITIONS: dict[str | None, set[str]] = {
    None: {CONNECTING},
    CONNECTING: {WAITING_FOR_EXTENSION},
    WAITING_FOR_EXTENSION: {CONNECTED},
    CONNECTED: set(),
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
            "items": [],
            "raw_text": "",
        })
        db.execute(
            "INSERT INTO account_data "
            "(user_id, source, display_name, icon, color, data_enc, synced_at, connection_status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (uid, AMEX_SOURCE, display, icon, color, stub, now, None),
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
    """Persist connection_status on account_data (column + encrypted blob)."""
    if target not in AMEX_CONNECTION_STATES:
        raise ValueError(f"unknown amex connection status: {target}")

    _ensure_amex_account(db, uid, iso_fn, encrypt_fn)

    row = db.execute(
        "SELECT data_enc, connection_status FROM account_data WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    ad_data = decrypt_fn(uid, row["data_enc"] or "") if row else {}
    current = _read_current_status(dict(row) if row else None, ad_data)

    if not force and not _can_transition(current, target):
        raise InvalidAmexConnectionTransition(current, target)

    now = iso_fn()
    ad_data["connection_status"] = target
    ad_data["connection_status_at"] = now

    db.execute(
        "UPDATE account_data SET data_enc=?, connection_status=? WHERE user_id=? AND source=?",
        (encrypt_fn(uid, ad_data), target, uid, AMEX_SOURCE),
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


def amex_extension_connected(db, uid: str, *, iso_fn, encrypt_fn, decrypt_fn) -> str:
    return set_amex_connection_status(
        db, uid, CONNECTED,
        iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
    )


def get_amex_connection_status(db, uid: str, *, decrypt_fn) -> dict:
    row = db.execute(
        "SELECT connection_status, data_enc, synced_at FROM account_data "
        "WHERE user_id=? AND source=?",
        (uid, AMEX_SOURCE),
    ).fetchone()
    if not row:
        return {"source": AMEX_SOURCE, "connection_status": None, "label": ""}

    ad_data = decrypt_fn(uid, row["data_enc"] or "")
    status = _read_current_status(dict(row), ad_data)
    return {
        "source": AMEX_SOURCE,
        "connection_status": status,
        "label": state_label(status),
        "synced_at": row["synced_at"],
    }
