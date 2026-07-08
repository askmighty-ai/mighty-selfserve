"""Static checks for extension quiescent mode (Phase 1A.5 gate)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = REPO_ROOT / "extension" / "background.js"


def _read_background_js() -> str:
    return BACKGROUND_JS.read_text(encoding="utf-8")


def test_extension_build_identifier_in_logs():
    src = _read_background_js()
    assert "1.4.8-amex-operational-global-overview-entry" in src
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


def test_amex_operational_account_entry_uses_global_overview():
    src = _read_background_js()
    assert "amex:         'https://global.americanexpress.com/overview'" in src


def test_amex_live_session_comparator_present():
    src = _read_background_js()
    assert "runAmexLiveSessionComparison" in src
    assert "collectAmexLiveSessionSnapshot" in src
    assert "live-session-comparison" in src
    assert "_liveSessionComparisonInProgress" in src
    assert "AMEX_LIVE_SESSION_COMPARISON_ENTRY_URLS" in src
    assert "https://global.americanexpress.com/overview" in src


def test_amex_live_session_tab_discovery_helpers_present():
    src = _read_background_js()
    assert "AMEX_LIVE_SESSION_TAB_URL_PATTERNS" in src
    assert "isAmexLoginPageUrlForLiveSessionComparison" in src
    assert "scoreAmexLoggedInTabForLiveSessionComparison" in src
    assert "queryAmexTabsForLiveSessionComparison" in src
    assert "global.americanexpress.com/overview" in src or "global_overview" in src
    assert "live session tab candidate" in src
    assert "live session tab selected" in src
    assert "_amexMrTabEvidenceByTabId" in src


def test_amex_live_session_tab_discovery_url_rules():
    """Mirror live session tab discovery URL rules used in extension/background.js."""

    import re
    from urllib.parse import urlparse

    account_login_re = re.compile(r"/en-us/account/log-?in", re.I)
    login_path_re = re.compile(r"/(log-?in|sign-?in)(/|$|\?)", re.I)

    def is_login(url: str) -> bool:
        u = urlparse(url)
        path = u.path or ""
        host = (u.hostname or "").lower()
        if account_login_re.search(path):
            return True
        if login_path_re.search(path):
            return True
        if host == "global.americanexpress.com" and re.match(r"^/login(/|$|\?)", path, re.I):
            return True
        first = host.split(".")[0]
        if first in {"login", "sso", "auth", "signin", "sign-in", "logon", "authenticate", "identity"}:
            return True
        return False

    def has_private_evidence(url: str, title: str = "") -> bool:
        u = urlparse(url)
        path = (u.path or "").lower()
        host = (u.hostname or "").lower()
        if host == "global.americanexpress.com" and re.search(r"/overview(/|$|\?)", path):
            return True
        if re.search(r"/en-us/account(/|$|\?)", path) and not re.search(r"/log-?in", path):
            return True
        if "membership rewards" in title.lower():
            return True
        return False

    overview = "https://global.americanexpress.com/overview"
    assert not is_login(overview)
    assert has_private_evidence(overview)

    assert is_login("https://www.americanexpress.com/en-us/account/login")
    assert is_login("https://global.americanexpress.com/login")
    assert not has_private_evidence("https://global.americanexpress.com/login")

    assert has_private_evidence(
        "https://www.americanexpress.com/en-us/account/",
        "Membership Rewards | American Express",
    )


def test_live_session_snapshot_uses_injected_collector_function():
    src = _read_background_js()
    assert "func: collectAmexLiveSessionSnapshot" in src
    assert "live session logged-in snapshot started" in src
    assert "live session logged-in snapshot succeeded" in src
    assert "live session comparison payload before POST" in src
    assert "logged_in_side" in src
