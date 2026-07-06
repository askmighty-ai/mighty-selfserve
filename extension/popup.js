const MIGHTY_URL = 'https://mighty-selfserve-production.up.railway.app';

const DEFAULT_COPY = {
  worker: {
    name: 'Mighty',
    subtitle_background: 'Working in the background',
    status_keeping_updated: 'Keeping your accounts up to date',
    status_open_account_center: 'Open Account Center to manage connections',
    open_account_center: 'Open Account Center',
    setup_needed: 'Setup needed',
    setup_detail: 'Open Account Center to connect the worker.',
  },
};

let _copy = DEFAULT_COPY;

function w() { return _copy.worker || DEFAULT_COPY.worker; }

function applyStaticCopy() {
  const worker = w();
  const title = document.getElementById('header-title');
  const headerSub = document.getElementById('header-sub');
  const dashBtn = document.getElementById('dashboard-btn');
  const setupBox = document.getElementById('setup-box');
  if (title && worker.name) title.textContent = worker.name;
  if (headerSub) {
    headerSub.textContent = worker.subtitle_background || 'Working in the background';
  }
  const ctaLabel = worker.open_account_center || 'Open Account Center';
  if (dashBtn) {
    dashBtn.textContent = ctaLabel;
    dashBtn.href = MIGHTY_URL + '/account-center';
  }
  if (setupBox) {
    setupBox.innerHTML =
      'Visit your <a href="' + MIGHTY_URL + '/extension-setup" target="_blank">Account Center</a> to connect the worker.';
  }
}

const dot = document.getElementById('status-dot');
const label = document.getElementById('status-label');
const detail = document.getElementById('status-detail');
const setupBox = document.getElementById('setup-box');

function showBackgroundStatus(statusLine) {
  detail.classList.add('hidden');
  detail.textContent = '';
  dot.className = 'status-dot';
  label.textContent = statusLine || w().status_keeping_updated || 'Keeping your accounts up to date';
}

function render(data) {
  setupBox.classList.add('hidden');

  if (!data.api_key) {
    dot.className = 'status-dot amber';
    label.textContent = w().setup_needed || 'Setup needed';
    detail.textContent = w().setup_detail || 'Open Account Center to connect the worker.';
    detail.classList.remove('hidden');
    setupBox.classList.remove('hidden');
    return;
  }

  const worker = w();
  const needsAttention = _summaryNeedsUserAction(data.account_status);
  const statusLine = needsAttention
    ? (worker.status_open_account_center || 'Open Account Center to manage connections')
    : (worker.status_keeping_updated || 'Keeping your accounts up to date');
  showBackgroundStatus(statusLine);
}

function _summaryNeedsUserAction(accountStatus) {
  if (!accountStatus || !accountStatus.summary) return false;
  const loop = accountStatus.summary.access_loop || accountStatus.summary;
  const needsSignIn = Number(loop.needs_sign_in || loop.needs_login_count || 0);
  const needsAttention = Number(loop.needs_attention || 0);
  return needsSignIn > 0 || needsAttention > 0;
}

async function fetchAccountStatus(apiKey) {
  try {
    const resp = await fetch(`${MIGHTY_URL}/api/account-status`, {
      headers: { 'X-Mighty-Key': apiKey },
    });
    if (resp.ok) return await resp.json();
  } catch (_) {}
  return null;
}

async function loadAndRender(storageData) {
  if (storageData.api_key) {
    const statusPayload = await fetchAccountStatus(storageData.api_key);
    if (statusPayload) {
      storageData.account_status = statusPayload;
      if (statusPayload.copy) {
        _copy = statusPayload.copy;
        applyStaticCopy();
      }
    }
  }
  render(storageData);
  return storageData;
}

const KEYS = ['api_key', 'last_sync', 'sync_status', 'sync_progress',
              'captured_accounts', 'ext_version', 'last_sync_ok', 'last_sync_failed',
              'last_sync_failures'];

var _currentData = {};
var _statusPollTimer = null;

function _scheduleStatusPoll() {
  if (_statusPollTimer) clearInterval(_statusPollTimer);
  _statusPollTimer = setInterval(function() {
    if (!_currentData.api_key) return;
    fetchAccountStatus(_currentData.api_key).then(function(status) {
      if (status) {
        _currentData.account_status = status;
        if (status.copy) {
          _copy = status.copy;
          applyStaticCopy();
        }
        render(_currentData);
      }
    });
  }, 5000);
}

applyStaticCopy();

chrome.storage.local.get(KEYS, function(d) {
  _currentData = d;
  loadAndRender(d).then(function(updated) {
    _currentData = updated;
    _scheduleStatusPoll();
  });
});

chrome.storage.onChanged.addListener(function(changes, area) {
  if (area !== 'local') return;
  var relevant = ['sync_status', 'sync_progress', 'last_sync', 'last_sync_ok',
                  'last_sync_failed', 'last_sync_failures', 'ext_version', 'api_key'];
  var hasRelevant = relevant.some(function(k) { return k in changes; });
  if (!hasRelevant) return;
  relevant.forEach(function(k) {
    if (k in changes) _currentData[k] = changes[k].newValue;
  });
  loadAndRender(_currentData).then(function(updated) {
    _currentData = updated;
  });
});
