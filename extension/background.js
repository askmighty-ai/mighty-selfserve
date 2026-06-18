// Mighty Sync — background service worker
// Opens account pages as background tabs, extracts text, pushes to Railway.

const MIGHTY_URL    = 'https://mighty-selfserve-production.up.railway.app';
const SYNC_ALARM    = 'mighty-sync';
const SYNC_INTERVAL = 240; // minutes (every 4 hours)

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

/** Report a fruitful path to the shared registry. Fire-and-forget. */
function reportPathToRegistry(site, url) {
  try {
    const path = normalizePath(new URL(url).pathname);
    if (!path || path === '/') return;
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
  united:       'https://www.united.com/en/us/myaccount/mileageplus',
  american_air: 'https://www.aa.com/loyalty/home.do',
  alaska_air:   'https://www.alaskaair.com/account/dashboard',
  sfcu:         'https://www.sfcu.org/accounts/online-banking',
  amex:         'https://www.americanexpress.com/en-us/account/',
  chase:        'https://secure.chase.com/web/auth/dashboard',
  wells_fargo:  'https://connect.secure.wellsfargo.com/auth/login/present',
  bofa:         'https://www.bankofamerica.com/myaccounts/brain/render.go',
  capital_one:  'https://myaccounts.capitalone.com/accountSummary',
  discover:     'https://portal.discover.com/customer/en/portal/account-home',
  citi:         'https://online.citi.com/US/login.do',
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
  state_farm:   'https://www.statefarm.com/customer-care/sign-in-to-my-account',
  pamf:         'https://mychart.pamf.org/MyChart/',
  ticketmaster: 'https://www.ticketmaster.com/member/orders',
  netflix:      'https://www.netflix.com/YourAccount',
  hulu:         'https://secure.hulu.com/account',
  spotify:      'https://www.spotify.com/us/account/overview/',
  disney_plus:  'https://www.disneyplus.com/identity/account',
  att:          'https://www.att.com/my/#/',
  att_wireless: 'https://myatt.att.com/exp/myconsumerdashboard/',
  xfinity:      'https://customer.xfinity.com/#/billing',
  pa_utilities: 'https://utilities.cityofpaloalto.org/',
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
  const combined = (href + ' ' + text).toLowerCase();
  if (_LINK_SKIP.some(t => combined.includes(t))) return -1;
  let score = 0;
  if (_LINK_HIGH_VALUE.some(t => combined.includes(t))) score += 10;
  if (_LINK_ACCOUNT.some(t => combined.includes(t))) score += 3;
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
const SUPPLEMENT_DOMAINS = {
  'delta.com':                    'delta',
  'marriott.com':                 'marriott',
  'hilton.com':                   'hilton',
  'hyatt.com':                    'hyatt',
  'united.com':                   'united',
  'southwest.com':                'southwest',
  'aa.com':                       'american_air',
  'alaskaair.com':                'alaska_air',
  'americanexpress.com':          'amex',
  'chase.com':                    'chase',
  'customer.xfinity.com':         'xfinity',
  'cityofpaloalto.org':           'pa_utilities',  // covers utilities. and myaccount. subdomains
  'ihg.com':                      'ihg',
  'wyndhamhotels.com':            'wyndham',
};

// Sources that must be synced via a regular tab (not a popup window).
// Akamai and similar bot-detection layers block popup windows; a regular tab
// using the user's existing session passes through cleanly.
const TAB_SYNC_SOURCES = new Set(['xfinity', 'pa_utilities']);

// Domain → source mapping for API interception
const INTERCEPT_DOMAIN_MAP = {
  'delta.com':            'delta',
  'marriott.com':         'marriott',
  'hilton.com':           'hilton',
  'hyatt.com':            'hyatt',
  'united.com':           'united',
  'southwest.com':        'southwest',
  'aa.com':               'american_air',
  'alaskaair.com':        'alaska_air',
  'americanexpress.com':  'amex',
  'chase.com':            'chase',
};

// In-memory dedup: url → timestamp, cleared after 10 minutes
const _interceptSeen = new Map();
const _INTERCEPT_COOLDOWN = 10 * 60 * 1000;

async function handleInterceptedApi(url, data) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Map URL to source
  let source = null;
  try {
    const hostname = new URL(url).hostname.replace(/^www\./, '');
    for (const [domain, src] of Object.entries(INTERCEPT_DOMAIN_MAP)) {
      if (hostname.endsWith(domain)) { source = src; break; }
    }
  } catch { return; }
  if (!source) return;

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

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_INTERVAL });
  console.log('[Mighty] Extension installed, sync scheduled every', SYNC_INTERVAL, 'minutes');
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) runSync();
});

