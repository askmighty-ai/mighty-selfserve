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

// Account page URLs — where to navigate to get each account's data.
// Values can be a single URL string or an array of URLs (visited in order,
// text concatenated) to capture sub-pages like vouchers, travel funds, etc.
// URLs are visited IN ORDER and text concatenated. Put high-value benefit/offer
// pages FIRST so they are never cut off by the character limit.
const ACCOUNT_URLS = {
  southwest: [
    'https://www.southwest.com/loyalty/rapidrewards/travelFunds.html',    // travel funds / LUV vouchers
    'https://www.southwest.com/loyalty/myaccount/',                        // points, status, companion pass
    'https://www.southwest.com/loyalty/myaccount/upcoming-trips.html',    // reservations
  ],
  delta: [
    'https://www.delta.com/my-profile/certificates',                      // upgrade certs, companion cert — CORRECT URL
    'https://www.delta.com/us/en/my-account/eCredits',                    // eCredits / travel vouchers
    'https://www.delta.com/myprofile/',                                    // miles, status
  ],
  united: [
    'https://www.united.com/en/us/myaccount/awards',                      // credits, PlusPoints, certs
    'https://www.united.com/en/us/myaccount/mileageplus',                 // miles, status
  ],
  american_air: [
    'https://www.aa.com/aadvantage-program/overview',                      // miles, status
    'https://www.aa.com/loyalty/home.do',                                  // dashboard
    'https://www.aa.com/aadvantage-program/my-account/trip-credit',       // trip credits
  ],
  alaska_air: [
    'https://www.alaskaair.com/account/wallet',                            // companion fare, credits
    'https://www.alaskaair.com/account/dashboard',                         // miles, status
  ],
  sfcu:         'https://www.sfcu.org/accounts/online-banking',
  amex: [
    'https://www.americanexpress.com/en-us/benefits/overview/',           // card benefits, credits
    'https://www.americanexpress.com/en-us/account/offers/eligible/',     // personalized offers
    'https://www.americanexpress.com/en-us/account/',                      // balance, points
  ],
  chase: [
    'https://secure.chase.com/web/auth/#/dashboard;dp/rewards/dashboard', // rewards / offers
    'https://secure.chase.com/web/auth/dashboard',                         // accounts overview
  ],
  wells_fargo:  'https://connect.secure.wellsfargo.com/auth/login/present',
  bofa:         'https://www.bankofamerica.com/myaccounts/brain/render.go',
  capital_one: [
    'https://myaccounts.capitalone.com/accountSummary',
    'https://www.capitalone.com/credit-cards/rewards/',                    // rewards balance
  ],
  discover:     'https://portal.discover.com/customer/en/portal/account-home',
  citi:         'https://online.citi.com/US/login.do',
  paypal:       'https://www.paypal.com/myaccount/summary',
  fidelity:     'https://digital.fidelity.com/ftgw/digital/portfolio/summary',
  schwab:       'https://client.schwab.com/app/accounts/#/',
  marriott: [
    'https://www.marriott.com/loyalty/myAccount/certificates.mi',          // free night certs, upgrades
    'https://www.marriott.com/loyalty/myAccount/benefits.mi',              // elite benefits
    'https://www.marriott.com/loyalty/myAccount/default.mi',               // points, status
  ],
  hilton: [
    'https://www.hilton.com/en/hilton-honors/profile/awards/',             // free night awards
    'https://www.hilton.com/en/hilton-honors/profile/benefits/',           // elite benefits
    'https://www.hilton.com/en/hilton-honors/guest/my-account/',           // points, status
  ],
  hyatt: [
    'https://www.hyatt.com/en-US/my-account/awards',                       // awards, certs
    'https://www.hyatt.com/en-US/my-account/home',                         // points, status
  ],
  ihg: [
    'https://www.ihg.com/rewardsclub/content/us/en/member-home',
    'https://www.ihg.com/rewardsclub/content/us/en/redeem/hotel-rewards', // reward nights
  ],
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
  att: [
    'https://www.att.com/buy/broadband/rewards.html',                      // reward cards
    'https://www.att.com/my/#/',
  ],
  att_wireless: 'https://myatt.att.com/exp/myconsumerdashboard/',
  verizon: [
    'https://www.verizon.com/home/mybenefits/',                            // perks
    'https://www.verizon.com/myverizon/',
  ],
  tmobile: [
    'https://account.t-mobile.com/overview',
    'https://account.t-mobile.com/offers',                                 // offers
  ],
  xfinity: [
    'https://customer.xfinity.com/#/billing',
    'https://customer.xfinity.com/#/internet',
    'https://customer.xfinity.com/#/rewards',                              // xFi rewards
  ],
  pa_utilities: [
    'https://utilities.cityofpaloalto.org/MyAccount',                      // account overview, balance
    'https://utilities.cityofpaloalto.org/Billing',                        // billing history
  ],
  hertz: [
    'https://www.hertz.com/rentacar/member/profile/myprofile',
    'https://www.hertz.com/rentacar/member/profile/promotions',            // promotions
  ],
  cvs:          'https://www.cvs.com/account/login.jsp',
  walgreens:    'https://www.walgreens.com/myaccount/mywalgreenssummary.jsp',
};

