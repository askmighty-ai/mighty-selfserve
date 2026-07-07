// Mighty Sync — background service worker
// Opens account pages as background tabs, extracts text, pushes to Railway.
const MIGHTY_EXT_VERSION = '1.3.7-manual-probe';
console.log('[Mighty] background.js loaded — version', MIGHTY_EXT_VERSION);
// Write version to storage so popup.js can display it without DevTools
chrome.storage.local.set({ ext_version: MIGHTY_EXT_VERSION });
// Clear the persistent sync lock on every service worker startup.
// When the SW restarts (extension reload or 5-min idle kill), any in-progress
// sync is already dead — the lock just blocks the next sync forever if left set.
chrome.storage.local.remove(['_sync_lock_ts', 'sync_status']).catch(() => {});

// ── Debug event log ──────────────────────────────────────────────────────────
// Writes timestamped entries to chrome.storage.local['_dbg'].
// Read from the service worker console: chrome.storage.local.get('_dbg', console.log)
// Clear: chrome.storage.local.remove('_dbg')
const DBG_MAX = 200; // keep last N entries
async function _dbg(event, data) {
  try {
    const ts = new Date().toISOString().slice(11,23); // HH:MM:SS.mmm
    const entry = `${ts} ${event}${data ? ' ' + JSON.stringify(data) : ''}`;
    console.log('[MightyDbg]', entry);
    const { _dbg: prev = [] } = await chrome.storage.local.get('_dbg');
    const next = [...prev, entry].slice(-DBG_MAX);
    await chrome.storage.local.set({ _dbg: next });
  } catch (_) {}
}

// Global listeners — fire for ALL tabs/windows, not just sync tabs.
// This lets us see if Chrome is switching tabs outside of sync code.
chrome.tabs.onCreated.addListener(t =>
  _dbg('TAB_CREATED', { id: t.id, windowId: t.windowId, openerTabId: t.openerTabId, url: t.pendingUrl || t.url }));
chrome.tabs.onActivated.addListener(info =>
  _dbg('TAB_ACTIVATED', { tabId: info.tabId, windowId: info.windowId }));
chrome.windows.onFocusChanged.addListener(wId =>
  _dbg('WIN_FOCUS', { windowId: wId }));
chrome.tabs.onRemoved.addListener((id, info) => {
  _dbg('TAB_REMOVED', { id, windowId: info.windowId });
  _newTabsSeen.delete(id); // prune — Set would otherwise grow for every tab ever opened
});
// Track first URL a newly-created tab navigates to — tells us which link/button opened it
const _newTabsSeen = new Set();
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'loading') return;
  if (!changeInfo.url) return;
  if (_newTabsSeen.has(tabId)) return; // only log first navigation
  _newTabsSeen.add(tabId);
  _dbg('TAB_FIRST_URL', { id: tabId, windowId: tab.windowId, url: changeInfo.url });
});

const MIGHTY_URL    = 'https://mighty-selfserve-production.up.railway.app';
const SYNC_ALARM    = 'mighty-sync';
const SYNC_INTERVAL = 60; // minutes (every 1 hour)
const _SKIP_PATH_RE = /\/(book|search|flight-search|find-flights|deals|shop|cart|checkout|help|faq|legal|careers|about|press|sitemap|accessibility|sign-?up|register|login|sign-?in|privacy|cookie|terms|policy|contact|feedback|newsroom|investor)(\b|\/|$)/i;

// ── Provider tab instrumentation (Phase 1A.5) ─────────────────────────────────

function logProviderTabAction(action, url, reason, extra = {}) {
  console.log(`[Mighty Tab] ${action} reason=${reason} url=${url || '(none)'}`, extra);
}

async function createProviderTab(url, opts, reason) {
  logProviderTabAction('create', url, reason, opts || {});
  return chrome.tabs.create({ url, ...(opts || {}) });
}

async function updateProviderTab(tabId, opts, reason) {
  logProviderTabAction('update', opts?.url || '(no url change)', reason, { tabId, ...(opts || {}) });
  return chrome.tabs.update(tabId, opts || {});
}

async function createProviderWindow(opts, reason) {
  logProviderTabAction('window.create', opts?.url || '(no url)', reason, opts || {});
  return chrome.windows.create(opts || {});
}

// ── Path registry helpers ─────────────────────────────────────────────────────

/** Strip personal ID segments from a URL path before reporting to the registry. */
function normalizePath(path) {
  return path
    .split('?')[0].split('#')[0]
    .replace(/\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '/*')
    .replace(/\/\d{5,}/g, '/*')
    .replace(/\/[a-zA-Z0-9]{20,}/g, '/*')
    .replace(/\/$/, '') || '/';
}

/** Report a sync failure to the server so the dashboard can show an actionable message.
 *  Fire-and-forget — never throws. reason: 'no_data' | 'timeout' | 'login_wall' */
function reportSyncFailure(apiKey, source, reason, pipelineRunId = null) {
  // Returns the promise so callers can await before reloading UI
  return fetch(`${MIGHTY_URL}/api/sync/failure`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
    body: JSON.stringify({
      source,
      reason,
      ...(pipelineRunId ? { pipeline_run_id: pipelineRunId } : {}),
    }),
  }).catch(() => {});
}

function createPipelineRunId() {
  return crypto.randomUUID();
}

function pipelineIsoNow() {
  return new Date().toISOString();
}

function countEvidenceMarkers(rawText) {
  return (String(rawText || '').match(/\n\n--- /g) || []).length;
}

function visibleTextCharCount(rawText) {
  const text = String(rawText || '');
  let total = 0;
  const sectionRe = /(?:^|\n\n)--- https?:\/\/[^\n]+ ---\n|=== URL[^\n]*===\n|=== https?:\/\/[^\n]+ ===\n([\s\S]*?)(?=\n\n--- |\n\n=== |\Z)/gm;
  let match;
  while ((match = sectionRe.exec(text)) !== null) {
    total += match[1].trim().length;
  }
  return total;
}

function summarizeEvidenceMarkers(rawText) {
  const text = String(rawText || '');
  const urlSections = (text.match(/(?:^|\n\n)--- https?:\/\/[^\n]+ ---\n/g) || []).length
    + (text.match(/=== https?:\/\/[^\n]+ ===\n/g) || []).length
    + (text.match(/=== URL[^\n]*===/gi) || []).length;
  const apiBlocks = (text.match(/=== API RESPONSE:/gi) || []).length;
  const networkJsonBlocks = (text.match(/=== NETWORK JSON:/gi) || []).length;
  const graphqlBlocks = (text.match(/=== GRAPHQL:/gi) || []).length;
  const embeddedBlocks = (text.match(/=== EMBEDDED STATE:/gi) || []).length;
  const pageMetaBlocks = (text.match(/=== PAGE META:/gi) || []).length;
  const jsonLdBlocks = (text.match(/=== JSON-LD:/gi) || []).length;
  let jsonPayloadChars = 0;
  for (const block of text.match(/=== (?:API RESPONSE|NETWORK JSON|GRAPHQL|EMBEDDED STATE|JSON-LD):[^\n]*===\n([\s\S]*?)(?=\n\n=== |\n\n--- |\Z)/g) || []) {
    jsonPayloadChars += block.length;
  }
  return {
    visible_text_chars: visibleTextCharCount(text),
    url_section_count: urlSections,
    api_response_blocks: apiBlocks,
    network_json_blocks: networkJsonBlocks,
    graphql_blocks: graphqlBlocks,
    embedded_state_blocks: embeddedBlocks,
    page_metadata_blocks: pageMetaBlocks,
    json_ld_blocks: jsonLdBlocks,
    json_payload_chars: jsonPayloadChars,
    measurement: 'extension_reported',
  };
}

function jsonPayloadSize(rawText) {
  return summarizeEvidenceMarkers(rawText).json_payload_chars || 0;
}

function extractTrackedUrls(rawText) {
  const urls = [];
  for (const match of String(rawText || '').matchAll(/\n\n--- (https?:\/\/[^\s]+) ---/g)) {
    if (!urls.includes(match[1])) urls.push(match[1]);
  }
  return urls;
}

function reportPipelineStages(apiKey, { source, runId, stages }) {
  if (!apiKey || !runId || !stages?.length) return Promise.resolve();
  return fetch(`${MIGHTY_URL}/api/pipeline/stages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
    body: JSON.stringify({
      source,
      run_id: runId,
      sync_source: 'extension',
      stages,
    }),
  }).catch(e => console.warn('[Mighty] pipeline stages report failed:', e.message));
}

function createStageTracker(apiKey, source) {
  const runId = createPipelineRunId();
  const stages = [];
  let connStart = null;
  let navStart = null;
  let capStart = null;
  const visitedUrls = [];

  const pushStage = (stage, startedAt, finishedAt, status, failureReason, artifacts) => {
    stages.push({
      stage,
      started_at: startedAt,
      finished_at: finishedAt,
      status,
      ...(failureReason ? { failure_reason: failureReason } : {}),
      artifacts: artifacts || {},
    });
  };

  return {
    runId,
    visitedUrls,
    startConnection() { connStart = pipelineIsoNow(); },
    finishConnection({
      success = true,
      failureReason = null,
      sessionVerified = null,
      loginDetectionMethod = null,
    } = {}) {
      const finishedAt = pipelineIsoNow();
      pushStage(
        'connection',
        connStart || finishedAt,
        finishedAt,
        success ? 'success' : 'failed',
        failureReason,
        {
          ...(sessionVerified != null ? { session_verified: sessionVerified } : {}),
          ...(loginDetectionMethod ? { login_detection_method: loginDetectionMethod } : {}),
        },
      );
    },
    startNavigation() { navStart = pipelineIsoNow(); },
    noteUrl(url) {
      if (url && !visitedUrls.includes(url)) visitedUrls.push(url);
    },
    finishNavigation({ success = true, failureReason = null } = {}) {
      const finishedAt = pipelineIsoNow();
      pushStage(
        'navigation',
        navStart || finishedAt,
        finishedAt,
        success ? 'success' : 'failed',
        failureReason,
        {
          pages_visited: visitedUrls.length,
          urls: visitedUrls.slice(0, 20),
        },
      );
    },
    startCapture() { capStart = pipelineIsoNow(); },
    finishCapture({
      success = true,
      failureReason = null,
      rawTextSize = 0,
      jsonPayloadSize = 0,
      evidenceMarkers = null,
    } = {}) {
      const finishedAt = pipelineIsoNow();
      pushStage(
        'capture',
        capStart || finishedAt,
        finishedAt,
        success ? 'success' : 'failed',
        failureReason,
        {
          raw_text_chars: rawTextSize,
          json_payload_chars: jsonPayloadSize,
          evidence_markers: evidenceMarkers || {},
        },
      );
    },
    async flush() {
      await reportPipelineStages(apiKey, { source, runId, stages });
    },
  };
}

// ── Passive login-page detection ──────────────────────────────────────────────
// When any tab navigates to a login page for a known account site, immediately
// mark that account as login_required on the server so the dashboard card updates.

const _DOMAIN_TO_SOURCE = {
  'delta.com':            'delta',
  'southwest.com':        'southwest',
  'united.com':           'united',
  'aa.com':               'american_air',
  'americanairlines.com': 'american_air',
  'alaskaair.com':        'alaska_air',
  'sfcu.org':             'sfcu',
  'americanexpress.com':  'amex',
  'chase.com':            'chase',
  'wellsfargo.com':       'wells_fargo',
  'bankofamerica.com':    'bofa',
  'capitalone.com':       'capital_one',
  'discover.com':         'discover',
  'discovercard.com':     'discover',
  'citi.com':             'citi',
  'citibank.com':         'citi',
  'paypal.com':           'paypal',
  'fidelity.com':         'fidelity',
  'schwab.com':           'schwab',
  'vanguard.com':         'vanguard',
  'etrade.com':           'etrade',
  'morganstanley.com':    'morgan_stanley',
  'robinhood.com':        'robinhood',
  'coinbase.com':         'coinbase',
  'marriott.com':         'marriott',
  'hilton.com':           'hilton',
  'hyatt.com':            'hyatt',
  'ihg.com':              'ihg',
  'wyndhamhotels.com':    'wyndham',
  'amazon.com':           'amazon',
  'target.com':           'target',
  'costco.com':           'costco',
  'starbucks.com':        'starbucks',
  'statefarm.com':        'state_farm',
  'pamf.org':             'pamf',
  'mychart.pamf.org':     'pamf',
  'ticketmaster.com':     'ticketmaster',
  'netflix.com':          'netflix',
  'hulu.com':             'hulu',
  'spotify.com':          'spotify',
  'disneyplus.com':       'disney_plus',
  'att.com':              'att',
  'xfinity.com':          'xfinity',
  'comcast.com':          'xfinity',
  'cityofpaloalto.org':   'pa_utilities',
};

// URL path/hostname terms that reliably indicate a login wall.
// Deliberately excludes 'sso' and 'authenticate' — those paths appear in
// authenticated flows (e.g. United /session/sso?token=...) and cause false
// login_required flags. /login, /signin etc. are unambiguous.
const _LOGIN_URL_RE = /\/(login|signin|sign-in|log-in|logon|log-on)(\/|$|\?)/i;

// Debounce per-source so rapid redirects don't fire multiple reports
const _loginReportedAt = {};

// Periodically prune stale entries from per-source timestamp maps to prevent
// unbounded growth during long browser sessions.
setInterval(() => {
  const _cutoff = Date.now() - 600_000; // prune entries older than 10 minutes
  for (const k of Object.keys(_loginReportedAt)) {
    if (_loginReportedAt[k] < _cutoff) delete _loginReportedAt[k];
  }
  for (const k of Object.keys(_postLoginSyncedAt)) {
    if (_postLoginSyncedAt[k] < _cutoff) delete _postLoginSyncedAt[k];
  }
}, 10 * 60 * 1000); // run every 10 minutes

// ── Persistent login-wall tracking ──────────────────────────────────────────
// Stored in chrome.storage.local so it survives service worker restarts.
// When a source is login-walled, its sync push is suppressed until it succeeds.
async function _markLoginWall(source) {
  const { login_wall_sources = [] } = await chrome.storage.local.get('login_wall_sources');
  if (!login_wall_sources.includes(source)) {
    await chrome.storage.local.set({ login_wall_sources: [...login_wall_sources, source] });
  }
}
async function _isLoginWall(source) {
  const { login_wall_sources = [] } = await chrome.storage.local.get('login_wall_sources');
  return login_wall_sources.includes(source);
}
async function _clearLoginWall(source) {
  const { login_wall_sources = [] } = await chrome.storage.local.get('login_wall_sources');
  await chrome.storage.local.set({ login_wall_sources: login_wall_sources.filter(s => s !== source) });
}

// Track tabs that recently showed a login page, keyed by tabId → source
const _tabLoginPending = {};

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'loading') return;
  const url = changeInfo.url || tab.url;
  if (!url || !url.startsWith('http')) return;

  // Skip popup windows used for background sync — we don't want sync tab
  // navigations to trigger login detection or syncSingleAccount calls.
  try {
    const winInfo = await chrome.windows.get(tab.windowId);
    if (winInfo.type === 'popup') return;
  } catch { return; }

  let hostname, pathname, search;
  try {
    const u = new URL(url);
    hostname = u.hostname.replace(/^www\./, '');
    pathname = u.pathname;
    search   = u.search;
  } catch { return; }

  // Match hostname to a known source (try exact, then strip subdomains one level)
  const source = _DOMAIN_TO_SOURCE[hostname]
    || _DOMAIN_TO_SOURCE[hostname.split('.').slice(-2).join('.')];
  if (!source) return;

  // Only use path-based detection — query params like ?returnUrl= appear in
  // authenticated SPA flows (e.g. United) and cause false login_required flags.
  const isLoginPage = _LOGIN_URL_RE.test(pathname);

  if (isLoginPage) {
    // Amex connect flow: route to connection state, not sync login_required.
    if (source === 'amex') {
      const { api_key } = await chrome.storage.local.get('api_key');
      if (api_key) {
        const accounts = await _fetchExtensionAccounts(api_key);
        const amex = accounts.find(a => a.source === 'amex');
        if (amex && ['waiting_for_extension', 'needs_login', 'connected'].includes(amex.connection_status)) {
          const now = Date.now();
          if (!_loginReportedAt[source] || now - _loginReportedAt[source] >= 300_000) {
            _loginReportedAt[source] = now;
            console.log('[Mighty] Amex login page — marking needs_login');
            await _postAmexNeedsLogin(api_key);
          }
          _tabLoginPending[tabId] = source;
          return;
        }
      }
    }

    // Debounce: don't re-report the same source within 5 minutes
    const now = Date.now();
    if (_loginReportedAt[source] && now - _loginReportedAt[source] < 300_000) {
      // Still mark tab as pending so we can detect successful login
      _tabLoginPending[tabId] = source;
      return;
    }
    _loginReportedAt[source] = now;
    _tabLoginPending[tabId] = source;

    const { api_key } = await chrome.storage.local.get('api_key');
    if (!api_key) return;

    console.log(`[Mighty] Detected login page for ${source} — marking login_required`);
    _markLoginWall(source);
    reportSyncFailure(api_key, source, 'login_wall');

  } else if (_tabLoginPending[tabId] === source) {
    // This tab was on a login page for this source and has now navigated away
    // on the same domain — treat as successful login.
    delete _tabLoginPending[tabId];
    const { api_key } = await chrome.storage.local.get('api_key');
    if (!api_key) return;

    if (source === 'amex') {
      console.log('[Mighty] Amex navigated away from login — probing session');
      const accounts = await _fetchExtensionAccounts(api_key);
      await probeAmexConnectionState(api_key, accounts);
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
      return;
    }

    console.log(`[Mighty] Login success detected for ${source} — clearing wall and syncing`);
    await _clearLoginWall(source);
    // Immediately clear login_required on the server so the dashboard updates right away
    fetch(`${MIGHTY_URL}/api/sync/login-cleared`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key, source }),
    }).catch(() => {});
    // Reload the dashboard now so the user sees green immediately
    chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
    // Then do a full sync after a short delay to refresh the actual data
    setTimeout(() => syncSingleAccount(source, api_key), 4000);
  }
});

/** Report a fruitful path to the shared registry. Fire-and-forget. */
function reportPathToRegistry(site, url) {
  try {
    const path = normalizePath(new URL(url).pathname);
    if (!path || path === '/') return;
    // Never store static assets, analytics endpoints, or infrastructure — these
    // pollute the registry and get visited on every future sync as junk pages.
    if (/\.(css|js|woff2?|ttf|eot|png|jpg|jpeg|gif|svg|ico|map)(\?|$)/i.test(path)) return;
    if (/_next\/static\/|_next\/webpack|\/v1\/interact|graphql\/customer/i.test(path)) return;
    fetch(`${MIGHTY_URL}/api/registry/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ site, path }),
    }).catch(() => {});
  } catch (_) {}
}

/** Fetch trusted paths for a site from the shared registry.
 *  Returns an array of path strings (e.g. ["/my-profile/certificates"]). */
