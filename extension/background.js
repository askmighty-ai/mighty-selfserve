// Mighty Sync — background service worker
// Opens account pages as background tabs, extracts text, pushes to Railway.

const MIGHTY_URL    = 'https://mighty-selfserve-production.up.railway.app';
const SYNC_ALARM    = 'mighty-sync';
const SYNC_INTERVAL = 240; // minutes (every 4 hours)

// Account page URLs — where to navigate to get each account's data.
// Keys match the source keys in the Mighty dashboard.
const ACCOUNT_URLS = {
  southwest:    'https://www.southwest.com/loyalty/myaccount/',
  united:       'https://www.united.com/en/us/myaccount/mileageplus',
  american_air: 'https://www.aa.com/aadvantage-program/overview',
  alaska_air:   'https://www.alaskaair.com/account/dashboard',
  delta:        'https://www.delta.com/us/en/skymiles/account-activity',
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
  xfinity:      'https://customer.xfinity.com/#/devices',
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
    const url = ACCOUNT_URLS[account.source];
    if (!url) {
      console.log(`[Mighty] No URL mapping for ${account.source} — skipping`);
      continue;
    }
    try {
      await syncAccount(api_key, account, url);
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

// ── Per-account sync ─────────────────────────────────────────────────────────

async function syncAccount(apiKey, account, url) {
  console.log(`[Mighty] → ${account.name} (${url})`);

  // Open the account page as an active tab so SPAs fully render.
  // (Background tabs get throttled — React apps won't finish loading.)
  const tab = await chrome.tabs.create({ url, active: true });

  try {
    await waitForTabLoad(tab.id, 20_000);
    await sleep(8_000); // let SPA content fully render

    // Extract all visible text from the page
    const [result] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractPageText,
    });

    const rawText = result?.result || '';
    if (!rawText || rawText.length < 100) {
      throw new Error('Page returned too little content — possibly not logged in');
    }

    console.log(`[Mighty] ${account.name}: got ${rawText.length} chars`);

    // Push to Railway using the existing data sync endpoint
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
