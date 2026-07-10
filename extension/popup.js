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

function timeAgo(isoStr) {
  if (!isoStr) return null;
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (isNaN(diff) || diff < 0) return 'just now';
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

function w() { return _copy.worker || DEFAULT_COPY.worker; }
function accessLoopCopy() { return _copy.access_loop || DEFAULT_COPY.access_loop; }

function applyStaticCopy() {
  const worker = w();
  const loop = accessLoopCopy();
  const title = document.getElementById('header-title');
  const headerSub = document.getElementById('header-sub');
  const dashBtn = document.getElementById('dashboard-btn');
  if (title && worker.name) title.textContent = worker.name;
  if (headerSub) {
    headerSub.textContent = worker.subtitle_background || 'Working in the background';
  }
  const ctaLabel =
    (loop && loop.open_account_center) ||
    worker.open_account_center ||
    'Open Account Center';
  if (dashBtn) {
    dashBtn.textContent = ctaLabel;
    dashBtn.href = MIGHTY_URL + '/account-center';
  }
  if (setupBox) {
    setupBox.innerHTML =
      'Visit your <a href="' + MIGHTY_URL + '/extension-setup" target="_blank">Account Center</a> to connect the worker.';
  }
}

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dot          = document.getElementById('status-dot');
const label        = document.getElementById('status-label');
const detail       = document.getElementById('status-detail');
const headerSub    = document.getElementById('header-sub');
const progressWrap = document.getElementById('progress-wrap');
const setupBox     = document.getElementById('setup-box');

function showBackgroundStatus(statusLine) {
  detail.classList.add('hidden');
  detail.textContent = '';
  dot.className = 'status-dot';
  label.textContent = statusLine || w().status_keeping_updated || 'Keeping your accounts up to date';
}

function setDot(cls) {
  dot.className = 'status-dot ' + cls;
}

function _accessLoopSummary(data) {
  const summary = data.account_status && data.account_status.summary;
  if (!summary) return null;
  return summary.access_loop || {
    headline: summary.headline,
    detail_lines: summary.detail_lines || (summary.subline ? summary.subline.split(' · ') : []),
    is_updating: summary.is_syncing,
    needs_sign_in: summary.needs_login_count,
    updating: summary.updating_count,
    ready: 0,
    needs_attention: 0,
    open_account_center_label: summary.open_account_center_label,
  };
}

function _dotForLoop(loop) {
  if (!loop) return 'amber';
  if (loop.is_updating) return 'green pulse';
  if (loop.needs_sign_in > 0) return 'red';
  if (loop.needs_attention > 0) return 'amber';
  if (loop.ready > 0) return 'green';
  return 'amber';
}

function renderAccessLoop(data) {
  const loop = _accessLoopSummary(data);
  progressWrap.classList.add('hidden');

  if (!loop || !loop.headline) {
    setDot('amber');
    label.textContent = w().not_updated_yet || 'Not updated yet';
    headerSub.textContent = w().subtitle_running || 'Running in Chrome';
    showDetail('Visit Account Center to connect accounts.');
    return;
  }

  setDot(_dotForLoop(loop));
  label.textContent = loop.headline;
  headerSub.textContent = loop.is_updating
    ? (w().access_loop_updating || w().subtitle_updating || 'Updating accounts')
    : (w().subtitle_running || 'Running in Chrome');

  const lines = (loop.detail_lines || []).filter(Boolean);
  if (loop.is_updating && lines.length) {
    showDetail(lines.join('<br>'));
    return;
  }
  if (lines.length) {
    showDetail(lines.join('<br>'));
    return;
  }
  showDetail('&nbsp;');
}

// ── Main render ───────────────────────────────────────────────────────────────
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

  if (data.account_status && data.account_status.summary) {
    renderAccessLoop(data.account_status);
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
