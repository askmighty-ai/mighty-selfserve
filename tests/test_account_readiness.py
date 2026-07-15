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


def test_session_evidence_alone_cannot_produce_ready():
    now = _now()
    session_at = _iso(now - timedelta(seconds=10))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        account=None,
        extraction_at=None,
        now=now,
    )
    assert readiness.state != READY
    assert readiness.status_label != "Connected"


def test_naive_and_missing_timestamps_cannot_accidentally_ready():
    now = _now()
    # Missing extraction timestamp.
    assert not extraction_correlates_with_access(
        session_evidence_at=_iso(now),
        extraction_at=None,
    )
    # Missing session timestamp.
    assert not extraction_correlates_with_access(
        session_evidence_at=None,
        extraction_at=_iso(now),
    )
    # Naive timestamps normalize to UTC and still compare safely.
    session_naive = (now - timedelta(seconds=30)).replace(tzinfo=None).isoformat()
    extraction_naive = (now - timedelta(seconds=5)).replace(tzinfo=None).isoformat()
    assert extraction_correlates_with_access(
        session_evidence_at=session_naive,
        extraction_at=extraction_naive,
    )
    # Extraction before session (naive) must not correlate.
    assert not extraction_correlates_with_access(
        session_evidence_at=(now - timedelta(seconds=5)).replace(tzinfo=None).isoformat(),
        extraction_at=(now - timedelta(seconds=30)).replace(tzinfo=None).isoformat(),
    )


def test_partial_or_empty_extraction_cannot_produce_ready():
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
            extraction_status=EXTRACTION_COMPLETE,
            normalized_fields=[{"label": "miles", "value": "—"}],
            synced_at=_iso(now),
        ),
        extraction_at=_iso(now),
        now=now,
    )
    assert readiness.state != READY


def test_stale_session_is_unverified_not_checking():
    """Checking requires active verification/extraction — not mere staleness."""
    now = _now()
    product = resolve_product_account_state(_session("delta", "unknown"))
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=_iso(now - timedelta(hours=1)),
        verification_lifecycle="completed",
        account=_acct(synced_at=_iso(now - timedelta(minutes=2))),
        extraction_at=_iso(now - timedelta(minutes=2)),
        now=now,
    )
    assert readiness.state == UNVERIFIED
    assert readiness.state != CHECKING


def test_verification_error_is_unverified_not_sign_in_required():
    """Network/verification failure must not ask the user to sign in."""
    product = resolve_product_account_state(_session("delta", "error"))
    assert product.session_state == "unknown"  # error → unknown / LOGIN_UNKNOWN
    assert product.authentication_state.value == "login_unknown"
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        verification_lifecycle="failed",
    )
    assert readiness.state == UNVERIFIED
    assert readiness.status_label == "Unable to verify"
    assert readiness.login_required is False


def test_timed_out_verification_is_unverified():
    product = resolve_product_account_state(_session("delta", "unknown"))
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        verification_lifecycle="timed_out",
    )
    assert readiness.state == UNVERIFIED
    assert readiness.login_required is False