// Messages from popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === 'sync_now') {
    // Respond immediately so the message channel doesn't time out during a long sync
    sendResponse({ ok: true });
    runSync().catch(console.error);
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
    handleInterceptedApi(msg.url, msg.data).catch(() => {});
    return false; // no sendResponse needed
  }
  if (msg.action === 'remove_captured') {
    chrome.storage.local.get('captured_accounts', ({ captured_accounts = {} }) => {
      delete captured_accounts[msg.source];
      chrome.storage.local.set({ captured_accounts }, () => sendResponse({ ok: true }));
    });
    return true;
  }
});

// ── Sync orchestration ───────────────────────────────────────────────────────

async function runSync() {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) {
    await setStatus('No API key — open the extension to set it up');
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
    await setStatus(`Error fetching accounts: ${e.message}`);
    return;
  }

  // Also load captured (custom) accounts from local storage
  const { captured_accounts = {} } = await chrome.storage.local.get('captured_accounts');
  const capturedList = Object.entries(captured_accounts);

  if (!accounts.length && !capturedList.length) {
    await setStatus('No connected accounts found in dashboard');
    return;
  }

  console.log(`[Mighty] Syncing ${accounts.length} accounts + ${capturedList.length} captured…`);
  let ok = 0, failed = 0;

  const ACCOUNT_TIMEOUT_MS = 90_000; // hard cap per account
  for (const account of accounts) {
    if (!ACCOUNT_ENTRY[account.source]) {
      console.log(`[Mighty] No entry URL for ${account.source} — skipping`);
      continue;
    }
    try {
      let syncFn;
      if (TAB_SYNC_SOURCES.has(account.source)) {
        // Xfinity / PA Utilities: use a real tab to bypass Akamai popup-window detection
        syncFn = syncAccountViaTab(api_key, account, [ACCOUNT_ENTRY[account.source]], syncSessionTime);
      } else {
        syncFn = crawlAccount(api_key, account, syncSessionTime);
      }
      await Promise.race([
        syncFn,
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), ACCOUNT_TIMEOUT_MS)),
      ]);
      ok++;
      // Autonomous gap-filling: after successful sync, check coverage and visit missing pages
      try {
        await gapFillAccount(api_key, account, syncSessionTime);
      } catch(gfe) {
        console.log(`[Mighty] ${account.name}: gap-fill skipped: ${gfe.message}`);
      }
    } catch (e) {
      console.error(`[Mighty] Failed: ${account.name}:`, e.message);
      failed++;
    }
  }

  // Re-sync captured accounts by re-visiting their saved URLs
  for (const [source, info] of capturedList) {
    if (!info.urls || !info.urls.length) continue;
    console.log(`[Mighty] Re-syncing captured: ${info.name} (${info.urls.length} URL(s))`);
    try {
      await resyncCaptured(api_key, source, info, syncSessionTime);
      ok++;
    } catch (e) {
      console.error(`[Mighty] Failed captured ${info.name}:`, e.message);
      failed++;
    }
  }

  const ts = new Date(syncSessionTime).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const msg = `Synced at ${ts} — ${ok} ok${failed ? `, ${failed} failed` : ''}`;
  await chrome.storage.local.set({ last_sync: syncSessionTime });
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

