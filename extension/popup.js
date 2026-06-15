function timeAgo(isoStr) {
  if (!isoStr) return null;
  const diff = Math.floor((Date.now() - new Date(isoStr).getTime()) / 1000);
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}

chrome.storage.local.get(['api_key', 'last_sync', 'sync_status', 'captured_accounts'], function(data) {
  const api_key          = data.api_key;
  const last_sync        = data.last_sync;
  const sync_status      = data.sync_status;
  const captured_accounts = data.captured_accounts || {};

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

  if (sync_status === 'syncing') {
    dot.classList.add('active');
    label.textContent = 'Syncing…';
    sub.textContent   = 'Updating your account data';
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
    lastSyncEl.innerHTML = 'Last synced <strong>' + ago + '</strong>';
  }

  autoBadge.classList.remove('hidden');
});