async function fetchRegistryPaths(site) {
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/registry/paths?site=${encodeURIComponent(site)}`);
    if (!resp.ok) return [];
    const { paths = [] } = await resp.json();
    return paths;
  } catch {
    return [];
  }
}

// Single entry URL per account — the crawler discovers all subpages from here.
// No need to hardcode sub-pages like wallet, certificates, offers —
// the link scorer finds them automatically using the user's live session.
const ACCOUNT_ENTRY = {
  southwest:    'https://www.southwest.com/loyalty/myaccount/',
  delta:        'https://www.delta.com/myprofile/',
  united:       'https://www.united.com/en/us/myunited',
  american_air: 'https://www.aa.com/loyalty/home.do',
  alaska_air:   'https://www.alaskaair.com/account/dashboard',
  sfcu:         'https://www.sfcu.org/accounts/online-banking',
  amex:         'https://www.americanexpress.com/en-us/account/',
  chase:        'https://secure.chase.com/web/auth/dashboard',
  wells_fargo:  'https://www.wellsfargo.com/change-the-way-you-bank/online-banking/jump/',
  bofa:         'https://www.bankofamerica.com/myaccounts/brain/render.go',
  capital_one:  'https://myaccounts.capitalone.com/accountSummary',
  discover:     'https://portal.discover.com/customer/en/portal/account-home',
  citi:         'https://online.citi.com/US/JRS/portal/Home.do',
  paypal:       'https://www.paypal.com/myaccount/summary',
  fidelity:     'https://digital.fidelity.com/ftgw/digital/portfolio/summary',
  schwab:       'https://client.schwab.com/app/accounts/#/',
  marriott:     'https://www.marriott.com/loyalty/myAccount/default.mi',
  hilton:       'https://www.hilton.com/en/hilton-honors/guest/my-account/',
  hyatt:        'https://www.hyatt.com/en-US/my-account/home',
  ihg:          'https://www.ihg.com/rewardsclub/content/us/en/member-home',
  wyndham:      'https://www.wyndhamhotels.com/registry',
  amazon:       'https://www.amazon.com/gp/css/order-history',
  target:       'https://www.target.com/account',
  costco:       'https://www.costco.com/OrderStatusCmd',
  starbucks:    'https://www.starbucks.com/rewards/',
  state_farm:   'https://www.statefarm.com/customer-care/my-accounts',
  pamf:         'https://mychart.pamf.org/MyChart/',
  ticketmaster: 'https://www.ticketmaster.com/member/orders',
  netflix:      'https://www.netflix.com/YourAccount',
  hulu:         'https://secure.hulu.com/account',
  spotify:      'https://www.spotify.com/us/account/overview/',
  disney_plus:  'https://www.disneyplus.com/identity/account',
  att:          'https://www.att.com/my/#/',
  att_wireless: 'https://myatt.att.com/exp/myconsumerdashboard/',
  xfinity:      'https://customer.xfinity.com/#/billing',
  pa_utilities: 'https://mycpau.cityofpaloalto.org/',
};

/**
 * Per-site login URL knowledge base.
 * Keyed by source name (matching ACCOUNT_ENTRY keys).
 * loginHostnames: exact hostnames that are unambiguously auth/SSO subdomains.
 * loginPathRe:    regex matching login paths on the site's own domain.
 * Either or both may be present. Checked before generic fallbacks.
 * Keep entries alphabetical within each category.
 */
const SITE_LOGIN_CONFIG = {
  // ── Airlines ──────────────────────────────────────────────────────────────
  alaska_air:   { loginPathRe: /\/(account\/)?log-?in(\/|$|\?)/i },
  american_air: { loginPathRe: /\/(loyalty\/)?(log-?in|sign-?in)(\/|$|\?)|\/login\.do/i },
  delta:        { loginPathRe: /\/(sign-?in|log-?in|skymiles\/login)(\/|$|\?|$)/i },
  southwest:    { loginPathRe: /\/(account|loyalty)\/(log-?in|sign-?in)/i },
  // United: only match explicit /login path — NOT /session/sso which appears in authenticated SSO flows
  united:       { loginPathRe: /\/(en\/us\/)?login(\/|$|\?)/i },

  // ── Hotels ────────────────────────────────────────────────────────────────
  hilton:       { loginPathRe: /\/en\/hilton-honors\/login/i },
  hyatt:        { loginPathRe: /\/en-US\/(log-?in|sign-?in)(\/|$|\?)/i },
  ihg:          { loginHostnames: ['login.ihg.com'], loginPathRe: /\/log-?in(\/|$|\?)/i },
  marriott:     { loginHostnames: ['login.marriott.com'], loginPathRe: /\/(sign-in|log-in|login)(\.mi|\/|$|\?)/i },
  wyndham:      { loginPathRe: /\/(account\/)?(log-?in|sign-?in)(\/|$|\?)/i },

  // ── Banking & Finance ─────────────────────────────────────────────────────
  amex:         { loginPathRe: /\/en-us\/account\/log-?in/i },
  bofa:         { loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },
  capital_one:  { loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },
  chase:        { loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },
  citi:         { loginPathRe: /\/log-?in(\/|$|\?)/i },
  coinbase:     { loginHostnames: ['login.coinbase.com'] },
  discover:     { loginPathRe: /\/sign-?in(\/|$|\?)/i },
  fidelity:     { loginPathRe: /\/(log-?in|sign-?in|nlcvauth)(\/|$|\?)/i },
  paypal:       { loginPathRe: /\/(sign-?in|log-?in|authflow)(\/|$|\?)/i },
  schwab:       { loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },
  wells_fargo:  { loginPathRe: /\/(log-?in|sign-?in|wfonlinebanking)(\/|$|\?)/i },

  // ── Retail ────────────────────────────────────────────────────────────────
  amazon:       { loginPathRe: /\/ap\/sign-?in/i },
  costco:       { loginPathRe: /\/sign-?in(\.view)?(\/|$|\?)/i },
  starbucks:    { loginPathRe: /\/account\/sign-?in(\/|$|\?)/i },
  target:       { loginPathRe: /\/account\/sign-?in(\/|$|\?)/i },

  // ── Streaming & Entertainment ─────────────────────────────────────────────
  disney_plus:  { loginPathRe: /\/identity\/(log-?in|sign-?in)(\/|$|\?)/i },
  hulu:         { loginHostnames: ['auth.hulu.com'], loginPathRe: /\/log-?in(\/|$|\?)/i },
  netflix:      { loginPathRe: /\/log-?in(\/|$|\?)/i },
  spotify:      { loginHostnames: ['accounts.spotify.com'] },
  ticketmaster: { loginHostnames: ['auth.ticketmaster.com'], loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },

  // ── Telecom ───────────────────────────────────────────────────────────────
  att:          { loginPathRe: /\/(log-?in|olam\/pub)(\/|$|\?)/i },
  att_wireless: { loginPathRe: /\/(log-?in|sign-?in)(\/|$|\?)/i },
  xfinity:      { loginHostnames: ['login.xfinity.com', 'oauth.xfinity.com', 'auth.xfinity.com'] },
};

/** Normalize a URL for deduplication: strip query + fragment, lowercase, no trailing slash. */
function _normUrl(href) {
  try {
    const u = new URL(href);
    return (u.hostname + u.pathname).replace(/\/$/, '').toLowerCase();
  } catch {
    return href.toLowerCase();
  }
}

// Terms whose presence in a link URL or text strongly suggests account benefit data
const _LINK_HIGH_VALUE = [
  'certificate', 'voucher', 'wallet', 'ecredit', 'e-credit', 'travelfund', 'travel-fund',
  'travel_fund', 'companion', 'upgrade', 'offer', 'benefit', 'reward', 'redeem',
  'anniversary', 'promotion', 'expir', 'free-night', 'free_night', 'award',
  'perks', 'perk', 'privilege', 'credit', 'bonus', 'gift', 'status',
];

// Lower-priority terms suggesting a useful account page
const _LINK_ACCOUNT = [
  'account', 'profile', 'loyalty', 'membership', 'my-account', 'myaccount',
  'dashboard', 'payment', 'billing', 'history', 'trip', 'reservation', 'order',
  'subscription', 'plan', 'tier', 'points', 'miles', 'earn',
  'overview', 'summary', 'manage', 'statement', 'transaction',
];

// Terms indicating links we should never follow
const _LINK_SKIP = [
  'logout', 'log-out', 'log_out', 'signout', 'sign-out', 'sign_out',
  'sign-up', 'signup', 'register', 'create-account', 'create_account',
  'help', 'faq', 'support', 'contact', 'careers', 'about', 'press', 'legal',
  // Static assets — never account data
  '_next/static/', '_next/webpack',
  // Specific API/analytics paths that appear as scoreable links
  'graphql/customer', '/v1/interact',
  // Public marketing comparison pages
  'credit-card-rewards', 'credit-cards.mi',
  'terms', 'privacy', 'cookie', 'sitemap', 'accessibility', 'advertise',
  'shop', 'book', 'buy', 'cart', 'purchase', 'search', 'find-flights',
  'flight-status', 'check-in', 'baggage', 'travel-info', 'destinations',
  'deals', 'cars', 'vacations', 'gift-card', 'partner', 'sponsor',
  'jobs', 'newsroom', 'investor', 'media', 'javascript:', 'mailto:', 'tel:',
];

/**
 * Score a link for how likely it is to contain useful account data.
 * Returns -1 to skip; 0 if neutral (also skipped); positive to visit (higher = sooner).
 */
function _scoreLink(href, text, baseDomain) {
  let url;
  try {
    url = new URL(href);
    if (!url.protocol.startsWith('http')) return -1;
  } catch {
    return -1;
  }
  const hostname = url.hostname.replace(/^www\./, '');
  if (!hostname.endsWith(baseDomain)) return -1;
  if (!url.pathname || url.pathname === '/') return -1;

  // Skip static file assets — never contain account data
  if (/\.(css|js|woff2?|ttf|eot|png|jpg|jpeg|gif|svg|ico|map)(\?|$)/i.test(url.pathname)) return -1;

  const combined = (href + ' ' + text).toLowerCase();
  if (_LINK_SKIP.some(t => combined.includes(t))) return -1;

  let score = 0;
  if (_LINK_HIGH_VALUE.some(t => combined.includes(t))) score += 10;
  if (_LINK_ACCOUNT.some(t => combined.includes(t))) score += 3;

  // Penalize public informational/marketing paths — these contain loyalty keywords
  // (miles, status, reward, etc.) but are not authenticated account pages.
  // A -10 penalty cancels a single high-value match, ensuring these are skipped
  // unless the page is also clearly inside a personal account section.
  const _INFORMATIONAL = ['/how-to', '/fly/products/', '/earn/credit-card'];
  if (_INFORMATIONAL.some(t => url.pathname.toLowerCase().includes(t))) score -= 10;

  return score;
}

// Accounts that span multiple subdomains — the crawler uses the parent domain so
// links across subdomains (e.g. myaccount.cityofpaloalto.org → utilities.cityofpaloalto.org)
// are not treated as off-site and scored/visited normally.
const ACCOUNT_BASE_DOMAIN_OVERRIDE = {
  pa_utilities: 'cityofpaloalto.org',
};

// Domain → source for passive supplement capture when user naturally browses.
// Any account-looking path on these domains gets captured — no hardcoded path list needed.
// All known account domains — full DOM supplement capture (no truncation).
// Matches INTERCEPT_DOMAIN_MAP plus utility/local sites.
// Add new sites here so they're pre-approved without a per-site dialog.
const SUPPLEMENT_DOMAINS = {
  // Airlines
  'delta.com':                    'delta',
  'united.com':                   'united',
  'southwest.com':                'southwest',
  'aa.com':                       'american_air',
  'americanairlines.com':         'american_air',
  'alaskaair.com':                'alaska_air',
  // Hotels
  'marriott.com':                 'marriott',
  'hilton.com':                   'hilton',
  'hyatt.com':                    'hyatt',
  'ihg.com':                      'ihg',
  'wyndham.com':                  'wyndham',
  'wyndhamhotels.com':            'wyndham',
  // Car rental
  'hertz.com':                    'hertz',
  'avis.com':                     'avis',
  // Banking & finance
  'americanexpress.com':          'amex',
  'chase.com':                    'chase',
  'wellsfargo.com':               'wells_fargo',
  'bankofamerica.com':            'bofa',
  'capitalone.com':               'capital_one',
  'discover.com':                 'discover',
  'discovercard.com':             'discover',
  'citi.com':                     'citi',
  'citibank.com':                 'citi',
  'paypal.com':                   'paypal',
  'fidelity.com':                 'fidelity',
  'schwab.com':                   'schwab',
  'sfcu.org':                     'sfcu',
  // Telecom & utilities
  'xfinity.com':                  'xfinity',
  'comcast.com':                  'xfinity',
  'customer.xfinity.com':         'xfinity',
  'att.com':                      'att',
  'verizon.com':                  'verizon',
  't-mobile.com':                 'tmobile',
  'cityofpaloalto.org':           'pa_utilities',
  'utilities.cityofpaloalto.org': 'pa_utilities',
  // Shopping
  'amazon.com':                   'amazon',
  'target.com':                   'target',
  'walmart.com':                  'walmart',
  'costco.com':                   'costco',
  'starbucks.com':                'starbucks',
  'cvs.com':                      'cvs',
  'walgreens.com':                'walgreens',
  // Entertainment & streaming
  'disneyplus.com':               'disney_plus',
  'netflix.com':                  'netflix',
  'hulu.com':                     'hulu',
  'spotify.com':                  'spotify',
  'max.com':                      'max',
  'peacocktv.com':                'peacock',
  'paramountplus.com':            'paramount_plus',
  'ticketmaster.com':             'ticketmaster',
  // Health & insurance
  'kp.org':                       'kaiser',
  'statefarm.com':                'state_farm',
};

// Sources that must be synced via a regular foreground tab.
// Previously: xfinity, pa_utilities — but silent fetch (credentials: 'include')
// is tried first for ALL sources now; tab fallback handles bot-gated SPAs.
// Supplement capture (passive) still fires when the user naturally browses these sites.
const TAB_SYNC_SOURCES = new Set([]);

// Domain → source mapping for API interception
const INTERCEPT_DOMAIN_MAP = {
  // Travel — airlines
  'delta.com':              'delta',
  'united.com':             'united',
  'southwest.com':          'southwest',
  'aa.com':                 'american_air',
  'americanairlines.com':   'american_air',
  'alaskaair.com':          'alaska_air',
  // Travel — hotels
  'marriott.com':           'marriott',
  'hilton.com':             'hilton',
  'hyatt.com':              'hyatt',
  'ihg.com':                'ihg',
  'wyndham.com':            'wyndham',
  // Travel — car rental
  'hertz.com':              'hertz',
  'avis.com':               'avis',
  // Banking & Finance
  'americanexpress.com':    'amex',
  'chase.com':              'chase',
  'wellsfargo.com':         'wells_fargo',
  'bankofamerica.com':      'bofa',
  'capitalone.com':         'capital_one',
  'discover.com':           'discover',
  'discovercard.com':       'discover',
  'citi.com':               'citi',
  'citibank.com':           'citi',
  'paypal.com':             'paypal',
  'fidelity.com':           'fidelity',
  'schwab.com':             'schwab',
  'sfcu.org':               'sfcu',
  // Utilities & Telecom
  'xfinity.com':            'xfinity',
  'comcast.com':            'xfinity',
  'att.com':                'att',
  'verizon.com':            'verizon',
  't-mobile.com':           'tmobile',
  // Shopping
  'amazon.com':             'amazon',
  'target.com':             'target',
  'walmart.com':            'walmart',
  'costco.com':             'costco',
  'starbucks.com':          'starbucks',
  'cvs.com':                'cvs',
  'walgreens.com':          'walgreens',
  // Entertainment
  'disneyplus.com':         'disney_plus',
  'netflix.com':            'netflix',
  'hulu.com':               'hulu',
  'spotify.com':            'spotify',
  'max.com':                'max',
  'peacocktv.com':          'peacock',
  'paramountplus.com':      'paramount_plus',
  'ticketmaster.com':       'ticketmaster',
  // Health
  'kp.org':                 'kaiser',
  // Insurance
  'statefarm.com':          'state_farm',
};

// In-memory dedup: url → timestamp, cleared after 10 minutes
const _interceptSeen = new Map();
const _INTERCEPT_COOLDOWN = 10 * 60 * 1000;

// Phase 2: buffer structured network responses during account sync tab crawls.
const _syncNetworkCapture = new Map(); // source -> { seen: Set, blocks: [] }
const _MAX_NETWORK_BLOCK_CHARS = 120_000;
const _MAX_SYNC_NETWORK_BUFFER_CHARS = 80_000;
const _MAX_RAW_TEXT_CHARS = 40_000;
const _SENSITIVE_JSON_RE = /"(access_token|refresh_token|id_token|password|secret|authorization|cookie|csrf|session_token|session_id|sessionid|set-cookie)"/i;

function beginSyncNetworkCapture(source) {
  _syncNetworkCapture.set(source, { seen: new Set(), blocks: [] });
}

function endSyncNetworkCapture(source) {
  _syncNetworkCapture.delete(source);
}

function _bufferSyncNetworkResponse(source, url, data, { graphql = false } = {}) {
  const buf = _syncNetworkCapture.get(source);
  if (!buf || !data) return;
  const dedupeKey = `${url}|${String(data).slice(0, 200)}`;
  if (buf.seen.has(dedupeKey)) return;
  buf.seen.add(dedupeKey);
  const marker = graphql ? 'GRAPHQL' : 'NETWORK JSON';
  const safe = _redactNetworkJson(data);
  buf.blocks.push(`\n\n=== ${marker}: ${String(url || '').slice(0, 500)} ===\n${safe}\n`);
}

function flushSyncNetworkBlocks(source) {
  const buf = _syncNetworkCapture.get(source);
  if (!buf || !buf.blocks.length) return '';
  return buf.blocks.join('').slice(0, _MAX_SYNC_NETWORK_BUFFER_CHARS);
}

function mergeSyncNetworkIntoRawText(rawText, source) {
  const networkPart = flushSyncNetworkBlocks(source);
  if (!networkPart) return rawText;
  return (networkPart + rawText).slice(0, _MAX_RAW_TEXT_CHARS);
}

function _redactNetworkJson(text) {
  if (!text || !_SENSITIVE_JSON_RE.test(text)) return String(text || '').slice(0, _MAX_NETWORK_BLOCK_CHARS);
  try {
    const walk = (value) => {
      if (Array.isArray(value)) return value.map(walk);
      if (value && typeof value === 'object') {
        const out = {};
        for (const [key, item] of Object.entries(value)) {
          out[key] = _SENSITIVE_JSON_RE.test(`"${key}"`) ? '[REDACTED]' : walk(item);
        }
        return out;
      }
      return value;
    };
    return JSON.stringify(walk(JSON.parse(text))).slice(0, _MAX_NETWORK_BLOCK_CHARS);
  } catch (_) {
    return String(text || '').slice(0, _MAX_NETWORK_BLOCK_CHARS);
  }
}

async function handleInterceptedApi(url, data, { graphql = false } = {}) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Map URL to source.
  // Handles both standard API URLs and embedded state URLs (embedded:KEY@https://...)
  let source = null;
  try {
    const realUrl = url.startsWith('embedded:') ? (url.split('@')[1] || '') : url;
    const hostname = new URL(realUrl).hostname.replace(/^www\./, '');
    for (const [domain, src] of Object.entries(INTERCEPT_DOMAIN_MAP)) {
      if (hostname.endsWith(domain)) { source = src; break; }
    }
  } catch { return; }
  if (!source) return;

  // During sync tab crawl, buffer for the sync payload instead of immediate intercept POST.
  if (_syncNetworkCapture.has(source)) {
    _bufferSyncNetworkResponse(source, url, data, { graphql });
    return;
  }

  // Dedup: skip if we already sent this URL recently
  const now = Date.now();
  const last = _interceptSeen.get(url);
  if (last && now - last < _INTERCEPT_COOLDOWN) return;
  _interceptSeen.set(url, now);
  // Prune old entries
  for (const [k, t] of _interceptSeen) {
    if (now - t > _INTERCEPT_COOLDOWN) _interceptSeen.delete(k);
  }

  console.log(`[Mighty] Intercepted API for ${source}: ${url} (${data.length} chars)`);

  const resp = await fetch(`${MIGHTY_URL}/api/extension/intercept`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': api_key },
    body:    JSON.stringify({
      source,
      url,
      json_data:  data,
      synced_at:  new Date().toISOString(),
    }),
  });

  if (resp.ok) {
    console.log(`[Mighty] Intercept accepted for ${source}`);
    // Note: do NOT report API intercept URLs to the registry — they are API endpoints,
    // not browsable pages, and would pollute the sync visit list.
    // Purple flash to confirm interception
    chrome.action.setBadgeText({ text: '●' });
    chrome.action.setBadgeBackgroundColor({ color: '#8b5cf6' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 2_000);
  }
}

// ── Lifecycle ────────────────────────────────────────────────────────────────

const KEEPALIVE_ALARM    = 'mighty-keepalive';
const KEEPALIVE_INTERVAL = 20; // minutes — short enough to beat most session timeouts

chrome.runtime.onInstalled.addListener(() => {
  // Always recreate alarms on install/reload to fix any stale periods from old versions.
  // onStartup keeps existing alarms (browser restart shouldn't reset timing).
  chrome.alarms.clear(SYNC_ALARM, () => {
    chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_INTERVAL });
  });
  chrome.alarms.clear(KEEPALIVE_ALARM, () => {
    chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_INTERVAL });
  });
  console.log('[Mighty] Extension installed/reloaded, sync every', SYNC_INTERVAL, 'min, keepalive every', KEEPALIVE_INTERVAL, 'min');
  // In manual-probe/dev mode, defer automatic sync so reload does not open provider tabs.
  setTimeout(() => runSyncIfAllowed('install-reload'), 3000);
});

chrome.runtime.onStartup.addListener(() => {
  // Re-create alarms if the browser restarted and cleared them
  chrome.alarms.get(SYNC_ALARM, (a) => {
    if (!a) chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_INTERVAL });
  });
  chrome.alarms.get(KEEPALIVE_ALARM, (a) => {
    if (!a) chrome.alarms.create(KEEPALIVE_ALARM, { periodInMinutes: KEEPALIVE_INTERVAL });
  });
  setTimeout(() => runSyncIfAllowed('browser-startup'), 3000);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM)     runSyncIfAllowed('sync-alarm');
  if (alarm.name === KEEPALIVE_ALARM) runSessionKeepalive();
});

// Sites that require MFA — auto-login fills username/password but the user must
// complete the MFA step manually. When auto-login detects MFA, Mighty opens the
// partially-authenticated login page in a real browser tab so the user can finish.
// Credentials are still worth storing for these sites: they skip the typing step.
const MFA_SITES = new Set([
  'delta', 'united', 'southwest', 'american_air', 'alaska_air',
  'hilton', 'marriott', 'hyatt', 'ihg', 'amex', 'chase',
  'citi', 'capital_one', 'wellsfargo', 'bank_of_america',
]);

/**
 * Silently ping each CONNECTED, LOGGED-IN account's entry URL to keep sessions alive.
 * Uses credentials: 'include' so the browser sends the user's cookies —
 * the site sees an authenticated request and resets its session expiry timer.
 * Only pings accounts the user has actually connected AND that are in ok status,
 * avoiding wasteful pings to 50+ sites the user hasn't connected or is logged out of.
 */
async function runSessionKeepalive() {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Sites where a headless fetch won't refresh sessions reliably
  const KEEPALIVE_SKIP = new Set(['xfinity', 'pa_utilities']);

  // Fetch the user's actual connected accounts — only ping those with active sessions
  let accounts = [];
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
      headers: { 'X-Mighty-Key': api_key },
    });
    if (resp.ok) accounts = await resp.json();
  } catch (e) {
    console.log('[Mighty] Keepalive: failed to fetch accounts —', e.message);
    return;
  }

  const toKeepAlive = accounts.filter(a => {
    if (KEEPALIVE_SKIP.has(a.source)) return false;
    const status = a.sync_status || '';
    // Only ping accounts with an active session — skip login_required ones
    return status === 'ok' || status === '' || status === 'needs_first_visit';
  });

  if (toKeepAlive.length === 0) {
    console.log('[Mighty] Keepalive: no logged-in accounts to ping');
    return;
  }

  // Stagger pings to avoid a burst of simultaneous requests
  let delay = 0;
  for (const account of toKeepAlive) {
    const url = ACCOUNT_ENTRY[account.source];
    if (!url) continue;
    setTimeout(async () => {
      try {
        await fetch(url, {
          credentials: 'include',
          cache: 'no-store',
          mode: 'no-cors',  // opaque response — we only need the request to reach the server
        });
        console.log(`[Mighty] Keepalive: ${account.source}`);
      } catch (e) {
        console.log(`[Mighty] Keepalive: ${account.source} failed — ${e.message}`);
      }
    }, delay);
    delay += 2000 + Math.floor(Math.random() * 1000); // stagger by ~2–3s each
  }
}

// Login detection via storage — more reliable than sendMessage for waking a sleeping service worker
chrome.storage.onChanged.addListener(async function(changes, area) {
  if (area !== 'local' || !changes.mighty_login_detected) return;
  // During an active sync, ignore api_relay.js login detections — the sync popup
  // window is visible (needed for SPAs) so api_relay sees Hilton's login form and
  // writes mighty_login_detected, which would reload the dashboard and re-trigger sync.
  // crawlAccount already handles login detection via _isSilentLoginPage + server-side.
  if (_syncInProgress) {
    chrome.storage.local.remove('mighty_login_detected');
    return;
  }
  const { href } = changes.mighty_login_detected.newValue || {};
  if (!href) return;
  let hostname;
  try { hostname = new URL(href).hostname.replace(/^www\./, ''); } catch { return; }
  const source = _DOMAIN_TO_SOURCE[hostname]
    || _DOMAIN_TO_SOURCE[hostname.split('.').slice(-2).join('.')];
  if (!source) return;
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;
  console.log(`[Mighty] Storage-based login detected for ${source} (${hostname})`);
  await _markLoginWall(source);
  await reportSyncFailure(api_key, source, 'login_wall');
  chrome.storage.local.remove('mighty_login_detected');

  // If credentials are stored, attempt auto-login immediately instead of
  // leaving the user with a red dot and asking them to sign in manually.
  const cred = await _getCred(api_key, source);
  if (cred) {
    console.log(`[Mighty] Credentials stored for ${source} — attempting auto-login`);
    // Reload dashboard now so it shows "Session expired" while we work
    chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
    const result = await autoLogin(source, api_key).catch(() => 'error');
    console.log(`[Mighty] Auto-login result for ${source}: ${result}`);
    if (result === 'success') {
      await _clearLoginWall(source);
      fetch(`${MIGHTY_URL}/api/sync/login-cleared`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ api_key, source }),
      }).catch(() => {});
      // Reload dashboard so dot turns green
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
      setTimeout(() => syncSingleAccount(source, api_key), 2000);
    } else if (result === '2fa') {
      // Auto-login filled the form but hit an MFA prompt — open the login page in a
      // real browser tab so the user can complete the MFA step. api_relay.js will detect
      // the successful login and auto-sync the account.
      console.log(`[Mighty] ${source}: MFA required — opening login tab for user to complete`);
      const loginUrl = _AUTO_LOGIN_URLS[source] || ACCOUNT_ENTRY[source];
      await createProviderTab(loginUrl, { active: true }, 'credential_validation');
    }
    // If failed/error: dashboard shows "Session expired" with manual sign-in button
    return;
  }

  // No credentials stored — just reload dashboard to show the "Session expired" card
  chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, (tabs) => {
    tabs.forEach(t => chrome.tabs.reload(t.id));
  });
});

// Login SUCCESS detection — fired by api_relay.js when the password field disappears
// after having been confirmed present, indicating the user just logged in.
// Uses storage (not sendMessage) so it wakes the service worker reliably,
// and works even for SPAs where tabs.onUpdated never fires for client-side routing.
chrome.storage.onChanged.addListener(async function(changes, area) {
  if (area !== 'local' || !changes.mighty_login_succeeded) return;
  const { href } = changes.mighty_login_succeeded.newValue || {};
  if (!href) return;
  chrome.storage.local.remove('mighty_login_succeeded');

  let hostname;
  try { hostname = new URL(href).hostname.replace(/^www\./, ''); } catch { return; }
  const source = _DOMAIN_TO_SOURCE[hostname]
    || _DOMAIN_TO_SOURCE[hostname.split('.').slice(-2).join('.')];
  if (!source) return;

  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;
  console.log(`[Mighty] Login success detected for ${source} (content script) — clearing wall`);
  await _clearLoginWall(source);

  // Immediately flip server status to ok so dashboard shows green
  fetch(`${MIGHTY_URL}/api/sync/login-cleared`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key, source }),
  }).catch(() => {});

  // Reload dashboard so the green dot appears right away
  chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));

  // Full data sync for this account after a short delay (lets SSO finish before crawling)
  setTimeout(() => syncSingleAccount(source, api_key), 4000);
});

// Messages from popup
chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
  if (msg.type === 'MIGHTY_FEEDBACK') {
    chrome.storage.local.get('api_key', async function(items) {
      const apiKey = items.api_key || '';
      if (!apiKey) return;
      try {
        // Get CSRF token first
        const csrfResp = await fetch(`${MIGHTY_URL}/api/csrf-token`, {
          headers: { 'X-Mighty-Key': apiKey },
          credentials: 'include'
        });
        const csrfData = csrfResp.ok ? await csrfResp.json() : {};

        await fetch(`${MIGHTY_URL}/api/benefits/feedback`, {
          method: 'POST',
          headers: {
            'X-Mighty-Key': apiKey,
            'X-CSRF-Token': csrfData.token || '',
            'Content-Type': 'application/json',
          },
          credentials: 'include',
          body: JSON.stringify({
            source:    msg.source,
            field_key: msg.field_key,
            feedback:  msg.feedback,
            context:   msg.context,
          }),
        });
      } catch(e) {
        console.log('[Mighty] Feedback error:', e);
      }
    });
    return true; // keep channel open for async
  }
  if (msg.type === 'AMEX_MR_EXTRACTED') {
    (async () => {
      const { api_key } = await chrome.storage.local.get('api_key');
      if (!api_key || !msg.value) return;
      console.log('[Mighty Amex] content script reported MR balance:', msg.value, msg.url || '');
      await _pushAmexExtraction(api_key, msg.value, 'content-script');
    })();
    return false;
  }
  if (msg.action === 'sync_now') {
    // Respond immediately so the message channel doesn't time out during a long sync
    sendResponse({ ok: true });
    runSyncIfAllowed('sync_now').catch(console.error);
    return false;
  }
  if (msg.action === 'run_manual_probe') {
    (async () => {
      const { api_key } = await chrome.storage.local.get('api_key');
      if (!api_key || !msg.provider) return;
      await runManualProviderAccessProbe(api_key, msg.provider, msg.manual_run_id || null);
    })().catch(console.error);
    sendResponse({ ok: true });
    return false;
  }
  if (msg.action === 'get_status') {
    chrome.storage.local.get(['last_sync', 'sync_status', 'api_key', 'captured_accounts'], sendResponse);
    return true;
  }
  if (msg.action === 'capture_tab') {
    captureCurrentTab(msg.tabId, msg.name, msg.category)
      .then(r => sendResponse({ ok: true, source: r.source }))
      .catch(e => sendResponse({ error: e.message }));
    return true;
  }
  if (msg.action === 'intercepted_api') {
    handleInterceptedApi(msg.url, msg.data, { graphql: !!msg.graphql }).catch(() => {});
    return false; // no sendResponse needed
  }
  if (msg.action === 'login_page_detected') {
    // Content script found a visible password field — map the tab's URL to a
    // source key and report login_required so the dashboard card updates.
    (async () => {
      const href = msg.href || sender?.tab?.url || '';
      if (!href) return;
      let hostname;
      try { hostname = new URL(href).hostname.replace(/^www\./, ''); } catch { return; }
      const source = _DOMAIN_TO_SOURCE[hostname]
        || _DOMAIN_TO_SOURCE[hostname.split('.').slice(-2).join('.')];
      if (!source) return;
      const { api_key } = await chrome.storage.local.get('api_key');
      if (!api_key) return;
      console.log(`[Mighty] Content script login detected for ${source} (${hostname})`);
      await _markLoginWall(source);
      reportSyncFailure(api_key, source, 'login_wall');
      // Reload any open dashboard tabs so the card immediately shows login_required
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, (tabs) => {
        tabs.forEach(t => chrome.tabs.reload(t.id));
      });
    })();
    return false;
  }
  if (msg.action === 'clear_login_wall') {
    // Dashboard "I'm signed in" button — clears the local login wall flag and
    // kicks off a single-account sync so the dot turns green immediately.
    (async () => {
      const source = msg.source;
      if (!source) { sendResponse({ ok: false, error: 'no source' }); return; }
      await _clearLoginWall(source);
      const { api_key } = await chrome.storage.local.get('api_key');
      if (api_key) {
        // Reset debounce so the sync runs even if we synced recently
        delete _postLoginSyncedAt[source];
        syncSingleAccount(source, api_key);
      }
      sendResponse({ ok: true });
    })();
    return true;
  }
  if (msg.action === 'remove_captured') {
    chrome.storage.local.get('captured_accounts', ({ captured_accounts = {} }) => {
      delete captured_accounts[msg.source];
      chrome.storage.local.set({ captured_accounts }, () => sendResponse({ ok: true }));
    });
    return true;
  }
  if (msg.action === 'store_credential') {
    (async () => {
      const { api_key } = await chrome.storage.local.get('api_key');
      if (!api_key || !msg.source || !msg.username || !msg.password) {
        sendResponse({ ok: false, error: 'missing fields' }); return;
      }
      await _storeCred(api_key, msg.source, msg.username, msg.password);
      sendResponse({ ok: true });
    })();
    return true;
  }
  if (msg.action === 'delete_credential') {
    (async () => {
      await _deleteCred(msg.source);
      sendResponse({ ok: true });
    })();
    return true;
  }
  if (msg.action === 'has_credential') {
    (async () => {
      const { api_key } = await chrome.storage.local.get('api_key');
      if (!api_key) { sendResponse({ has: false, source: msg.source }); return; }
      const cred = await _getCred(api_key, msg.source);
      sendResponse({ has: !!cred, source: msg.source });
    })();
    return true;
  }
});

// ── Sync orchestration ───────────────────────────────────────────────────────

// Prevent concurrent sync runs (each would open its own tab set)
let _syncInProgress = false;
// Sources that hit login_required during the current sync run and have credentials
// stored — processed for proactive auto-login after _syncInProgress clears.
const _pendingAutoLogins = new Set();

// ── Silent fetch helpers (no tabs needed) ────────────────────────────────────

// Sites where silent fetch cannot reliably detect auth state:
// - SPA shells: same HTML is served regardless of auth (Hilton, Hyatt, United)
// - .mi-extension paths: Marriott's redirect to /sign-in.mi evades generic regex
// These sites are forced through the tab-based path, which executes JS + checks
// post-settle URL and password fields in the actual rendered page.
const SILENT_FETCH_SKIP = new Set(['hilton', 'marriott', 'hyatt', 'southwest', 'delta', 'american_air', 'alaska_air', 'united']);

// ── Authenticated path knowledge for SILENT_FETCH_SKIP (SPA) sites ────────────
// Registry paths for these sites are filtered to only include paths that are
// under KNOWN authenticated prefixes. Public marketing pages can end up in the
// registry from old syncs and would otherwise pass as successful auth pages.

// Path prefixes that are unambiguously behind auth for each SPA site.
const _AUTH_PATH_PREFIXES = {
  delta:        ['/myprofile', '/my-profile', '/us/en/my-account', '/en/us/my-account', '/delta-vacations'],
  united:       ['/en/us/myunited', '/en/US/mileageplus', '/en/us/mileageplus'],
  hilton:       ['/en/hilton-honors/guest/my-account'],
  marriott:     ['/loyalty/myaccount', '/loyalty/registrations', '/loyalty/my-account'],
  hyatt:        ['/en-US/my-account', '/en-us/my-account'],
  southwest:    ['/loyalty/myaccount', '/rapid-rewards'],
  alaska_air:   ['/account'],
  american_air: ['/loyalty/home', '/myprofile', '/aadvantage'],
};

// When the registry has no valid auth paths, probe these known auth pages.
// If the probe redirects to login, the session is expired.
const _AUTH_PROBE_PATHS = {
  delta:        '/my-profile/certificates',
  united:       '/en/US/mileageplus/account',
  hilton:       '/en/hilton-honors/guest/my-account/',
  marriott:     '/loyalty/myAccount/default.mi',
  hyatt:        '/en-US/my-account/home',
  southwest:    '/loyalty/myaccount/mytrips/',
  alaska_air:   '/account/dashboard',
  american_air: '/loyalty/home.do',
};

// Definitive auth cookie per site — present when logged in, absent when not.
// Checked before any tab crawl; faster and more reliable than page-content heuristics.
// To add a new site: compare COOKIES_* debug dumps between logged-in and logged-out runs.
const _AUTH_COOKIE_SIGNALS = {
  southwest: { name: 'id_token',   minLen: 100 }, // JWT; absent when logged out
  united:    { name: 'AuthCookie', minLen: 32  }, // session auth token; absent when logged out
  delta:     { name: 'isin',       minLen: 1   }, // "Y" when signed in; absent when logged out
};

/** Strip HTML tags to plain text. Service workers have no DOM, so we use regex. */
function _htmlToText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&nbsp;/g, ' ')
    .replace(/&#\d+;/g, ' ').replace(/\s+/g, ' ').trim();
}

const _EVIDENCE_MAX_BLOCK = 12_000;
const _EMBEDDED_SCRIPT_IDS = ['__NEXT_DATA__', '__NUXT__'];

function _safeEvidenceJson(text) {
  if (!text) return null;
  const trimmed = String(text).trim();
  if (trimmed.length < 20 || trimmed.length > _EVIDENCE_MAX_BLOCK) return null;
  if (_SENSITIVE_JSON_RE.test(trimmed)) return null;
  try {
    JSON.parse(trimmed);
    return trimmed;
  } catch {
    return null;
  }
}

function _formatUniversalEvidenceSections(pageUrl, visibleText, doc) {
  const parts = [];
  const text = String(visibleText || '').trim();
  if (text.length >= 50) {
    parts.push(`\n\n--- ${pageUrl} ---\n${text.slice(0, 15_000)}`);
  }

  const meta = { title: '', canonical: '', url: pageUrl };
  if (doc) {
    meta.title = (doc.title || '').slice(0, 300);
    meta.canonical = doc.querySelector('link[rel="canonical"]')?.getAttribute('href') || '';
    for (const el of doc.querySelectorAll('meta[name], meta[property]')) {
      const key = el.getAttribute('name') || el.getAttribute('property') || '';
      const val = el.getAttribute('content') || '';
      if (!key || !val || val.length > 500) continue;
      if (/password|token|cookie|csrf|auth|secret/i.test(key)) continue;
      meta[key] = val.slice(0, 300);
    }
    for (const script of doc.querySelectorAll('script[type="application/ld+json"]')) {
      const block = _safeEvidenceJson(script.textContent || '');
      if (block) parts.push(`\n\n=== JSON-LD: ${pageUrl} ===\n${block}`);
    }
    for (const id of _EMBEDDED_SCRIPT_IDS) {
      const el = doc.getElementById(id) || doc.querySelector(`script#${id}, script[data-next-page]`);
      const block = _safeEvidenceJson(el?.textContent || '');
      if (block) parts.push(`\n\n=== EMBEDDED STATE: embedded:${id}@${pageUrl} ===\n${block}`);
    }
  }
  if (Object.keys(meta).length > 2) {
    parts.push(`\n\n=== PAGE META: ${pageUrl} ===\n${JSON.stringify(meta)}`);
  }
  return parts.join('');
}

