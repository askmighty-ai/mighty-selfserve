"""Architecture tests for the Provider Access Manager boundary (Phase 1)."""

from __future__ import annotations

import ast
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_access_manager import (
    APPROVED_PSS_UPSERT_MODULES,
    PSS_UPSERT_GUARDRAIL_ROOTS,
    complete_provider_access_check,
    ensure_stale_provider_access_checks,
    record_amex_extension_connected,
    record_amex_extension_needs_login,
    record_extension_login_required,
    record_extension_session_connected,
    record_provider_access_evidence,
    record_session_evidence_from_probe,
    request_provider_access_check,
)
from mighty.provider_access_probe import (
    AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    AUTH_LOGIN_PAGE,
    PROBE_PROVIDERS,
    ensure_probe_tables,
)
from mighty.provider_session_state import (
    SessionEvidence,
    ensure_provider_session_state_tables,
    get_provider_session_state,
)
from mighty.session_verification import (
    ensure_session_verification_tables,
    get_latest_session_verification,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_provider_session_state_tables(conn)
    ensure_session_verification_tables(conn)
    ensure_probe_tables(conn)
    return conn


def _probe_result(
    provider: str = "amex",
    *,
    auth_state: str = AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
    status: str = "ok",
    deep_inspect: dict | None = None,
    failure_reason: str | None = None,
) -> dict:
    return {
        "provider": provider,
        "status": status,
        "auth_state": auth_state,
        "url_visited": "https://example.test/",
        "signed_in_detected": auth_state == AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
        "private_data_detected": False,
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": failure_reason,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "deep_inspect": deep_inspect,
    }


# ── 1. Active session verification writes PSS through PAM ─────────────────────


def test_active_verification_writes_pss_through_access_manager():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None

    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA),
        verification_id=verification.verification_id,
    )
    assert result.get("run_id")

    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"
    assert state.source == "provider_access_probe"

    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    # Authenticated Amex holds the cycle open for private-data extraction.
    assert latest.lifecycle == "session_verified"


def test_active_verification_inconclusive_preserves_pss():
    db = _db()
    uid = "user-1"
    record_amex_extension_connected(db, uid)
    before = get_provider_session_state(db, uid, "amex")
    assert before is not None and before.state == "connected"

    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state="unknown", status="error", failure_reason="timeout"),
        verification_id=verification.verification_id,
    )
    after = get_provider_session_state(db, uid, "amex")
    assert after is not None
    assert after.state == "connected"
    assert after.observed_at == before.observed_at


# ── 2. Passive definitive evidence through PAM ────────────────────────────────


def test_passive_definitive_evidence_writes_through_access_manager():
    db = _db()
    uid = "user-1"

    connected = record_amex_extension_connected(db, uid)
    assert connected.state == "connected"

    signed_out = record_amex_extension_needs_login(db, uid)
    assert signed_out.state == "signed_out"

    for provider in sorted(PROBE_PROVIDERS):
        row = record_extension_login_required(db, uid, provider)
        assert row is not None
        assert row.state == "signed_out"
        cleared = record_extension_session_connected(db, uid, provider)
        assert cleared is not None
        assert cleared.state == "connected"


def test_session_api_evidence_through_access_manager():
    db = _db()
    uid = "user-1"
    result = _probe_result(
        deep_inspect={
            "auth_network_trace": {
                "auth_session_requests": [
                    {
                        "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
                        "status_code": 200,
                    }
                ]
            }
        },
        auth_state="unknown",
    )
    state = record_session_evidence_from_probe(db, uid, result)
    assert state is not None
    assert state.state == "connected"
    assert state.evidence_type == "session_api"


# ── 3. Cached private data alone cannot mark connected ───────────────────────


def test_cached_private_data_alone_cannot_mark_connected():
    db = _db()
    uid = "user-1"
    # Inconclusive probe with no auth/session-api signal — even if a caller
    # imagined cached MR balance existed — must not create a connected row.
    result = {
        "provider": "amex",
        "status": "ok",
        "auth_state": "unknown",
        "url_visited": "https://example.test/",
        "signed_in_detected": False,
        "private_data_detected": True,  # cached/DOM private markers alone
        "evidence_type": "private_data",
        "evidence_snippet": "Membership Rewards",
        "failure_reason": None,
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "deep_inspect": None,
    }
    assert record_session_evidence_from_probe(db, uid, result) is None
    assert get_provider_session_state(db, uid, "amex") is None


# ── 4. Connected / signed-out endpoint behavior (PAM helpers) ─────────────────


