"""Account readiness — Connected requires live access + correlated private data."""

from __future__ import annotations

import os
import secrets
import sys
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.account_readiness import (
    CHECKING,
    READY,
    SIGNED_OUT,
    UNVERIFIED,
    extraction_correlates_with_access,
    resolve_account_readiness,
)
from mighty.account_status import (
    NEEDS_LOGIN,
    UP_TO_DATE,
    build_account_status,
    load_all_account_statuses,
)
from mighty.accounts_ui import SECTION_NEEDS_LOGIN, resolve_accounts_section
from mighty.home_state import resolve_home_state
from mighty.login_truth import CurrentAccountAccess
from mighty.provider_account import EXTRACTION_COMPLETE, EXTRACTION_FAILED, ProviderAccount
from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
from mighty.session_access import resolve_product_account_state


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _session(
    provider: str,
    current_access: str,
    *,
    last_verified: str | None = None,
    last_private_data: str | None = None,
    verification_lifecycle: str | None = None,
    verification_id: str | None = None,
    cached: str = "fresh",
) -> CurrentAccountAccess:
    product_next = {
        "connected_now": ("none", ""),
        "checking": ("verifying", ""),
        "signed_out": ("reauthenticate", ""),
        "unknown": ("none", ""),
        "error": ("reauthenticate", ""),
    }
    nxt = product_next[current_access]
    return CurrentAccountAccess(
        provider=provider,
        current_access=current_access,  # type: ignore[arg-type]
        cached_data_state=cached,  # type: ignore[arg-type]
        last_verified=last_verified,
        last_private_data=last_private_data,
        evidence="test",
        source="test",
        next_action_type=nxt[0],  # type: ignore[arg-type]
        next_action_text=nxt[1],
        verification_lifecycle=verification_lifecycle,
        verification_id=verification_id,
    )


def _acct(**kwargs) -> ProviderAccount:
    defaults = dict(
        source="delta",
        sync_status="ok",
        extraction_status=EXTRACTION_COMPLETE,
        normalized_fields=[{"label": "miles", "value": "12000"}],
    )
    defaults.update(kwargs)
    return ProviderAccount(**defaults)


def test_ready_requires_fresh_session_and_correlated_extraction():
    now = _now()
    session_at = _iso(now - timedelta(seconds=30))
    extraction_at = _iso(now - timedelta(seconds=10))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        verification_id="ver-1",
        account=_acct(synced_at=extraction_at),
        extraction_at=extraction_at,
        extraction_access_cycle_id="ver-1",
        now=now,
    )
    assert readiness.state == READY
    assert readiness.status_label == "Connected"
    assert "can see your data" in readiness.status_copy


def test_fresh_session_without_extraction_is_unverified_or_checking():
    now = _now()
    session_at = _iso(now - timedelta(seconds=20))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        account=_acct(
            extraction_status="not_started",
            normalized_fields=[],
            synced_at=None,
        ),
        extraction_at=None,
        now=now,
    )
    assert readiness.state in (UNVERIFIED, CHECKING)
    assert readiness.state != READY


def test_fresh_extraction_without_session_is_unverified():
    now = _now()
    extraction_at = _iso(now - timedelta(seconds=5))
    product = resolve_product_account_state(_session("delta", "unknown"))
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=None,
        account=_acct(synced_at=extraction_at),
        extraction_at=extraction_at,
        last_private_data_at=extraction_at,
        now=now,
    )
    assert readiness.state == UNVERIFIED
    assert readiness.login_required is False


def test_cached_data_plus_fresh_signed_out_is_signed_out():
    now = _now()
    cached_at = _iso(now - timedelta(minutes=2))
    product = resolve_product_account_state(
        _session(
            "delta",
            "signed_out",
            last_verified=_iso(now - timedelta(seconds=10)),
            last_private_data=cached_at,
            cached="fresh",
        )
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=_iso(now - timedelta(seconds=10)),
        account=_acct(synced_at=cached_at),
        extraction_at=cached_at,
        last_private_data_at=cached_at,
        now=now,
    )
    assert readiness.state == SIGNED_OUT
    assert readiness.status_label == "Sign in required"
    assert readiness.cached_data_label is not None
    assert "Last saved data" in readiness.cached_data_label


def test_session_verified_extraction_failed_is_unverified():
    now = _now()
    session_at = _iso(now - timedelta(seconds=15))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        account=_acct(
            extraction_status=EXTRACTION_FAILED,
            normalized_fields=[],
            synced_at=None,
        ),
        extraction_status=EXTRACTION_FAILED,
        now=now,
    )
    assert readiness.state == UNVERIFIED


def test_verification_running_is_checking():
    now = _now()
    product = resolve_product_account_state(
        _session("delta", "checking", verification_lifecycle="running", verification_id="v2")
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        verification_id="v2",
        verification_lifecycle="running",
        now=now,
    )
    assert readiness.state == CHECKING
    assert readiness.status_label == "Checking"


def test_stale_extraction_from_prior_session_does_not_make_ready():
    now = _now()
    session_at = _iso(now - timedelta(seconds=20))
    old_extraction = _iso(now - timedelta(hours=2))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        account=_acct(synced_at=old_extraction),
        extraction_at=old_extraction,
        now=now,
    )
    # Old extraction predates the winning access check.
    assert not extraction_correlates_with_access(
        session_evidence_at=session_at,
        extraction_at=old_extraction,
    )
    assert readiness.state != READY


def test_access_cycle_id_mismatch_rejects_extraction():
    now = _now()
    session_at = _iso(now - timedelta(seconds=10))
    extraction_at = _iso(now - timedelta(seconds=5))
    assert not extraction_correlates_with_access(
        session_evidence_at=session_at,
        extraction_at=extraction_at,
        access_cycle_id="cycle-a",
        extraction_access_cycle_id="cycle-b",
    )


