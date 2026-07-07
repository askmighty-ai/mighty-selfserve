"""Static checks for extension quiescent mode (Phase 1A.5 gate)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = REPO_ROOT / "extension" / "background.js"


def _read_background_js() -> str:
    return BACKGROUND_JS.read_text(encoding="utf-8")


def test_extension_build_identifier_in_logs():
    src = _read_background_js()
    assert "1.4.3-amex-bootstrap-trace" in src
    assert "background.js loaded — version" in src


def test_startup_uses_run_sync_if_allowed_not_raw_run_sync():
    src = _read_background_js()
    assert "setTimeout(() => runSyncIfAllowed('install-reload')" in src
    assert "setTimeout(() => runSyncIfAllowed('browser-startup')" in src
    assert "runSyncIfAllowed('sync-alarm')" in src
    assert "runSyncIfAllowed('sync_now')" in src
    assert "runSyncIfAllowed('extension-setup')" in src
    assert "setTimeout(() => runSync()," not in src
    # runSync may only be invoked from runSyncIfAllowed
    assert src.count("return runSync();") == 1


def test_config_disabled_log_message():
    src = _read_background_js()
    assert (
        "[Mighty] automatic navigation disabled by server config — "
        "skipping automatic probes and sync"
    ) in src


def test_tab_wrappers_block_when_navigation_disabled():
    src = _read_background_js()
    assert "_providerTabBlocked" in src
    assert "MANUAL_PROBE_TAB_REASON" in src
    assert "_logAutomaticNavigationDisabled(`tab create reason=${reason}`)" in src
    assert "_logAutomaticNavigationDisabled(`tab update reason=${reason}`)" in src


def test_automatic_probe_hard_guard():
    src = _read_background_js()
    assert "_logAutomaticNavigationDisabled(`automatic_probe ${provider}`)" in src
    assert "_logAutomaticNavigationDisabled('runProviderAccessProbes')" in src


def test_sync_and_discovery_hard_guards():
    src = _read_background_js()
    assert "_logAutomaticNavigationDisabled('runSync')" in src
    assert "_logAutomaticNavigationDisabled(`runSyncIfAllowed trigger=${trigger}`)" in src
    assert "_logAutomaticNavigationDisabled(`crawlAccount ${account.source}`)" in src
    assert "_logAutomaticNavigationDisabled(`syncSingleAccount ${source}`)" in src
    assert "_logAutomaticNavigationDisabled('runAmexExtraction')" in src


def test_manual_probe_exempt_from_tab_block():
    src = _read_background_js()
    assert (
        "createProviderTab(entry, { active: false }, MANUAL_PROBE_TAB_REASON)" in src
    )
    assert "if (reason === MANUAL_PROBE_TAB_REASON) return false;" in src


def test_manual_runner_still_uses_manual_probe_reason():
    src = _read_background_js()
    assert "createProviderTab(entry, { active: false }, MANUAL_PROBE_TAB_REASON)" in src
    assert "runManualProviderAccessProbe" in src
    assert "classifierStartedAt" in src


def test_extension_probe_page_diagnostics_in_page_script():
    src = _read_background_js()
    assert "collectPageDiagnostics" in src
    assert "blank_or_unloaded_page" in src
    assert "page_diagnostics" in src


def test_extension_deep_inspect_for_manual_amex():
    src = _read_background_js()
    assert "collectDeepInspectInPage" in src
    assert "DEEP_INSPECT_PROVIDERS" in src
    assert "injectDeepInspectObservers" in src
    assert "deep_inspect" in src
    assert "deepInspect" in src
    assert "cookie_names" in src
    assert "local_storage_keys" in src
    assert "session_storage_keys" in src
    assert "outer_html_preview" in src
    assert "spa_roots" in src
    assert "mutation_timeline" in src
    assert "console_diagnostics" in src
    assert "resource_diagnostics" in src
    assert "framework_detection" in src
    assert "observation_window" in src
    assert "AMEX_MANUAL_PROBE_OBSERVATION_MS = 15000" in src
    assert "auth_network_trace" in src
    assert "__mightyProbeNetworkTrace" in src
    assert "sanitizeProbeUrl" in src
    assert "ReadUserSession\\.v1" in src
    assert "AMEX_MUTATION_OBSERVE_MS = 10000" in src


def test_amex_manual_probe_uses_15_second_observation():
    src = _read_background_js()
    assert "observationMs: deepInspect ? AMEX_MANUAL_PROBE_OBSERVATION_MS : 5000" in src


def test_delta_manual_probe_keeps_5_second_observation():
    src = _read_background_js()
    assert "waitForProbePageStability(tab.id, {" in src
    assert "DEEP_INSPECT_PROVIDERS.has(provider)" in src or "deepInspect ? AMEX_MANUAL_PROBE_OBSERVATION_MS : 5000" in src


def test_automatic_probe_still_uses_default_wait():
    src = _read_background_js()
    assert "await waitForProbePageStability(tab.id);" in src


def test_prefetch_config_on_startup():
    src = _read_background_js()
    assert "prefetchAutomaticProbesConfig()" in src
    assert "server config: automatic_probes_enabled=" in src


def test_amex_bootstrap_trace_runner_present():
    src = _read_background_js()
    assert "runAmexBootstrapTrace" in src
    assert "AMEX_BOOTSTRAP_TRACE_MS = 20000" in src
    assert "collectBootstrapTraceInPage" in src
    assert "bootstrap-trace" in src
    assert "_bootstrapTraceInProgress" in src