def test_legacy_payload_without_access_cycle_id_uses_timestamp_correlation():
    now = _now()
    session_at = _iso(now - timedelta(seconds=40))
    extraction_at = _iso(now - timedelta(seconds=10))
    product = resolve_product_account_state(
        _session("delta", "connected_now", last_verified=session_at)
    )
    readiness = resolve_account_readiness(
        provider="delta",
        product=product,
        session_evidence_at=session_at,
        account=_acct(synced_at=extraction_at),
        extraction_at=extraction_at,
        extraction_access_cycle_id=None,  # legacy rows
        now=now,
    )
    assert readiness.state == READY


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
    _seed_provider(client, "amex", synced_at=_iso(extraction_at))
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
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
            display_names={"amex": "American Express"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
    by_source = {a.source: a for a in accounts}
    assert by_source["amex"].readiness == READY
    assert by_source["amex"].presentation_label == "Connected"

    home = resolve_home_state(accounts=accounts, actions=[], freshness_label="")
    assert home.health.up_to_date >= 1

    resp = client.get("/api/account-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    amex = next(a for a in payload["accounts"] if a["source"] == "amex")
    assert amex["readiness"] == READY
    assert amex["status_label"] == "Connected"
    assert amex["status"] == UP_TO_DATE
    assert amex["capability_state"] == "extraction_success"


def test_signed_out_with_cached_data_never_connected_on_api(client):
    import app as mighty

    uid = _uid(client)
    now = _now()
    cached_at = now - timedelta(minutes=2)
    _seed_provider(client, "amex", synced_at=_iso(cached_at))
    with mighty.app.app_context():
        db = mighty.get_db()
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
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
            display_names={"amex": "American Express"},
            login_url_fn=lambda s: "",
        )
    amex = next(a for a in accounts if a.source == "amex")
    assert amex.readiness == SIGNED_OUT
    assert amex.status == NEEDS_LOGIN
    assert amex.presentation_label == "Sign in required"
    assert amex.cached_data_label is not None

    resp = client.get("/api/account-status")
    payload = resp.get_json()
    row = next(a for a in payload["accounts"] if a["source"] == "amex")
    assert row["readiness"] == SIGNED_OUT
    assert row["status_label"] == "Sign in required"
    assert row["status"] != UP_TO_DATE
    assert row["capability_state"] == "signed_out"


def test_delta_cached_data_with_signed_out_agrees_on_every_surface(client):
    """Regression: recent Delta cache + fresh signed_out never shows Connected.

    Expected on Dashboard, Accounts, Account Center, popup, and /api/account-status:
    readiness=signed_out, Sign in required, optional secondary cached-data copy.
    """
    import app as mighty
    from mighty.account_center_ui import build_card_view
    from mighty.account_state import (
        ACCESS_BROWSER_SESSION,
        CONN_CONNECTED,
        DATA_COMPLETE,
        SESSION_EXPIRED,
        AccountState,
        Confidence,
        ConfidenceFactors,
    )
    from mighty.accounts_ui import SECTION_NEEDS_LOGIN, resolve_accounts_section
    from mighty.home_state import resolve_home_state
    from mighty.login_truth import compute_current_account_access_rows
    from mighty.session_access import resolve_product_account_state

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
                evidence_summary="login wall",
                observed_at=now - timedelta(seconds=8),
                source="test",
                confidence="high",
            ),
        )
        access = next(
            r
            for r in compute_current_account_access_rows(
                db, uid, decrypt_account_fn=mighty.decrypt_account_data, providers=["delta"],
            )
            if r.provider == "delta"
        )
        product = resolve_product_account_state(access)
        assert product.session_state == "signed_out"

        accounts, summary = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"delta": "Delta"},
            login_url_fn=lambda s: "https://example.com/delta",
        )

    delta = next(a for a in accounts if a.source == "delta")
    assert delta.readiness == SIGNED_OUT
    assert delta.presentation_label == "Sign in required"
    assert delta.status == NEEDS_LOGIN
    assert delta.cached_data_label and "Last saved data" in delta.cached_data_label
    assert "Connected" not in delta.presentation_label
    assert "Up to date" not in (delta.presentation_label or "")

    # Dashboard health
    home = resolve_home_state(accounts=accounts, actions=[])
    assert home.health.needs_login >= 1
    assert home.health.up_to_date == 0

    # Accounts section
    section = resolve_accounts_section(
        delta_lifecycle := resolve_account_lifecycle(
            "delta",
            in_credentials=True,
            account=_acct(source="delta", synced_at=_iso(cached_at)),
        ),
        "ok",
        source="delta",
        session_state="signed_out",
        readiness=SIGNED_OUT,
    )
    assert section == SECTION_NEEDS_LOGIN
    del delta_lifecycle

    # Account Center card
    state = AccountState(
        user_id=uid,
        provider="delta",
        display_name="Delta",
        category="travel_loyalty",
        access_method=ACCESS_BROWSER_SESSION,
        connection_state=CONN_CONNECTED,  # legacy must not win
        session_health=SESSION_EXPIRED,
        last_verified_at=None,
        data_status=DATA_COMPLETE,
        last_data_refresh=_iso(cached_at),
        observations_available=["miles"],
        field_count=1,
        next_recommended_action=None,
        confidence=Confidence(level="high", score=90, factors=ConfidenceFactors()),
        status_line="legacy up to date",
        is_actionable=False,
        updated_at=_iso(cached_at),
        extraction_status=EXTRACTION_COMPLETE,
        sync_status="ok",
    )
    card = build_card_view(
        state,
        fmt_relative=lambda _x: "2 minutes ago",
        session_access=access,
        account=_acct(source="delta", synced_at=_iso(cached_at)),
    )
    assert card.status_label == "Sign in required"
    assert "Connected" not in card.status_label
    assert "Last saved data" in (card.data_freshness or "") or "2 minutes ago" in (
        card.data_freshness or ""
    )

    # /api/account-status (popup + dashboard poll) — Amex-only customer surface.
    # Non-Amex providers remain via load_all_account_statuses (asserted above).
    resp = client.get("/api/account-status")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert all(a["source"] == "amex" for a in payload["accounts"])
    # Delta is hidden from customer API but still signed_out in load_all.
    assert summary.needs_login_count >= 1
    assert delta.readiness == SIGNED_OUT
    assert delta.status == NEEDS_LOGIN
    assert delta.presentation_label == "Sign in required"
    assert delta.status != UP_TO_DATE


