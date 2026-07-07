"""Static checks for extension quiescent mode (Phase 1A.5 gate)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = REPO_ROOT / "extension" / "background.js"


def _read_background_js() -> str:
    return BACKGROUND_JS.read_text(encoding="utf-8")


def test_extension_build_identifier_in_logs():
    src = _read_background_js()
    assert "1.3.7-manual-probe" in src
    assert "background.js loaded — version" in src


def test_startup_uses_run_sync_if_allowed_not_raw_run_sync():
    src = _read_background_js()
    assert "setTimeout(() => runSyncIfAllowed('install-reload')" in src
    assert "setTimeout(() => runSyncIfAllowed('browser-startup')" in src
    assert "runSyncIfAllowed('sync-alarm')" in src
    assert "runSyncIfAllowed('sync_now')" in src
    # install-reload must not call runSync() directly
    assert "setTimeout(() => runSync()," not in src


def test_manual_probe_mode_defers_sync_and_starts_polling():
    src = _read_background_js()
    assert "shouldDeferAutomaticProviderNavigation" in src
    assert "automatic_probes_enabled=false" in src
    assert "ensureManualProbePolling()" in src
    assert "runSync: manual-probe mode — aborting (no provider tabs)" in src


def test_automatic_probe_tab_uses_reason_log():
    src = _read_background_js()
    assert "createProviderTab(entry, { active: false }, 'automatic_probe')" in src
    assert "createProviderTab(entry, { active: false }, 'manual_probe')" in src
    assert "logProviderTabAction" in src
    assert "reason=manual_probe" not in src  # reason is passed as arg, logged dynamically


def test_extraction_tab_uses_reason_log():
    src = _read_background_js()
    assert "createProviderTab(ACCOUNT_ENTRY.amex, { active: false }, 'extraction')" in src


def test_run_provider_access_probes_respects_server_config():
    src = _read_background_js()
    assert "fetchAutomaticProbesEnabled(apiKey)" in src
    assert "automatic probes disabled — manual only" in src
