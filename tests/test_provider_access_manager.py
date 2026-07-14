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
    **extra,
) -> dict:
    url = extra.pop(
        "url_visited",
        (
            "https://www.americanexpress.com/en-us/account/login"
            if auth_state == AUTH_LOGIN_PAGE
            else "https://global.americanexpress.com/overview"
        ),
    )
    payload = {
        "provider": provider,
        "status": status,
        "auth_state": auth_state,
        "url_visited": url,
        "final_url": extra.pop("final_url", url),
        "signed_in_detected": auth_state == AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
        "private_data_detected": extra.pop("private_data_detected", False),
        "evidence_type": "page",
        "evidence_snippet": "test",
        "failure_reason": failure_reason,
        "login_form_present": extra.pop(
            "login_form_present",
            auth_state == AUTH_LOGIN_PAGE,
        ),
        "probed_at": datetime.now(timezone.utc).isoformat(),
        "deep_inspect": deep_inspect,
    }
    payload.update(extra)
    return payload


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
    assert "Amex session verified — attempting extraction" in bg
    assert "runAmexExtractionForAccessCycle(apiKey, verificationId, tab?.id)" in bg
    # Passive needs-login suppressed during active verification.
    assert "needs-login suppressed during active verification" in bg
    assert "_activeSessionVerificationTabId" in bg


# ── Amex verification decision precedence (false signed_out fix) ───────────────


def _session_api_inspect(status: int, *, start_ms: float | None = 100.0) -> dict:
    entry = {
        "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
        "status_code": status,
    }
    if start_ms is not None:
        entry["start_time_ms"] = start_ms
    return {"auth_network_trace": {"auth_session_requests": [entry]}}


def _static_only_inspect() -> dict:
    return {
        "auth_network_trace": {
            "requests": [
                {
                    "url": "https://global.americanexpress.com/header.json",
                    "status_code": 200,
                    "start_time_ms": 10,
                },
                {
                    "url": "https://global.americanexpress.com/footer.json",
                    "status_code": 200,
                    "start_time_ms": 20,
                },
            ]
        }
    }


def test_amex_verification_session_api_200_connected():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state="unknown",
            deep_inspect=_session_api_inspect(200),
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "connected"
    assert decision.session_api_200_detected is True
    assert "200" in decision.decision_reason


def test_amex_verification_authenticated_page_connected():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA),
        verification_id="v1",
    )
    assert decision.final_decision == "connected"
    assert decision.authenticated_page_detected is True


def test_amex_verification_login_page_signed_out():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(auth_state=AUTH_LOGIN_PAGE, failure_reason="login_required"),
        verification_id="v1",
    )
    assert decision.final_decision == "signed_out"
    assert decision.login_url_detected is True


def test_amex_verification_session_api_401_signed_out():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state="unknown",
            deep_inspect=_session_api_inspect(401),
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "signed_out"
    assert decision.session_api_401_or_403_detected is True


def test_amex_verification_static_header_footer_inconclusive():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state="unknown",
            deep_inspect=_static_only_inspect(),
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "inconclusive"
    assert decision.decision_reason == "static_assets_only"


def test_amex_verification_passive_needs_login_ignored():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(auth_state="unknown"),
        verification_id="v1",
        passive_needs_login_seen=True,
    )
    assert decision.final_decision == "inconclusive"
    assert "passive" in decision.decision_reason


def test_amex_verification_newer_login_overrides_connected():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            deep_inspect=_session_api_inspect(200, start_ms=100),
            login_url_observed_at_ms=500,
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "signed_out"


def test_amex_verification_newer_connected_overrides_signed_out():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            deep_inspect=_session_api_inspect(200, start_ms=900),
            login_url_observed_at_ms=100,
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "connected"


def test_amex_verification_conflicting_unordered_inconclusive():
    from mighty.provider_session_state import decide_amex_verification_session

    # Session API 200 without timestamp + login URL without timestamp.
    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            deep_inspect=_session_api_inspect(200, start_ms=None),
        ),
        verification_id="v1",
    )
    assert decision.final_decision == "inconclusive"
    assert decision.decision_reason == "conflicting_evidence_unordered"


def test_amex_inconclusive_skips_extraction():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(
            auth_state="unknown",
            deep_inspect=_static_only_inspect(),
        ),
        verification_id=verification.verification_id,
    )
    assert result.get("extraction_required") is not True
    assert result.get("verification_decision") == "inconclusive"
    latest = get_latest_session_verification(db, uid, "amex")
    assert latest is not None
    assert latest.lifecycle == "failed"
    assert get_provider_session_state(db, uid, "amex") is None


def test_amex_connected_triggers_same_cycle_extraction():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "amex")
    assert verification is not None
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(
            auth_state="unknown",
            deep_inspect=_session_api_inspect(200),
        ),
        verification_id=verification.verification_id,
    )
    assert result.get("extraction_required") is True
    assert result.get("verification_decision") == "connected"
    assert get_latest_session_verification(db, uid, "amex").lifecycle == "session_verified"
    state = get_provider_session_state(db, uid, "amex")
    assert state is not None
    assert state.state == "connected"


def test_amex_verification_decision_log_has_no_secrets():
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
            deep_inspect={
                "auth_network_trace": {
                    "auth_session_requests": [
                        {
                            "url": "https://global.americanexpress.com/api/servicing/v1/ReadUserSession.v1",
                            "status_code": 200,
                            "start_time_ms": 50,
                            "request_body": "secret-token=abc",
                            "response_body": '{"accountNumber":"1234"}',
                            "cookies": "session=secret",
                        }
                    ]
                }
            },
        ),
        verification_id="v-secret-test",
    )
    fields = decision.to_log_fields()
    blob = str(fields).lower()
    assert "secret" not in blob
    assert "accountnumber" not in blob
    assert "cookie" not in blob
    assert "request_body" not in blob
    assert "response_body" not in blob
    assert fields["final_decision"] == "connected"


def test_non_amex_provider_verification_unchanged():
    db = _db()
    uid = "user-1"
    verification = request_provider_access_check(db, uid, "delta")
    # Delta has no entry URL in SESSION_VERIFICATION_ENTRY_URLS — may be None.
    # Exercise derive path via complete without Amex decision.
    result = complete_provider_access_check(
        db,
        uid,
        _probe_result(
            provider="delta",
            auth_state=AUTH_AUTHENTICATED_NO_PRIVATE_DATA,
            url_visited="https://www.delta.com/myprofile/",
        ),
        verification_id="delta-vid-1",
    )
    assert "verification_decision" not in result or result.get("provider") == "delta"
    # Non-Amex connected evidence still writes PSS via legacy derive.
    state = get_provider_session_state(db, uid, "delta")
    assert state is not None
    assert state.state == "connected"
    # Delta is not an extraction-cycle provider.
    assert result.get("extraction_required") is not True


def test_login_chrome_on_amex_overview_not_false_signed_out():
    """Regression: login form chrome on overview must not force signed_out."""
    from mighty.provider_session_state import decide_amex_verification_session

    decision = decide_amex_verification_session(
        _probe_result(
            auth_state=AUTH_LOGIN_PAGE,
            failure_reason="login_required",
            login_form_present=True,
            url_visited="https://global.americanexpress.com/overview",
            final_url="https://global.americanexpress.com/overview",
        ),
        verification_id="v1",
    )
    assert decision.final_decision != "signed_out"
    assert decision.final_decision == "inconclusive"
