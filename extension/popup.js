const statusBox  = document.getElementById('status-box');
const statusText = document.getElementById('status-text');
const spinner    = document.getElementById('spinner');
const apiKeyEl   = document.getElementById('api-key');
const saveBtn    = document.getElementById('save-btn');
const syncBtn    = document.getElementById('sync-btn');

function setStatus(msg, { loading = false, error = false } = {}) {
  statusText.textContent = msg;
  spinner.classList.toggle('hidden', !loading);
  statusBox.classList.toggle('error', error);
}

// Load saved state on open
chrome.runtime.sendMessage({ action: 'get_status' }, ({ api_key, sync_status } = {}) => {
  if (api_key) {
    apiKeyEl.value = api_key;
    syncBtn.disabled = false;
    setStatus(sync_status || 'Ready — click Sync Now or wait for auto-sync');
  } else {
    setStatus('Enter your API key to get started');
  }
});

// Save API key
saveBtn.addEventListener('click', () => {
  const key = apiKeyEl.value.trim();
  if (!key) { setStatus('Please enter your API key', { error: true }); return; }
  chrome.storage.local.set({ api_key: key }, () => {
    syncBtn.disabled = false;
    setStatus('API key saved ✓');
  });
});

// Allow saving with Enter key
apiKeyEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') saveBtn.click();
});

// Trigger manual sync
syncBtn.addEventListener('click', () => {
  syncBtn.disabled = true;
  setStatus('Syncing…', { loading: true });

  chrome.runtime.sendMessage({ action: 'sync_now' }, (resp) => {
    syncBtn.disabled = false;
    if (resp?.error) {
      setStatus('Error: ' + resp.error, { error: true });
    } else {
      // Fetch updated status from storage
      chrome.storage.local.get('sync_status', ({ sync_status }) => {
        setStatus(sync_status || 'Done');
      });
    }
  });
});
