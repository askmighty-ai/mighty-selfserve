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

// ── DOM refs ──────────────────────────────────────────────────────────────────
const dot          = document.getElementById('status-dot');
const label        = document.getElementById('status-label');
const detail       = document.getElementById('status-detail');
const headerSub    = document.getElementById('header-sub');
const progressWrap = document.getElementById('progress-wrap');
const progressFill = document.getElementById('progress-fill');
const progressLbl  = document.getElementById('progress-label');
const resetBtn     = document.getElementById('reset-sync-btn');
const setupBox     = document.getElementById('setup-box');

function showDetail(html) {
  detail.innerHTML = html;
  detail.classList.remove('hidden');
}

function setDot(cls) {
  dot.className = 'status-dot ' + cls;
}

// ── Render: syncing state ─────────────────────────────────────────────────────
function renderSyncing(last_sync, progress) {
  setDot('green pulse');
  label.textContent = 'Syncing…';
  headerSub.textContent = 'Updating your accounts';
  showDetail(last_sync ? 'Last completed ' + timeAgo(last_sync) : 'First sync running');
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
}

// ── Render: idle state ────────────────────────────────────────────────────────
function renderIdle(data) {
  progressWrap.classList.add('hidden');
  headerSub.textContent = 'Background sync active';

  const { last_sync, last_sync_ok, last_sync_failed, ext_version, captured_accounts } = data;
  const capturedCount = Object.keys(captured_accounts || {}).length;
  const ago = timeAgo(last_sync);

  chrome.alarms.get('mighty-sync', function(alarm) {
    const nextMins = minsUntil(alarm && alarm.scheduledTime);

    if (!ago) {
      setDot('amber');
      label.textContent = 'Not synced yet';
      showDetail(
        (nextMins !== null ? 'First sync in ' + nextMins + 'm' : 'Waiting for first sync…') +
        (ext_version ? '<br><span class="dim">' + ext_version + '</span>' : '')
      );
      return;
    }

    const hadFailures = typeof last_sync_failed === 'number' && last_sync_failed > 0;
    const allFailed   = typeof last_sync_ok === 'number' && last_sync_ok === 0 && hadFailures;

    setDot(allFailed ? 'red' : hadFailures ? 'amber' : 'green');
    label.textContent = 'Synced ' + ago;

    let outcomeStr = '';
    if (typeof last_sync_ok === 'number') {
      const total = (last_sync_ok || 0) + (last_sync_failed || 0);
      outcomeStr = hadFailures
        ? last_sync_ok + ' of ' + total + ' accounts updated'
        : last_sync_ok + ' account' + (last_sync_ok !== 1 ? 's' : '') + ' updated';
      if (capturedCount > 0) outcomeStr += ' · ' + capturedCount + ' captured';
    }

    const lines = [
      outcomeStr,
      nextMins !== null ? 'Next sync in ' + nextMins + 'm' : '',
      ext_version ? '<span class="dim">' + ext_version + '</span>' : '',
    ].filter(Boolean).join('<br>');
    showDetail(lines || '&nbsp;');
  });
}

// ── Render: stuck sync ────────────────────────────────────────────────────────
function renderStuck() {
  setDot('red');
  label.textContent = 'Sync stuck';
  showDetail('A sync started but never finished.<br>Use the button below to reset it.');
  resetBtn.classList.remove('hidden');
  resetBtn.addEventListener('click', function() {
    chrome.storage.local.remove(['_sync_lock_ts', 'sync_status', 'mighty_login_detected'], function() {
      resetBtn.textContent = 'Cleared — reload dashboard';
      resetBtn.disabled = true;
    });
  });
}

// ── Main render ───────────────────────────────────────────────────────────────
function render(data) {
  if (!data.api_key) {
    setDot('amber');
    label.textContent = 'Setup needed';
    showDetail('Open your Mighty dashboard to connect.');
    setupBox.classList.remove('hidden');
    headerSub.textContent = 'Not configured';
    return;
  }

  const sync_status   = data.sync_status || '';
  const _sync_lock_ts = data._sync_lock_ts || 0;
  const isSyncing     = sync_status.startsWith('Syncing');
  const isStuck       = isSyncing && _sync_lock_ts && (Date.now() - _sync_lock_ts) > 5 * 60 * 1000;

  if (isStuck)    { renderStuck(); return; }
  if (isSyncing)  { renderSyncing(data.last_sync, data.sync_progress); return; }
  renderIdle(data);
}

// ── Initial load ──────────────────────────────────────────────────────────────
const KEYS = ['api_key', 'last_sync', 'sync_status', 'sync_progress', '_sync_lock_ts',
              'captured_accounts', 'ext_version', 'last_sync_ok', 'last_sync_failed'];

chrome.storage.local.get(KEYS, render);

// ── Reactive updates via storage.onChanged ────────────────────────────────────
// When the service worker writes to storage (e.g. sync starts 3s after popup opens,
// or progress updates during a sync), re-read all keys and re-render.
// This replaces the polling interval approach.
var _currentData = {};
chrome.storage.local.get(KEYS, function(d) { _currentData = d; });

chrome.storage.onChanged.addListener(function(changes, area) {
  if (area !== 'local') return;
  var relevant = ['sync_status', 'sync_progress', 'last_sync', 'last_sync_ok',
                  'last_sync_failed', '_sync_lock_ts', 'ext_version'];
  var hasRelevant = relevant.some(function(k) { return k in changes; });
  if (!hasRelevant) return;

  // Merge changed values into current data snapshot, then re-render
  relevant.forEach(function(k) {
    if (k in changes) _currentData[k] = changes[k].newValue;
  });
  // Also pick up sync_progress specifically (it's large, update directly)
  if ('sync_progress' in changes) {
    _currentData.sync_progress = changes.sync_progress.newValue;
  }

  render(_currentData);
});
