// Mighty Sync — background service worker
// Opens account pages as background tabs, extracts text, pushes to Railway.

const MIGHTY_URL    = 'https://mighty-selfserve-production.up.railway.app';
const SYNC_ALARM    = 'mighty-sync';
const SYNC_INTERVAL = 240; // minutes (every 4 hours)

// Account page URLs — where to navigate to get each account's data.
// Values can be a single URL string or an array of URLs (visited in order,
// text concatenated) to capture sub-pages like vouchers, travel funds, etc.
const ACCOUNT_URLS = {
  southwest: [
    'https://www.southwest.com/loyalty/myaccount/',
    'https://www.southwest.com/loyalty/rapidrewards/travelFunds.html',
    'https://www.southwest.com/loyalty/myaccount/upcoming-trips.html',
  ],
  // Delta sub-pages beyond myprofile trigger Akamai when navigated directly.
  // Use SPA_NAV_URLS to navigate via in-page clicks instead of URL changes.
  delta: ['https://www.delta.com/myprofile/'],
  united:       'https://www.united.com/en/us/myaccount/mileageplus',
  american_air: [
    'https://www.aa.com/aadvantage-program/overview',
    'https://www.aa.com/loyalty/home.do',
  ],
  alaska_air:   'https://www.alaskaair.com/account/dashboard',
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
  netflix:      'https://www.netflix.com/YourAccount',
  hulu:         'https://secure.hulu.com/account',
  spotify:      'https://www.spotify.com/us/account/overview/',
  disney_plus:  'https://www.disneyplus.com/identity/account',
  att:          'https://www.att.com/my/#/',
  verizon:      'https://www.verizon.com/myverizon/',
  tmobile:      'https://account.t-mobile.com/overview',
  xfinity: [
    'https://customer.xfinity.com/#/account',
    'https://customer.xfinity.com/#/services',
  ],
  hertz:        'https://www.hertz.com/rentacar/member/profile/myprofile',
  cvs:          'https://www.cvs.com/account/login.jsp',
  walgreens:    'https://www.walgreens.com/myaccount/mywalgreenssummary.jsp',
};

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
    runSync()
      .then(() => sendResponse({ ok: true }))
      .catch(e => sendResponse({ error: e.message }));
    return true; // keep channel open for async response
  }
  if (msg.action === 'get_status') {
    chrome.storage.local.get(['last_sync', 'sync_status', 'api_key'], sendResponse);
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

  if (!accounts.length) {
    await setStatus('No connected accounts found in dashboard');
    return;
  }

  console.log(`[Mighty] Syncing ${accounts.length} accounts…`);
  let ok = 0, failed = 0;

  for (const account of accounts) {
    const urlEntry = ACCOUNT_URLS[account.source];
    if (!urlEntry) {
      console.log(`[Mighty] No URL mapping for ${account.source} — skipping`);
      continue;
    }
    const urls = Array.isArray(urlEntry) ? urlEntry : [urlEntry];
    try {
      await syncAccount(api_key, account, urls);
      ok++;
    } catch (e) {
      console.error(`[Mighty] Failed: ${account.name}:`, e.message);
      failed++;
    }
  }

  const ts = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const msg = `Synced at ${ts} — ${ok} ok${failed ? `, ${failed} failed` : ''}`;
  await chrome.storage.local.set({ last_sync: new Date().toISOString() });
  await setStatus(msg);
  console.log('[Mighty]', msg);
}

// Sites that need a warm-up page visit before the real account URL,
// to establish session context and avoid bot-detection on cold navigation.
const WARMUP_URLS = {
  delta: 'https://www.delta.com/',
};

// Per-site settle time (ms) after page load before extracting text.
// Override for SPAs that need longer to fully render.
const SETTLE_MS = {
  xfinity: 20_000,
  default:  8_000,
  subsequent: 8_000,
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
  'gate change',       // Delta/Akamai
  'access denied',
  'checking your browser',
  'ddos protection',
  'please wait',       // Cloudflare challenge
  'cf-browser-verification',
  'unusual traffic',
];

// ── Per-account sync ─────────────────────────────────────────────────────────

async function syncAccount(apiKey, account, urls) {
  console.log(`[Mighty] → ${account.name} (${urls.length} page${urls.length > 1 ? 's' : ''})`);

  const warmup = WARMUP_URLS[account.source];
  const allText = [];

  // Open a tab once, reuse it for all sub-pages
  const startUrl = warmup || urls[0];
  const tab = await chrome.tabs.create({ url: startUrl, active: true });

  try {
    await waitForTabLoad(tab.id, 20_000);

    if (warmup) {
      await sleep(3_000);
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
        : SETTLE_MS.subsequent;
      await sleep(settleMs);

      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: extractPageText,
      });

      const pageText = result?.result || '';

      // Check for bot-detection
      const lower = pageText.toLowerCase();
      const blocked = BOT_DETECTION_PHRASES.find(p => lower.includes(p));
      if (blocked) {
        console.warn(`[Mighty] ${account.name} page ${i + 1}: bot detection ("${blocked}") — skipping page`);
        continue;
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
        api_key: apiKey,
        source: account.source,
        data: {
          name:     account.name,
          icon:     account.icon,
          color:    account.color,
          status:   'ok',
          items:    [],
          raw_text: rawText,
        },
        synced_at: new Date().toISOString(),
      }),
    });

    if (!pushResp.ok) {
      const body = await pushResp.text();
      throw new Error(`Push failed: HTTP ${pushResp.status} — ${body.slice(0, 100)}`);
    }

    console.log(`[Mighty] ${account.name}: ✓`);

  } finally {
    chrome.tabs.remove(tab.id).catch(() => {});
  }
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