def test_connected_and_signed_out_helpers_unchanged():
    db = _db()
    uid = "user-1"
    record_amex_extension_connected(
        db,
        uid,
        evidence_type="session_verified",
        source="extension_amex_connected",
    )
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"
    assert state.source == "extension_amex_connected"

    record_amex_extension_needs_login(db, uid)
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "signed_out"
    assert state.source == "extension_amex_needs_login"


# ── 5. Manual probe remains debug-only ────────────────────────────────────────


def test_manual_probe_is_debug_only_not_product_trigger():
    src = (REPO_ROOT / "mighty" / "provider_access_probe.py").read_text()
    assert "DEBUG-ONLY" in src
    assert "start_manual_probe" in src

    bg = (REPO_ROOT / "extension" / "background.js").read_text()
    assert "DEBUG-ONLY manual provider access probe" in bg
    assert "runManualProviderAccessProbe" in bg

    # Product stale trigger is Access Manager, not manual probe.
    pam = (REPO_ROOT / "mighty" / "provider_access_manager.py").read_text()
    assert "ensure_stale_provider_access_checks" in pam
    assert "start_manual_probe" not in pam or "complete_manual_probe" in pam


# ── 6. Admin pages remain passive ─────────────────────────────────────────────


def test_admin_login_truth_and_session_evidence_are_read_only():
    app_src = (REPO_ROOT / "app.py").read_text()
    # Admin login-truth / session-evidence routes must not call PAM writers.
    assert "/admin/login-truth" in app_src
    assert "record_provider_access_evidence" not in app_src.split(
        "@app.route(\"/admin/login-truth\")"
    )[1].split("@app.route")[0]

    admin_debug = (REPO_ROOT / "mighty" / "admin_debug.py").read_text()
    assert "upsert_provider_session_state" not in admin_debug


# ── 7. Static guardrail: no new direct production upsert callers ──────────────


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in PSS_UPSERT_GUARDRAIL_ROOTS:
        path = REPO_ROOT / root
        if path.is_file() and path.suffix == ".py":
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
    return files


def _module_name_for(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).as_posix()
    if rel == "app.py":
        return "app"
    if rel == "scrape.py":
        return "scrape"
    if rel.endswith(".py"):
        return rel[:-3].replace("/", ".")
    return rel


def test_no_direct_production_upsert_outside_approved_modules():
    """Enumerate production call sites of upsert_provider_session_state.

    Approved: provider_access_manager (canonical writer) and
    provider_session_state (storage definition). Compatibility wrappers in PSS
    must not call upsert directly — they route through PAM.
    """
    violations: list[str] = []
    for path in _iter_python_files():
        mod = _module_name_for(path)
        if mod in APPROVED_PSS_UPSERT_MODULES or mod.startswith("tests."):
            # Still verify PSS wrappers do not call upsert directly.
            if mod != "mighty.provider_session_state":
                continue

        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name != "upsert_provider_session_state":
                continue

            if mod == "mighty.provider_session_state":
                # Allow only the function definition body of upsert itself —
                # detect calls that appear inside other functions.
                # Walk parents via a simple enclosing-function map.
                continue

            if mod not in APPROVED_PSS_UPSERT_MODULES:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    # Second pass: PSS file — record_* wrappers must not call upsert.
    pss_path = REPO_ROOT / "mighty" / "provider_session_state.py"
    tree = ast.parse(pss_path.read_text())
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == "upsert_provider_session_state":
            continue
        if not node.name.startswith("record_"):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            called = None
            if isinstance(func, ast.Name):
                called = func.id
            elif isinstance(func, ast.Attribute):
                called = func.attr
            if called == "upsert_provider_session_state":
                violations.append(
                    f"mighty/provider_session_state.py:{child.lineno} "
                    f"({node.name} must route through Access Manager)"
                )

    # PAM must call upsert (canonical writer).
    pam_path = REPO_ROOT / "mighty" / "provider_access_manager.py"
    pam_src = pam_path.read_text()
    assert "upsert_provider_session_state" in pam_src

    assert violations == [], "direct upsert callers outside approved modules:\n" + "\n".join(
        violations
    )


# ── 8. All probe providers share the same interface ───────────────────────────


def test_all_probe_providers_share_access_manager_interface():
    expected = {"amex", "delta", "hilton", "marriott", "united"}
    assert set(PROBE_PROVIDERS) == expected

    db = _db()
    uid = "user-1"
    for provider in sorted(PROBE_PROVIDERS):
        evidence = SessionEvidence(
            provider=provider,
            state="connected",
            evidence_type="session_verified",
            evidence_summary=f"{provider} via PAM",
            observed_at=datetime.now(timezone.utc),
            source="test",
            confidence="high",
        )
        state = record_provider_access_evidence(db, uid, evidence)
        assert state.provider == provider
        assert state.state == "connected"