// Supplement watch: specific benefit sub-pages to capture from user's real browser.
// These pages are bot-detected in popup windows but work fine in normal browsing.
const SUPPLEMENT_WATCH = [
  { source: 'delta',       domain: 'delta.com',                paths: ['/my-profile/certificates', '/us/en/my-account/wallet', '/us/en/my-account/eCredits'] },
  { source: 'marriott',    domain: 'marriott.com',             paths: ['/loyalty/myAccount/certificates', '/loyalty/myAccount/benefits'] },
  { source: 'hilton',      domain: 'hilton.com',               paths: ['/en/hilton-honors/profile/awards', '/en/hilton-honors/profile/benefits'] },
  { source: 'hyatt',       domain: 'hyatt.com',                paths: ['/en-US/my-account/awards'] },
  { source: 'united',      domain: 'united.com',               paths: ['/en/us/myaccount/awards'] },
  { source: 'alaska_air',  domain: 'alaskaair.com',            paths: ['/account/wallet'] },
  // Sites that always fail in popup windows — capture on natural browser visits instead
  { source: 'xfinity',     domain: 'customer.xfinity.com',     paths: ['/'] },   // match any page — Akamai blocks popups
  { source: 'pa_utilities',domain: 'utilities.cityofpaloalto.org', paths: ['/'] }, // capture any account page
];

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

  for (const account of accounts) {
    const urlEntry = ACCOUNT_URLS[account.source];
    if (!urlEntry) {
      console.log(`[Mighty] No URL mapping for ${account.source} — skipping`);
      continue;
    }
    const urls = Array.isArray(urlEntry) ? urlEntry : [urlEntry];
    // Registry path merging is kept for future use — disabled here until
    // path quality is validated to avoid extra/wrong pages in sync.
    try {
      await syncAccount(api_key, account, urls, syncSessionTime);
      ok++;
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
  Object.values(ACCOUNT_URLS)
    .flat()
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

  // Skip the Mighty dashboard itself
  if (tab.url.includes('mighty-selfserve-production.up.railway.app')) return;

  // Skip popup windows used by the scheduled sync (avoid double-capturing)
  try {
    const winInfo = await chrome.windows.get(tab.windowId);
    if (winInfo.type === 'popup') return;
  } catch { return; }

  // Must look like an account page
  if (!_ACCOUNT_PATH_RE.test(tab.url)) return;

  // Must not look like a login page
  if (_LOGIN_PATH_RE.test(tab.url)) return;

  // Check supplement watch BEFORE the known-domain skip
  try {
    const tabDomain = new URL(tab.url).hostname.replace(/^www\./, '');
    const tabPath   = new URL(tab.url).pathname;
    const supp = SUPPLEMENT_WATCH.find(w =>
      tabDomain.endsWith(w.domain) && w.paths.some(p => tabPath.startsWith(p))
    );
    if (supp) {
      _supplementCapturePage(tabId, tab, supp.source).catch(() => {});
      return;
    }
    // Skip other known scheduled accounts (already synced by popup)
    if (_KNOWN_DOMAINS.has(tabDomain)) return;
  } catch { return; }

  // Debounce: skip if captured recently
  const last = _autoCaptureRecent.get(tab.url);
  if (last && Date.now() - last < _AUTO_COOLDOWN_MS) return;

  // Kick off async capture without blocking the listener
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

  try {
    await _pushCapture(api_key, name, category, tab.url, text);
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

// Per-site settle time (ms) after page load before extracting text.
// Override for SPAs that need longer to fully render.
const SETTLE_MS = {
  xfinity:            20_000,
  xfinity_subsequent: 20_000,  // Xfinity SPA redirects between hash routes; needs full settle
  delta:              18_000,  // Delta wallet renders certificates late via React hydration
  delta_subsequent:   15_000,  // eCredits also dynamic
  default:             8_000,
  subsequent:          8_000,
};

// SPA sub-sections to extract via in-page navigation (clicking links).
// Used for sites like Delta where direct URL changes trigger bot detection.
// Each entry: { label, terms[] } — finds an <a> whose href OR text matches any term.
// SPA_NAV_URLS: in-page link clicking for sites where direct URL changes
// trigger bot detection. Delta's certificate/wallet pages are behind identity
// verification gates and don't render via this approach — left empty for now.
const SPA_NAV_URLS = {};

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

// ── Per-account sync ─────────────────────────────────────────────────────────

async function syncAccount(apiKey, account, urls, syncSessionTime = new Date().toISOString()) {
  console.log(`[Mighty] → ${account.name} (${urls.length} page${urls.length > 1 ? 's' : ''})`);

  const warmup = WARMUP_URLS[account.source];
  const allText = [];

  // Open a minimized popup window so sync tabs never steal focus
  const startUrl = warmup || urls[0];
  const win = await chrome.windows.create({
    url: startUrl,
    type: 'popup',
    width: 800,
    height: 600,
  });
  chrome.windows.update(win.id, { state: 'minimized' });
  const tab = { id: win.tabs[0].id };

  try {
    await waitForTabLoad(tab.id, 20_000);

    if (warmup) {
      // Give Akamai/bot-detection time to set its domain cookies before navigating away
      const warmupWait = SETTLE_MS[account.source] ? 6_000 : 3_000;
      await sleep(warmupWait);
      // Navigate to first real URL after warm-up
      await chrome.tabs.update(tab.id, { url: urls[0] });
      await waitForTabLoad(tab.id, 20_000);
    }

    for (let i = 0; i < urls.length; i++) {
      // Already on urls[0] from above; navigate for subsequent pages
      if (i > 0) {
        await chrome.tabs.update(tab.id, { url: urls[i] });
        await waitForTabLoad(tab.id, 20_000);
      }

      const settleMs = i === 0
        ? (SETTLE_MS[account.source] || SETTLE_MS.default)
        : (SETTLE_MS[`${account.source}_subsequent`] || SETTLE_MS.subsequent);
      await sleep(settleMs);

      // Dismiss any session-timeout / "stay logged in?" modal before extracting
      try {
        const [dismissed] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: dismissSessionTimeouts,
        });
        if (dismissed?.result) {
          console.log(`[Mighty] ${account.name} page ${i + 1}: dismissed session timeout dialog`);
          await sleep(8_000); // SPA needs time to re-render real content after session refresh
        }
      } catch (_) { /* frame may have briefly navigated — proceed */ }

      // Extract text with one retry if the frame was momentarily removed
      let result;
      try {
        [result] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: extractPageText,
        });
      } catch (frameErr) {
        if (frameErr.message && frameErr.message.includes('Frame with ID')) {
          console.log(`[Mighty] ${account.name} page ${i + 1}: frame removed, retrying in 5s…`);
          await sleep(5_000);
          try {
            [result] = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: extractPageText,
            });
          } catch (_) { result = undefined; }
        } else {
          throw frameErr;
        }
      }

      let pageText = result?.result || '';

      // Check for bot-detection
      let lower = pageText.toLowerCase();
      let blocked = BOT_DETECTION_PHRASES.find(p => lower.includes(p));
      if (blocked) {
        console.warn(`[Mighty] ${account.name} page ${i + 1}: bot detection ("${blocked}")`);
        // For delta wallet: navigate away and back to reset the bot challenge, then retry once
        if (urls[i] && urls[i].includes('delta.com/us/en/my-account')) {
          console.log(`[Mighty] ${account.name}: navigating away then back to reset bot challenge…`);
          await chrome.tabs.update(tab.id, { url: 'https://www.delta.com/' });
          await waitForTabLoad(tab.id, 20_000);
          await sleep(8_000);
          await chrome.tabs.update(tab.id, { url: urls[i] });
          await waitForTabLoad(tab.id, 20_000);
          await sleep(20_000); // longer settle after reset
          try {
            const [r2] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPageText });
            pageText = r2?.result || '';
            lower = pageText.toLowerCase();
            blocked = BOT_DETECTION_PHRASES.find(p => lower.includes(p));
          } catch (_) {}
          if (blocked) {
            console.warn(`[Mighty] ${account.name} page ${i + 1}: still bot-detected after retry — skipping`);
            continue;
          }
        } else {
          continue;
        }
      }

      // For Delta wallet/eCredits: retry once if content looks thin (certificates render late)
      if (pageText.length < 2000 && urls[i] && urls[i].includes('delta.com/us/en/my-account')) {
        console.log(`[Mighty] ${account.name} page ${i + 1}: thin content (${pageText.length} chars), waiting 15s for React render…`);
        await sleep(15_000);
        try {
          const [r2] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractPageText });
          if ((r2?.result || '').length > pageText.length) pageText = r2.result;
        } catch (_) {}
      }

      if (pageText.length >= 100) {
        console.log(`[Mighty] ${account.name} page ${i + 1}: ${pageText.length} chars`);
        allText.push(`\n\n--- ${urls[i]} ---\n${pageText}`);
      } else {
        console.warn(`[Mighty] ${account.name} page ${i + 1}: too short (${pageText.length} chars) — skipping`);
      }
    }

    // SPA in-page navigation for sites where direct URL changes trigger bot detection
    const spaNav = SPA_NAV_URLS[account.source];
    if (spaNav && allText.length > 0) {
      for (const nav of spaNav) {
        try {
          // Click the matching link within the current page (stays in same origin session)
          const [clicked] = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: (terms) => {
              const links = Array.from(document.querySelectorAll('a[href]'));
              const match = links.find(a => {
                const href = (a.href || '').toLowerCase();
                const text = (a.textContent || '').toLowerCase().trim();
                return terms.some(t => href.includes(t.toLowerCase()) || text.includes(t.toLowerCase()));
              });
              if (match) { match.click(); return match.href || true; }
              return false;
            },
            args: [nav.terms],
          });
          if (!clicked?.result) {
            // Dump all links on the page to help diagnose
            const [linkDump] = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              func: () => Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(h => h && !h.startsWith('javascript') && h.length < 120)
                .slice(0, 40),
            });
            console.warn(`[Mighty] ${account.name}: no link found for "${nav.label}". Page links:`, linkDump?.result);
            continue;
          }
          console.log(`[Mighty] ${account.name}: clicked "${nav.label}" → ${clicked.result}`);
          await sleep(6_000); // wait for SPA to render new section
          const [res] = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: extractPageText,
          });
          const pageText = res?.result || '';
          const lower = pageText.toLowerCase();
          const blocked = BOT_DETECTION_PHRASES.find(p => lower.includes(p));
          if (blocked) {
            console.warn(`[Mighty] ${account.name} SPA "${nav.label}": bot detection — skipping`);
          } else if (pageText.length >= 100) {
            console.log(`[Mighty] ${account.name} SPA "${nav.label}": ${pageText.length} chars`);
            allText.push(`\n\n--- ${nav.label} ---\n${pageText}`);
          }
        } catch(e) {
          console.warn(`[Mighty] ${account.name} SPA "${nav.label}" error:`, e.message);
        }
      }
    }

    if (allText.length === 0) {
      throw new Error('All pages returned too little content — possibly not logged in');
    }

    const rawText = allText.join('').slice(0, 40_000); // cap at 40k chars
    console.log(`[Mighty] ${account.name}: total ${rawText.length} chars from ${allText.length} page(s)`);

    const pushResp = await fetch(`${MIGHTY_URL}/api/data/sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
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