function _extractEvidenceSectionsFromHtml(html, pageUrl) {
  try {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const visibleText = _htmlToText(html);
    return _formatUniversalEvidenceSections(pageUrl, visibleText, doc);
  } catch {
    const visibleText = _htmlToText(html);
    return visibleText.length >= 50 ? `\n\n--- ${pageUrl} ---\n${visibleText.slice(0, 15_000)}` : '';
  }
}

/** True if the text looks like a login/auth page redirect. */
function _isSilentLoginPage(text) {
  const lower = text.slice(0, 3000).toLowerCase();
  // High-confidence signals: any single match is enough — these almost never
  // appear on real authenticated account pages.
  const highConf = [
    // Generic login-page indicators
    'forgot password', 'enter your password', 'enter your email',
    'continue with google', 'remember me', 'sign in with your',
    'log in with your', 'create an account', 'join for free',
    // Help text that only appears on login forms
    'need help signing in', 'need help logging in',
    // "forgot your X" — "forgot your password", "forgot your info?" (Hilton), etc.
    'forgot your',
    // First-time / create-password (Hilton: "First time signing in?", "Create your password")
    'first time signing in', 'create your password',
    // Phone/SMS login option (United: "Login with phone number")
    'login with phone',
    // Visible password field label (Hilton renders "Show password" toggle in main content)
    'show password',
  ];
  if (highConf.some(s => lower.includes(s))) return true;
  // Lower-confidence signals: require 2+ (they can appear in logged-in nav/footers).
  const lowConf = ['sign in', 'log in', 'create account'];
  return lowConf.filter(s => lower.includes(s)).length >= 2;
}

/** Extract hrefs from raw HTML without DOM. Returns [{href, text}]. */
function _extractLinksFromHtml(html, baseUrl) {
  const links = [];
  // Match href="..." and href='...'
  const re = /href=["']([^"'#\s][^"']*?)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    try {
      const href = new URL(m[1], baseUrl).href;
      if (!href.startsWith('http')) continue;
      // Get surrounding text for scoring
      const ctx = html.slice(Math.max(0, m.index - 80), m.index + 120)
        .replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      links.push({ href, text: ctx });
    } catch {}
  }
  return links;
}

/**
 * Silently fetch an account's pages using the user's existing session cookies.
 * Extension service workers with <all_urls> host_permissions can read cross-origin
 * responses — no tab, no visibility, no Chrome Memory Saver warnings.
 *
 * Returns combined page text if content is substantive, or null to signal
 * that tab-based fallback is needed.
 */
async function _silentFetchPages(source, account) {
  const entry = ACCOUNT_ENTRY[source];
  if (!entry) return null;
  // Skip sites where the static HTML doesn't reflect auth state (SPAs, .mi-path sites)
  if (SILENT_FETCH_SKIP.has(source)) return null;

  let entryHtml, finalUrl;
  try {
    const resp = await fetch(entry, { credentials: 'include' });
    if (!resp.ok) return null;
    finalUrl  = resp.url;
    entryHtml = await resp.text();
  } catch {
    return null;
  }

  // If the fetch was redirected to a login page URL, we're not logged in.
  // Checks: site-specific config → generic path regex → auth subdomain prefix.
  const _REDIRECT_LOGIN_RE = /\/(login|signin|sign-in|log-in|logon|log-on)(\/|$|\?)/i;
  try {
    const finalU   = new URL(finalUrl);
    const finalSub = finalU.hostname.split('.')[0].toLowerCase();
    const cfg = SITE_LOGIN_CONFIG[source];
    if (cfg?.loginHostnames?.includes(finalU.hostname)) return null;
    if (cfg?.loginPathRe?.test(finalU.pathname)) return null;
    if (_REDIRECT_LOGIN_RE.test(finalU.pathname)) return null;
    if (/^(login|sso|auth|signin|sign-in|logon|authenticate|identity)$/.test(finalSub)) return null;
  } catch {}

  const entryText = _htmlToText(entryHtml);
  const entryEvidence = _extractEvidenceSectionsFromHtml(entryHtml, finalUrl || entry);

  // Too short = empty SPA shell or error page — needs tab
  if (entryText.length < 600) return null;
  // Login redirect — not logged in
  if (_isSilentLoginPage(entryText)) return null;
  // Bot detection
  if (BOT_DETECTION_PHRASES.some(p => entryText.toLowerCase().includes(p))) return null;

  let allText = entryEvidence || `\n\n--- ${entry} ---\n${entryText}`;
  const visitedNorm = new Set([_normUrl(entry)]);

  // Discover and silently fetch high-value subpages
  const MAX_SILENT_SUBPAGES = 4;
  let baseDomain;
  try {
    baseDomain = ACCOUNT_BASE_DOMAIN_OVERRIDE[source] || new URL(entry).hostname.replace(/^www\./, '');
  } catch { return allText.length > 600 ? allText : null; }

  const links = _extractLinksFromHtml(entryHtml, entry);
  const scored = links
    .map(l => ({ ...l, score: _scoreLink(l.href, l.text, baseDomain) }))
    .filter(l => l.score > 0)
    .sort((a, b) => b.score - a.score);

  // Supplement with registry paths
  const regPaths = await fetchRegistryPaths(source).catch(() => []);
  const entryOrigin = new URL(entry).origin;
  for (const path of regPaths) {
    const regUrl = entryOrigin + path;
    if (!visitedNorm.has(_normUrl(regUrl))) {
      scored.push({ href: regUrl, text: '', score: 5, fromRegistry: true });
    }
  }

  for (const link of scored) {
    if (visitedNorm.size - 1 >= MAX_SILENT_SUBPAGES) break;
    const norm = _normUrl(link.href);
    if (visitedNorm.has(norm)) continue;
    visitedNorm.add(norm);

    try {
      await randomDelay(300, 900); // polite pacing
      const r = await fetch(link.href, { credentials: 'include' });
      if (!r.ok) continue;
      const html = await r.text();
      const text = _htmlToText(html);
      if (text.length < 200) continue;
      if (BOT_DETECTION_PHRASES.some(p => text.toLowerCase().includes(p))) continue;
      if (_isSilentLoginPage(text)) continue;
      allText += _extractEvidenceSectionsFromHtml(html, link.href) || `\n\n--- ${link.href} ---\n${text}`;
      reportPathToRegistry(source, link.href);
      console.log(`[Mighty] ${source}: silent-fetched ${link.href} (${text.length} chars)`);
    } catch {}
  }

  return allText.length > 600 ? allText : null;
}

/**
 * Pre-flight login redirect check for SILENT_FETCH_SKIP sites.
 * Does a credentialed fetch to the entry URL and checks whether the server
 * HTTP-redirects to a login page before any JavaScript runs.
 * Returns true if a login redirect is detected, false otherwise.
 * Fast (~200ms) because it needs no tab. Only catches server-side 302s, not JS redirects.
 */
async function _prefetchLoginCheck(source) {
  const url = ACCOUNT_ENTRY[source];
  if (!url) return false;
  try {
    const resp = await fetch(url, { credentials: 'include', redirect: 'follow' });
    const finalUrl = resp.url;
    if (!finalUrl || finalUrl === url) return false; // no redirect
    const finalU = new URL(finalUrl);
    const cfg = SITE_LOGIN_CONFIG[source];
    if (cfg?.loginHostnames?.includes(finalU.hostname)) return true;
    if (cfg?.loginPathRe?.test(finalU.pathname)) return true;
    // Generic fallbacks
    const _re = /\/(login|signin|sign-in|log-in|logon)(\/|$|\?)/i;
    if (_re.test(finalU.pathname)) return true;
    const sub = finalU.hostname.split('.')[0].toLowerCase();
    return /^(login|sso|auth|signin|sign-in|logon)$/.test(sub);
  } catch { return false; }
}

/** Create a hidden background tab for sync work (fallback when silent fetch fails).
 *  Returns { win: { id }, tabId } or null on failure. */
async function _createSyncWindow(initialUrl = 'about:blank') {
  try {
    // Create an off-screen POPUP window (NOT minimized).
    //
    // Why NOT minimized: when state='minimized', document.hidden=true inside the tab.
    // SPAs (United, Hilton, etc.) check document.hidden and skip or defer their auth
    // flow when the tab is hidden — so the login redirect/form never fires and the
    // extension sees generic content instead of a login page, causing false greens.
    //
    // By positioning the window off-screen (left/top far negative) instead of
    // minimizing it, document.hidden stays false.  SPAs run their full auth check:
    //   • If NOT logged in → redirect to login URL (caught by post-settle URL check)
    //                      → login form rendered in DOM (caught by _pwCheck)
    //   • If logged in    → auth resolves quickly, any transient form disappears
    //                        before _pwCheck's 5-second double-check fires
    //
    // api_relay.js login detection also runs correctly (document.hidden=false),
    // with its 6-second consecutive check filtering transient SPA auth flashes.
    //
    // Chrome requires windows to be at least 50% within visible screen space —
    // negative coordinates are rejected outright (v14's approach failed with
    // "Invalid value for bounds"). Instead: create a small 100×100 popup at the
    // top-left corner with focused:false so it doesn't steal focus.
    // document.hidden stays false (not minimized) so SPAs run their full auth check.
    // Position the popup behind the user's current window so it's invisible
    // but NOT minimized (minimized → document.hidden=true → SPAs skip auth).
    // Strategy: find the focused browser window, spawn the popup at the same
    // coordinates, then immediately re-focus the original window.
    let spawnLeft = 0, spawnTop = 0;
    try {
      const focusedWin = await chrome.windows.getLastFocused({ windowTypes: ['normal'] });
      if (focusedWin && focusedWin.left != null) {
        // Position at the BOTTOM-RIGHT corner of the user's window, well away from
        // the tab strip (top-left).  Even if Chrome briefly focuses the popup before
        // the user's window is restored, it won't overlap the tab strip area.
        spawnLeft = Math.max(0, (focusedWin.left || 0) + (focusedWin.width  || 1000) - 110);
        spawnTop  = Math.max(0, (focusedWin.top  || 0) + (focusedWin.height ||  800) - 110);
      }
    } catch (_) {}

    _dbg('CREATE_SYNC_WIN', { spawnLeft, spawnTop, initialUrl });
    const win = await createProviderWindow({
      url:     initialUrl,
      type:    'popup',
      left:    spawnLeft,
      top:     spawnTop,
      width:   100,
      height:  100,
      focused: false,
    }, 'sync');
    const tabId = win.tabs?.[0]?.id;
    if (!tabId) throw new Error('no tab in popup');
    _dbg('SYNC_WIN_CREATED', { winId: win.id, tabId });

    // NOTE: we intentionally do NOT call windows.update(..., {focused:true}) here.
    // Chrome MV3 does not reliably honour focused:false on windows.create — the popup
    // may briefly steal focus — but calling windows.update immediately after causes its
    // own visual artifact (the user's window "flashes" to the foreground).  The
    // bottom-right positioning above ensures any brief appearance is unobtrusive.

    return { win, tabId };
  } catch (e) {
    console.warn('[Mighty] Could not create sync window:', e.message);
    return null;
  }
}

// ── Credential storage (AES-GCM encrypted, local extension only) ──────────────
// Credentials are derived-key encrypted using HKDF from the user's API key and
// stored only in chrome.storage.local — they are never sent to Mighty servers.

async function _credKey(apiKey) {
  const km = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(apiKey), { name: 'HKDF' }, false, ['deriveKey']
  );
  return crypto.subtle.deriveKey(
    {
      name: 'HKDF',
      hash: 'SHA-256',
      salt: new TextEncoder().encode('mighty-creds-v1'),
      info: new TextEncoder().encode('credential-encryption'),
    },
    km,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt']
  );
}

async function _encryptCred(apiKey, data) {
  const key = await _credKey(apiKey);
  const iv  = crypto.getRandomValues(new Uint8Array(12));
  const enc = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    new TextEncoder().encode(JSON.stringify(data))
  );
  const out = new Uint8Array(12 + enc.byteLength);
  out.set(iv);
  out.set(new Uint8Array(enc), 12);
  return btoa(String.fromCharCode(...out));
}

async function _decryptCred(apiKey, b64) {
  try {
    const key   = await _credKey(apiKey);
    const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const plain = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: bytes.slice(0, 12) },
      key,
      bytes.slice(12)
    );
    return JSON.parse(new TextDecoder().decode(plain));
  } catch { return null; }
}

async function _storeCred(apiKey, source, username, password) {
  const blob = await _encryptCred(apiKey, { username, password });
  const { _creds = {} } = await chrome.storage.local.get('_creds');
  _creds[source] = blob;
  await chrome.storage.local.set({ _creds });
  console.log(`[Mighty] Credentials stored for ${source}`);
}

async function _getCred(apiKey, source) {
  const { _creds = {} } = await chrome.storage.local.get('_creds');
  if (!_creds[source]) return null;
  return _decryptCred(apiKey, _creds[source]);
}

