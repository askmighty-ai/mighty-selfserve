const MIGHTY_URL = 'https://mighty-selfserve-production.up.railway.app';

// Fallback copy — kept in sync with mighty/user_copy.py
const DEFAULT_COPY = {
  taglines: { manual_step: 'Login is the only manual step. Everything else is automatic.' },
  worker: {
    name: 'Mighty',
    subtitle_running: 'Running in Chrome',
    subtitle_updating: 'Updating accounts',
    subtitle_not_configured: 'Not configured',
    setup_needed: 'Setup needed',
    setup_detail: 'Open your control center to connect the worker.',
    open_dashboard: 'Open control center →',
    not_updated_yet: 'Not updated yet',
    needs_login_subline: 'Sign in to refresh this account',
  },
  failure_hints: {
    login_required: 'Sign in to refresh this account',
    login_wall: 'Sign in to refresh this account',
    timeout: 'Site took too long — will retry on the next automatic update',
    no_data: 'Could not read account data',
    domain_unreachable: 'Site unreachable',
  },
  failure_icons: {
    login_required: '🔐',
    login_wall: '🔐',
    timeout: '⏱',
    no_data: '⚠️',
    domain_unreachable: '🌐',
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

function minsUntil(ts) {
  if (!ts) return null;
  const mins = Math.round((ts - Date.now()) / 60000);
  return mins > 0 ? mins : 0;
}

function w() { return _copy.worker || DEFAULT_COPY.worker; }
function failureCopy(reason) {
  const hints = _copy.failure_hints || DEFAULT_COPY.failure_hints;
  const icons = _copy.failure_icons || DEFAULT_COPY.failure_icons;
  return {
    icon: icons[reason] || '⚠️',
    msg: hints[reason] || 'Update failed',
  };
}

function applyStaticCopy() {
  const worker = w();
  const title = document.getElementById('header-title');
  const modelLine = document.getElementById('model-line');
  const dashBtn = document.getElementById('dashboard-btn');
  const setupBox = document.getElementById('setup-box');
  if (title && worker.name) title.textContent = worker.name;
  if (modelLine && _copy.taglines && _copy.taglines.manual_step) {
    modelLine.textContent = _copy.taglines.manual_step;
  }
  if (dashBtn && worker.open_dashboard) dashBtn.textContent = worker.open_dashboard;
  if (setupBox) {
    setupBox.innerHTML =
      'Visit your <a href="' + MIGHTY_URL + '/extension-setup" target="_blank">control center</a> in Chrome — the worker configures itself automatically.';
  }
}

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dot          = document.getElementById('status-dot');
const label        = document.getElementById('status-label');
const detail       = document.getElementById('status-detail');
const headerSub    = document.getElementById('header-sub');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-fill');
const progressLbl  = document.getElementById('progress-label');
const setupBox     = document.getElementById('setup-box');

function showDetail(html) {
  detail.innerHTML = html;
  detail.classList.remove('hidden');
}

function setDot(cls) {
  dot.className = 'status-dot ' + cls;
}

function _isLocallySyncing(data) {
  const progress = data.sync_progress;
  const status = data.sync_status || '';
  return status.startsWith('Syncing') || status.startsWith('Updating')
    || status === 'sync_active'
    || (progress && progress.total > 0 && progress.done < progress.total);
}

function _resolveHeadline(data) {
  const summary = data.account_status && data.account_status.summary;
  const progress = data.sync_progress;

  if (summary && summary.headline) {
    return summary.headline;
  }
  if (progress && progress.name) {
    return 'Updating ' + progress.name;
  }
  return '';
}

// ── Render: active update / needs-login mix ───────────────────────────────────
function renderActive(data) {
  const summary = data.account_status && data.account_status.summary;
  const progress = data.sync_progress;
  const headline = _resolveHeadline(data);
  const isSyncing = (summary && summary.is_syncing) || _isLocallySyncing(data);
  const worker = w();

  if (isSyncing) {
    setDot('green pulse');
    label.textContent = headline || (progress && progress.name ? 'Updating ' + progress.name : worker.subtitle_updating || 'Updating…');
    headerSub.textContent = (summary && summary.subline) || worker.subtitle_updating || 'Updating accounts';
    showDetail(data.last_sync ? 'Last completed ' + timeAgo(data.last_sync) : 'First update running');
    progressWrap.classList.remove('hidden');

    if (progress && progress.total > 0) {
      const pct = Math.min(99, Math.round(
        ((progress.done + (progress.name ? 0.5 : 0)) / progress.total) * 100
      ));
      progressFill.style.width = pct + '%';
      progressLbl.textContent = progress.name
        ? progress.name + ' (' + (progress.done + 1) + ' of ' + progress.total + ')'
        : progress.done + ' of ' + progress.total + ' done';
    }
    return;
  }

  if (summary && summary.needs_login_count > 0) {
    renderNeedsLogin(data, summary);
  }
}

function renderNeedsLogin(data, summary) {
  progressWrap.classList.add('hidden');
  setDot('red');
  label.textContent = summary.headline;
  headerSub.textContent = w().needs_login_subline || DEFAULT_COPY.worker.needs_login_subline;
  const lines = (summary.needs_login_accounts || []).map(function(name) {
    const c = failureCopy('login_required');
    return c.icon + ' <strong>' + name + '</strong> — ' + c.msg;
  });
  showDetail(lines.join('<br>') || summary.subline);
}

// ── Render: idle state (worker running in Chrome) ───────────────────────────────
function renderIdle(data) {
  progressWrap.classList.add('hidden');
  headerSub.textContent = w().subtitle_running || DEFAULT_COPY.worker.subtitle_running;

  const summary = data.account_status && data.account_status.summary;
  if (summary && summary.needs_login_count > 0 && !summary.is_syncing) {
    renderNeedsLogin(data, summary);
    return;
  }

  const { last_sync, last_sync_ok, last_sync_failed, last_sync_failures,
          ext_version, captured_accounts } = data;
  const capturedCount = Object.keys(captured_accounts || {}).length;
  const ago = timeAgo(last_sync);
  const failures = last_sync_failures || [];
  const worker = w();

  chrome.alarms.get('mighty-sync', function(alarm) {
    const nextMins = minsUntil(alarm && alarm.scheduledTime);

    if (!ago) {
      setDot('amber');
      label.textContent = worker.not_updated_yet || DEFAULT_COPY.worker.not_updated_yet;
      showDetail(
        (nextMins !== null ? 'First update in ' + nextMins + 'm' : 'Waiting for first update…') +
        (ext_version ? '<br><span class="dim">' + ext_version + '</span>' : '')
      );
      return;
    }

    const hadFailures = typeof last_sync_failed === 'number' && last_sync_failed > 0;
    const allFailed   = typeof last_sync_ok === 'number' && last_sync_ok === 0 && hadFailures;

    setDot(allFailed ? 'red' : hadFailures ? 'amber' : 'green');
    label.textContent = 'Updated ' + ago;

    let summaryStr = '';
    if (typeof last_sync_ok === 'number') {
      const total = (last_sync_ok || 0) + (last_sync_failed || 0);
      summaryStr = hadFailures
        ? last_sync_ok + ' of ' + total + ' accounts updated'
        : last_sync_ok + ' account' + (last_sync_ok !== 1 ? 's' : '') + ' updated';
      if (capturedCount > 0) summaryStr += ' · ' + capturedCount + ' captured';
    }

    const failureLines = failures.map(function(f) {
      const c = failureCopy(f.reason);
      return c.icon + ' <strong>' + f.name + '</strong> — ' + c.msg;
    });

    const nextStr = nextMins !== null ? 'Next update in ' + nextMins + 'm' : '';

    const lines = [summaryStr]
      .concat(failureLines)
      .concat([nextStr, ext_version ? '<span class="dim">' + ext_version + '</span>' : ''])
      .filter(Boolean).join('<br>');
    showDetail(lines || '&nbsp;');
  });
}

// ── Main render ───────────────────────────────────────────────────────────────
function render(data) {
  progressWrap.classList.add('hidden');
  setupBox.classList.add('hidden');

  if (!data.api_key) {
    const worker = w();
    setDot('amber');
    label.textContent = worker.setup_needed || DEFAULT_COPY.worker.setup_needed;
    showDetail(worker.setup_detail || DEFAULT_COPY.worker.setup_detail);
    setupBox.classList.remove('hidden');
    headerSub.textContent = worker.subtitle_not_configured || DEFAULT_COPY.worker.subtitle_not_configured;
    return;
  }

  const summary = data.account_status && data.account_status.summary;
  const isSyncing = (summary && summary.is_syncing) || _isLocallySyncing(data);

  if (isSyncing || (summary && summary.is_syncing)) {
    renderActive(data);
    return;
  }

  if (summary && summary.needs_login_count > 0) {
    renderNeedsLogin(data, summary);
    return;
  }

  renderIdle(data);
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

// ── Initial load ──────────────────────────────────────────────────────────────
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

// ── Reactive updates via storage.onChanged ────────────────────────────────────
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