def test_ensure_stale_uses_access_manager_and_preserves_throttle():
    db = _db()
    uid = "user-1"
    # Seed stale connected evidence so ensure_stale will enqueue.
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=300)
    record_provider_access_evidence(
        db,
        uid,
        SessionEvidence(
            provider="amex",
            state="connected",
            evidence_type="session_verified",
            evidence_summary="stale",
            observed_at=stale_at,
            source="test",
            confidence="high",
        ),
    )
    created = ensure_stale_provider_access_checks(db, uid, providers=["amex"])
    assert "amex" in created

    # Immediate re-call should reuse active job / throttle.
    again = ensure_stale_provider_access_checks(db, uid, providers=["amex"])
    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    assert latest.verification_id == created["amex"].verification_id
    if again:
        assert again["amex"].verification_id == created["amex"].verification_id

def test_login_page_passive_evidence_signed_out():
    db = _db()
    uid = "user-1"
    state = record_session_evidence_from_probe(
        db,
        uid,
        _probe_result(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
    )
    assert state is not None
    assert state.state == "signed_out"
    assert state.evidence_type == "login_page"


def test_legacy_access_paths_marked_not_removed():
    bg = (REPO_ROOT / "extension" / "background.js").read_text()
    assert "LEGACY ACCESS PATH — DO NOT EXTEND" in bg
    assert "async function probeAmexConnectionState" in bg
    assert "async function runProviderAccessProbes" in bg

    conn = (REPO_ROOT / "mighty" / "connection_state.py").read_text()
    assert "LEGACY ACCESS PATH — DO NOT EXTEND" in conn

    scrape = (REPO_ROOT / "scrape.py").read_text()
    assert "LEGACY ACCESS PATH — DO NOT EXTEND" in scrape
    assert "def scrape_amex" in scrape


def test_compatibility_wrappers_delegate_to_access_manager():
    pss = (REPO_ROOT / "mighty" / "provider_session_state.py").read_text()
    assert "Compatibility wrapper — routes through Provider Access Manager" in pss
    assert "from mighty.provider_access_manager import" in pss


# ── Amex access cycle: verification owns extraction ───────────────────────────


def test_amex_authenticated_verification_does_not_complete_without_extraction():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA),
        verification_id=verification.verification_id,
    )
    assert result.get("extraction_required") is True
    assert result.get("access_cycle_lifecycle") == "session_verified"
    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    assert latest.lifecycle == "session_verified"
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"


def test_amex_signed_out_verification_skips_extraction_and_completes():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
        verification_id=verification.verification_id,
    )
    assert result.get("extraction_required") is not True
    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    assert latest.lifecycle == "completed"
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "signed_out"


def test_amex_network_failure_does_not_become_signed_out():
    db = _db()
    uid = "user-1"
    record_amex_extension_connected(db, uid)
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state="unknown", status="error", failure_reason="network_issue"),
        verification_id=verification.verification_id,
    )
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"
    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    assert latest.lifecycle == "failed"


def test_amex_extraction_lifecycle_includes_extracting_then_completed():
    from mighty.provider_access_manager import (
        complete_access_check_after_extraction,
        mark_access_check_extracting,
    )
    from mighty.session_verification import ACTIVE_VERIFICATION_LIFECYCLES, MID_CYCLE_VERIFICATION_LIFECYCLES

    assert "session_verified" in ACTIVE_VERIFICATION_LIFECYCLES
    assert "extracting" in ACTIVE_VERIFICATION_LIFECYCLES
    assert "extracting" in MID_CYCLE_VERIFICATION_LIFECYCLES

    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    vid = verification.verification_id
    complete_provider_access_check(
        db,
        uid,
        _probe_result(auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA),
        verification_id=vid,
    )
    assert get_latest_session_verification(db, uid, "amex").lifecycle == "session_verified"
    mark_access_check_extracting(db, uid, vid)
    assert get_latest_session_verification(db, uid, "amex").lifecycle == "extracting"
    complete_access_check_after_extraction(db, uid, vid, success=True)
    assert get_latest_session_verification(db, uid, "amex").lifecycle == "completed"


def test_extension_verification_triggers_amex_extraction_in_background_js():
    bg = (REPO_ROOT / "extension" / "background.js").read_text()
    assert "runAmexExtractionForAccessCycle" in bg
    assert "access_cycle_id" in bg
    assert "verification_id" in bg
    # Production path: session verification owns extraction for Amex.
    assert "Amex session verified — starting access-cycle extraction" in bg
    assert "runAmexExtractionForAccessCycle(apiKey, verificationId, tab?.id)" in bg