async function _deleteCred(source) {
  const { _creds = {} } = await chrome.storage.local.get('_creds');
  delete _creds[source];
  await chrome.storage.local.set({ _creds });
  console.log(`[Mighty] Credentials removed for ${source}`);
}

// ── Auto-login (fills login form in a background popup window) ────────────────

let _autoLoginInProgress = false;

// Per-site login page URLs. We navigate here directly rather than letting the
// site redirect from the account page, which can be slower and less reliable.
const _AUTO_LOGIN_URLS = {
  delta:        'https://www.delta.com/us/en/sign-in/start',
  united:       'https://www.united.com/ux/en/login',
  southwest:    'https://www.southwest.com/account/login',
  american_air: 'https://www.aa.com/login.do',
  alaska_air:   'https://www.alaskaair.com/account/login',
  marriott:     'https://www.marriott.com/sign-in.mi',
  hilton:       'https://www.hilton.com/en/hilton-honors/sign-in/',
  hyatt:        'https://world.hyatt.com/content/gp/en/member/loginRegister.html',
  ihg:          'https://login.ihg.com/',
  amex:         'https://www.americanexpress.com/en-us/account/login',
  chase:        'https://secure.chase.com/web/auth/dashboard#/dashboard/loginWithChaseButton/index',
  amazon:       'https://www.amazon.com/ap/signin',
  starbucks:    'https://www.starbucks.com/account/signin',
};

/**
 * Fill visible login form fields. Works for both native inputs and
 * React/Vue controlled inputs (uses native prototype setter to bypass
 * framework value interception, then dispatches events to notify the framework).
 *
 * Handles single-step (email+password together) and two-step (email first,
 * password on the next step) login flows.
 */
function _fillFormScript(username, password) {
  function setVal(el, val) {
    try {
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(el, val);
    } catch { el.value = val; }
    ['input', 'change', 'keyup'].forEach(t => el.dispatchEvent(new Event(t, { bubbles: true })));
  }

  function vis(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && !el.disabled;
  }

  const allPw   = Array.from(document.querySelectorAll('input[type="password"]')).filter(vis);
  const allUser = Array.from(document.querySelectorAll('input[type="email"], input[type="text"]')).filter(vis);

  let filledUser = false, filledPw = false;
  if (allUser.length > 0 && allPw.length === 0) {
    setVal(allUser[0], username); filledUser = true;
  } else if (allUser.length > 0 && allPw.length > 0) {
    setVal(allUser[0], username); setVal(allPw[0], password);
    filledUser = true; filledPw = true;
  } else if (allPw.length > 0) {
    setVal(allPw[0], password); filledPw = true;
  }

  let submitted = false;
  if (filledUser || filledPw) {
    const btn = document.querySelector('button[type="submit"], input[type="submit"]')
      || Array.from(document.querySelectorAll('button')).find(
           b => /log.?in|sign.?in|continue|next|submit/i.test(b.textContent)
         );
    if (btn) { btn.click(); submitted = true; }
  }
  return { filledUser, filledPw, submitted };
}

function _checkOutcomeScript() {
  const text = (document.body && document.body.innerText) || '';
  const has2FA = /verification code|one.time password|authenticator|check your (phone|email|text)|enter.the.code|security code|two.factor|2-step/i.test(text)
    || Array.from(document.querySelectorAll('input[type="tel"]')).some(e => {
         const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
       });
  const hasPw = Array.from(document.querySelectorAll('input[type="password"]')).some(e => {
    const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0;
  });
  return { url: location.href, has2FA, hasPw };
}

/**
 * Attempt automatic login for `source` using stored credentials.
 * Returns: 'success' | '2fa' | 'failed' | 'error' | null (no creds)
 */
async function autoLogin(source, apiKey) {
  if (_autoLoginInProgress || _syncInProgress) return null;
  const cred = await _getCred(apiKey, source);
  if (!cred) return null;

  _autoLoginInProgress = true;
  const loginUrl = _AUTO_LOGIN_URLS[source] || ACCOUNT_ENTRY[source];
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const created = await _createSyncWindow(loginUrl);
  if (!created) { _autoLoginInProgress = false; return 'error'; }
  const { win, tabId } = created;

  try {
    console.log(`[Mighty] autoLogin: navigating to ${loginUrl} for ${source}`);
    await sleep(5000); // allow SPA to settle + auth check to complete

    // Tag tab as sync popup so api_relay.js login polling skips it
    await chrome.scripting.executeScript({
      target: { tabId },
      func: () => { window.__mightySyncTab = true; },
    }).catch(() => {});

    // Step 1: fill whatever form fields are visible
    const [r1] = await chrome.scripting.executeScript({
      target: { tabId },
      func: _fillFormScript,
      args: [cred.username, cred.password],
    });
    console.log(`[Mighty] autoLogin ${source} step1:`, r1?.result);

    if (!r1?.result?.submitted) {
      console.log(`[Mighty] autoLogin ${source}: no submit on step1 — aborting`);
      return 'failed';
    }

    await sleep(4000); // wait for navigation / SPA transition

    const [c1] = await chrome.scripting.executeScript({
      target: { tabId }, func: _checkOutcomeScript,
    });
    console.log(`[Mighty] autoLogin ${source} after step1:`, c1?.result);
    if (c1?.result?.has2FA) return '2fa';

    if (c1?.result?.hasPw) {
      // Two-step login: password field now visible — fill it
      const [r2] = await chrome.scripting.executeScript({
        target: { tabId },
        func: _fillFormScript,
        args: [cred.username, cred.password],
      });
      console.log(`[Mighty] autoLogin ${source} step2:`, r2?.result);
      await sleep(4000);
    }

    const [c2] = await chrome.scripting.executeScript({
      target: { tabId }, func: _checkOutcomeScript,
    });
    console.log(`[Mighty] autoLogin ${source} final:`, c2?.result);
    if (c2?.result?.has2FA) return '2fa';
    if (c2?.result?.hasPw)  return 'failed';
    return 'success';

  } catch (e) {
    console.warn(`[Mighty] autoLogin ${source} error:`, e.message);
    return 'error';
  } finally {
    _autoLoginInProgress = false;
    chrome.windows.remove(win.id).catch(() => {});
  }
}

// Debounce map for post-login syncs — prevent multiple triggers per source
const _postLoginSyncedAt = {};

/** Sync a single account by source key after a successful re-login. */
async function syncSingleAccount(source, apiKey) {
  const now = Date.now();
  if (_postLoginSyncedAt[source] && now - _postLoginSyncedAt[source] < 300_000) {
    console.log(`[Mighty] Post-login sync for ${source} debounced — already ran within 5 min`);
    return;
  }
  _postLoginSyncedAt[source] = now;
  console.log(`[Mighty] Post-login sync for ${source}`);
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
      headers: { 'X-Mighty-Key': apiKey }
    });
    if (!resp.ok) return;
    const accounts = await resp.json();
    const account = accounts.find(a => a.source === source);
    if (!account) return;
    await crawlAccount(apiKey, account, new Date().toISOString(), null, null);
    chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
  } catch (e) {
    console.warn(`[Mighty] Post-login sync failed for ${source}: ${e.message}`);
  }
}

// ── Extension adapter: provider connection probes (not the account model) ─────
const _EXTENSION_ADAPTER = 'extension';
const _AMEX_ACTIVE_CONNECTION = new Set([
  'connecting', 'waiting_for_extension', 'needs_login',
]);

const _AMEX_SESSION_SIGNALS = [
  'membership rewards',
  'account home',
  'card ending',
  'recent activity',
  'payment due',
  'available credit',
  'manage account',
  'account services',
];

const _amexConnReportedAt = {};
const _amexExtractReportedAt = {};

async function _pushAmexExtraction(apiKey, value, source = 'extension') {
  if (!apiKey || !value) return false;
  const cacheKey = String(value).replace(/\D/g, '');
  const now = Date.now();
  if (_amexExtractReportedAt[cacheKey] && now - _amexExtractReportedAt[cacheKey] < 300_000) {
    console.log('[Mighty Amex] extract unchanged — skip post', value);
    return true;
  }
  console.log('[Mighty Amex] posting Membership Rewards →', value, `(${source})`);
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/amex/extract`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
      body: JSON.stringify({
        session_verified: true,
        value,
        adapter: _EXTENSION_ADAPTER,
      }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      _amexExtractReportedAt[cacheKey] = now;
      console.log('[Mighty Amex] extract stored on server:', data);
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
      return true;
    }
    console.warn('[Mighty Amex] extract POST failed', resp.status, data);
  } catch (e) {
    console.warn('[Mighty Amex] extract POST error:', e.message);
  }
  return false;
}

async function extractAmexRewardsInTab(tabId, apiKey) {
  console.log('[Mighty Amex] running DOM extraction in tab', tabId);
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractAmexMembershipRewardsPage,
    });
    const result = r?.result;
    console.log('[Mighty Amex] tab extract result:', result);
    if (result?.loggedIn && result?.value) {
      return _pushAmexExtraction(apiKey, result.value, 'tab');
    }
    if (result && result.loggedIn === false) {
      console.log('[Mighty Amex] tab not logged in during extraction');
      await _postAmexNeedsLogin(apiKey);
    } else if (result?.loggedIn) {
      console.log('[Mighty Amex] logged in but Membership Rewards balance not found in DOM');
    }
  } catch (e) {
    console.warn('[Mighty Amex] tab extraction failed:', e.message);
  }
  return false;
}

async function runAmexExtraction(apiKey, accounts) {
  if (!apiKey) return;
  const amex = accounts?.find(a => a.source === 'amex');
  if (!amex) {
    console.log('[Mighty Amex] no amex account configured — skip extraction');
    return;
  }
  if (amex.is_synced) {
    console.log('[Mighty Amex] already synced — skip extraction');
    return;
  }
  const conn = amex.connection_status || '';
  if (!['connected', 'waiting_for_extension', 'needs_login'].includes(conn)) {
    console.log('[Mighty Amex] connection state not ready for extraction:', conn);
    return;
  }

  const openTabs = await chrome.tabs.query({ url: '*://*.americanexpress.com/*' });
  for (const tab of openTabs) {
    if (!tab.id || !tab.url || /\/account\/log-?in/i.test(tab.url)) continue;
    console.log('[Mighty Amex] trying open tab', tab.url);
    const ok = await extractAmexRewardsInTab(tab.id, apiKey);
    if (ok) return;
  }

  console.log('[Mighty Amex] opening background account tab for extraction');
  let tab;
  try {
    tab = await createProviderTab(ACCOUNT_ENTRY.amex, { active: false }, 'extraction');
    await waitForTabLoad(tab.id, 25_000);
    await sleep(6000);
    await extractAmexRewardsInTab(tab.id, apiKey);
  } catch (e) {
    console.warn('[Mighty Amex] background tab extraction failed:', e.message);
  } finally {
    if (tab?.id) chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function _fetchExtensionAccounts(apiKey) {
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
      headers: { 'X-Mighty-Key': apiKey },
    });
    if (!resp.ok) return [];
    return await resp.json();
  } catch {
    return [];
  }
}

/** Probe Amex with session cookies — true only when logged-in account content is present. */
async function _probeAmexLoggedIn() {
  const entry = ACCOUNT_ENTRY.amex;
  const cfg = SITE_LOGIN_CONFIG.amex;
  const _REDIRECT_LOGIN_RE = /\/(login|signin|sign-in|log-in|logon|log-on)(\/|$|\?)/i;
  try {
    const resp = await fetch(entry, { credentials: 'include', redirect: 'follow' });
    if (!resp.ok) return false;
    const finalUrl = resp.url;
    const html = await resp.text();
    try {
      const finalU = new URL(finalUrl);
      if (cfg?.loginPathRe?.test(finalU.pathname)) return false;
      if (_REDIRECT_LOGIN_RE.test(finalU.pathname)) return false;
      if (/^(login|sso|auth|signin|sign-in|logon|authenticate|identity)$/.test(
        finalU.hostname.split('.')[0].toLowerCase()
      )) return false;
    } catch {}
    const text = _htmlToText(html);
    if (text.length < 300) return false;
    if (_isSilentLoginPage(text)) return false;
    const lower = text.toLowerCase();
    return _AMEX_SESSION_SIGNALS.some(s => lower.includes(s));
  } catch {
    return false;
  }
}

async function _postAmexNeedsLogin(apiKey) {
  const now = Date.now();
  if (_amexConnReportedAt.needs_login && now - _amexConnReportedAt.needs_login < 60_000) return;
  _amexConnReportedAt.needs_login = now;
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/amex/needs-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
    });
    if (resp.ok) {
      console.log('[Mighty] Amex connection state → needs_login');
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
    }
  } catch (e) {
    console.warn('[Mighty] Amex needs-login report failed:', e.message);
  }
}

async function _postAmexConnected(apiKey) {
  const now = Date.now();
  if (_amexConnReportedAt.connected && now - _amexConnReportedAt.connected < 60_000) return;
  _amexConnReportedAt.connected = now;
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/amex/connected`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
      body: JSON.stringify({ session_verified: true, adapter: _EXTENSION_ADAPTER }),
    });
    if (resp.ok) {
      console.log('[Mighty] Amex connection state → connected (session verified)');
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
    } else {
      console.log('[Mighty] Amex connected report skipped:', resp.status);
    }
  } catch (e) {
    console.warn('[Mighty] Amex connected report failed:', e.message);
  }
}

/** Update provider connection state via the extension adapter (session probe). */
async function probeAmexConnectionState(apiKey, accounts) {
  if (!apiKey || !Array.isArray(accounts)) return;
  const amex = accounts.find(a => a.source === 'amex');
  if (!amex) return;

  const status = amex.connection_status;
  const loggedIn = await _probeAmexLoggedIn();

  if (status === 'connected') {
    if (!loggedIn) await _postAmexNeedsLogin(apiKey);
    else if (!amex.is_synced) await runAmexExtraction(apiKey, accounts);
    return;
  }

  if (!['waiting_for_extension', 'needs_login'].includes(status)) return;

  if (loggedIn) {
    await _postAmexConnected(apiKey);
    const refreshed = await _fetchExtensionAccounts(apiKey);
    await runAmexExtraction(apiKey, refreshed);
  } else if (status === 'waiting_for_extension') {
    await _postAmexNeedsLogin(apiKey);
  }
}

// ── Provider Access Probe (Phase 1 reliability diagnostic) ────────────────────

const PROVIDER_ACCESS_PROBE_SOURCES = new Set(['amex', 'delta']);
const MANUAL_PROBE_PROVIDERS = ['amex', 'delta'];
const _probeReportedAt = {};
let _automaticProbesEnabled = true;
let _automaticProbesConfigFetchedAt = 0;
let _manualProbeInProgress = false;
let _lastProcessedManualRunId = null;
let _manualProbePollTimer = null;

async function fetchAutomaticProbesEnabled(apiKey) {
  if (!apiKey) return true;
  const now = Date.now();
  if (_automaticProbesConfigFetchedAt && now - _automaticProbesConfigFetchedAt < 60_000) {
    return _automaticProbesEnabled;
  }
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/provider-access-probe/config`, {
      headers: { 'X-Mighty-Key': apiKey },
    });
    if (resp.ok) {
      const data = await resp.json();
      _automaticProbesEnabled = !!data.automatic_probes_enabled;
      _automaticProbesConfigFetchedAt = now;
    }
  } catch (e) {
    console.warn('[Mighty Probe] config fetch failed:', e.message);
  }
  return _automaticProbesEnabled;
}

async function _postProviderAccessProbe(apiKey, payload, { skipDedup = false } = {}) {
  if (!apiKey || !payload?.provider) return null;
  if (!skipDedup) {
    const cacheKey = `${payload.provider}:${payload.status}:${payload.evidence_snippet || ''}`;
    const now = Date.now();
    if (_probeReportedAt[cacheKey] && now - _probeReportedAt[cacheKey] < 120_000) return null;
    _probeReportedAt[cacheKey] = now;
  }
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/provider-access-probe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
      body: JSON.stringify({ ...payload, timestamp: new Date().toISOString() }),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      console.log(`[Mighty Probe] ${payload.provider} → ${data.status || payload.status}`, data);
    } else {
      console.warn(`[Mighty Probe] ${payload.provider} POST failed`, resp.status, data);
    }
    return { ok: resp.ok, data };
  } catch (e) {
    console.warn(`[Mighty Probe] ${payload.provider} POST error:`, e.message);
    return { ok: false, error: e.message };
  }
}

async function runProviderAccessProbeInTab(tabId, provider) {
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: runProviderAccessProbeInPage,
      args: [provider],
    });
    return r?.result || null;
  } catch (e) {
    console.warn(`[Mighty Probe] tab script failed for ${provider}:`, e.message);
    return { provider, error: e.message, blocked: false };
  }
}

async function waitForProbePageStability(tabId) {
  await waitForTabLoad(tabId, 25_000);
  await sleep(5000);
}

async function runManualProviderAccessProbe(apiKey, provider, manualRunId) {
  if (_manualProbeInProgress) {
    console.log('[Mighty Probe] manual probe already in progress — skipping');
    return;
  }
  if (!MANUAL_PROBE_PROVIDERS.includes(provider)) {
    console.warn(`[Mighty Probe] unsupported manual provider: ${provider}`);
    return;
  }
  if (manualRunId && manualRunId === _lastProcessedManualRunId) {
    return;
  }

  const entry = ACCOUNT_ENTRY[provider];
  if (!entry) {
    console.log(`[Mighty Probe] no entry URL for manual ${provider}`);
    return;
  }

  _manualProbeInProgress = true;
  console.log(`[Mighty Probe] manual run for ${provider} (${manualRunId || 'no-id'})`);
  let tab;
  try {
    tab = await createProviderTab(entry, { active: false }, 'manual_probe');
    await waitForProbePageStability(tab.id);
    const result = await runProviderAccessProbeInTab(tab.id, provider);
    const payload = result || {
      provider,
      url_visited: entry,
      signed_in_detected: false,
      private_data_detected: false,
      error: 'probe_no_result',
      failure_reason: 'probe_no_result',
    };
    if (manualRunId) payload.manual_run_id = manualRunId;
    await _postProviderAccessProbe(apiKey, payload, { skipDedup: true });
    if (manualRunId) _lastProcessedManualRunId = manualRunId;
  } catch (e) {
    await _postProviderAccessProbe(apiKey, {
      provider,
      url_visited: entry,
      signed_in_detected: false,
      private_data_detected: false,
      error: e.message,
      failure_reason: 'probe_navigation_error',
      manual_run_id: manualRunId || undefined,
    }, { skipDedup: true });
    if (manualRunId) _lastProcessedManualRunId = manualRunId;
  } finally {
    if (tab?.id) chrome.tabs.remove(tab.id).catch(() => {});
    _manualProbeInProgress = false;
  }
}

async function pollManualProbeTrigger() {
  if (_manualProbeInProgress) return;
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/provider-access-probe/manual`, {
      headers: { 'X-Mighty-Key': api_key },
    });
    if (!resp.ok) return;
    const data = await resp.json();
    if (data.lifecycle !== 'running' || !data.provider || !data.manual_run_id) return;
    if (data.manual_run_id === _lastProcessedManualRunId) return;
    await runManualProviderAccessProbe(api_key, data.provider, data.manual_run_id);
  } catch (e) {
    console.warn('[Mighty Probe] manual poll failed:', e.message);
  }
}

function ensureManualProbePolling() {
  if (_manualProbePollTimer) return;
  _manualProbePollTimer = setInterval(() => {
    pollManualProbeTrigger().catch(console.error);
  }, 5000);
}

async function runProviderAccessProbe(apiKey, provider) {
  const entry = ACCOUNT_ENTRY[provider];
  if (!entry) {
    console.log(`[Mighty Probe] no entry URL for ${provider}`);
    return;
  }

  const domainPattern = provider === 'amex' ? '*://*.americanexpress.com/*' : '*://*.delta.com/*';
  const openTabs = await chrome.tabs.query({ url: domainPattern });
  for (const tab of openTabs) {
    if (!tab.id || !tab.url) continue;
    const cfg = SITE_LOGIN_CONFIG[provider];
    if (cfg?.loginPathRe?.test(tab.url)) continue;
    console.log(`[Mighty Probe] trying open tab for ${provider}:`, tab.url);
    const result = await runProviderAccessProbeInTab(tab.id, provider);
    if (result && !result.error) {
      await _postProviderAccessProbe(apiKey, result);
      return;
    }
  }

  console.log(`[Mighty Probe] opening background tab for ${provider}`);
  let tab;
  try {
    tab = await createProviderTab(entry, { active: false }, 'automatic_probe');
    await waitForProbePageStability(tab.id);
    const result = await runProviderAccessProbeInTab(tab.id, provider);
    if (result) await _postProviderAccessProbe(apiKey, result);
  } catch (e) {
    await _postProviderAccessProbe(apiKey, {
      provider,
      url_visited: entry,
      signed_in_detected: false,
      private_data_detected: false,
      error: e.message,
      failure_reason: 'probe_navigation_error',
    });
  } finally {
    if (tab?.id) chrome.tabs.remove(tab.id).catch(() => {});
  }
}

async function runProviderAccessProbes(apiKey, accounts) {
  if (!apiKey || !Array.isArray(accounts)) return;
  const autoEnabled = await fetchAutomaticProbesEnabled(apiKey);
  if (!autoEnabled) {
    console.log('[Mighty Probe] automatic probes disabled — manual only');
    ensureManualProbePolling();
    return;
  }
  const configured = accounts
    .map(a => a.source)
    .filter(src => PROVIDER_ACCESS_PROBE_SOURCES.has(src));
  for (const provider of configured) {
    await runProviderAccessProbe(apiKey, provider);
  }
}

/** True when server config disables automatic provider navigation (manual-probe/dev mode). */
async function shouldDeferAutomaticProviderNavigation(apiKey) {
  if (!apiKey) return true;
  return !(await fetchAutomaticProbesEnabled(apiKey));
}

/** Gate sync on server config — reload/startup must not open provider tabs in manual-probe mode. */
async function runSyncIfAllowed(trigger) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) {
    console.log(`[Mighty] ${trigger}: no api_key — skip sync`);
    return;
  }
  if (await shouldDeferAutomaticProviderNavigation(api_key)) {
    console.log(
      `[Mighty] ${trigger}: manual-probe mode (automatic_probes_enabled=false) — ` +
      'deferring sync; no provider tabs will open'
    );
    ensureManualProbePolling();
    return;
  }
  return runSync();
}

