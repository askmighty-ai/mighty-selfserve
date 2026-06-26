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

chrome.storage.local.get(
  ['api_key', 'last_sync', 'sync_status', '_sync_lock_ts',
   'captured_accounts', 'ext_version', 'last_sync_ok', 'last_sync_failed'],
  function(data) {
    const api_key         = data.api_key;
    const last_sync       = data.last_sync;
    const sync_status     = data.sync_status || '';
    const _sync_lock_ts   = data._sync_lock_ts || 0;
    const capturedCount   = Object.keys(data.captured_accounts || {}).length;
    const ext_version     = data.ext_version || '';
    const last_sync_ok    = data.last_sync_ok;     // number or undefined
    const last_sync_failed = data.last_sync_failed; // number or undefined

    const dot    = document.getElementById('status-dot');
    const label  = document.getElementById('status-label');
    const detail = document.getElementById('status-detail');
    const headerSub = document.getElementById('header-sub');

    function showDetail(html) {
      detail.innerHTML = html;
      detail.classList.remove('hidden');
    }

    // ── No API key ────────────────────────────────────────────────────────────
    if (!api_key) {
      dot.classList.add('amber');
      label.textContent = 'Setup needed';
      showDetail('Open your Mighty dashboard to connect.');
      document.getElementById('setup-box').classList.remove('hidden');
      headerSub.textContent = 'Not configured';
      return;
    }

    // ── Stuck sync detection ──────────────────────────────────────────────────
    const isSyncing = sync_status.startsWith('Syncing');
    const isStuck   = isSyncing && _sync_lock_ts && (Date.now() - _sync_lock_ts) > 5 * 60 * 1000;
    if (isStuck) {
      const resetBtn = document.getElementById('reset-sync-btn');
      resetBtn.classList.remove('hidden');
      resetBtn.addEventListener('click', function() {
        chrome.storage.local.remove(['_sync_lock_ts', 'sync_status', 'mighty_login_detected'], function() {
          resetBtn.textContent = 'Cleared — reload dashboard';
          resetBtn.disabled = true;
        });
      });
      dot.classList.add('red');
      label.textContent = 'Sync stuck';
      showDetail('A sync started but never finished.<br>Use the button below to reset it.');
      return;
    }

    // ── Actively syncing ──────────────────────────────────────────────────────
    if (isSyncing) {
      dot.classList.add('green', 'pulse');
      label.textContent = 'Syncing…';
      headerSub.textContent = 'Updating your accounts';
      const ago = last_sync ? `Last completed ${timeAgo(last_sync)}` : 'First sync running';
      showDetail(ago);

      // Show progress bar and poll storage for per-account updates
      const progressWrap  = document.getElementById('progress-wrap');
      const progressFill  = document.getElementById('progress-fill');
      const progressLabel = document.getElementById('progress-label');
      if (progressWrap) progressWrap.classList.remove('hidden');

      function updateProgress() {
        chrome.storage.local.get(['sync_progress', 'sync_status'], function(d) {
          // If sync ended, reload popup to show final state
          if (!d.sync_status || !d.sync_status.startsWith('Syncing')) {
            clearInterval(pollId);
            window.location.reload();
            return;
          }
          const p = d.sync_progress;
          if (p && p.total > 0) {
            const pct = Math.round((p.done / p.total) * 100);
            if (progressFill)  progressFill.style.width = pct + '%';
            if (progressLabel) {
              progressLabel.textContent = p.name
                ? p.name + ' (' + (p.done + 1) + ' of ' + p.total + ')'
                : p.done + ' of ' + p.total + ' done';
            }
          }
        });
      }

      updateProgress();
      var pollId = setInterval(updateProgress, 900);
      window.addEventListener('unload', function() { clearInterval(pollId); });
      return;
    }

    // ── Configured and idle — show real last-sync outcome ────────────────────
    // Now query the alarm to get next scheduled time
    chrome.alarms.get('mighty-sync', function(alarm) {
      const nextMins = minsUntil(alarm && alarm.scheduledTime);

      const ago = timeAgo(last_sync);

      if (!ago) {
        // Never synced
        dot.classList.add('amber');
        label.textContent = 'Not synced yet';
        const nextStr = nextMins !== null
          ? `First sync in ${nextMins}m`
          : 'Waiting for first sync…';
        showDetail(nextStr + (ext_version ? `<br><span class="dim">${ext_version}</span>` : ''));
        return;
      }

      // Determine dot color based on last sync outcome
      const hadFailures = typeof last_sync_failed === 'number' && last_sync_failed > 0;
      const allFailed   = typeof last_sync_ok     === 'number' && last_sync_ok === 0 && hadFailures;

      if (allFailed) {
        dot.classList.add('red');
      } else if (hadFailures) {
        dot.classList.add('amber');
      } else {
        dot.classList.add('green');
      }

      label.textContent = `Synced ${ago}`;

      // Build detail line
      let outcomeStr = '';
      if (typeof last_sync_ok === 'number') {
        const total = (last_sync_ok || 0) + (last_sync_failed || 0);
        if (hadFailures) {
          outcomeStr = `${last_sync_ok} of ${total} accounts updated`;
        } else {
          outcomeStr = `${last_sync_ok} account${last_sync_ok !== 1 ? 's' : ''} updated`;
        }
        if (capturedCount > 0) outcomeStr += ` · ${capturedCount} captured`;
      }

      const nextStr = nextMins !== null
        ? `Next sync in ${nextMins}m`
        : '';

      const lines = [outcomeStr, nextStr, ext_version ? `<span class="dim">${ext_version}</span>` : '']
        .filter(Boolean).join('<br>');
      showDetail(lines || '&nbsp;');
    });
  }
);