# ── Stale-while-revalidate: preserve ready during background re-verification ──


def _prior_ready_inputs(now: datetime, *, cycle_id: str = "cycle-a"):
    session_at = _iso(now - timedelta(seconds=90))
    extraction_at = _iso(now - timedelta(seconds=60))
    account = _acct(synced_at=extraction_at)
    return session_at, extraction_at, account, cycle_id


@pytest.mark.parametrize(
    "lifecycle",
    ["requested", "running", "session_verified", "extracting"],
)
def test_prior_ready_plus_active_lifecycle_stays_connected(lifecycle):
    """1–4. Prior ready + requested/running/session_verified/extracting → Connected."""
    now = _now()
    session_at, extraction_at, account, cycle_a = _prior_ready_inputs(now)
    # Product may show checking while a later cycle is active.
    product = resolve_product_account_state(
        _session(
            "amex",
            "checking",
            last_verified=session_at,
            verification_lifecycle=lifecycle,
            verification_id="cycle-b",
        )
    )
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        session_evidence_at=session_at,
        verification_id="cycle-b",
        verification_lifecycle=lifecycle,
        account=account,
        extraction_at=extraction_at,
        extraction_access_cycle_id=cycle_a,
        now=now,
    )
    assert readiness.state == READY
    assert readiness.status_label == "Connected"
    assert readiness.background_verification is True
    assert readiness.secondary_label == "Verifying in the background"
    assert readiness.access_cycle_id == cycle_a
    assert readiness.last_confirmed_access_cycle_id == cycle_a


def test_no_prior_ready_plus_running_is_checking():
    """5. No prior ready + running → Checking."""
    now = _now()
    product = resolve_product_account_state(
        _session("amex", "checking", verification_lifecycle="running", verification_id="v1")
    )
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        verification_id="v1",
        verification_lifecycle="running",
        account=_acct(
            extraction_status="not_started",
            normalized_fields=[],
            synced_at=None,
        ),
        extraction_at=None,
        now=now,
    )
    assert readiness.state == CHECKING
    assert readiness.status_label == "Checking"
    assert readiness.background_verification is False


def test_prior_ready_plus_definitive_signed_out_is_immediate():
    """6. Prior ready + definitive signed_out → Sign in required immediately."""
    now = _now()
    session_at, extraction_at, account, cycle_a = _prior_ready_inputs(now)
    product = resolve_product_account_state(
        _session(
            "amex",
            "signed_out",
            last_verified=_iso(now - timedelta(seconds=5)),
            last_private_data=extraction_at,
        )
    )
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        session_evidence_at=_iso(now - timedelta(seconds=5)),
        account=account,
        extraction_at=extraction_at,
        extraction_access_cycle_id=cycle_a,
        last_private_data_at=extraction_at,
        now=now,
    )
    assert readiness.state == SIGNED_OUT
    assert readiness.status_label == "Sign in required"
    assert readiness.login_required is True


def test_prior_ready_plus_inconclusive_inside_grace_stays_connected():
    """7. Prior ready + inconclusive/timeout inside grace → Connected + warning."""
    now = _now()
    session_at, extraction_at, account, cycle_a = _prior_ready_inputs(now)
    product = resolve_product_account_state(_session("amex", "error"))
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        session_evidence_at=session_at,
        verification_lifecycle="timed_out",
        account=account,
        extraction_at=extraction_at,
        extraction_access_cycle_id=cycle_a,
        now=now,
    )
    assert readiness.state == READY
    assert readiness.status_label == "Connected"
    assert readiness.background_verification is True
    assert readiness.secondary_label is not None
    assert readiness.login_required is False


