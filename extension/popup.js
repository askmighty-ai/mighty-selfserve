function timeAgo(isoStr) {
  if (!isoStr) return null;
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (isNaN(diff)) return 'unknown';
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

chrome.storage.local.get(['api_key', 'last_sync', 'sync_status', '_sync_lock_ts', 'captured_accounts', 'ext_version'], function(data) {
  const api_key          = data.api_key;
  const last_sync        = data.last_sync;
  const sync_status      = data.sync_status;
  const _sync_lock_ts    = data._sync_lock_ts || 0;
  const captured_accounts = data.captured_accounts || {};
  const ext_version      = data.ext_version || '';

  const dot        = document.getElementById('status-dot');
  const label      = document.getElementById('status-label');
  const sub        = document.getElementById('status-sub');
  const setupBox   = document.getElementById('setup-box');
  const lastSyncEl = document.getElementById('last-sync-row');
  const autoBadge  = document.getElementById('auto-badge');

  if (!api_key) {
    dot.classList.add('warning');
    label.textContent = 'Setup needed';
    sub.textContent   = 'Tap below to configure';
    setupBox.classList.remove('hidden');
    return;
  }

  const capturedCount = Object.keys(captured_accounts).length;

  const isSyncStuck = sync_status && sync_status.startsWith('Syncing')
    && _sync_lock_ts && (Date.now() - _sync_lock_ts) > 5 * 60 * 1000;

  if (isSyncStuck) {
    // Lock held >5 min — likely a stuck sync from a previous crash/loop
    const resetBtn = document.getElementById('reset-sync-btn');
    resetBtn.classList.remove('hidden');
    resetBtn.addEventListener('click', function() {
      chrome.storage.local.remove(['_sync_lock_ts', 'sync_status', 'mighty_login_detected'], function() {
        resetBtn.textContent = 'Cleared — reload dashboard';
        resetBtn.disabled = true;
      });
    });
  }

  if (sync_status && sync_status.startsWith('Syncing')) {
    dot.classList.add('active');
    label.textContent = 'Syncing…';
    sub.textContent   = isSyncStuck ? 'Sync may be stuck — see Reset button' : 'Updating your account data';
  } else {
    dot.classList.add('active');
    label.textContent = 'Active';
    sub.textContent   = capturedCount
      ? capturedCount + ' account' + (capturedCount !== 1 ? 's' : '') + ' tracked'
      : 'Watching for account pages';
  }

  const ago = timeAgo(last_sync);
  if (ago) {
    lastSyncEl.classList.remove('hidden');
    lastSyncEl.innerHTML = 'Last synced <strong>' + ago + '</strong>'
      + (ext_version ? ' &nbsp;·&nbsp; <span style="color:#d1d5db">' + ext_version + '</span>' : '');
  }

  autoBadge.classList.remove('hidden');

  // Show version even if no sync yet
  if (!ago && ext_version) {
    lastSyncEl.classList.remove('hidden');
    lastSyncEl.innerHTML = '<span style="color:#d1d5db">' + ext_version + '</span>';
  }
});