async function runSync() {
  // Prevent concurrent syncs — each would spawn its own tab set.
  // _syncInProgress is in-memory only and resets on MV3 service worker restart.
  // We also check a persistent storage lock so restarts don't trigger a second sync
  // while the first is still running (service workers live ~5 min, syncs take longer).
  if (_syncInProgress) {
    console.log('[Mighty] Sync already in progress (in-memory) — skipping');
    return;
  }
  try {
    const { _sync_lock_ts } = await chrome.storage.local.get('_sync_lock_ts');
    if (_sync_lock_ts && (Date.now() - _sync_lock_ts) < 45 * 60 * 1000) {
      console.log('[Mighty] Sync lock held (persistent) — skipping duplicate trigger');
      return;
    }
    await chrome.storage.local.set({ _sync_lock_ts: Date.now() });
  } catch {}
  _syncInProgress = true;

  // Notify the server that a sync is starting so /sync/status returns running:true.
  // This lets the dashboard header update to "Syncing..." immediately on page load
  // or when the user opens the dashboard while a background sync is underway.
  // Fire-and-forget — don't let a network failure block the sync.
  chrome.storage.local.get('api_key').then(({ api_key: _notifyKey }) => {
    if (_notifyKey) {
      fetch(`${MIGHTY_URL}/api/sync/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': _notifyKey },
        body: JSON.stringify({}),
      }).catch(() => {});
    }
  }).catch(() => {});

  // Clean up ALL tabs leaked by previous crashed/interrupted sync runs
  try {
    const { _leaked_sync_tabs = [] } = await chrome.storage.local.get('_leaked_sync_tabs');
    for (const tid of _leaked_sync_tabs) {
      chrome.tabs.remove(tid).catch(() => {});
    }
    if (_leaked_sync_tabs.length) await chrome.storage.local.remove('_leaked_sync_tabs');
    // Also handle old single-tab key for backwards compatibility
    const { _leaked_sync_tab } = await chrome.storage.local.get('_leaked_sync_tab');
    if (_leaked_sync_tab) {
      chrome.tabs.remove(_leaked_sync_tab).catch(() => {});
      await chrome.storage.local.remove('_leaked_sync_tab');
    }
  } catch {}

  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) {
    _syncInProgress = false;
    await setStatus('No API key — open the extension to set it up');
    return;
  }

  if (await shouldDeferAutomaticProviderNavigation(api_key)) {
    console.log('[Mighty] runSync: manual-probe mode — aborting (no provider tabs)');
    _syncInProgress = false;
    try { await chrome.storage.local.remove('_sync_lock_ts'); } catch {}
    ensureManualProbePolling();
    return;
  }

  // Record session start time — all accounts in this sync use the same timestamp
  // so the dashboard shows consistent "Synced X" labels across all cards.
  const syncSessionTime = new Date().toISOString();

  await setStatus('Syncing…');

  // Fetch the accounts connected in the Mighty dashboard
  let accounts;
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
      headers: { 'X-Mighty-Key': api_key }
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    accounts = await resp.json();
  } catch (e) {
    _syncInProgress = false;
    await setStatus(`Error fetching accounts: ${e.message}`);
    return;
  }

  await probeAmexConnectionState(api_key, accounts);
  accounts = await _fetchExtensionAccounts(api_key);
  const amexAfterProbe = accounts.find(a => a.source === 'amex');
  if (amexAfterProbe && !amexAfterProbe.is_synced) {
    await runAmexExtraction(api_key, accounts);
    accounts = await _fetchExtensionAccounts(api_key);
  }

  // Phase 1 reliability: diagnostic access probes (does not affect account state)
  await runProviderAccessProbes(api_key, accounts);

  // Also load captured (custom) accounts from local storage
  const { captured_accounts = {} } = await chrome.storage.local.get('captured_accounts');
  const capturedList = Object.entries(captured_accounts);

  if (!accounts.length && !capturedList.length) {
    _syncInProgress = false;
    await setStatus('No connected accounts found in dashboard');
    return;
  }

  console.log(`[Mighty] Syncing ${accounts.length} accounts + ${capturedList.length} captured…`);
  let ok = 0, failed = 0;

  // Create ONE shared minimized window for the entire sync run.
  // All per-account crawls reuse this single tab — no new windows appear mid-sync.
  // TAB_SYNC_SOURCES (xfinity etc.) are excluded: they rely on the supplement watcher
  // which needs a real tab in the user's main window.
  const crawlAccounts = accounts.filter(a => {
    if (a.source === 'amex') return false;
    return (ACCOUNT_ENTRY[a.source] || a.entry_url) && !TAB_SYNC_SOURCES.has(a.source);
  });
  const tabAccounts   = accounts.filter(a => ACCOUNT_ENTRY[a.source] &&  TAB_SYNC_SOURCES.has(a.source));

  // Progress + failure tracking — written to storage so the popup can display them.
  // Must come AFTER crawlAccounts/tabAccounts are defined.
  const _totalAccounts = crawlAccounts.length + tabAccounts.length + capturedList.length;
  let _syncDone = 0;
  const _syncFailures = []; // { name, reason } — per-account failures for popup display
  async function _setProgress(account) {
    const name = account && (account.name || account.source) || '';
    const source = account && account.source || '';
    try {
      await chrome.storage.local.set({ sync_progress: { done: _syncDone, total: _totalAccounts, name, source } });
    } catch {}
    if (source) {
      fetch(`${MIGHTY_URL}/api/sync/progress`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': api_key },
        body: JSON.stringify({ source }),
      }).catch(() => {});
    }
  }
  await _setProgress(null);

  // Shared tab is only created if needed (crawl fallback) — most accounts will use
  // silent fetch and never need it. Created lazily on first tab-based fallback.
  let sharedSync = null;
  let sharedTabId = null;

  // Wrap the entire sync in try/finally to guarantee tab cleanup even on crash
  try {

  const ACCOUNT_TIMEOUT_MS = 90_000; // hard cap per account

  // Helper: get-or-create the shared tab (only when silent fetch has failed for an account)
  async function getSharedTab() {
    if (sharedSync) {
      // Verify the shared tab is still alive
      try {
        await chrome.tabs.get(sharedSync.tabId);
      } catch (e) {
        // Tab was closed — clear the reference and create a new one
        sharedSync = null;
        // fall through to create a new shared tab
      }
    }
    if (sharedSync) return sharedSync.tabId;
    sharedSync = await _createSyncWindow();
    if (sharedSync) {
      // Persist tab ID so we can clean it up even if service worker restarts mid-sync.
      // Use an array so multiple leaked tabs from concurrent restarts all get cleaned up.
      try {
        const { _leaked_sync_tabs = [] } = await chrome.storage.local.get('_leaked_sync_tabs');
        _leaked_sync_tabs.push(sharedSync.tabId);
        await chrome.storage.local.set({ _leaked_sync_tabs });
      } catch {}
      console.log('[Mighty] Created shared sync tab (tab-based fallback needed)');
    }
    return sharedSync?.tabId ?? null;
  }

  // Tab-based accounts (xfinity etc.) — supplement watcher handles these passively;
  // open a real tab only to warm up the session and let supplement fire.
  for (const account of tabAccounts) {
    await _setProgress(account);
    try {
      await Promise.race([
        syncAccountViaTab(api_key, account, [ACCOUNT_ENTRY[account.source]], syncSessionTime),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ACCOUNT_TIMEOUT_MS)),
      ]);
      ok++;
    } catch (e) {
      console.warn(`[Mighty] ${account.name}: sync skipped — ${e.message}`);
      failed++;
      const _tabReason = e.message === 'timeout' ? 'timeout' : 'no_data';
      _syncFailures.push({ name: account.name || account.source, reason: _tabReason });
      reportSyncFailure(api_key, account.source, _tabReason);
    }
    _syncDone++;
  }

  // Crawl-based accounts — silent fetch first; shared tab only as fallback
  for (const account of crawlAccounts) {
    await _setProgress(account);
    try {
      // Patch crawlAccount to use lazy shared tab
      const _lazySharedTabId = { get: getSharedTab };
      await Promise.race([
        crawlAccount(api_key, account, syncSessionTime, null, _lazySharedTabId),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ACCOUNT_TIMEOUT_MS)),
      ]);
      ok++;
      // Always run gap-fill — this visits wallet/certificate sub-pages that silent
      // fetch of the entry page misses. Creates a tab only if one doesn't exist yet.
      // Hard cap: gap-fill is best-effort; don't let it block the rest of the sync.
      try {
        const gfTabId = await getSharedTab();
        await Promise.race([
          gapFillAccount(api_key, account, syncSessionTime, 2, gfTabId),
          new Promise((_, rej) => setTimeout(() => rej(new Error('gap-fill timeout')), 60_000)),
        ]);
      } catch(gfe) {
        console.log(`[Mighty] ${account.name}: gap-fill skipped: ${gfe.message}`);
      }
    } catch (e) {
      console.warn(`[Mighty] ${account.name}: sync skipped — ${e.message}`);
      failed++;
      // Map error message to a popup reason code.
      // login_required and domain_unreachable are already reported to the server
      // inside crawlAccount — don't double-report them here.
      const _crawlReason = e.message === 'timeout' ? 'timeout'
        : e.message === 'login_required' ? 'login_required'
        : e.message === 'domain_unreachable' ? 'domain_unreachable'
        : 'no_data';
      _syncFailures.push({ name: account.name || account.source, reason: _crawlReason });
      if (_crawlReason !== 'login_required' && _crawlReason !== 'domain_unreachable') {
        reportSyncFailure(api_key, account.source, _crawlReason);
      }
    }
    _syncDone++;
  }

  // Re-sync captured accounts — try silent fetch for each URL, tab as fallback
  for (const [source, info] of capturedList) {
    if (!info.urls || !info.urls.length) continue;
    console.log(`[Mighty] Re-syncing captured: ${info.name} (${info.urls.length} URL(s))`);
    await _setProgress({ name: info.name || source, source });
    try {
      const tabId = await getSharedTab();
      await resyncCaptured(api_key, source, info, syncSessionTime, tabId);
      ok++;
    } catch (e) {
      console.warn(`[Mighty] ${info.name}: captured re-sync skipped — ${e.message}`);
      failed++;
      _syncFailures.push({ name: info.name || source, reason: 'no_data' });
    }
    _syncDone++;
  }

  await chrome.storage.local.remove('sync_progress');
  const ts = new Date(syncSessionTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const msg = `Synced at ${ts} — ${ok} ok${failed ? `, ${failed} failed` : ''}`;
  await chrome.storage.local.set({
    last_sync: syncSessionTime,
    last_sync_ok: ok,
    last_sync_failed: failed,
    last_sync_failures: _syncFailures,  // per-account failure details for popup
  });
  await setStatus(msg);
  console.log('[Mighty]', msg);

  // Normalize all recently-synced accounts to the same session timestamp so
  // the dashboard shows a consistent "Synced X ago" across every card.
  try {
    await fetch(`${MIGHTY_URL}/api/sync/finalize`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': api_key },
      body:    JSON.stringify({ session_ts: syncSessionTime }),
    });
  } catch (e) {
    console.warn('[Mighty] finalize failed (non-critical):', e.message);
  }

  } finally {
    // Guaranteed cleanup — runs even if sync throws or times out
    if (sharedSync?.tabId) {
      chrome.tabs.remove(sharedSync.tabId).catch(() => {});
      console.log('[Mighty] Shared sync tab closed');
    }
    await chrome.storage.local.remove(['_leaked_sync_tabs', '_leaked_sync_tab', '_sync_lock_ts']);
    _syncInProgress = false;

    // ── Proactive auto-login ──────────────────────────────────────────────────
    // For any account that hit login_required during this sync AND has stored
    // credentials, attempt auto-login now that _syncInProgress is cleared.
    // Runs sequentially so windows don't overlap.
    if (_pendingAutoLogins.size > 0) {
      const pending = [..._pendingAutoLogins];
      _pendingAutoLogins.clear();
      const { api_key: _alApiKey } = await chrome.storage.local.get('api_key');
      if (_alApiKey) {
        for (const source of pending) {
          const cred = await _getCred(_alApiKey, source).catch(() => null);
          if (!cred) continue;
          console.log(`[Mighty] ${source}: session expired — attempting proactive auto-login`);
          const result = await autoLogin(source, _alApiKey).catch(() => 'error');
          console.log(`[Mighty] ${source}: proactive auto-login result: ${result}`);
          if (result === 'success') {
            await _clearLoginWall(source);
            chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
            setTimeout(() => syncSingleAccount(source, _alApiKey), 2000);
          } else if (result === '2fa') {
            // MFA site: fill was successful, open real tab for user to complete MFA step.
            // api_relay.js will detect login success and fire syncSingleAccount automatically.
            const loginUrl = _AUTO_LOGIN_URLS[source] || ACCOUNT_ENTRY[source];
            console.log(`[Mighty] ${source}: MFA required — opening login tab`);
            await createProviderTab(loginUrl, { active: true }, 'credential_validation');
          }
          // 'failed' / 'error' / null: leave as login_required, user sees the red dot
        }
      }
    }
  }
}

// ── Capture mode ─────────────────────────────────────────────────────────────

async function captureCurrentTab(tabId, name, category) {
  const tab = await chrome.tabs.get(tabId);
  const url = tab.url;

  // Extract page text
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: extractPageText,
  });
  const rawText = result?.result || '';

  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) throw new Error('No API key configured');

  const source = await _pushCapture(api_key, name, category, url, rawText);
  console.log(`[Mighty] Captured "${name}" (${rawText.length} chars) → ${source}`);
  return { source };
}

async function resyncCaptured(apiKey, source, info, syncSessionTime = new Date().toISOString(), sharedTabId = null) {
  const allTexts = [];
  const useShared = !!sharedTabId;
  let win = null;
  let tabId = sharedTabId;

  if (!useShared) {
    const created = await _createSyncWindow(info.urls[0]);
    if (!created) throw new Error('Could not create sync window');
    win = created.win;
    tabId = created.tabId;
  }

  try {
    for (let i = 0; i < info.urls.length; i++) {
      await updateProviderTab(tabId, { url: info.urls[i] }, 'sync');
      await waitForTabLoad(tabId, 20_000);
      await sleep(SETTLE_MS.default);

      let result;
      try {
        [result] = await chrome.scripting.executeScript({ target: { tabId }, func: extractPageText });
      } catch (_) {
        await sleep(4_000);
        try { [result] = await chrome.scripting.executeScript({ target: { tabId }, func: extractPageText }); }
        catch (_) { result = undefined; }
      }
      const text = result?.result || '';
      if (text.length >= 100) {
        allTexts.push(`\n\n--- ${info.urls[i]} ---\n${text}`);
        console.log(`[Mighty] ${info.name} captured page ${i + 1}: ${text.length} chars`);
      }
    }
  } finally {
    if (!useShared) chrome.tabs.remove(tabId).catch(() => {});
    else updateProviderTab(tabId, { url: 'about:blank' }, 'sync').catch(() => {});
  }

  if (!allTexts.length) throw new Error('No page text captured');

  const resp = await fetch(`${MIGHTY_URL}/api/extension/capture`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
    body: JSON.stringify({
      name:      info.name,
      category:  info.category,
      url:       info.urls[0],
      raw_text:  allTexts.join('\n'),
      synced_at: syncSessionTime,
    }),
  });
  if (!resp.ok) throw new Error(`Server error ${resp.status}`);
}

// ── Auto-capture: watch tabs as user browses ──────────────────────────────────

// URL path patterns that suggest a logged-in account page
const _ACCOUNT_PATH_RE = /\/(my[-_]?account|myaccount|myunited|account[-_/]|dashboard|my[-_]?profile|profile\/|loyalty|rewards|member[-_/]|membership|portal|billing|overview|summary|wallet|benefits|perks|certificates|ecredits|statement|transactions)/i;

// URL patterns that indicate a login/auth page — skip these
const _LOGIN_PATH_RE = /\/(login|log[-_]in|signin|sign[-_]in|auth\/(login|signin)|oauth|forgot|reset[-_]password|register|signup|sign[-_]up|create[-_]account)/i;

// Domains belonging to known scheduled accounts — don't auto-capture (already synced)
const _KNOWN_DOMAINS = new Set(
  Object.values(ACCOUNT_ENTRY)
    .map(u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } })
    .filter(Boolean)
);

// In-memory debounce: url → ms timestamp of last auto-capture
const _autoCaptureRecent = new Map();
const _AUTO_COOLDOWN_MS  = 60 * 60 * 1000; // 1 hour per URL
const _AUTO_CAPTURE_MAX  = 200; // cap map size to prevent unbounded growth in long sessions

function _autoCaptureSet(key, ts) {
  // Evict oldest entry when at capacity (keep hottest URLs in map)
  if (_autoCaptureRecent.size >= _AUTO_CAPTURE_MAX) {
    const oldest = [..._autoCaptureRecent.entries()].sort((a, b) => a[1] - b[1])[0];
    if (oldest) _autoCaptureRecent.delete(oldest[0]);
  }
  _autoCaptureRecent.set(key, ts);
}

function _guessCategory(url) {
  const u = url.toLowerCase();
  if (/delta|united|southwest|american.?air|alaska.?air|marriott|hilton|hyatt|ihg|wyndham|hertz|hotel|airline|flight/.test(u)) return 'Travel';
  if (/amex|chase|wellsfargo|bankofamerica|capitalone|discover|citi|paypal|fidelity|schwab|bank|credit/.test(u)) return 'Banking & Finance';
  if (/xfinity|att|verizon|t-mobile|tmobile|comcast|spectrum|utility|electric|gas|water/.test(u)) return 'Utilities & Telecom';
  if (/amazon|target|walmart|costco|shop|store|retail/.test(u)) return 'Shopping';
  if (/netflix|hulu|spotify|disney|hbo|max|peacock|paramount|ticket/.test(u)) return 'Entertainment';
  if (/health|hospital|clinic|doctor|pharmacy|cvs|walgreen|kaiser|pamf|mychart/.test(u)) return 'Health';
  return 'Other';
}

function _nameFromTab(tab) {
  const title = (tab.title || '')
    .replace(/[-|–—]\s*(log.?in|sign.?in|home|dashboard|account|my account|overview).*/i, '')
    .replace(/\s*[-|]\s*.*$/, '')   // strip "Site Name - Tagline"
    .trim();
  if (title && title.length > 1 && title.length < 60) return title;
  try {
    return new URL(tab.url).hostname
      .replace(/^www\./, '').split('.')[0]
      .replace(/[-_]/g, ' ')
      .replace(/\b\w/g, c => c.toUpperCase());
  } catch { return 'Unknown'; }
}

async function _pushCapture(apiKey, name, category, url, rawText) {
  const resp = await fetch(`${MIGHTY_URL}/api/extension/capture`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
    body:    JSON.stringify({ name, category, url, raw_text: rawText, synced_at: new Date().toISOString() }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  const data = await resp.json();

  // Save URL in local storage for future scheduled re-syncs
  const { captured_accounts = {} } = await chrome.storage.local.get('captured_accounts');
  const source = data.source;
  if (!captured_accounts[source]) captured_accounts[source] = { name, category, urls: [] };
  captured_accounts[source].name     = name;
  captured_accounts[source].category = category;
  if (url && !captured_accounts[source].urls.includes(url)) captured_accounts[source].urls.push(url);
  await chrome.storage.local.set({ captured_accounts });
  return source;
}

// ── Extension auto-setup: read API key from /extension-setup page ────────────
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (!tab.url) return;
  if (!tab.url.includes('/extension-setup')) return;

  chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const key = document.querySelector('meta[name="mighty-api-key"]')?.content;
      if (key) sessionStorage.setItem('mighty_setup_done', '1');
      return key || null;
    },
  }).then(([result]) => {
    const key = result?.result;
    if (key) {
      chrome.storage.local.set({ api_key: key }, () => {
        console.log('[Mighty] API key auto-configured from /extension-setup — starting sync');
        fetch(`${MIGHTY_URL}/api/extension/accounts`, {
          headers: { 'X-Mighty-Key': key },
        })
          .then(r => r.ok ? r.json() : [])
          .then(accts => probeAmexConnectionState(key, accts))
          .catch(() => {});
        runSyncIfAllowed('extension-setup');
      });
    }
  }).catch(() => {});
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (!tab.url || !tab.url.startsWith('http')) return;
  if (tab.url.includes('mighty-selfserve-production.up.railway.app')) return;

  // Skip popup windows used by the scheduled sync
  try {
    const winInfo = await chrome.windows.get(tab.windowId);
    if (winInfo.type === 'popup') return;
  } catch { return; }

  // Skip login pages
  if (_LOGIN_PATH_RE.test(tab.url)) return;

  let tabDomain;
  try { tabDomain = new URL(tab.url).hostname.replace(/^www\./, ''); }
  catch { return; }

  // Known account domain: always attempt supplement capture.
  // We don't gate on _ACCOUNT_PATH_RE here — URL structures change (e.g. United
  // moved from /myaccount/mileageplus to /myunited). The supplement function already
  // rejects pages with too little text, login screens, or bot-detection walls.
  // Skip only obvious non-account paths (booking, search, homepage root).
  for (const [domain, source] of Object.entries(SUPPLEMENT_DOMAINS)) {
    if (tabDomain.endsWith(domain)) {
      const path = new URL(tab.url).pathname;
      if (path !== '/' && !_SKIP_PATH_RE.test(path)) {
        _supplementCapturePage(tabId, tab, source).catch(() => {});
      }
      return;
    }
  }

  // Unknown domain: auto-capture if path looks like an account page
  if (!_ACCOUNT_PATH_RE.test(tab.url)) return;
  const last = _autoCaptureRecent.get(_normUrl(tab.url));
  if (last && Date.now() - last < _AUTO_COOLDOWN_MS) return;
  _autoCapturePage(tabId, tab).catch(() => {});
});

async function _autoCapturePage(tabId, tab) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Give SPA a moment to render content
  await sleep(3_500);

  // Extract text and check for login signals in one injection
  let extracted;
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const hasPassword  = !!document.querySelector('input[type="password"]');
        const text         = document.body?.innerText || '';
        const lower        = text.slice(0, 2000).toLowerCase();
        const loginSignals = ['sign in', 'log in', 'create account', 'forgot password', 'enter your password']
          .filter(s => lower.includes(s)).length;
        return { text, hasPassword, loginSignals };
      },
    });
    extracted = r?.result;
  } catch { return; }

  if (!extracted) return;
  const { text, hasPassword, loginSignals } = extracted;

  // Skip login pages
  if (hasPassword || loginSignals >= 2) return;

  // Skip pages with too little content
  if (!text || text.length < 400) return;

  // Mark debounce before async work to avoid duplicate triggers
  _autoCaptureSet(_normUrl(tab.url), Date.now());

  const name     = _nameFromTab(tab);
  const category = _guessCategory(tab.url);

  // Privacy mode: for unapproved domains, only send first 500 chars of raw text
  let rawText = text;
  const baseDomain = (() => { try { return new URL(tab.url).hostname.replace(/^www\./, ''); } catch(e) { return ''; } })();
  const isApprovedDomain = Object.keys(SUPPLEMENT_DOMAINS || {}).some(d => baseDomain === d || baseDomain.endsWith('.' + d)) ||
    Object.keys(ACCOUNT_ENTRY || {}).some(k => {
      try { return new URL(ACCOUNT_ENTRY[k]).hostname.replace(/^www\./, '') === baseDomain; } catch(e) { return false; }
    });
  const payload = {};
  if (!isApprovedDomain && rawText) {
    rawText = rawText.slice(0, 500);
    payload.privacy_mode = true;
  }

  try {
    await _pushCapture(api_key, name, category, tab.url, rawText);
    console.log(`[Mighty] Auto-captured: "${name}" (${text.length} chars) from ${tab.url}`);

    // Flash badge briefly so user knows something happened
    chrome.action.setBadgeText({ text: '●' });
    chrome.action.setBadgeBackgroundColor({ color: '#059669' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 4_000);
  } catch (e) {
    _autoCaptureRecent.delete(_normUrl(tab.url)); // allow retry
    console.warn(`[Mighty] Auto-capture failed for ${tab.url}:`, e.message);
  }
}

async function _supplementCapturePage(tabId, tab, source) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Debounce: skip if we supplemented this URL recently (normalize to strip query/fragment)
  const _suppNorm = _normUrl(tab.url);
  const last = _autoCaptureRecent.get(_suppNorm);
  if (last && Date.now() - last < _AUTO_COOLDOWN_MS) return;
  _autoCaptureSet(_suppNorm, Date.now());

  // Diagnostic: yellow flash = supplement triggered for this page
  chrome.action.setBadgeText({ text: '●' });
  chrome.action.setBadgeBackgroundColor({ color: '#f59e0b' });
  setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3_000);

  await sleep(6_000); // give SPA time to render

  let extracted;
  try {
    const [r] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const hasPassword  = !!document.querySelector('input[type="password"]');
        const text         = document.body?.innerText || '';
        const lower        = text.slice(0, 2000).toLowerCase();
        const loginSignals = ['sign in', 'log in', 'create account', 'forgot password']
          .filter(s => lower.includes(s)).length;
        return { text, hasPassword, loginSignals };
      },
    });
    extracted = r?.result;
  } catch { _autoCaptureRecent.delete(_suppNorm); return; }

  // Use threshold of 3 (not 2) — known account domains often have "Sign In" in nav even when logged in
  if (!extracted || extracted.hasPassword || extracted.loginSignals >= 3) return;
  if (!extracted.text || extracted.text.length < 200) return;

  const lower = extracted.text.toLowerCase();
  if (BOT_DETECTION_PHRASES.some(p => lower.includes(p))) return;

  try {
    const resp = await fetch(`${MIGHTY_URL}/api/extension/supplement`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': api_key },
      body:    JSON.stringify({
        source,
        url:       tab.url,
        raw_text:  extracted.text.slice(0, 12_000),
        synced_at: new Date().toISOString(),
      }),
    });
    if (resp.ok) {
      console.log(`[Mighty] Supplemented ${source} from ${tab.url} (${extracted.text.length} chars)`);
      reportPathToRegistry(source, tab.url);
      chrome.action.setBadgeText({ text: '●' });
      chrome.action.setBadgeBackgroundColor({ color: '#6366f1' });
      setTimeout(() => chrome.action.setBadgeText({ text: '' }), 3_000);
    }
  } catch (e) {
    _autoCaptureRecent.delete(_suppNorm);
    console.warn(`[Mighty] Supplement failed for ${source}:`, e.message);
  }
}

// Sites that need a warm-up page visit before the real account URL,
// to establish session context and avoid bot-detection on cold navigation.
const WARMUP_URLS = {
  // Warm up on the profile page so the account session is hot before hitting wallet.
  // delta.com homepage alone isn't enough — Akamai still gates the wallet cold.
  delta:  'https://www.delta.com/myprofile/',
  // Warmup must be on customer.xfinity.com (not xfinity.com) — Akamai bot cookies
  // are domain-scoped, so warming up the wrong domain doesn't help.
  xfinity: 'https://customer.xfinity.com/',
};

// Settle time used by resyncCaptured for custom accounts. crawlAccount uses inline values.
const SETTLE_MS = {
  default:    5_000,
  subsequent: 3_000,
};

// Phrases that indicate a bot-detection or access-denied page.
const BOT_DETECTION_PHRASES = [
  'gate change',             // Delta/Akamai
  'access denied',
  'checking your browser',
  'ddos protection',
  'cf-browser-verification', // Cloudflare challenge page
  'unusual traffic from your computer',  // Google/Akamai — full phrase to avoid false positives
  'enable cookies to continue',          // More specific than 'please enable cookies'
  'cookie functionality is turned off',
  'verifying you are human',
  'security check to access',
];

// ── Bot detection helpers ────────────────────────────────────────────────────

// Random delay between page visits to avoid bot detection
async function randomDelay(minMs = 800, maxMs = 2500) {
  const ms = Math.floor(Math.random() * (maxMs - minMs)) + minMs;
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Domains known to aggressively detect bots — visit fewer pages for these
const BOT_SENSITIVE_DOMAINS = new Set([
  'delta.com', 'aa.com', 'united.com', 'chase.com', 'citi.com', 'barclays.com'
]);

function maxPagesForSite(source) {
  const url = (ACCOUNT_ENTRY[source] || source || '').replace(/https?:\/\//, '');
  for (const d of BOT_SENSITIVE_DOMAINS) {
    if (url.includes(d)) return 2; // only visit 2 pages for bot-sensitive sites
  }
  return 5; // default
}

// Persist bot-detected path counts so we skip persistent bad paths across syncs
async function markBotDetected(site, path) {
  const data = await chrome.storage.local.get('botDetectedPaths');
  const bots = data.botDetectedPaths || {};
  const key = `${site}::${path}`;
  bots[key] = (bots[key] || 0) + 1;
  await chrome.storage.local.set({ botDetectedPaths: bots });
}

async function isBotBlocked(site, path) {
  const data = await chrome.storage.local.get('botDetectedPaths');
  const bots = data.botDetectedPaths || {};
  return (bots[`${site}::${path}`] || 0) >= 2;
}

// Detect SPA/hash-routed URLs that need extra settle time after load
function isSpaUrl(url) {
  return url.includes('#/') || url.includes('/#') ||
         url.includes('xfinity.com') || url.includes('spectrum.net') ||
         url.includes('pge.com') || url.includes('att.com/my/') ||
         url.includes('southwest.com');
}

// ── Per-account sync ─────────────────────────────────────────────────────────

/**
 * Sync a bot-detection site by opening a regular foreground-invisible tab.
 * A regular tab uses the user's full browser session and avoids popup-window
 * fingerprinting that Akamai and similar systems block.
 * The SUPPLEMENT_WATCH onUpdated listener captures the page automatically.
 */
async function syncAccountViaTab(apiKey, account, urls, syncSessionTime) {
  const source = account.source;
  const url = urls[0];
  console.log(`[Mighty] Tab-sync ${source}: opening tab → ${url}`);

  // Remember the synced_at before we open the tab so we can detect a fresh capture.
  const before = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
    headers: { 'X-Mighty-Key': apiKey }
  }).then(r => r.json()).then(list => {
    const acct = list.find(a => a.source === source);
    return acct ? acct.synced_at : null;
  }).catch(() => null);

  // Open a real tab (not a popup) — SUPPLEMENT_WATCH fires on load.
  const tab = await createProviderTab(url, { active: false }, 'sync');
  const tabId = tab.id;

  // Wait up to 25 s for the page to load and be captured.
  const WAIT_MS = 25_000;
  const CHECK_EVERY = 2_000;
  let elapsed = 0;
  await new Promise(resolve => {
    const interval = setInterval(async () => {
      elapsed += CHECK_EVERY;
      // Check if the account's synced_at has updated (meaning SUPPLEMENT_WATCH fired).
      try {
        const list = await fetch(`${MIGHTY_URL}/api/extension/accounts`, {
          headers: { 'X-Mighty-Key': apiKey }
        }).then(r => r.json());
        const acct = list.find(a => a.source === source);
        if (acct && acct.synced_at && acct.synced_at !== before) {
          // Captured — normalize timestamp to session time
          clearInterval(interval);
          resolve();
          return;
        }
      } catch {}
      if (elapsed >= WAIT_MS) { clearInterval(interval); resolve(); }
    }, CHECK_EVERY);
  });

  // Close the tab we opened
  try { await chrome.tabs.remove(tabId); } catch {}

  // Align the stored timestamp with the session time so the dashboard shows
  // a consistent "Synced X ago" alongside other accounts.
  try {
    await fetch(`${MIGHTY_URL}/api/sync/finalize`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-Mighty-Key': apiKey },
      body:    JSON.stringify({ session_ts: syncSessionTime, sources: [source] }),
    });
  } catch {}

  console.log(`[Mighty] Tab-sync ${source}: complete`);
}

/**
 * Smart crawler: lands on the account's entry page, scores all discovered links,
 * visits the most account-relevant ones, and sends accumulated text to the server.
 * No hardcoded sub-paths — the crawler finds benefit/certificate/wallet pages automatically.
 */
// Autonomous gap-filling: check coverage and visit missing-field pages
async function gapFillAccount(apiKey, account, syncSessionTime, maxIterations = 2, sharedTabId = null) {
  const source = account.source;
  const entry = (account && account.entry_url) || ACCOUNT_ENTRY[source];
  if (!entry) return;

  let entryOrigin;
  try { entryOrigin = new URL(entry).origin; } catch { return; }

  let prevCoverage = -1;

  for (let iter = 0; iter < maxIterations; iter++) {
    try {
      const covResp = await fetch(`${MIGHTY_URL}/api/coverage/${source}`, {
        headers: { 'X-Mighty-Key': apiKey }
      });
      if (!covResp.ok) break;
      const cov = await covResp.json();

      const currentCoverage = cov.coverage_pct || 0;
      console.log(`[Mighty] ${source} coverage: ${currentCoverage}% (${cov.found_count}/${cov.expected_count} fields) (iter ${iter + 1})`);

      // Skip gap-filling entirely if already well-covered on first iteration
      if (iter === 0 && currentCoverage >= 85) {
        console.log(`[Mighty] ${source}: coverage already ${currentCoverage}%, skipping gap-fill`);
        break;
      }

      // STOP CONDITION: information gain < 5% → plateau reached
      if (prevCoverage >= 0 && (currentCoverage - prevCoverage) < 5) {
        console.log(`[Mighty] ${source}: coverage plateaued (${prevCoverage}% → ${currentCoverage}%), stopping`);
        break;
      }
      prevCoverage = currentCoverage;

      if (!cov.should_continue || !cov.targets || cov.targets.length === 0) {
        console.log(`[Mighty] ${source}: coverage sufficient or no targets`);
        break;
      }

      // Find known registry paths matching gap target keywords
      const knownPaths = await fetchRegistryPaths(source);

      const targetPaths = knownPaths.filter(p =>
        cov.targets.some(kw => p.toLowerCase().includes(kw))
      ).slice(0, 3); // visit at most 3 gap-fill pages per iteration

      if (targetPaths.length === 0) {
        console.log(`[Mighty] ${source}: no matching gap-fill paths found`);
        break;
      }

      console.log(`[Mighty] ${source}: gap-filling ${targetPaths.length} pages for missing: ${(cov.gaps || []).map(g => g.key).join(', ')}`);

      // Reuse the shared sync tab if available; otherwise create a temporary window
      const useShared = !!sharedTabId;
      let gfWin = null;
      let tabId = sharedTabId;
      if (!useShared) {
        const created = await _createSyncWindow(entry);
        if (!created) break;
        gfWin = created.win;
        tabId = created.tabId;
      }

      let newText = '';
      try {
        // Navigate to entry URL to establish session context
        await updateProviderTab(tabId, { url: entry }, 'sync');
        await waitForTabLoad(tabId, 15_000);
        await sleep(3_000);

        for (const path of targetPaths) {
          const fullUrl = entryOrigin + path;
          await randomDelay(1000, 2000);
          try {
            await updateProviderTab(tabId, { url: fullUrl }, 'sync');
            await waitForTabLoad(tabId, 15_000);
            await sleep(3_000);

            const [r] = await chrome.scripting.executeScript({
              target: { tabId },
              func: async function waitForContent() {
                for (let i = 0; i < 10; i++) {
                  const text = document.body ? document.body.innerText : '';
                  if (text && text.trim().length > 500) return text;
                  await new Promise(res => setTimeout(res, 500));
                }
                return document.body ? document.body.innerText : '';
              },
            });
            const pageText = r?.result || '';
            // Skip login pages — target path redirected to login wall
            if (pageText && _isSilentLoginPage(pageText)) {
              console.log(`[Mighty] gap-fill ${source} → ${fullUrl}: login page content detected — skipping`);
              continue;
            }
            if (pageText && pageText.length > 200) {
              newText += `\n\n--- ${fullUrl} ---\n${pageText}`;
              reportPathToRegistry(source, fullUrl);
            }
          } catch(e) {
            console.log(`[Mighty] gap-fill visit failed: ${fullUrl}: ${e.message}`);
          }
        }
      } finally {
        if (!useShared) chrome.tabs.remove(tabId).catch(() => {});
      }

      if (newText.trim().length < 200) break;

      // Push gap-fill data to server (merged into existing raw_text by api_data_sync)
      const syncResp = await fetch(`${MIGHTY_URL}/api/data/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key:     apiKey,
          source:      source,
          sync_source: 'extension',
          gap_fill:    true,
          data: {
            name:     account.name,
            icon:     account.icon,
            color:    account.color,
            status:   'ok',
            items:    [],
            raw_text: newText.slice(0, 20_000),
          },
          synced_at: syncSessionTime,
        }),
      });
      if (!syncResp.ok) break;

      console.log(`[Mighty] ${source}: gap-fill iteration ${iter + 1} complete`);
    } catch(e) {
      console.log(`[Mighty] ${source}: gap-fill error: ${e.message}`);
      break;
    }
  }

  // ── Unconditional certificate/wallet pass ──────────────────────────────────
  // Coverage-based gap-fill stops early (>=85%) and may skip certificate pages
  // even when no certificates have been captured.  Always fetch registry paths
  // whose names suggest redeemable benefits, regardless of coverage score.
  try {
    const certKeywords = ['certificate', 'cert', 'companion', 'wallet', 'award', 'ecredit', 'voucher', 'upgrade', 'free-night', 'reward'];
    const allPaths = await fetchRegistryPaths(source);
    const certPaths = allPaths.filter(p =>
      certKeywords.some(kw => p.toLowerCase().includes(kw))
    ).slice(0, 4);

    if (certPaths.length > 0) {
      console.log(`[Mighty] ${source}: unconditional cert pass — ${certPaths.length} path(s): ${certPaths.join(', ')}`);
      const tabId = sharedTabId || null;
      if (!tabId) {
        console.log(`[Mighty] ${source}: no shared tab for cert pass, skipping`);
      } else {
        let certText = '';
        await updateProviderTab(tabId, { url: entry }, 'sync');
        await waitForTabLoad(tabId, 15_000);
        await sleep(2_000);

        for (const path of certPaths) {
          const fullUrl = entryOrigin + path;
          await randomDelay(800, 1500);
          try {
            await updateProviderTab(tabId, { url: fullUrl }, 'sync');
            await waitForTabLoad(tabId, 15_000);
            await sleep(4_000); // extra settle time for SPAs

            const [r] = await chrome.scripting.executeScript({
              target: { tabId },
              func: async function waitForContent() {
                for (let i = 0; i < 14; i++) {
                  const text = document.body ? document.body.innerText : '';
                  if (text && text.trim().length > 300) return text;
                  await new Promise(res => setTimeout(res, 500));
                }
                return document.body ? document.body.innerText : '';
              },
            });
            const pageText = r?.result || '';

            // Check for login redirect — cert paths require auth; if they redirect to
            // login, the session has expired and we must not push login page content.
            let _certLoginDetected = false;
            try {
              const _certTab = await chrome.tabs.get(tabId);
              const _certUrl = _certTab.url || '';
              if (_certUrl && /\/(sign-?in|log-?in|login)(\/|$|\?)/i.test(new URL(_certUrl).pathname)) {
                console.log(`[Mighty] cert pass ${source} → ${fullUrl}: redirected to login URL — skipping`);
                _certLoginDetected = true;
              }
            } catch (_) {}
            if (!_certLoginDetected && pageText && _isSilentLoginPage(pageText)) {
              console.log(`[Mighty] cert pass ${source} → ${fullUrl}: login page content detected — skipping`);
              _certLoginDetected = true;
            }
            if (_certLoginDetected) continue;

            if (pageText && pageText.length > 200) {
              certText += `\n\n--- ${fullUrl} ---\n${pageText}`;
              reportPathToRegistry(source, fullUrl);
            }
          } catch(e) {
            console.log(`[Mighty] cert pass visit failed: ${fullUrl}: ${e.message}`);
          }
        }

        if (certText.trim().length > 200) {
          await fetch(`${MIGHTY_URL}/api/data/sync`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              api_key:     apiKey,
              source:      source,
              sync_source: 'extension',
              gap_fill:    true,
              data: {
                name:     account.name,
                icon:     account.icon,
                color:    account.color,
                status:   'ok',
                items:    [],
                raw_text: certText.slice(0, 20_000),
              },
              synced_at: syncSessionTime,
            }),
          }).catch(() => {});
          console.log(`[Mighty] ${source}: cert pass complete — ${certText.length} chars`);
        }
      }
    }
  } catch(e) {
    console.log(`[Mighty] ${source}: cert pass error: ${e.message}`);
  }
}

