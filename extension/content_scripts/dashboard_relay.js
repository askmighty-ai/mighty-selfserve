// Mighty Sync — dashboard relay (runs on the Mighty dashboard page)
// Listens for postMessages from the dashboard JS and relays them to background.js.
// This lets the dashboard's "Sync All" button trigger the extension sync.

(function () {
  'use strict';

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.type !== '__mighty_dashboard__') return;

    const action = event.data.action;
    if (!action) return;

    chrome.runtime.sendMessage({ action }, (resp) => {
      // Relay response back to page
      window.postMessage({ type: '__mighty_dashboard_reply__', action, resp }, '*');
    });
  });

  // Tell the page the extension is present
  window.postMessage({ type: '__mighty_ext_present__' }, '*');
})();