def test_prior_ready_plus_expired_grace_plus_inconclusive_is_unverified():
    """8. Prior ready + expired grace + inconclusive → Unable to verify."""
    from mighty.session_verification import READY_RESULT_GRACE_SECONDS

    now = _now()
    extraction_at = _iso(now - timedelta(seconds=READY_RESULT_GRACE_SECONDS + 60))
    product = resolve_product_account_state(_session("amex", "error"))
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        verification_lifecycle="timed_out",
        account=_acct(synced_at=extraction_at),
        extraction_at=extraction_at,
        extraction_access_cycle_id="cycle-a",
        now=now,
        grace_seconds=READY_RESULT_GRACE_SECONDS,
    )
    assert readiness.state == UNVERIFIED
    assert readiness.status_label == "Unable to verify"
    assert readiness.login_required is False


def test_successful_new_extraction_refreshes_ready_timestamp():
    """9. Successful new correlated extraction refreshes ready timestamp."""
    now = _now()
    old_extraction = _iso(now - timedelta(minutes=10))
    new_session = _iso(now - timedelta(seconds=20))
    new_extraction = _iso(now - timedelta(seconds=5))
    product = resolve_product_account_state(
        _session("amex", "connected_now", last_verified=new_session)
    )
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        session_evidence_at=new_session,
        verification_id="cycle-b",
        verification_lifecycle="completed",
        account=_acct(synced_at=new_extraction),
        extraction_at=new_extraction,
        extraction_access_cycle_id="cycle-b",
        now=now,
    )
    assert readiness.state == READY
    assert readiness.extraction_at == new_extraction
    assert readiness.last_confirmed_ready_at == new_extraction
    assert readiness.access_cycle_id == "cycle-b"
    assert readiness.background_verification is False
    assert old_extraction != new_extraction


def test_awaiting_data_never_after_valid_correlated_extraction():
    """13. awaiting data never appears after a valid correlated extraction."""
    now = _now()
    session_at, extraction_at, account, cycle_a = _prior_ready_inputs(now)
    product = resolve_product_account_state(
        _session(
            "amex",
            "checking",
            last_verified=session_at,
            verification_lifecycle="running",
            verification_id="cycle-b",
        )
    )
    readiness = resolve_account_readiness(
        provider="amex",
        product=product,
        session_evidence_at=session_at,
        verification_id="cycle-b",
        verification_lifecycle="running",
        account=account,
        extraction_at=extraction_at,
        extraction_access_cycle_id=cycle_a,
        now=now,
    )
    assert readiness.state == READY
    blob = " ".join(
        filter(
            None,
            [
                readiness.status_label,
                readiness.status_copy,
                readiness.secondary_label,
                readiness.cached_data_label,
            ],
        )
    ).lower()
    assert "awaiting data" not in blob