async function crawlAccount(apiKey, account, syncSessionTime, sharedTabId = null, _lazySharedTab = null) {
  const entry = account.entry_url || ACCOUNT_ENTRY[account.source];
  if (!entry) {
    console.log(`[Mighty] No entry URL for ${account.source} — skipping`);
    return;
  }

  const tracker = createStageTracker(apiKey, account.source);
  tracker.startConnection();

  // ── Try silent fetch first (no tab, completely invisible) ───────────────────
  // Extension service workers can fetch cross-origin pages with user cookies via
  // credentials: 'include' + <all_urls> host_permissions. Zero UI, zero tabs.
  const silentText = await _silentFetchPages(account.source, account);
  if (silentText) {
    // _silentFetchPages already verifies: non-login redirect URL, non-login page content,
    // and sufficient text length. If it returned content, the user IS logged in —
    // clear any stale login wall flag immediately.
    await _clearLoginWall(account.source);
    {
      console.log(`[Mighty] ${account.name}: silent fetch succeeded (${silentText.length} chars) — no tab needed`);
      for (const url of extractTrackedUrls(silentText)) tracker.noteUrl(url);
      tracker.finishConnection({
        success: true,
        sessionVerified: true,
        loginDetectionMethod: 'silent_fetch',
      });
      tracker.startNavigation();
      tracker.finishNavigation({ success: true });
      tracker.startCapture();
      tracker.finishCapture({
        success: true,
        rawTextSize: silentText.length,
        jsonPayloadSize: jsonPayloadSize(silentText),
        evidenceMarkers: summarizeEvidenceMarkers(silentText),
      });
      await tracker.flush();
      const silentPayload = mergeSyncNetworkIntoRawText(silentText, account.source);
      const pushResp = await fetch(`${MIGHTY_URL}/api/data/sync`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          api_key:     apiKey,
          source:      account.source,
          sync_source: 'extension',
          pipeline_run_id: tracker.runId,
          data: {
            name:     account.name,
            icon:     account.icon,
            color:    account.color,
            status:   'ok',
            items:    [],
            raw_text: silentPayload.slice(0, 40_000),
          },
          synced_at: syncSessionTime,
        }),
      });
      if (pushResp.ok) return;
      // Push failed — fall through to tab-based
      console.warn(`[Mighty] ${account.name}: silent push failed, falling back to tab`);
    }
  }

  // ── Cookie-based auth check ────────────────────────────────────────────────
  // For sites with a known auth cookie signal, check it before any tab crawl.
  // Cookie presence/absence is definitive and instant — no page load needed.
  // To add a site: compare COOKIES_* debug dumps logged-in vs logged-out above.
  let _cookieAuthConfirmed = false;
  if (SILENT_FETCH_SKIP.has(account.source) && _AUTH_COOKIE_SIGNALS[account.source]) {
    const sig = _AUTH_COOKIE_SIGNALS[account.source];
    try {
      const entryUrl = ACCOUNT_ENTRY[account.source];
      const cookies  = await chrome.cookies.getAll({ url: entryUrl, name: sig.name });
      const authCookie = cookies.find(c => c.value.length >= sig.minLen);
      if (!authCookie) {
        console.log(`[Mighty] ${account.name}: auth cookie "${sig.name}" absent — login_required`);
        await _markLoginWall(account.source);
        tracker.finishConnection({
          success: false,
          failureReason: 'login_required',
          sessionVerified: false,
          loginDetectionMethod: 'cookie',
        });
        await tracker.flush();
        await reportSyncFailure(apiKey, account.source, 'login_wall', tracker.runId);
        chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
        _pendingAutoLogins.add(account.source);
        throw new Error('login_required');
      }
      _cookieAuthConfirmed = true;
      console.log(`[Mighty] ${account.name}: auth cookie "${sig.name}" present (${authCookie.value.length} chars) — session confirmed`);
    } catch (e) {
      if (e.message === 'login_required') throw e;
      console.warn(`[Mighty] ${account.name}: cookie auth check failed — ${e.message} — falling through to pre-flight`);
    }
  }

  // ── Pre-flight login check (disabled for SPA/bot-protected sites) ────────────
  // Headless fetch-based pre-flight only works for simple sites that do server-side
  // HTTP 302 redirects on unauthenticated access. SPA sites (everything in
  // SILENT_FETCH_SKIP) do auth in JavaScript, so the fetch lands on a page shell
  // that looks fine — or worse, Akamai/bot-protection intercepts the headless fetch
  // and returns a redirect that looks like a login wall even when logged in.
  //
  // For SILENT_FETCH_SKIP sites, the tab crawl is the universal reliable check:
  //   - post-settle URL check catches JS-driven login redirects
  //   - _isSilentLoginPage catches login form content
  //   - zero _subSuccesses catches error-frame responses (e.g. PA Utilities)
  // Cookie signals (_AUTH_COOKIE_SIGNALS) provide a fast-path to skip the tab
  // entirely when we already know the session is live — but they're optional.
  // Skipped when cookie check already confirmed the session above.
  // ── Tab-based fallback (SPA sites / login-gated / insufficient silent content) ─
  // Resolve the shared tab lazily — only created now if actually needed
  if (_lazySharedTab) {
    sharedTabId = await _lazySharedTab.get();
  }

  const warmup = WARMUP_URLS[account.source];
  let baseDomain;
  try {
    // Use override if the account spans multiple subdomains (e.g. pa_utilities spans
    // myaccount.cityofpaloalto.org and utilities.cityofpaloalto.org).
    baseDomain = ACCOUNT_BASE_DOMAIN_OVERRIDE[account.source]
               || new URL(entry).hostname.replace(/^www\./, '');
  } catch {
    console.error(`[Mighty] Invalid entry URL for ${account.source}: ${entry}`);
    return;
  }

  const MAX_SUBPAGES   = maxPagesForSite(account.source);
  const ENTRY_SETTLE   = 5_000;
  const SUBPAGE_SETTLE = 3_000;
  const allText        = [];
  const visitedNorm    = new Set();

  // Use shared tab if provided; otherwise create a dedicated minimized window
  const useShared = !!sharedTabId;
  let win = null;
  let tabId = sharedTabId;
  if (!useShared) {
    const created = await _createSyncWindow(warmup || entry);
    if (!created) throw new Error('Could not create sync window');
    win = created.win;
    tabId = created.tabId;
  }

  // ── Rogue-tab containment ────────────────────────────────────────────────────
  // Some sites (e.g. Hilton privacy banner) call window.open() or click target=_blank
  // links, spawning new tabs in the user's main Chrome window.  Even with active:false
  // the newly created tab briefly becomes the active tab (rightmost position), causing
  // the user's view to jump.  Two-layer defence:
  //
  //   Layer 1 (proactive): on every page load inside the sync tab, inject a MAIN-world
  //   script that overrides window.open → no-op and suppresses _blank link clicks.
  //   This prevents the rogue tab from being created at all.
  //
  //   Layer 2 (reactive): onCreated listener closes any tab that slips through before
  //   Layer 1 runs (e.g. during the very first page load before injection completes).
  //   When the rogue tab opened in the user's main window we try to deactivate it first
  //   so Chrome re-activates the user's previous tab before we remove the rogue one.

  const _blockWindowOpen = async () => {
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        world: 'MAIN',
        func: () => {
          // Suppress any attempt to open a new window or tab
          window.open = function() { return null; };
          // Suppress _blank link clicks that bypass window.open
          document.addEventListener('click', function(e) {
            var t = e.target && e.target.closest('a[target="_blank"]');
            if (t) { e.preventDefault(); e.stopImmediatePropagation(); }
          }, true);
        },
      });
    } catch (_) {}
  };

  // Re-inject on every navigation of the sync tab so new pages also get blocked.
  // Fire on BOTH 'loading' (catches window.open calls that happen early during page
  // load, before 'complete' fires) and 'complete' (re-blocks after any late injection).
  const _blockOpenOnLoad = (updatedTabId, changeInfo) => {
    if (updatedTabId !== tabId) return;
    if (changeInfo.status === 'loading' || changeInfo.status === 'complete') {
      _blockWindowOpen();
    }
  };
  chrome.tabs.onUpdated.addListener(_blockOpenOnLoad);

  // Backup: close any rogue tab that still slips through.
  // Capture win?.id now — win is null when useShared=true (shared popup window), and
  // referencing win.id inside the async listener would throw a TypeError that the
  // catch block silently swallows, skipping the deactivation step entirely.
  const _syncWinId = win?.id ?? null;
  _dbg('CRAWL_START', { source: account.source, tabId, syncWinId: _syncWinId, useShared });
  const _closeRogueTab = async (newTab) => {
    if (newTab.openerTabId !== tabId) return;
    _dbg('ROGUE_TAB', { rogueId: newTab.id, rogueWin: newTab.windowId, syncWin: _syncWinId, url: newTab.pendingUrl || newTab.url });
    try {
      // If opened outside the sync popup window, deactivate it first so Chrome
      // restores the user's previous tab before we remove it — preventing the
      // visible "jump to rightmost tab" glitch.
      // When useShared=true (_syncWinId=null) we always deactivate because we
      // don't have a dedicated sync window to compare against.
      if (_syncWinId == null || newTab.windowId !== _syncWinId) {
        await updateProviderTab(newTab.id, { active: false }, 'sync').catch(() => {});
        _dbg('ROGUE_DEACTIVATED', { rogueId: newTab.id });
      }
    } catch (e) { _dbg('ROGUE_ERR', { err: e.message }); }
    chrome.tabs.remove(newTab.id).catch(() => {});
  };
  chrome.tabs.onCreated.addListener(_closeRogueTab);

  const _reportLoginRequired = async (loginDetectionMethod) => {
    tracker.finishConnection({
      success: false,
      failureReason: 'login_required',
      sessionVerified: false,
      loginDetectionMethod,
    });
    await tracker.flush();
    await _markLoginWall(account.source);
    await reportSyncFailure(apiKey, account.source, 'login_wall', tracker.runId);
    chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
  };

  beginSyncNetworkCapture(account.source);
  try {
    // Helper: mark the sync tab in the ISOLATED world so api_relay.js skips
    // its login-detection poll — otherwise api_relay.js detects the login form
    // in the sync popup (document.hidden=false) and triggers a dashboard reload
    // which auto-starts another sync, creating an infinite loop of Hilton tabs.
    const _markSyncTab = async () => {
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          world:  'ISOLATED',
          func:   () => { window.__mightySyncTab = true; },
        });
        await chrome.scripting.executeScript({
          target: { tabId },
          world:  'MAIN',
          func:   () => { window.__mightySyncCapture = true; },
        });
      } catch (_) {}
      // Simultaneously block window.open in the MAIN world
      await _blockWindowOpen();
    };

    // Navigate to the entry URL (always — shared tab may be on a different domain)
    await updateProviderTab(tabId, { url: warmup || entry }, 'sync');
    await waitForTabLoad(tabId, 15_000);
    await _markSyncTab(); // prevent api_relay.js from detecting login in this tab

    // Only do warmup re-navigation when warmup is a DIFFERENT URL from entry.
    // When warmup === entry (e.g. Delta), skipping the redundant re-navigation avoids
    // a 3-second blind window where the auth redirect fires before the settle listener
    // is registered.
    if (warmup && warmup !== entry) {
      await sleep(3_000);
      await updateProviderTab(tabId, { url: entry }, 'sync');
      await waitForTabLoad(tabId, 15_000);
      await _markSyncTab();
    }

    // Helper: detect login/auth URLs using three layers (most→least specific):
    //  1. Per-site SITE_LOGIN_CONFIG (exact hostname / curated path regex)
    //  2. Generic path regex (_LOGIN_PATH_RE)
    //  3. Auth subdomain prefix (login.*, sso.*, auth.*, ...)
    const _isLoginUrl = (u) => {
      try {
        const { hostname, pathname } = new URL(u);
        // Layer 1 — site-specific config
        const cfg = SITE_LOGIN_CONFIG[account.source];
        if (cfg) {
          if (cfg.loginHostnames?.includes(hostname)) return true;
          if (cfg.loginPathRe?.test(pathname)) return true;
        }
        // Layer 2 — generic path patterns
        if (_LOGIN_PATH_RE.test(pathname)) return true;
        // Layer 3 — auth subdomain (login.marriott.com, sso.example.com, …)
        const sub = hostname.split('.')[0].toLowerCase();
        return /^(login|sso|auth|signin|sign-in|logon|authenticate|identity)$/.test(sub);
      } catch { return false; }
    };

    // Abort if domain is unreachable (DNS failure) or redirected to a different domain
    // Use a flag variable — raw throw would be swallowed by the outer catch (_) {}
    let _urlCheckFailure = null;
    try {
      const currentTab = await chrome.tabs.get(tabId);
      const tabUrl = currentTab.url || '';
      // Chrome error page means DNS failure or network error
      if (tabUrl.startsWith('chrome-error://') || tabUrl.startsWith('about:neterror')) {
        console.log(`[Mighty] ${account.name}: domain unreachable (${tabUrl}) — reporting`);
        const _ak = apiKey;
        if (_ak) {
          tracker.finishConnection({
            success: false,
            failureReason: 'domain_unreachable',
            sessionVerified: false,
          });
          await tracker.flush();
          await reportSyncFailure(_ak, account.source, 'domain_unreachable', tracker.runId);
        }
        _urlCheckFailure = 'domain_unreachable';
      } else {
        // Detect unexpected domain redirect (e.g. utilities.cityofpaloalto.org → paloalto.gov)
        try {
          const expectedDomain = baseDomain.split('.').slice(-2).join('.');
          const landedDomain   = new URL(tabUrl).hostname.split('.').slice(-2).join('.');
          if (landedDomain && expectedDomain && landedDomain !== expectedDomain) {
            console.log(`[Mighty] ${account.name}: domain redirected ${expectedDomain} → ${landedDomain} — reporting`);
            const _ak = apiKey;
            if (_ak) {
              tracker.finishConnection({
                success: false,
                failureReason: 'domain_unreachable',
                sessionVerified: false,
              });
              await tracker.flush();
              await reportSyncFailure(_ak, account.source, 'domain_moved', tracker.runId);
            }
            _urlCheckFailure = 'domain_unreachable';
          }
        } catch (_) {}
        if (!_urlCheckFailure && tabUrl && _isLoginUrl(tabUrl)) {
          console.log(`[Mighty] ${account.name}: redirected to login URL — reporting login_required`);
          await _reportLoginRequired('url_redirect');
          _urlCheckFailure = 'login_required';
        }
      }
    } catch (_) {}
    if (_urlCheckFailure) throw new Error(_urlCheckFailure);

    // Settle while actively watching for login redirects.
    // SPA sites (e.g. Delta) serve an initial shell page that fires 'complete' immediately,
    // then run a JS auth check and redirect to login seconds later. A fixed sleep + single
    // URL snapshot misses redirects that fire mid-settle. The active listener below catches
    // them the instant Chrome reports changeInfo.url — regardless of timing.
    // For SILENT_FETCH_SKIP sites we wait up to 12s (vs 5s) to accommodate slow auth checks.
    const _settleMs = SILENT_FETCH_SKIP.has(account.source) ? 12_000 : ENTRY_SETTLE;
    const _settledState = await new Promise(resolve => {
      let _resolved = false;
      const _settleListener = (updatedTabId, changeInfo) => {
        if (updatedTabId !== tabId || _resolved) return;
        const url = changeInfo.url || '';
        if (url && _isLoginUrl(url)) {
          _resolved = true;
          chrome.tabs.onUpdated.removeListener(_settleListener);
          resolve('login_redirect');
        }
      };
      chrome.tabs.onUpdated.addListener(_settleListener);
      sleep(_settleMs).then(() => {
        if (!_resolved) {
          _resolved = true;
          chrome.tabs.onUpdated.removeListener(_settleListener);
          resolve('settled');
        }
      });
    });

    if (_settledState === 'login_redirect') {
      console.log(`[Mighty] ${account.name}: login redirect detected during settle — reporting login_required`);
      await _reportLoginRequired('url_redirect');
      throw new Error('login_required');
    }

    // Also snapshot URL after settle — catches redirects that completed before the listener
    // was registered (gap between pre-settle check and listener attachment).
    let _settledUrlFailure = null;
    try {
      const settledTab = await chrome.tabs.get(tabId);
      const settledUrl = settledTab.url || '';
      if (settledUrl && _isLoginUrl(settledUrl)) {
        console.log(`[Mighty] ${account.name}: login URL detected after settle — reporting login_required`);
        await _reportLoginRequired('url_redirect');
        _settledUrlFailure = 'login_required';
      }
    } catch (_) {}
    if (_settledUrlFailure) throw new Error(_settledUrlFailure);

    // Dismiss session-timeout modals
    try {
      // For SILENT_FETCH_SKIP (SPA) sites, skip dismissSessionTimeouts entirely.
      // Their popup modals are login prompts, not session-keepalive dialogs — clicking
      // them away allows the page to show public marketing content and falsely succeed.
      // The login modal content is caught below by _isSilentLoginPage(entryText).
      if (!SILENT_FETCH_SKIP.has(account.source)) {
        const [d] = await chrome.scripting.executeScript({ target: { tabId }, func: dismissSessionTimeouts });
        if (d?.result) { console.log(`[Mighty] ${account.name}: dismissed session modal`); await sleep(3_000); }
      }
    } catch (_) {}

    // Detect login form via password field EXISTENCE (not visibility rect).
    // getBoundingClientRect() returns zero in minimized windows, so we rely on
    // DOM presence instead. Double-check pattern: if found, wait 5s and look again.
    // SPAs like United briefly render a login form while verifying the session cookie —
    // if the form disappears, it was transient (user IS logged in).
    // Sites where the user is genuinely logged out keep the form → correctly reported.
    const _pwCheck = async () => {
      try {
        const [r] = await chrome.scripting.executeScript({
          target: { tabId },
          func: () => document.querySelectorAll('input[type="password"]').length > 0,
        });
        return r?.result === true;
      } catch { return false; }
    };
    if (await _pwCheck()) {
      // Possibly a transient SPA auth flash — wait and verify it's still there
      await sleep(5_000);
      if (await _pwCheck()) {
        console.log(`[Mighty] ${account.name}: login form persists after recheck — reporting login_required`);
        await _reportLoginRequired('password_field');
        throw new Error('login_required');
      }
      console.log(`[Mighty] ${account.name}: login form was transient (session resolved) — continuing`);
    }

    // Extract entry page text — poll up to 10s for SPA rendering (same pattern as subpages).
    // A single extractPageText call can miss content if the SPA hasn't finished rendering
    // by the time ENTRY_SETTLE expires (e.g. United resolves the auth cookie check late).
    // If stripped text stays <100 chars, fall back to full body innerText — the content
    // may be inside a <header> or <nav> element that extractPageText strips.
    let entryEvidence = '';
    let entryText = '';
    try {
      const [r] = await chrome.scripting.executeScript({
        target: { tabId },
        func: waitForEntryEvidence,
      });
      entryEvidence = r?.result?.evidence || '';
      entryText = r?.result?.visibleText || '';
    } catch (_) {}

    if (BOT_DETECTION_PHRASES.some(p => entryText.toLowerCase().includes(p))) {
      console.warn(`[Mighty] ${account.name}: bot detection on entry page — skipping`);
      throw new Error('no_data');
    }

    // Check entry page content for login page signals — catches cases where
    // URL checks miss (non-standard login URLs, JS redirects that settle after
    // our URL check) and where getBoundingClientRect() returns 0 in minimized windows.
    if (entryText.length >= 100 && _isSilentLoginPage(entryText)) {
      console.log(`[Mighty] ${account.name}: login page content detected in tab — reporting login_required`);
      await _reportLoginRequired('content_signal');
      throw new Error('login_required');
    }

    tracker.finishConnection({
      success: true,
      sessionVerified: true,
      loginDetectionMethod: _cookieAuthConfirmed ? 'cookie' : 'tab_session',
    });
    tracker.startNavigation();

    if (entryEvidence || entryText.length >= 100) {
      allText.push(entryEvidence || `\n\n--- ${entry} ---\n${entryText}`);
      visitedNorm.add(_normUrl(entry));
      tracker.noteUrl(entry);
    }

    // ── Discover subpages ───────────────────────────────────────────────────────
    let rawLinks = [];
    try {
      const [lr] = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
          href: a.href || '',
          text: (a.textContent || a.getAttribute('aria-label') || '')
            .replace(/\s+/g, ' ').trim().slice(0, 100),
        })),
      });
      rawLinks = lr?.result || [];
    } catch (_) {}

    const scored = rawLinks
      .map(l => ({ ...l, score: _scoreLink(l.href, l.text, baseDomain) }))
      .filter(l => l.score > 0)
      .sort((a, b) => b.score - a.score);

    const toVisit = [];

    // For SPA sites (SILENT_FETCH_SKIP), homepage link scoring is unreliable —
    // the homepage loads the same public marketing content whether logged in or not,
    // and high-value terms like "benefit", "status", "reward" appear on public pages.
    // Use ONLY registry paths for these sites: they come from prior successful logged-in
    // syncs and are known authenticated pages. When the session is expired, they redirect
    // to login and the all-login-redirects check fires correctly.
    // For non-SPA sites and first syncs (empty registry), fall back to scored homepage links.
    const _useRegistryOnly = SILENT_FETCH_SKIP.has(account.source);

    if (!_useRegistryOnly) {
      for (const link of scored) {
        if (toVisit.length >= MAX_SUBPAGES) break;
        const norm = _normUrl(link.href);
        if (visitedNorm.has(norm)) continue;
        visitedNorm.add(norm);
        toVisit.push(link);
      }
    }

    // Supplement with registry-known paths not already discovered.
    // For SILENT_FETCH_SKIP (SPA) sites: filter registry paths to only those under
    // known authenticated path prefixes. Public marketing pages can accumulate in the
    // registry from old syncs and will load without any login redirect, falsely
    // signalling a successful session.
    try {
      const regPaths = await fetchRegistryPaths(account.source);
      const entryOrigin = new URL(entry).origin;
      const _authPrefixes = _useRegistryOnly ? (_AUTH_PATH_PREFIXES[account.source] || null) : null;
      for (const path of regPaths) {
        if (toVisit.length >= MAX_SUBPAGES) break;
        // Auth-prefix filter for SPA sites
        if (_authPrefixes) {
          const pathLower = path.toLowerCase();
          const isAuthPath = _authPrefixes.some(p => pathLower.startsWith(p.toLowerCase()));
          if (!isAuthPath) {
            console.log(`[Mighty] ${account.name}: skipping public registry path ${path}`);
            continue;
          }
        }
        const regUrl = entryOrigin + path;
        const norm   = _normUrl(regUrl);
        if (!visitedNorm.has(norm)) {
          visitedNorm.add(norm);
          toVisit.push({ href: regUrl, text: '', score: 5, fromRegistry: true });
        }
      }
    } catch (_) {}

    // For SILENT_FETCH_SKIP sites: always prepend the probe path so it is visited
    // FIRST, regardless of how many registry paths exist. This catches cases like
    // Delta where registry paths (/us/en/my-account/*) get Akamai bot-detected
    // instead of login-redirecting, while the probe (/my-profile/certificates)
    // correctly SPA-redirects to login when not authenticated.
    if (_useRegistryOnly) {
      const probePath = _AUTH_PROBE_PATHS[account.source];
      if (probePath) {
        try {
          const probeUrl = new URL(entry).origin + probePath;
          const probeNorm = _normUrl(probeUrl);
          if (!visitedNorm.has(probeNorm)) {
            visitedNorm.add(probeNorm);
            toVisit.unshift({ href: probeUrl, text: '', score: 10, fromRegistry: false });
            console.log(`[Mighty] ${account.name}: prepending auth probe ${probeUrl}`);
          }
        } catch (_) {}
      }
    }

    console.log(`[Mighty] ${account.name}: ${scored.length} candidates → visiting top ${toVisit.length}`);

    // ── Visit subpages ──────────────────────────────────────────────────────────
    // Track login redirects vs successes to detect "user not logged in" state.
    // If every subpage we attempted redirected to a login page, the session is gone.
    let _subLoginRedirects = 0;
    let _subSuccesses = 0;

    for (const link of toVisit) {
      try {
        // Skip paths that have been bot-detected 2+ times
        let linkPath;
        try { linkPath = new URL(link.href).pathname; } catch { linkPath = link.href; }
        if (await isBotBlocked(account.source, linkPath)) {
          console.log(`[Mighty] ${account.name} → ${link.href}: bot-blocked path, skipping`);
          continue;
        }

        // Random inter-page delay to avoid bot detection
        await randomDelay();

        await updateProviderTab(tabId, { url: link.href }, 'discovery');
        await waitForTabLoad(tabId, 15_000);

        // Settle while watching for login redirect — catches slow JS auth checks.
        // Same race condition as the entry page: SPA shell fires 'complete' first,
        // then redirects to login via JavaScript after a variable delay.
        const extraSettle = isSpaUrl(link.href) ? 4000 : 1000;
        const _subSettleMs = SUBPAGE_SETTLE + extraSettle;
        const _subState = await new Promise(resolve => {
          let _done = false;
          const _subSettleListener = (updatedTabId, changeInfo) => {
            if (updatedTabId !== tabId || _done) return;
            const url = changeInfo.url || '';
            if (url && _isLoginUrl(url)) {
              _done = true;
              chrome.tabs.onUpdated.removeListener(_subSettleListener);
              resolve('login_redirect');
            }
          };
          chrome.tabs.onUpdated.addListener(_subSettleListener);
          sleep(_subSettleMs).then(() => {
            if (!_done) {
              _done = true;
              chrome.tabs.onUpdated.removeListener(_subSettleListener);
              resolve('settled');
            }
          });
        });

        if (_subState === 'login_redirect') {
          console.log(`[Mighty] ${account.name} → ${link.href}: login redirect detected during settle — skipping`);
          _subLoginRedirects++;
          continue;
        }

        // Also snapshot URL — catches redirects that completed before listener registration
        try {
          const _subTabInfo = await chrome.tabs.get(tabId);
          if (_subTabInfo.url && _isLoginUrl(_subTabInfo.url)) {
            console.log(`[Mighty] ${account.name} → ${link.href}: subpage redirected to login URL — skipping`);
            _subLoginRedirects++;
            continue;
          }
        } catch (_) {}

        try {
          const [d] = await chrome.scripting.executeScript({ target: { tabId }, func: dismissSessionTimeouts });
          if (d?.result) await sleep(2_000);
        } catch (_) {}

        const [r] = await chrome.scripting.executeScript({
          target: { tabId },
          func: waitForSubpageEvidence,
        });
        const pageEvidence = r?.result?.evidence || '';
        const text = r?.result?.visibleText || '';

        if (BOT_DETECTION_PHRASES.some(p => text.toLowerCase().includes(p))) {
          console.warn(`[Mighty] ${account.name} → ${link.href}: bot detected — skipping`);
          await markBotDetected(account.source, linkPath);
          continue;
        }
        if (text.length < 100) {
          console.warn(`[Mighty] ${account.name} → ${link.href}: too short (${text.length} chars) — skipping`);
          continue;
        }

        // Detect login page content — catches SPAs that render a login modal without
        // changing the URL (e.g. Delta shows a login overlay while URL stays /en/us/...)
        if (_isSilentLoginPage(text)) {
          console.log(`[Mighty] ${account.name} → ${link.href}: login page content in subpage — skipping`);
          _subLoginRedirects++;
          continue;
        }

        console.log(`[Mighty] ${account.name} → ${link.href}: ${text.length} chars`);
        allText.push(pageEvidence || `\n\n--- ${link.href} ---\n${text}`);
        reportPathToRegistry(account.source, link.href);
        tracker.noteUrl(link.href);
        _subSuccesses++;

      } catch (e) {
        console.warn(`[Mighty] ${account.name} → ${link.href}: ${e.message}`);
      }
    }

    // If every subpage we attempted was a login redirect and none returned real content,
    // the session has expired — report login_required and abort.
    //
    // For SILENT_FETCH_SKIP (SPA sites): even ONE login redirect from a registry path
    // means the session is expired — all registry paths come from prior authenticated
    // syncs, so a login redirect on any of them is definitive proof.
    // For other sites: require ALL subpages to have been login redirects (more lenient,
    // because public marketing pages might redirect while account pages succeed).
    // If cookie auth confirmed the session above, don't let _isSilentLoginPage false-positives
    // (e.g. United's SPA showing login UI elements on authenticated pages) override it.
    // For non-SILENT_FETCH_SKIP sites: if we attempted subpages and none succeeded
    // (whether they redirected to login or threw frame/network errors), treat it as
    // a login wall. PA Utilities renders an error frame instead of a login redirect,
    // so _subLoginRedirects stays 0 — but zero successes is equally definitive.
    const _loginWallCondition = SILENT_FETCH_SKIP.has(account.source)
      ? (!_cookieAuthConfirmed && _subLoginRedirects > 0 && toVisit.length > 0)
      : (_subSuccesses === 0 && toVisit.length > 0);
    if (_loginWallCondition) {
      console.log(`[Mighty] ${account.name}: ${_subLoginRedirects}/${toVisit.length} subpages were login redirects — session expired`);
      tracker.finishNavigation({ success: false, failureReason: 'login_required' });
      tracker.startCapture();
      tracker.finishCapture({ success: false, failureReason: 'login_wall', rawTextSize: 0 });
      await tracker.flush();
      await _markLoginWall(account.source);
      await reportSyncFailure(apiKey, account.source, 'login_wall', tracker.runId);
      chrome.tabs.query({ url: `${MIGHTY_URL}/*` }, ts => ts.forEach(t => chrome.tabs.reload(t.id)));
      _pendingAutoLogins.add(account.source);
      throw new Error('login_required');
    }

    // ── Push to server ──────────────────────────────────────────────────────────
    if (allText.length === 0) {
      tracker.finishNavigation({ success: false, failureReason: 'no_pages_visited' });
      tracker.startCapture();
      tracker.finishCapture({ success: false, failureReason: 'no_data', rawTextSize: 0 });
      await tracker.flush();
      throw new Error('No usable content captured (page may not have rendered)');
    }

    // If we got here with content, the tab-based path already verified:
    // (1) tab did not redirect to a login URL, (2) no visible password field.
    // Trust the content — clear any stale login wall and push.
    await _clearLoginWall(account.source);

    // Truncate each page contribution before joining to avoid mid-sentence cuts
    const rawText = allText.map((t, i) => i === 0 ? t.slice(0, 20_000) : t.slice(0, 10_000)).join('').slice(0, 40_000);
    const syncRawText = mergeSyncNetworkIntoRawText(rawText, account.source);
    console.log(`[Mighty] ${account.name}: ${syncRawText.length} chars across ${allText.length} page(s) — pushing`);

    tracker.finishNavigation({ success: true });
    tracker.startCapture();
    tracker.finishCapture({
      success: true,
      rawTextSize: syncRawText.length,
      jsonPayloadSize: jsonPayloadSize(syncRawText),
      evidenceMarkers: summarizeEvidenceMarkers(syncRawText),
    });
    await tracker.flush();

    const pushResp = await fetch(`${MIGHTY_URL}/api/data/sync`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        api_key:     apiKey,
        source:      account.source,
        sync_source: 'extension',
        pipeline_run_id: tracker.runId,
        data: {
          name:     account.name,
          icon:     account.icon,
          color:    account.color,
          status:   'ok',
          items:    [],
          raw_text: syncRawText,
        },
        synced_at: syncSessionTime,
      }),
    });

    if (!pushResp.ok) {
      const body = await pushResp.text();
      throw new Error(`Push failed: HTTP ${pushResp.status} — ${body.slice(0, 100)}`);
    }

    console.log(`[Mighty] ${account.name}: ✓`);
    // Successful push means the user is logged in — clear any login wall flag
    _clearLoginWall(account.source);

  } finally {
    endSyncNetworkCapture(account.source);
    chrome.tabs.onCreated.removeListener(_closeRogueTab);
    chrome.tabs.onUpdated.removeListener(_blockOpenOnLoad);
    if (!useShared) {
      chrome.tabs.remove(tabId).catch(() => {});
    } else {
      // Leave the tab open for the next account; navigate to blank to clear state
      updateProviderTab(tabId, { url: 'about:blank' }, 'sync').catch(() => {});
      await sleep(500); // brief pause before next account
    }
  }
}

