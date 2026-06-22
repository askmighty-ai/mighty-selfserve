// Mighty Sync — API relay (runs in ISOLATED world)
// Listens for postMessages from the MAIN world interceptor and forwards them
// to the background service worker via chrome.runtime.sendMessage.
// Deduplicates by URL within a page session to avoid flooding the server.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';
  const seen = new Set(); // dedupe within page session

  // ── Login detection ────────────────────────────────────────────────────────
  // Fires after page load. If a visible password field exists, the site is
  // showing a login form — report it so the dashboard card can be updated.
  window.addEventListener('load', function() {
    // Give JS-rendered overlays (e.g. United) an extra moment to appear
    setTimeout(function() {
      const pwFields = document.querySelectorAll('input[type="password"]');
      const hasVisiblePw = Array.from(pwFields).some(function(el) {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      });
      if (!hasVisiblePw) return;
      try {
        chrome.runtime.sendMessage({
          action: 'login_page_detected',
          href: window.location.href,
        }).catch(function() {});
      } catch (_e) {}
    }, 1500);
  });

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
