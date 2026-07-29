"""Complete American Express Experience — Acceptance Test contracts (AT-00–AT-15).

Automatable slices for lifecycle honesty, Home↔Accounts agreement, Chrome-first
ranking, and narrator non-contradiction. Live Founder walkthroughs remain the
gate for AT-00 / AT-15.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.account_lifecycle import resolve_account_lifecycle
from mighty.account_readiness import UNVERIFIED, AccountReadiness
from mighty.account_status import AccountStatus
from mighty.attention import AttentionClass
from mighty.attention_compiler import (
    WorkerSignal,
    compile_attention_candidates,
    compile_auth_attention,
)
from mighty.auth_truth import (
    ACCESS_BROWSER_SESSION,
    AuthInterruption,
    AuthTruth,
    EvidenceClass,
)
from mighty.authentication_state import AuthenticationState
from mighty.capability_state import CAPABILITY_STATUS_LABELS, CapabilityState
from mighty.customer_account_access import (
    BG_UNSUPPORTED_DATA,
    build_customer_account_access_view,
    resolve_meaning,
    resolve_status_label,
)
from mighty.home_state import _waiting_row_label
from mighty.provider_account import EXTRACTION_NO_ACCOUNT_DATA
from mighty import user_copy


def _amex_auth_truth(*, needs_human: bool = True) -> AuthTruth:
    now = datetime.now(timezone.utc).isoformat()
    return AuthTruth(
        schema_version=1,
        user_id="u1",
        provider="amex",
        state=AuthenticationState.SIGNED_OUT,
        access_method=ACCESS_BROWSER_SESSION,
        evidence_class=EvidenceClass.DEFINITIVE,
        evidence_source="access_manager",
        evidence_id=None,
        observed_at=now,
        projected_at=now,
        interruption=AuthInterruption.LOGIN,
        interruption_expected=True,
        needs_human=needs_human,
        needs_human_reason="login" if needs_human else None,
        evidence_age_seconds=10.0,
        stale=False,
    )


def _amex_unverified_connected_readiness(**overrides) -> AccountReadiness:
    payload = dict(
        provider="amex",
        state=UNVERIFIED,
        status_label="Unable to verify",
        status_copy=user_copy.READINESS_COPY_UNVERIFIED,
        presentation_key="unknown",
        canonical_status="unverified",
        login_required=False,
        session_state="connected",
        access_cycle_id="cycle-1",
        session_evidence_at=None,
        extraction_at=None,
        extraction_ok=False,
        extraction_correlated=False,
        verification_id="cycle-1",
    )
    payload.update(overrides)
    return AccountReadiness(**payload)


def test_at05_nested_status_label_not_unable_to_verify():
    """AT-05 / AT-08: nested customer_access matches unsupported-data lifecycle."""
    view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=_amex_unverified_connected_readiness(),
        discovered_from="Manual add",
        verification_lifecycle="completed",
        extraction_status=EXTRACTION_NO_ACCOUNT_DATA,
    )
    honest = CAPABILITY_STATUS_LABELS[CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA]
    assert view.background_work == BG_UNSUPPORTED_DATA
    assert view.private_data_state == "unsupported"
    assert view.status_label == honest
    assert view.status_label != user_copy.ACCOUNTS_STATUS_NOT_VERIFIED
    assert "Unable to verify" not in view.status_label
    assert view.meaning == user_copy.ACCESS_MEANING_NO_ACCOUNT_DATA
    assert "has not read account data yet" not in view.meaning


def test_at05_resolve_status_label_helpers():
    honest = CAPABILITY_STATUS_LABELS[CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA]
    assert (
        resolve_status_label(
            readiness=UNVERIFIED,
            live_access="Connected",
            background_work=BG_UNSUPPORTED_DATA,
            private_data_state="unsupported",
        )
        == honest
    )
    assert (
        resolve_meaning(
            live_access="Connected",
            private_data_state="unsupported",
            readiness=UNVERIFIED,
        )
        == user_copy.ACCESS_MEANING_NO_ACCOUNT_DATA
    )


def test_at08_waiting_row_prefers_presentation_label():
    """Home waiting chip uses top-level presentation, not nested contradiction."""
    honest = CAPABILITY_STATUS_LABELS[CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA]
    view = build_customer_account_access_view(
        provider="amex",
        display_name="American Express",
        readiness=_amex_unverified_connected_readiness(),
        discovered_from="Manual add",
        verification_lifecycle="completed",
        extraction_status=EXTRACTION_NO_ACCOUNT_DATA,
    )
    acct = AccountStatus(
        source="amex",
        display_name="American Express",
        status="unverified",
        presentation_key="unknown",
        presentation_label=honest,
        last_successful_sync_at=None,
        current_attempt_at=None,
        last_error=None,
        user_action_label=None,
        user_action_url=None,
        login_required=False,
        readiness=UNVERIFIED,
        customer_access=view,
    )
    assert _waiting_row_label(acct) == honest
    assert _waiting_row_label(acct) == view.status_label


def test_at13_chrome_ranks_over_amex_auth_when_worker_missing():
    """AT-13: missing Chrome + Amex needs-login → Chrome SYSTEM is the candidate set winner."""
    truth = _amex_auth_truth()
    auth_item = compile_auth_attention(truth)
    assert auth_item is not None
    worker = WorkerSignal(
        user_id="u1",
        installed=False,
        reachable=False,
        last_seen_at=None,
        version=None,
        update_required=False,
        enrolled_account_count=1,
    )
    items = compile_attention_candidates(
        auth_truths=(truth,),
        worker_signal=worker,
    )
    classes = {i.attention_class for i in items}
    providers = {
        i.provider for i in items if i.attention_class == AttentionClass.AUTH_BLOCKER
    }
    assert AttentionClass.SYSTEM in classes
    assert "amex" not in providers
    assert all(
        not (
            i.attention_class == AttentionClass.AUTH_BLOCKER
            and (i.provider or "") == "amex"
        )
        for i in items
    )


def test_at13_amex_auth_emits_when_worker_healthy():
    truth = _amex_auth_truth()
    worker = WorkerSignal(
        user_id="u1",
        installed=True,
        reachable=True,
        last_seen_at=datetime.now(timezone.utc).isoformat(),
        version="1.0.0",
        update_required=False,
        enrolled_account_count=1,
    )
    items = compile_attention_candidates(
        auth_truths=(truth,),
        worker_signal=worker,
    )
    assert any(
        i.attention_class == AttentionClass.AUTH_BLOCKER and i.provider == "amex"
        for i in items
    )
    assert not any(i.attention_class == AttentionClass.SYSTEM for i in items)


def test_at13_narrator_skips_overlay_when_chrome_primary():
    from dataclasses import dataclass, replace

    from mighty.journey_narrative import apply_journey_narrative_to_projection

    @dataclass
    class _Card:
        headline: str
        body: str
        cta_label: str
        cta_url: str
        provider: str | None = "amex"

    @dataclass
    class _Proj:
        featured: _Card
        story_kind: str = "handoff"
        answer: str = "x"
        narrative_beat: str | None = None

    class _Db:
        def execute(self, *a, **k):
            class _R:
                def fetchall(self):
                    return []

                def fetchone(self):
                    return None

            return _R()

        def commit(self):
            return None

    card = _Card(
        headline="Set up Chrome",
        body="Install Mighty in Chrome",
        cta_label="Set up Mighty in Chrome",
        cta_url="/extension-setup",
    )
    proj = _Proj(featured=card)
    out = apply_journey_narrative_to_projection(
        proj,
        _Db(),
        "user-1",
        still_needs_user=True,
        provider_key="amex",
        provider_display="American Express",
        verification_active=False,
        terminal_ok=False,
    )
    assert out.featured.cta_url == "/extension-setup"
    assert out.featured.headline == "Set up Chrome"
    assert out.narrative_beat is None


def test_accounts_cta_html_amex_unsupported(monkeypatch, tmp_path):
    """AT-05/08: Accounts shows Open Amex CTA for unsupported-data."""
    db_path = str(tmp_path / "amex_cta.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    lc = resolve_account_lifecycle("amex", from_email=True)
    html = mighty._accounts_primary_cta_html(
        lc,
        "amex",
        "American Express",
        "ok",
        session_state="connected",
        login_required=False,
        private_data_state="unsupported",
        background_work=BG_UNSUPPORTED_DATA,
    )
    assert html
    assert 'data-amex-lifecycle="unsupported-data"' in html
    assert "American Express" in html or "Visit" in html or "Open" in html


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import secrets

    db_path = str(tmp_path / "mighty_complete_amex.db")
    monkeypatch.setenv("DATABASE_PATH", db_path)
    import app as mighty

    mighty.DATABASE = db_path
    monkeypatch.setattr(mighty, "_rate_limit", lambda *a, **k: True)
    with mighty.app.app_context():
        mighty.init_db()
    mighty.app.config["TESTING"] = True
    c = mighty.app.test_client()
    email = f"amex_at_{secrets.token_hex(4)}@test.local"
    c.get("/signup")
    with c.session_transaction() as sess:
        csrf = sess["_csrf"]
    c.post("/signup", data={"email": email, "password": "pass12345", "_csrf": csrf})
    return c


def test_at05_api_nested_label_after_no_qualifying(client):
    """AT-05: /api/account-status nested status_label is not Unable to verify."""
    from tests.test_amex_value_pipeline_lb import _probe, _seed_amex, _uid
    from mighty.provider_access_manager import (
        complete_provider_access_check,
        request_provider_access_check,
    )
    from mighty.provider_account import EXTRACTION_PENDING
    import app as mighty

    uid = _uid(client)
    with mighty.app.app_context():
        db = mighty.get_db()
        _seed_amex(mighty, uid)
        db.execute(
            "UPDATE account_data SET extraction_status=? WHERE user_id=? AND source=?",
            (EXTRACTION_PENDING, uid, "amex"),
        )
        db.commit()
        verification = request_provider_access_check(db, uid, "amex")
        vid = verification.verification_id
        complete_provider_access_check(db, uid, _probe(), verification_id=vid)
        api_key = db.execute(
            "SELECT api_key FROM users WHERE id=?", (uid,)
        ).fetchone()["api_key"]

    assert client.post(
        "/api/extension/amex/no-qualifying-private-data",
        headers={"X-Mighty-Key": api_key},
        json={
            "verification_id": vid,
            "extraction_attempted": True,
            "extraction_reason": "no_publishable_widgets",
        },
    ).status_code == 200

    r = client.get("/api/account-status")
    assert r.status_code == 200
    body = r.get_json()
    accounts = body.get("accounts") or body.get("account_statuses") or []
    if isinstance(body.get("accounts"), dict):
        accounts = list(body["accounts"].values())
    amex = next(
        (a for a in accounts if isinstance(a, dict) and a.get("source") == "amex"),
        None,
    )
    assert amex is not None
    access = amex.get("customer_access") or {}
    honest = CAPABILITY_STATUS_LABELS[CapabilityState.LOGGED_IN_NO_ACCOUNT_DATA]
    assert access.get("status_label") == honest
    assert access.get("status_label") != "Unable to verify"
    assert (
        amex.get("presentation_label") == honest
        or amex.get("status_label") == honest
    )