// Runs inside the page context — finds and clicks "Stay logged in" / "Yes" buttons
// on session-timeout modals. Returns true if a button was clicked.
function dismissSessionTimeouts() {
  // Text patterns that appear on session-keepalive buttons
  const KEEP_ALIVE = [
    'stay logged in', 'stay signed in', 'keep me logged in', 'keep me signed in',
    'yes, keep me logged in', 'yes, stay logged in', 'continue session',
    'remain logged in', 'extend session', 'i\'m still here',
  ];
  // Broader fallback: a modal is visible AND a "Yes" / "Continue" button is inside it
  const MODAL_SELECTORS = [
    '[role="dialog"]', '[aria-modal="true"]', '.modal', '.dialog',
    '[class*="timeout"]', '[class*="session"]', '[id*="timeout"]', '[id*="session"]',
  ];

  // 1. Look for any visible button whose text matches a keep-alive phrase
  const buttons = Array.from(document.querySelectorAll('button, a[role="button"], input[type="button"], input[type="submit"]'));
  for (const btn of buttons) {
    const text = (btn.textContent || btn.value || '').toLowerCase().trim();
    if (KEEP_ALIVE.some(phrase => text.includes(phrase))) {
      btn.click();
      return true;
    }
  }

  // 2. Look for a modal dialog containing timeout/session language, then click Yes/Continue/OK
  for (const sel of MODAL_SELECTORS) {
    const modal = document.querySelector(sel);
    if (!modal) continue;
    const modalText = modal.textContent.toLowerCase();
    if (!modalText.match(/session|timeout|log.*out|sign.*out|still here|inactiv/)) continue;
    // Found a session modal — click the affirmative button inside it
    const inner = Array.from(modal.querySelectorAll('button, a[role="button"]'));
    const yes = inner.find(b => /^(yes|ok|continue|stay|keep|extend|confirm)\b/i.test((b.textContent || '').trim()));
    if (yes) { yes.click(); return true; }
    // Last resort: click the first button in the modal
    if (inner[0]) { inner[0].click(); return true; }
  }

  return false;
}