async function resyncCaptured(apiKey, source, info, syncSessionTime = new Date().toISOString()) {
  const allTexts = [];
  const win = await chrome.windows.create({
    url: info.urls[0],
    type: 'popup',
    width: 800,
    height: 600,
  });
  chrome.windows.update(win.id, { state: 'minimized' });
  const tabId = win.tabs[0].id;

  try {
    for (let i = 0; i < info.urls.length; i++) {
      if (i > 0) {
        await chrome.tabs.update(tabId, { url: info.urls[i] });
      }
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
    chrome.windows.remove(win.id).catch(() => {});
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
const _ACCOUNT_PATH_RE = /\/(my[-_]?account|myaccount|account[-_/]|dashboard|my[-_]?profile|profile\/|loyalty|rewards|member[-_/]|membership|portal|billing|overview|summary|wallet|benefits|perks|certificates|ecredits|statement|transactions)/i;

// URL patterns that indicate a login/auth page — skip these
const _LOGIN_PATH_RE = /\/(login|log[-_]in|signin|sign[-_]in|auth\/|sso\/|oauth|forgot|reset[-_]password|register|signup|sign[-_]up|create[-_]account)/i;

// Domains belonging to known scheduled accounts — don't auto-capture (already synced)
const _KNOWN_DOMAINS = new Set(
  Object.values(ACCOUNT_ENTRY)
    .map(u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return ''; } })
    .filter(Boolean)
);

// In-memory debounce: url → ms timestamp of last auto-capture
const _autoCaptureRecent = new Map();
const _AUTO_COOLDOWN_MS  = 60 * 60 * 1000; // 1 hour per URL

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
        // Kick off an immediate sync so data appears right away
        runSync();
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

  // Known account domain: supplement-capture if path looks like account data.
  // No hardcoded paths — _ACCOUNT_PATH_RE decides what's worth capturing.
  for (const [domain, source] of Object.entries(SUPPLEMENT_DOMAINS)) {
    if (tabDomain.endsWith(domain)) {
      if (_ACCOUNT_PATH_RE.test(tab.url)) {
        _supplementCapturePage(tabId, tab, source).catch(() => {});
      }
      return;
    }
  }

  // Unknown domain: auto-capture if path looks like an account page
  if (!_ACCOUNT_PATH_RE.test(tab.url)) return;
  const last = _autoCaptureRecent.get(tab.url);
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
  _autoCaptureRecent.set(tab.url, Date.now());

  const name     = _nameFromTab(tab);
  const category = _guessCategory(tab.url);

  // Privacy mode: for unapproved domains, only send first 500 chars of raw text
  let rawText = text;
  const baseDomain = (() => { try { return new URL(tab.url).hostname.replace(/^www./, ''); } catch(e) { return ''; } })();
  const isApprovedDomain = Object.keys(SUPPLEMENT_DOMAINS || {}).some(d => baseDomain.includes(d)) ||
    Object.keys(ACCOUNT_ENTRY || {}).some(k => {
      try { return new URL(ACCOUNT_ENTRY[k]).hostname.replace(/^www./, '') === baseDomain; } catch(e) { return false; }
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
    _autoCaptureRecent.delete(tab.url); // allow retry
    console.warn(`[Mighty] Auto-capture failed for ${tab.url}:`, e.message);
  }
}

async function _supplementCapturePage(tabId, tab, source) {
  const { api_key } = await chrome.storage.local.get('api_key');
  if (!api_key) return;

  // Debounce: skip if we supplemented this URL recently
  const last = _autoCaptureRecent.get(tab.url);
  if (last && Date.now() - last < _AUTO_COOLDOWN_MS) return;
  _autoCaptureRecent.set(tab.url, Date.now());

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
  } catch { _autoCaptureRecent.delete(tab.url); return; }

  if (!extracted || extracted.hasPassword || extracted.loginSignals >= 2) return;
  if (!extracted.text || extracted.text.length < 200) return;

  const lower = extracted.text.toLowerCase();
  if (['gate change', 'access denied', 'checking your browser'].some(p => lower.includes(p))) return;

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
    _autoCaptureRecent.delete(tab.url);
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
  'please wait',             // Cloudflare challenge
  'cf-browser-verification',
  'unusual traffic',
  'please enable cookies',   // Xfinity/Akamai cookie-check page
  'cookie functionality is turned off',
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
         url.includes('pge.com') || url.includes('att.com/my/');
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
  const tab = await chrome.tabs.create({ url, active: false });
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
async function gapFillAccount(apiKey, account, syncSessionTime, maxIterations = 2) {
  const source = account.source;
  const entry = ACCOUNT_ENTRY[source];
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

      console.log(`[Mighty] ${source}: gap-filling ${targetPaths.length} pages for missing: ${cov.gaps.map(g => g.key).join(', ')}`);

      // Open a popup window to visit gap-fill pages (reuses the crawlAccount pattern)
      const win = await chrome.windows.create({
        url: entry,
        type: 'popup',
        width: 800,
        height: 600,
      });
      chrome.windows.update(win.id, { state: 'minimized' });
      const tabId = win.tabs[0].id;

      let newText = '';
      try {
        await waitForTabLoad(tabId, 15_000);
        await sleep(3_000);

        for (const path of targetPaths) {
          const fullUrl = entryOrigin + path;
          await randomDelay(1000, 2000);
          try {
            await chrome.tabs.update(tabId, { url: fullUrl });
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
            if (pageText && pageText.length > 200) {
              newText += `\n\n--- ${fullUrl} ---\n${pageText}`;
              reportPathToRegistry(source, fullUrl);
            }
          } catch(e) {
            console.log(`[Mighty] gap-fill visit failed: ${fullUrl}: ${e.message}`);
          }
        }
      } finally {
        chrome.windows.remove(win.id).catch(() => {});
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
}

async function crawlAccount(apiKey, account, syncSessionTime) {
  const entry = ACCOUNT_ENTRY[account.source];
  if (!entry) {
    console.log(`[Mighty] No entry URL for ${account.source} — skipping`);
    return;
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

  const win = await chrome.windows.create({
    url: warmup || entry,
    type: 'popup',
    width: 800,
    height: 600,
  });
  chrome.windows.update(win.id, { state: 'minimized' });
  const tabId = win.tabs[0].id;

  try {
    await waitForTabLoad(tabId, 15_000);

    if (warmup) {
      await sleep(3_000);
      await chrome.tabs.update(tabId, { url: entry });
      await waitForTabLoad(tabId, 15_000);
    }

    // Abort if redirected to login
    try {
      const currentTab = await chrome.tabs.get(tabId);
      if (currentTab.url && _LOGIN_PATH_RE.test(new URL(currentTab.url).pathname)) {
        console.log(`[Mighty] ${account.name}: redirected to login — not logged in, skipping`);
        return;
      }
    } catch (_) {}

    await sleep(ENTRY_SETTLE);

    // Dismiss session-timeout modals
    try {
      const [d] = await chrome.scripting.executeScript({ target: { tabId }, func: dismissSessionTimeouts });
      if (d?.result) { console.log(`[Mighty] ${account.name}: dismissed session modal`); await sleep(3_000); }
    } catch (_) {}

    // Extract entry page text
    let entryText = '';
    try {
      const [r] = await chrome.scripting.executeScript({ target: { tabId }, func: extractPageText });
      entryText = r?.result || '';
    } catch (_) {}

    if (BOT_DETECTION_PHRASES.some(p => entryText.toLowerCase().includes(p))) {
      console.warn(`[Mighty] ${account.name}: bot detection on entry page — skipping`);
      return;
    }

    if (entryText.length >= 100) {
      allText.push(`\n\n--- ${entry} ---\n${entryText}`);
      visitedNorm.add(_normUrl(entry));
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
    for (const link of scored) {
      if (toVisit.length >= MAX_SUBPAGES) break;
      const norm = _normUrl(link.href);
      if (visitedNorm.has(norm)) continue;
      visitedNorm.add(norm);
      toVisit.push(link);
    }

    // Supplement with registry-known paths not already discovered
    try {
      const regPaths = await fetchRegistryPaths(account.source);
      const entryOrigin = new URL(entry).origin;
      for (const path of regPaths) {
        if (toVisit.length >= MAX_SUBPAGES) break;
        const regUrl = entryOrigin + path;
        const norm   = _normUrl(regUrl);
        if (!visitedNorm.has(norm)) {
          visitedNorm.add(norm);
          toVisit.push({ href: regUrl, text: '', score: 5, fromRegistry: true });
        }
      }
    } catch (_) {}

    console.log(`[Mighty] ${account.name}: ${scored.length} candidates → visiting top ${toVisit.length}`);

    // ── Visit subpages ──────────────────────────────────────────────────────────
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

        await chrome.tabs.update(tabId, { url: link.href });
        await waitForTabLoad(tabId, 15_000);

        // Extra settle time for SPA pages that render content after load
        const extraSettle = isSpaUrl(link.href) ? 4000 : 1000;
        await sleep(SUBPAGE_SETTLE + extraSettle);

        try {
          const [d] = await chrome.scripting.executeScript({ target: { tabId }, func: dismissSessionTimeouts });
          if (d?.result) await sleep(2_000);
        } catch (_) {}

        const [r] = await chrome.scripting.executeScript({
          target: { tabId },
          func: async function waitForContent() {
            // Wait up to 5s for meaningful content to appear
            for (let i = 0; i < 10; i++) {
              const text = document.body ? document.body.innerText : '';
              if (text && text.trim().length > 500) return text;
              await new Promise(res => setTimeout(res, 500));
            }
            return document.body ? document.body.innerText : '';
          },
        });
        const text = r?.result || '';

        if (BOT_DETECTION_PHRASES.some(p => text.toLowerCase().includes(p))) {
          console.warn(`[Mighty] ${account.name} → ${link.href}: bot detected — skipping`);
          await markBotDetected(account.source, linkPath);
          continue;
        }
        if (text.length < 100) {
          console.warn(`[Mighty] ${account.name} → ${link.href}: too short (${text.length} chars) — skipping`);
          continue;
        }

        console.log(`[Mighty] ${account.name} → ${link.href}: ${text.length} chars`);
        allText.push(`\n\n--- ${link.href} ---\n${text}`);
        reportPathToRegistry(account.source, link.href);

      } catch (e) {
        console.warn(`[Mighty] ${account.name} → ${link.href}: ${e.message}`);
      }
    }

    // ── Push to server ──────────────────────────────────────────────────────────
    if (allText.length === 0) {
      throw new Error('No usable content captured — possibly not logged in');
    }

    const rawText = allText.join('').slice(0, 40_000);
    console.log(`[Mighty] ${account.name}: ${rawText.length} chars across ${allText.length} page(s) — pushing`);

    const pushResp = await fetch(`${MIGHTY_URL}/api/data/sync`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        api_key:     apiKey,
        source:      account.source,
        sync_source: 'extension',
        data: {
          name:     account.name,
          icon:     account.icon,
          color:    account.color,
          status:   'ok',
          items:    [],
          raw_text: rawText,
        },
        synced_at: syncSessionTime,
      }),
    });

    if (!pushResp.ok) {
      const body = await pushResp.text();
      throw new Error(`Push failed: HTTP ${pushResp.status} — ${body.slice(0, 100)}`);
    }

    console.log(`[Mighty] ${account.name}: ✓`);

  } finally {
    chrome.windows.remove(win.id).catch(() => {});
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
    const timer = setTimeout(resolve, timeout); // resolve anyway on timeout

    function listener(id, info) {
      if (id === tabId && info.status === 'complete') {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
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

// Tab intent detection — runs when a tab finishes loading
chrome.tabs.onUpdated.addListener(async function(tabId, changeInfo, tab) {
  if (changeInfo.status !== 'complete' || !tab.url) return;

  const intent = detectIntent(tab.url);
  if (!intent) return;

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

    // Send to content script
    chrome.tabs.sendMessage(tabId, {
      type: 'MIGHTY_BENEFITS',
      context: intent,
      benefits: data.benefits,
      count: data.count,
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
