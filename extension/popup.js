// ── DOM refs ─────────────────────────────────────────────────────────────────
const statusBox   = document.getElementById('status-box');
const statusText  = document.getElementById('status-text');
const spinnerEl   = document.getElementById('spinner');
const apiKeyEl    = document.getElementById('api-key');
const saveBtn     = document.getElementById('save-btn');
const syncBtn     = document.getElementById('sync-btn');
const captureBtn  = document.getElementById('capture-btn');
const addPageBtn  = document.getElementById('add-page-btn');
const captureNameEl = document.getElementById('capture-name');
const captureCatEl  = document.getElementById('capture-category');
const capturedList  = document.getElementById('captured-list');

// ── State ─────────────────────────────────────────────────────────────────────
let currentTab   = null;   // active Chrome tab
let lastCaptured = null;   // source key of last captured account (for "add another page")

// ── Helpers ──────────────────────────────────────────────────────────────────
function setStatus(msg, { loading = false, error = false, success = false } = {}) {
  statusText.textContent = msg;
  spinnerEl.classList.toggle('hidden', !loading);
  statusBox.classList.toggle('error',   error);
  statusBox.classList.toggle('success', success && !error);
  if (!error && !success) statusBox.classList.remove('error', 'success');
}

function switchTab(name) {
  ['capture', 'accounts', 'settings'].forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === name);
    document.getElementById('panel-' + t).classList.toggle('active', t === name);
  });
}

function toggleKeyVis() {
  const isPass = apiKeyEl.type === 'password';
  apiKeyEl.type = isPass ? 'text' : 'password';
  document.getElementById('toggle-key').textContent = isPass ? 'Hide' : 'Show';
}

function domainFromUrl(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch { return url; }
}

function guessName(tab) {
  // Strip generic suffixes from page title
  const title = (tab.title || '').replace(/[-|–—]\s*(log.?in|sign.?in|home|dashboard|account|my account|overview).*/i, '').trim();
  if (title && title.length > 2 && title.length < 50) return title;
  return domainFromUrl(tab.url).split('.')[0].replace(/[-_]/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function guessCategory(url) {
  const u = url.toLowerCase();
  if (/delta|united|southwest|american.?air|alaska.?air|marriott|hilton|hyatt|ihg|wyndham|hertz|hotel|airline|flight/.test(u)) return 'Travel';
  if (/amex|chase|wellsfargo|bankofamerica|capitalone|discover|citi|paypal|fidelity|schwab|bank|credit/.test(u)) return 'Banking & Finance';
  if (/xfinity|att|verizon|t-mobile|tmobile|comcast|spectrum|utility|electric|gas|water/.test(u)) return 'Utilities & Telecom';
  if (/amazon|target|walmart|costco|shop|store|retail/.test(u)) return 'Shopping';
  if (/netflix|hulu|spotify|disney|hbo|max|peacock|paramount|ticket/.test(u)) return 'Entertainment';
  if (/health|hospital|clinic|doctor|pharmacy|cvs|walgreen|kaiser|pamf|mychart/.test(u)) return 'Health';
  return 'Other';
}

function renderCapturedList(captured) {
  const entries = Object.entries(captured || {});
  if (!entries.length) {
    capturedList.innerHTML = '<div class="empty-state">No captured accounts yet.<br>Use the Capture tab to add one.</div>';
    return;
  }
  capturedList.innerHTML = entries.map(([source, info]) => `
    <div class="captured-item">
      <div style="flex:1;min-width:0">
        <div class="captured-item-name">${info.name}</div>
        <div class="captured-item-cat">${info.category} · ${info.urls ? info.urls.length : 0} page(s)</div>
      </div>
      <button class="captured-item-del" title="Remove" onclick="removeCaptured('${source}')">×</button>
    </div>
  `).join('');
}

function removeCaptured(source) {
  chrome.runtime.sendMessage({ action: 'remove_captured', source }, () => {
    chrome.storage.local.get('captured_accounts', ({ captured_accounts = {} }) => {
      renderCapturedList(captured_accounts);
    });
  });
}

// ── Init ─────────────────────────────────────────────────────────────────────
chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  currentTab = tab;
  if (!tab) return;

  // Populate site preview
  const domain = domainFromUrl(tab.url);
  document.getElementById('site-domain').textContent = domain;
  document.getElementById('site-url').textContent    = tab.url;
  document.getElementById('site-favicon').textContent = '🌐';

  // Auto-fill name and category
  captureNameEl.value = guessName(tab);
  const guessedCat    = guessCategory(tab.url);
  const catOpts       = Array.from(captureCatEl.options);
  const match         = catOpts.find(o => o.value === guessedCat);
  if (match) captureCatEl.value = guessedCat;
});