def test_legacy_fields_cannot_produce_ready():
    now = _now()
    product = resolve_product_account_state(_session("delta", "unknown"))
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        account=_acct(
            connection_status="connected",
            sync_status="ok",
            synced_at=_iso(now),
        ),
        extraction_at=_iso(now),
        last_private_data_at=_iso(now),
        now=now,
    )
    assert readiness.state == UNVERIFIED


def test_build_account_status_maps_readiness_to_surfaces():
    now = _now()
    session_at = _iso(now - timedelta(seconds=30))
    extraction_at = _iso(now - timedelta(seconds=5))
    lc = resolve_account_lifecycle(
        "delta",
        in_credentials=True,
        account=_acct(synced_at=extraction_at),
    )
    status = build_account_status(
        "delta",
        "Delta",
        lc,
        _acct(synced_at=extraction_at),
        sync_status="ok",
        updating_source=None,
        session_access=_session("delta", "connected_now", last_verified=session_at),
        last_data_refresh=extraction_at,
        extraction_access_cycle_id=None,
    )
    assert status.readiness == READY
    assert status.status == UP_TO_DATE
    assert status.presentation_label == "Connected"

    signed_out = build_account_status(
        "delta",
        "Delta",
        lc,
        _acct(synced_at=extraction_at),
        sync_status="ok",
        updating_source=None,
        session_access=_session(
            "delta",
            "signed_out",
            last_verified=_iso(now),
            last_private_data=extraction_at,
        ),
        last_data_refresh=extraction_at,
    )
    assert signed_out.readiness == SIGNED_OUT
    assert signed_out.status == NEEDS_LOGIN
    assert signed_out.presentation_label == "Sign in required"
    assert signed_out.cached_data_label is not None

    section = resolve_accounts_section(
        lc,
        "ok",
        source="delta",
        session_state="signed_out",
        readiness=SIGNED_OUT,
    )
    assert section == SECTION_NEEDS_LOGIN


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "mighty_readiness.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post(
        "/signup",
        data={
            "email": f"ready_{secrets.token_hex(4)}@test.local",
            "password": "pass12345",
            "_csrf": csrf,
        },
    )
    return c


def _uid(client):
    with client.session_transaction() as sess:
        return sess["user_id"]


def _seed_provider(client, source: str, *, items=None, synced_at=None):
    import app as mighty

    uid = _uid(client)
    now = _iso(_now())
    payload = {
        "items": items
        or [{"label": "balance", "value": "100"}],
        "sync_status": "ok",
        "extraction_status": EXTRACTION_COMPLETE,
    }
    with mighty.app.app_context():
        db = mighty.get_db()
        enc = mighty.encrypt_account_data(uid, payload)
        ts = mighty.iso()
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, "
            "extra_enc, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", ts, ts),
        )
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, "
            "synced_at, connection_status, sync_status, extraction_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                uid,
                source,
                source.title(),
                "✈️",
                "#000",
                enc,
                synced_at or now,
                "connected",
                "ok",
                EXTRACTION_COMPLETE,
            ),
        )
        db.commit()


@pytest.mark.parametrize("provider", ["amex", "delta", "hilton", "united", "marriott"])
def test_providers_share_generic_readiness_contract(provider):
    now = _now()
    session_at = _iso(now - timedelta(seconds=20))
    extraction_at = _iso(now - timedelta(seconds=5))
    product = resolve_product_account_state(
        _session(provider, "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider=provider,
        product=product,
        session_evidence_at=session_at,
        account=_acct(source=provider, synced_at=extraction_at),
        extraction_at=extraction_at,
        now=now,
    )
    assert readiness.state == READY
    assert readiness.provider == provider


def test_customer_surfaces_agree_on_readiness(client):
    import app as mighty

    uid = _uid(client)
    now = _now()
    session_at = now - timedelta(seconds=30)
    extraction_at = now - timedelta(seconds=5)
    _seed_provider(client, "delta", synced_at=_iso(extraction_at))
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="delta",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="test connected",
                observed_at=session_at,
                source="test",
                confidence="high",
            ),
        )

        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"delta": "Delta"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    by_source = {a.source: a for a in accounts}
    assert by_source["delta"].readiness == READY
    assert by_source["delta"].presentation_label == "Connected"

    home = resolve_home_state(accounts=accounts, actions=[], freshness_label="")
    assert home.health.up_to_date >= 1

    resp = client.get("/api/account-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    delta = next(a for a in payload["accounts"] if a["source"] == "delta")
    assert delta["readiness"] == READY
    assert delta["status_label"] == "Connected"
    assert delta["status"] == UP_TO_DATE


def test_signed_out_with_cached_data_never_connected_on_api(client):
    import app as mighty

    uid = _uid(client)
    now = _now()
    cached_at = now - timedelta(minutes=2)
    _seed_provider(client, "delta", synced_at=_iso(cached_at))
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="delta",
                state="signed_out",
                evidence_type="login_page",
                evidence_summary="login page",
                observed_at=now - timedelta(seconds=5),
                source="test",
                confidence="high",
            ),
        )

        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"delta": "Delta"},
            login_url_fn=lambda s: "",
        )
    delta = next(a for a in accounts if a.source == "delta")
    assert delta.readiness == SIGNED_OUT
    assert delta.status == NEEDS_LOGIN
    assert delta.presentation_label == "Sign in required"
    assert delta.cached_data_label is not None

    resp = client.get("/api/account-status")
    payload = resp.get_json()
    row = next(a for a in payload["accounts"] if a["source"] == "delta")
    assert row["readiness"] == SIGNED_OUT
    assert row["status_label"] == "Sign in required"
    assert row["status"] != UP_TO_DATE
