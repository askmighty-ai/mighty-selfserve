// Mighty Sync — API relay (runs in ISOLATED world)
// Listens for postMessages from the MAIN world interceptor and forwards them
// to the background service worker via chrome.runtime.sendMessage.
// Deduplicates by URL within a page session to avoid flooding the server.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';
  const seen = new Set(); // dedupe within page session

  // ── Login detection ────────────────────────────────────────────────────────
  // Reports to the background script when a login form becomes visible.
  // Uses MutationObserver so it catches JS-rendered overlays and SPA navigations
  // that appear long after the initial page load event.
  var _loginReported = false;
  function _reportLoginDetected() {
    if (_loginReported) return;
    var pwFields = document.querySelectorAll('input[type="password"]');
    var hasVisiblePw = Array.from(pwFields).some(function(el) {
      var r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    if (!hasVisiblePw) return;
    _loginReported = true;
    try {
      chrome.runtime.sendMessage({
        action: 'login_page_detected',
        href: window.location.href,
      }).catch(function() {});
    } catch (_e) {}
  }

  // Watch for password inputs being added to the DOM (SPA navigation / lazy modals)
  var _pwObserver = new MutationObserver(function(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var added = mutations[i].addedNodes;
      for (var j = 0; j < added.length; j++) {
        var node = added[j];
        if (node.nodeType !== 1) continue;
        var hasPw = node.tagName === 'INPUT' && node.type === 'password';
        if (!hasPw && node.querySelector) hasPw = !!node.querySelector('input[type="password"]');
        if (hasPw) {
          // Give the animation a moment to make it visible
          setTimeout(_reportLoginDetected, 400);
          return;
        }
      }
    }
  });

  // Start observing immediately and also check once at load (for static login pages)
  _pwObserver.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('load', function() {
    setTimeout(_reportLoginDetected, 1000);
  });
  // Stop after 5 minutes to avoid resource leaks on long sessions
  setTimeout(function() { _pwObserver.disconnect(); }, 300000);

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