// Load status + api key + captured accounts
chrome.runtime.sendMessage({ action: 'get_status' }, ({ api_key, sync_status, captured_accounts } = {}) => {
  if (api_key) {
    apiKeyEl.value = api_key;
    setStatus(sync_status || 'Ready');
    captureBtn.disabled = false;
  } else {
    setStatus('Set your API key in Settings to get started');
    captureBtn.disabled = true;
    switchTab('settings');
  }
  renderCapturedList(captured_accounts);
});

// ── Capture ──────────────────────────────────────────────────────────────────
captureBtn.addEventListener('click', async () => {
  const name = captureNameEl.value.trim();
  if (!name) { setStatus('Enter an account name first', { error: true }); return; }
  if (!currentTab) { setStatus('No active tab found', { error: true }); return; }

  captureBtn.disabled = true;
  captureBtn.textContent = '⏳ Capturing…';
  setStatus('Reading page…', { loading: true });

  chrome.runtime.sendMessage({
    action:   'capture_tab',
    tabId:    currentTab.id,
    name:     name,
    category: captureCatEl.value,
  }, (resp) => {
    captureBtn.disabled  = false;
    captureBtn.textContent = '📸 Capture this page';

    if (resp?.error) {
      setStatus('Error: ' + resp.error, { error: true });
      return;
    }

    lastCaptured = resp.source;
    setStatus(`✓ "${name}" captured — AI is extracting fields`, { success: true });

    // Show "add another page" button and refresh captured list
    addPageBtn.style.display = 'block';
    chrome.storage.local.get('captured_accounts', ({ captured_accounts = {} }) => {
      renderCapturedList(captured_accounts);
    });
  });
});

// "Add another page" — navigates to same account, adds current page's URL
addPageBtn.addEventListener('click', () => {
  if (!lastCaptured || !currentTab) return;
  const name = captureNameEl.value.trim();
  captureBtn.click(); // reuses same name/category, appends new URL
});

// ── Sync ─────────────────────────────────────────────────────────────────────
syncBtn.addEventListener('click', () => {
  syncBtn.disabled = true;
  setStatus('Syncing…', { loading: true });
  chrome.runtime.sendMessage({ action: 'sync_now' }, (resp) => {
    syncBtn.disabled = false;
    if (resp?.error) {
      setStatus('Error: ' + resp.error, { error: true });
    } else {
      chrome.storage.local.get(['sync_status', 'captured_accounts'], ({ sync_status, captured_accounts }) => {
        setStatus(sync_status || 'Done');
        renderCapturedList(captured_accounts);
      });
    }
  });
});

// ── Settings ─────────────────────────────────────────────────────────────────
saveBtn.addEventListener('click', () => {
  const key = apiKeyEl.value.trim();
  if (!key) { setStatus('Please enter your API key', { error: true }); return; }
  chrome.storage.local.set({ api_key: key }, () => {
    captureBtn.disabled = false;
    setStatus('API key saved ✓', { success: true });
  });
});

apiKeyEl.addEventListener('keydown', e => { if (e.key === 'Enter') saveBtn.click(); });