def test_production_sequence_cycle_a_ready_cycle_b_running_customer_stays_connected(client):
    """Regression: cycle A ready, cycle B starts ~2m later → customer Connected.

    Admin may still expose cycle B as the active lifecycle separately.
    """
    import app as mighty
    from mighty.provider_session_state import SessionEvidence, upsert_provider_session_state
    from mighty.session_verification import (
        ensure_session_verification_tables,
        get_latest_session_verification,
        request_session_verification,
    )

    uid = _uid(client)
    now = _now()
    cycle_a_at = now - timedelta(minutes=2, seconds=10)
    extraction_at = now - timedelta(minutes=2)
    _seed_provider(client, "amex", synced_at=_iso(extraction_at))

    with mighty.app.app_context():
        db = mighty.get_db()
        # Persist extraction access_cycle_id from cycle A.
        row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
            (uid, "amex"),
        ).fetchone()
        data = mighty.decrypt_account_data(uid, row["data_enc"] or "")
        data["access_cycle_id"] = "cycle-a"
        data["extraction_access_cycle_id"] = "cycle-a"
        db.execute(
            "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
            (mighty.encrypt_account_data(uid, data), uid, "amex"),
        )
        upsert_provider_session_state(
            db,
            uid,
            SessionEvidence(
                provider="amex",
                state="connected",
                evidence_type="session_verified",
                evidence_summary="cycle A connected",
                observed_at=cycle_a_at,
                source="test",
                confidence="high",
            ),
        )
        ensure_session_verification_tables(db)
        # Simulate completed cycle A verification row, then start cycle B.
        db.execute(
            """
            INSERT INTO provider_session_verification (
                verification_id, user_id, provider, lifecycle, entry_url,
                requested_at, started_at, completed_at
            ) VALUES (?, ?, 'amex', 'completed', ?, ?, ?, ?)
            """,
            (
                "cycle-a",
                uid,
                "https://global.americanexpress.com/overview",
                _iso(cycle_a_at - timedelta(seconds=30)),
                _iso(cycle_a_at - timedelta(seconds=25)),
                _iso(extraction_at),
            ),
        )
        db.commit()
        # Force a new cycle B (bypass throttle by using older completed request).
        db.execute(
            "UPDATE provider_session_verification SET requested_at=? WHERE verification_id=?",
            (_iso(now - timedelta(minutes=5)), "cycle-a"),
        )
        db.commit()
        cycle_b = request_session_verification(db, uid, "amex", now=now, throttle_seconds=0)
        assert cycle_b is not None
        assert cycle_b.lifecycle == "requested"
        assert cycle_b.verification_id != "cycle-a"

        accounts, _ = load_all_account_statuses(
            uid,
            db,
            decrypt_fn=mighty.decrypt_account_data,
            display_names={"amex": "Amex"},
            login_url_fn=lambda s: f"https://example.com/{s}",
        )
        latest = get_latest_session_verification(db, uid, "amex")

    amex = next(a for a in accounts if a.source == "amex")
    assert latest is not None
    assert latest.verification_id == cycle_b.verification_id
    assert latest.lifecycle == "requested"
    # Customer product remains Connected based on cycle A.
    assert amex.readiness == READY
    assert amex.presentation_label == "Connected"
    assert amex.status == UP_TO_DATE
    assert amex.background_verification is True
    assert amex.access_cycle_id == "cycle-a"
    assert "awaiting data" not in (amex.verification_message or "").lower()

    home = resolve_home_state(accounts=accounts, actions=[], freshness_label="")
    assert home.health.up_to_date >= 1

    resp = client.get("/api/account-status")
    assert resp.status_code == 200
    row = next(a for a in resp.get_json()["accounts"] if a["source"] == "amex")
    assert row["readiness"] == READY
    assert row["status_label"] == "Connected"
    assert row["status"] == UP_TO_DATE
    assert row.get("background_verification") is True
    assert "awaiting data" not in (row.get("status_copy") or "").lower()


def test_ready_revalidation_interval_suppresses_churn_while_preserving_active_reuse():
    """12. No repeated verification jobs while one is active; ready interval suppresses churn."""
    from mighty.provider_session_state import (
        SessionEvidence,
        ensure_provider_session_state_tables,
        upsert_provider_session_state,
    )
    from mighty.session_verification import (
        READY_REVALIDATION_INTERVAL_SECONDS,
        ensure_provider_session_verification_if_stale,
        ensure_session_verification_tables,
        get_latest_session_verification,
        request_session_verification,
    )
    import sqlite3

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    ensure_session_verification_tables(db)
    ensure_provider_session_state_tables(db)
    uid = "u1"
    now = _now()
    # Stale session evidence (past 120s) but recent ready extraction.
    upsert_provider_session_state(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="connected",
            observed_at=now - timedelta(seconds=180),
            source="test",
            confidence="high",
        ),
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS account_data (
            user_id TEXT, source TEXT, synced_at TEXT, extraction_status TEXT
        )
        """
    )
    db.execute(
        "INSERT INTO account_data (user_id, source, synced_at, extraction_status) VALUES (?,?,?,?)",
        (uid, "amex", _iso(now - timedelta(minutes=5)), "complete"),
    )
    db.commit()

    created = ensure_provider_session_verification_if_stale(db, uid, "amex", now=now)
    assert created is None  # within READY_REVALIDATION_INTERVAL_SECONDS

    # Past revalidation interval → enqueue once.
    old_ready = _iso(now - timedelta(seconds=READY_REVALIDATION_INTERVAL_SECONDS + 30))
    db.execute(
        "UPDATE account_data SET synced_at=? WHERE user_id=? AND source=?",
        (old_ready, uid, "amex"),
    )
    db.commit()
    first = ensure_provider_session_verification_if_stale(db, uid, "amex", now=now)
    assert first is not None
    second = ensure_provider_session_verification_if_stale(db, uid, "amex", now=now)
    assert second is not None
    assert second.verification_id == first.verification_id
    assert get_latest_session_verification(db, uid, "amex").verification_id == first.verification_id
    # Explicit request also reuses active job.
    again = request_session_verification(db, uid, "amex", now=now)
    assert again is not None
    assert again.verification_id == first.verification_id
