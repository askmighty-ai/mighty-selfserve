"""
mighty.adapters.extension
─────────────────────────
Chrome extension as a provider data-source adapter.

The extension verifies provider sessions (connection layer) and may deliver
raw or normalized data via /api/data/sync. It is not the core account model —
see mighty.provider_account.
"""

from __future__ import annotations

ADAPTER_ID = "extension"
DATA_SOURCE = ADAPTER_ID


def report_session_verified(
    db,
    uid: str,
    source: str,
    *,
    session_verified: bool,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
) -> str:
    """Extension confirmed a logged-in provider session."""
    if not session_verified:
        raise ValueError("session_verified required")

    if source == "amex":
        from mighty.connection_state import amex_extension_connected
        from mighty.provider_account import mark_extraction_pending

        status = amex_extension_connected(
            db, uid,
            iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
            session_verified=True,
        )
        mark_extraction_pending(
            db, uid, source,
            encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn, iso_fn=iso_fn,
        )
        return status

    raise ValueError(f"extension session verification not supported for {source!r}")


def report_needs_login(
    db,
    uid: str,
    source: str,
    *,
    iso_fn,
    encrypt_fn,
    decrypt_fn,
) -> str:
    """Extension saw the provider without an authenticated session."""
    if source == "amex":
        from mighty.connection_state import amex_extension_needs_login
        return amex_extension_needs_login(
            db, uid,
            iso_fn=iso_fn, encrypt_fn=encrypt_fn, decrypt_fn=decrypt_fn,
        )
    raise ValueError(f"extension needs-login not supported for {source!r}")


def supports_connection_probe(source: str) -> bool:
    """Providers whose session can be probed by the extension adapter."""
    return source == "amex"
