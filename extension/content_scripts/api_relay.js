// Mighty Sync — API relay (runs in ISOLATED world)
// Listens for postMessages from the MAIN world interceptor and forwards them
// to the background service worker via chrome.runtime.sendMessage.
// Deduplicates by URL within a page session to avoid flooding the server.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';
  const seen = new Set(); // dedupe within page session

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    if (!event.data || event.data.type !== MSG_TYPE) return;

    const url = event.data.url || '';

    // Dedupe: skip if we already forwarded this URL this page session
    if (seen.has(url)) return;
    seen.add(url);

    // Wrap in try/catch: sendMessage throws synchronously when the extension
    // context is invalidated (e.g. after a reload), before .catch() can fire.
    try {
      chrome.runtime.sendMessage({
        action:  'intercepted_api',
        url,
        data:    event.data.data,
      }).catch(() => {/* background may not be ready */});
    } catch (_e) {
      // Context invalidated — content script will be replaced on next navigation.
    }
  });
})();