// Runs inside Amex account pages — extract Membership Rewards points balance
function extractAmexMembershipRewardsPage() {
  const LOG = '[Mighty Amex Page]';
  function formatPoints(numStr) {
    const n = parseInt(String(numStr).replace(/[^\d]/g, ''), 10);
    if (!n || n <= 0) return null;
    return n.toLocaleString('en-US');
  }
  function pickBestNumber(text) {
    if (!text) return null;
    const matches = String(text).match(/\b[\d]{1,3}(?:,\d{3})+\b|\b[\d]{4,}\b/g);
    if (!matches) return null;
    let best = null;
    let bestN = 0;
    for (const m of matches) {
      const n = parseInt(m.replace(/,/g, ''), 10);
      if (n > bestN && n < 100000000) { bestN = n; best = m; }
    }
    return best;
  }
  function findInRoot(root) {
    if (!root) return null;
    return pickBestNumber(root.innerText || root.textContent || '');
  }
  const path = location.pathname.toLowerCase();
  if (/\/account\/log-?in/.test(path)) {
    console.log(LOG, 'login page');
    return { loggedIn: false, value: null };
  }
  const sample = (document.body?.innerText || '').slice(0, 2500).toLowerCase();
  const loginHits = ['sign in to your account', 'user id', 'show password', 'forgot password']
    .filter(s => sample.includes(s)).length;
  if (loginHits >= 2 && !sample.includes('membership rewards')) {
    console.log(LOG, 'login form detected');
    return { loggedIn: false, value: null };
  }
  const sels = [
    '[data-testid*="membership-rewards" i]',
    '[data-testid*="rewards-balance" i]',
    '[data-automation-id*="rewards" i]',
    '[class*="membership-rewards" i]',
    '[class*="rewards-balance" i]',
    'a[href*="/rewards"]',
  ];
  for (const sel of sels) {
    try {
      for (const node of document.querySelectorAll(sel)) {
        const num = findInRoot(node);
        const formatted = formatPoints(num);
        if (formatted) {
          console.log(LOG, 'selector', sel, formatted);
          return { loggedIn: true, value: formatted };
        }
      }
    } catch (_) {}
  }
  for (const node of document.querySelectorAll('h1,h2,h3,h4,span,div,p,a,button')) {
    const t = (node.textContent || '').trim();
    if (!/membership rewards/i.test(t)) continue;
    let scope = node.closest('section,article,li,div') || node.parentElement;
    for (let d = 0; d < 4 && scope; d++) {
      const formatted = formatPoints(findInRoot(scope));
      if (formatted) {
        console.log(LOG, 'heading walk', formatted);
        return { loggedIn: true, value: formatted };
      }
      scope = scope.parentElement;
    }
  }
  const body = document.body?.innerText || '';
  const m = body.match(/Membership Rewards[^0-9\n]{0,120}([\d][\d,]*)/i);
  if (m) {
    const formatted = formatPoints(m[1]);
    if (formatted) {
      console.log(LOG, 'regex', formatted);
      return { loggedIn: true, value: formatted };
    }
  }
  const loggedIn = sample.includes('membership rewards')
    || sample.includes('account home')
    || sample.includes('recent activity');
  console.log(LOG, loggedIn ? 'balance not found' : 'not logged in');
  return { loggedIn, value: null };
}

// Runs inside provider account pages — Phase 1 access probe (Amex + Delta first)
function runProviderAccessProbeInPage(provider) {
  const url = location.href;
  const path = location.pathname.toLowerCase();
  const bodyText = (document.body?.innerText || '').slice(0, 12000);
  const lower = bodyText.toLowerCase();
  const ts = new Date().toISOString();

  function finish(payload) {
    return {
      provider,
      url_visited: url,
      signed_in_detected: !!payload.signed_in_detected,
      private_data_detected: !!payload.private_data_detected,
      evidence_type: payload.evidence_type || null,
      evidence_snippet: payload.evidence_snippet || null,
      failure_reason: payload.failure_reason || null,
      dom_text: bodyText.slice(0, 8000),
      timestamp: ts,
      blocked: !!payload.blocked,
      error: payload.error || null,
    };
  }

  const blockedSignals = ['access denied', 'captcha', 'verify you are human', 'bot detection', 'unusual activity'];
  if (blockedSignals.some(s => lower.includes(s))) {
    return finish({ blocked: true, failure_reason: 'access_blocked' });
  }

  if (provider === 'amex') {
    const loginPath = /\/account\/log-?in/.test(path);
    const marketingPath = /\/en-us\/(?:credit-cards|business|prepaid|gift-cards|benefits|offers)(?:\/|$)/i.test(path);
    const accountPath = /\/en-us\/account/.test(path) || /\/en-us\/rewards/.test(path);
    const loginHits = ['sign in to your account', 'user id', 'show password', 'forgot password']
      .filter(s => lower.includes(s)).length;
    const signedInSignals = [
      'membership rewards', 'account home', 'recent activity', 'card ending',
      'payment due', 'available credit', 'manage account', 'statement balance',
    ];
    let signedIn = !loginPath && !marketingPath && loginHits < 2
      && signedInSignals.some(s => lower.includes(s))
      && (accountPath || signedInSignals.filter(s => lower.includes(s)).length >= 2);

    let privateData = false;
    let evidenceType = 'dom_text';
    let snippet = null;

    const privatePatterns = [
      { re: /membership rewards[^0-9\n]{0,120}([\d][\d,]*)/i, label: 'membership_rewards_balance' },
      { re: /statement\s+balance[^$\d]{0,40}\$?([\d][\d,]*(?:\.\d{2})?)/i, label: 'statement_balance' },
      { re: /card\s+ending\s+(?:in\s+)?[\d*]{4,}/i, label: 'card_ending' },
      { re: /(?:points|rewards)\s*(?:balance|:)?\s*([\d][\d,]*)/i, label: 'points_balance' },
    ];
    for (const p of privatePatterns) {
      const m = bodyText.match(p.re);
      if (m) {
        privateData = true;
        snippet = m[0].trim().slice(0, 240);
        break;
      }
    }

    if (loginPath || (loginHits >= 2 && !lower.includes('membership rewards'))) {
      return finish({ signed_in_detected: false, private_data_detected: false, failure_reason: 'login_required' });
    }
    if (marketingPath && !accountPath) {
      return finish({ signed_in_detected: false, private_data_detected: false, failure_reason: 'marketing_page_only' });
    }
    return finish({
      signed_in_detected: signedIn,
      private_data_detected: privateData,
      evidence_type: privateData ? evidenceType : null,
      evidence_snippet: snippet,
      failure_reason: signedIn ? (privateData ? null : 'signed_in_no_private_evidence') : 'login_required',
    });
  }

  if (provider === 'delta') {
    const loginPath = /\/(sign-?in|log-?in|skymiles\/login)(\/|$|\?)/i.test(path);
    const marketingPath = /\/us\/en\/(?:flights|destinations|vacations|deals)(?:\/|$)/i.test(path);
    const accountPath = /\/myprofile|\/myskymiles|\/my-trips|\/wallet|\/profile/i.test(path);
    const signedInSignals = [
      'my skymiles', 'skymiles number', 'medallion', 'miles available', 'available miles',
      'my wallet', 'my trips', 'welcome back', 'member since', 'ecredit',
    ];
    let signedIn = !loginPath && !marketingPath
      && signedInSignals.some(s => lower.includes(s))
      && (accountPath || signedInSignals.filter(s => lower.includes(s)).length >= 2);

    let privateData = false;
    let snippet = null;
    const privatePatterns = [
      { re: /skymiles\s*(?:#|number|no\.?)?\s*:?\s*(\d{9,10})/i },
      { re: /(?:available\s+miles|miles\s+(?:balance|available))[^0-9]{0,20}([\d][\d,]*)/i },
      { re: /(?:medallion|elite)\s+(?:status|member)\s*:?\s*([^\n]{3,40})/i },
      { re: /(?:e-?credit|ecredit)s?[^$\d]{0,30}\$?([\d][\d,]*(?:\.\d{2})?)/i },
      { re: /(?:upcoming|next)\s+(?:trip|flight)[^\n]{0,80}/i },
    ];
    for (const p of privatePatterns) {
      const m = bodyText.match(p.re);
      if (m) {
        privateData = true;
        snippet = m[0].trim().slice(0, 240);
        break;
      }
    }

    if (loginPath) {
      return finish({ signed_in_detected: false, private_data_detected: false, failure_reason: 'login_required' });
    }
    if (marketingPath && !accountPath) {
      return finish({ signed_in_detected: false, private_data_detected: false, failure_reason: 'marketing_page_only' });
    }
    return finish({
      signed_in_detected: signedIn,
      private_data_detected: privateData,
      evidence_type: privateData ? 'dom_text' : null,
      evidence_snippet: snippet,
      failure_reason: signedIn ? (privateData ? null : 'signed_in_no_private_evidence') : 'login_required',
    });
  }

  return finish({ error: `unsupported probe provider: ${provider}`, failure_reason: 'unsupported_provider' });
}

// Runs inside the page context — captures universal page evidence for sync.
function captureUniversalPageEvidence() {
  const pageUrl = location.href;
  const MAX_BLOCK = 12000;
  const SENSITIVE = /"(access_token|refresh_token|id_token|password|secret|authorization|cookie|csrf|session_token)"/i;
  const EMBEDDED_IDS = ['__NEXT_DATA__', '__NUXT__'];

  function safeJson(text) {
    if (!text) return null;
    const trimmed = String(text).trim();
    if (trimmed.length < 20 || trimmed.length > MAX_BLOCK) return null;
    if (SENSITIVE.test(trimmed)) return null;
    try {
      JSON.parse(trimmed);
      return trimmed;
    } catch {
      return null;
    }
  }

  const clone = document.body ? document.body.cloneNode(true) : null;
  if (clone) clone.querySelectorAll('script, style, noscript, header, footer, nav').forEach(el => el.remove());
  const visibleText = (clone?.innerText || clone?.textContent || '').trim().slice(0, 15000);

  const meta = { title: (document.title || '').slice(0, 300), canonical: '', url: pageUrl };
  const canonical = document.querySelector('link[rel="canonical"]');
  if (canonical) meta.canonical = canonical.href || canonical.getAttribute('href') || '';
  for (const el of document.querySelectorAll('meta[name], meta[property]')) {
    const key = el.getAttribute('name') || el.getAttribute('property') || '';
    const val = el.getAttribute('content') || '';
    if (!key || !val || val.length > 500) continue;
    if (/password|token|cookie|csrf|auth|secret/i.test(key)) continue;
    meta[key] = val.slice(0, 300);
  }
  try {
    const storageKeys = [];
    for (let i = 0; i < localStorage.length && storageKeys.length < 30; i++) {
      const key = localStorage.key(i);
      if (key && !/password|token|cookie|csrf|auth|secret/i.test(key)) storageKeys.push(key);
    }
    if (storageKeys.length) meta.storage_keys = storageKeys;
  } catch {}

  const parts = [];
  if (visibleText.length >= 50) {
    parts.push(`\n\n--- ${pageUrl} ---\n${visibleText}`);
  }
  if (Object.keys(meta).length > 2) {
    parts.push(`\n\n=== PAGE META: ${pageUrl} ===\n${JSON.stringify(meta)}`);
  }
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    const block = safeJson(script.textContent || '');
    if (block) parts.push(`\n\n=== JSON-LD: ${pageUrl} ===\n${block}`);
  }
  for (const id of EMBEDDED_IDS) {
    const el = document.getElementById(id) || document.querySelector(`script#${id}, script[data-next-page]`);
    const block = safeJson(el?.textContent || '');
    if (block) parts.push(`\n\n=== EMBEDDED STATE: embedded:${id}@${pageUrl} ===\n${block}`);
  }
  return { evidence: parts.join(''), visibleText };
}

async function waitForEntryEvidence() {
  let lastLen = 0;
  let stableRounds = 0;
  for (let i = 0; i < 20; i++) {
    if (document.body) {
      const clone = document.body.cloneNode(true);
      clone.querySelectorAll('script, style, noscript').forEach(el => el.remove());
      const stripped = (clone.innerText || clone.textContent || '').trim();
      const len = stripped.length;
      if (len >= 100) {
        const delta = Math.abs(len - lastLen);
        if (delta <= 150 && lastLen > 0) {
          stableRounds++;
          if (stableRounds >= 2) return captureUniversalPageEvidence();
        } else {
          stableRounds = 0;
        }
        lastLen = len;
      }
    }
    await new Promise(res => setTimeout(res, 500));
  }
  return captureUniversalPageEvidence();
}

async function waitForSubpageEvidence() {
  for (let i = 0; i < 10; i++) {
    const text = document.body ? document.body.innerText : '';
    if (text && text.trim().length > 500) return captureUniversalPageEvidence();
    await new Promise(res => setTimeout(res, 500));
  }
  return captureUniversalPageEvidence();
}

// Runs inside the page context — extracts visible body text
function extractPageText() {
  // Remove script/style/nav noise before extracting
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('script, style, noscript, header, footer, nav').forEach(el => el.remove());
  return (clone.innerText || clone.textContent || '').slice(0, 15000);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function waitForTabLoad(tabId, timeout = 20_000) {
  return new Promise((resolve) => {
    function listener(id, info) {
      if (id === tabId && info.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
    // Always remove the listener on timeout — otherwise it leaks and fires
    // for future tab updates long after this call has resolved.
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve();
    }, timeout);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function setStatus(msg) {
  await chrome.storage.local.set({ sync_status: msg });
}

// ── Contextual benefit surfacing — intent detection ───────────────────────────

const INTENT_PATTERNS = {
  flight: [
    /google\.com\/flights.*[?&](q|hl|tfs)=/i,          // Google Flights with search
    /google\.com\/travel\/flights\//i,
    /kayak\.com\/flights\//i,                            // Kayak results
    /kayak\.com\/flight/i,
    /expedia\.com\/Flights-Search/i,
    /delta\.com\/.*\/book-a-flight/i,
    /delta\.com\/en-us\/flight-search/i,
    /delta\.com\/us\/en\/flight-search/i,
    /aa\.com\/booking\/choose-flights/i,
    /united\.com\/en\/us\/fsr\/choose-flights/i,
    /southwest\.com\/air\/booking\/select\.html/i,
    /skiplagged\.com\/flights\//i,
  ],
  hotel: [
    /google\.com\/travel\/hotels\/.*entity/i,            // Google Hotels with selection
    /google\.com\/maps\/search\/hotels/i,
    /booking\.com\/searchresults/i,                      // Booking.com search results (not homepage)
    /booking\.com\/hotel\//i,                            // Specific hotel page
    /hotels\.com\/search/i,
    /hotels\.com\/ho\d+/i,                               // Hotel detail page
    /marriott\.com\/search\/findHotels/i,
    /marriott\.com\/hotels\/travel\//i,
    /hyatt\.com\/en-US\/hotel\//i,
    /hilton\.com\/en\/hotels\//i,
    /hilton\.com\/en\/search\//i,
  ],
  car: [
    /hertz\.com\/rentacar\/reservation/i,
    /avis\.com\/site\/car-rental\/search/i,
    /enterprise\.com\/en\/car-rental\/deeplinking/i,
    /budget\.com\/en\/reservation/i,
    /turo\.com\/cars\//i,
    /turo\.com\/search/i,
  ],
  shopping: [
    /amazon\.com\/dp\/[A-Z0-9]{10}/i,                   // Amazon product page
    /amazon\.com\/gp\/product\//i,
    /amazon\.com\/.*\/dp\//i,
    /bestbuy\.com\/site\/[^/]+\/[^/]+\.p\?/i,           // Best Buy product page
    /apple\.com\/shop\/product\//i,
    /apple\.com\/shop\/buy-/i,
    /walmart\.com\/ip\/[^/]+\/\d+/i,                     // Walmart product
    /target\.com\/-\/A-\d+/i,                            // Target product
    /costco\.com\/[^/]+\.[0-9]+\.html/i,                 // Costco product
  ],
  dining: [
    /opentable\.com\/r\//i,                              // OpenTable specific restaurant
    /opentable\.com\/restaurant\/profile\//i,
    /resy\.com\/cities\/.*\/venues\//i,
    /yelp\.com\/biz\//i,
  ],
}

function detectIntent(url) {
  for (const [ctx, patterns] of Object.entries(INTENT_PATTERNS)) {
    if (patterns.some(p => p.test(url))) return ctx;
  }
  return null;
}

// Per-URL cooldown for intent detection — prevents re-firing when the user refreshes
// a search results page or the tab reloads during an SPA navigation.
const _intentCooldown = new Map();
const _INTENT_COOLDOWN_MS = 5 * 60 * 1000; // 5 minutes per URL

// Cached notification preference — fetched once and refreshed every 15 minutes.
// Avoids a round-trip to the server on every matching tab load.
let _cachedNotifPref = null;
let _cachedNotifPrefAt = 0;
const _NOTIF_PREF_TTL = 15 * 60 * 1000;

async function _getNotifPref(apiKey) {
  const now = Date.now();
  if (_cachedNotifPref !== null && now - _cachedNotifPrefAt < _NOTIF_PREF_TTL) {
    return _cachedNotifPref;
  }
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/settings/notifications`, {
      headers: { 'X-Mighty-Key': apiKey }, credentials: 'include',
    });
    if (resp.ok) {
      const data = await resp.json();
      _cachedNotifPref = data.pref || 'quiet';
      _cachedNotifPrefAt = now;
      return _cachedNotifPref;
    }
  } catch (_) {}
  return _cachedNotifPref || 'quiet';
}

// Tab intent detection — runs when a tab finishes loading
chrome.tabs.onUpdated.addListener(async function(tabId, changeInfo, tab) {
  if (changeInfo.status !== 'complete' || !tab.url) return;

  const intent = detectIntent(tab.url);
  if (!intent) return;

  // Per-URL cooldown — skip if we already fired for this URL recently
  const _normIntentUrl = _normUrl(tab.url);
  const _lastIntent = _intentCooldown.get(_normIntentUrl);
  if (_lastIntent && Date.now() - _lastIntent < _INTENT_COOLDOWN_MS) return;
  _intentCooldown.set(_normIntentUrl, Date.now());
  // Prune stale entries to avoid unbounded growth
  if (_intentCooldown.size > 500) {
    const _cutoff = Date.now() - _INTENT_COOLDOWN_MS;
    for (const [k, t] of _intentCooldown) { if (t < _cutoff) _intentCooldown.delete(k); }
  }

  // Get API key from storage (key name is 'api_key')
  const stored = await chrome.storage.local.get(['api_key']);
  const apiKey = stored.api_key;
  if (!apiKey) return;

  try {
    const resp = await fetch(
      `${MIGHTY_URL}/api/benefits/relevant?context=${intent}`,
      { headers: { 'X-Mighty-Key': apiKey }, credentials: 'include' }
    );
    if (!resp.ok) return;
    const data = await resp.json();
    if (!data.count || data.count === 0) return;

    if (notifPref === 'never') return;

    const checkoutPatterns = [
      /\/checkout/i, /\/cart/i, /\/payment/i, /\/booking\/confirm/i,
      /\/purchase/i, /\/order\/review/i, /\/reserve/i, /\/book\//i,
      /booking.*confirm/i, /order.*confirm/i, /step=payment/i, /step=review/i,
    ];
    const isCheckout = checkoutPatterns.some(p => p.test(tab.url));

    // 'checkout' pref: only fire at booking/payment pages
    if (notifPref === 'checkout' && !isCheckout) return;

    // Split into existing benefits (derived from user's accounts) vs card suggestions
    let allBenefits = data.benefits || [];

    // 'expiring' mode: only urgent items
    if (notifPref === 'expiring') {
      allBenefits = allBenefits.filter(b => b._why && b._why.urgency_factor >= 0.7);
      if (!allBenefits.length) return;
    }

    // Separate derived (existing status/membership unlocks) from direct matches
    const existing  = allBenefits.filter(b => b.derived).slice(0, 2);   // max 2
    const direct    = allBenefits.filter(b => !b.derived).slice(0, 2);  // max 2

    // At checkout: show up to 2 existing + 1 direct. While browsing: 1 existing only.
    let surfaced;
    if (isCheckout) {
      surfaced = [...existing.slice(0, 2), ...direct.slice(0, 1)];
    } else {
      // Quiet while browsing — at most 1 high-confidence existing benefit
      const highConf = existing.filter(b => b.confidence !== 'low');
      surfaced = highConf.slice(0, 1);
      if (!surfaced.length) surfaced = direct.slice(0, 1);
    }

    if (!surfaced.length) return;

    // Fetch card recommendations for this context (shown separately, more quietly)
    let cardRecs = [];
    if (isCheckout) {
      try {
        const recResp = await fetch(
          `${MIGHTY_URL}/api/benefits/discover?context=${intent}`,
          { headers: { 'X-Mighty-Key': apiKey }, credentials: 'include' }
        );
        if (recResp.ok) {
          const recData = await recResp.json();
          cardRecs = (recData.recommendations || []).slice(0, 1); // max 1 card rec at checkout
        }
      } catch(e) {}
    }

    // Send to content script
    chrome.tabs.sendMessage(tabId, {
      type: 'MIGHTY_BENEFITS',
      context: intent,
      benefits: surfaced,
      cardRecs: cardRecs,
      isCheckout: isCheckout,
      count: surfaced.length,
      dashUrl: MIGHTY_URL + '/dashboard',
    }).catch(() => {}); // content script may not be loaded yet

    // Log the intent so the dashboard can show "Relevant Right Now"
    try {
      const csrfResp = await fetch(`${MIGHTY_URL}/api/csrf-token`, {
        headers: { 'X-Mighty-Key': apiKey },
        credentials: 'include'
      });
      const csrfData = csrfResp.ok ? await csrfResp.json() : {};
      const csrfToken = csrfData.token || '';

      await fetch(`${MIGHTY_URL}/api/intent/log`, {
        method: 'POST',
        headers: {
          'X-Mighty-Key': apiKey,
          'X-CSRF-Token': csrfToken,
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          intent_type: intent,
          page_url: tab.url,
          benefits: data.benefits,
        }),
      });
    } catch(e) {
      console.log('[Mighty] Intent log error:', e);
    }
  } catch (e) {
    console.log('[Mighty] Intent fetch error:', e);
  }
});
