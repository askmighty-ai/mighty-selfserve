// Mighty Sync — API relay (runs in ISOLATED world)
// Listens for postMessages from the MAIN world interceptor and forwards them
// to the background service worker via chrome.runtime.sendMessage.
// Deduplicates by URL within a page session to avoid flooding the server.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';
  const seen = new Set(); // dedupe within page session

  // ── Login detection ────────────────────────────────────────────────────────
  // Polls every 2s for a visible password field. Polling beats MutationObserver
  // here because United (and similar SPAs) render the input hidden in the DOM
  // first and reveal it via CSS — no DOM insertion event fires.
  console.log('[Mighty] api_relay.js loaded on', window.location.href);
  var _loginReported = false;
  // Write a heartbeat so we can verify the content script is actually running
  chrome.storage.local.set({ mighty_cs_alive: { href: window.location.href, ts: Date.now() } });
  var _loginPollCount = 0;
  var _loginPollId = setInterval(function() {
    if (_loginReported) { clearInterval(_loginPollId); return; }
    _loginPollCount++;
    var pwFields = document.querySelectorAll('input[type="password"]');
    var rects = Array.from(pwFields).map(function(el) {
      var r = el.getBoundingClientRect();
      return { w: r.width, h: r.height, display: getComputedStyle(el).display, visibility: getComputedStyle(el).visibility };
    });
    // Log every 5 polls (every 10s) so we can see what's happening
    if (_loginPollCount % 5 === 1) {
      console.log('[Mighty] poll #' + _loginPollCount + ' — pw fields:', pwFields.length, rects);
    }
    var visible = rects.some(function(r) { return r.w > 0 && r.h > 0; });
    if (!visible) return;
    console.log('[Mighty] visible password field detected — reporting login_wall');
    _loginReported = true;
    clearInterval(_loginPollId);
    // Use storage instead of sendMessage — storage.onChanged reliably wakes
    // the MV3 service worker even when it's been terminated due to inactivity.
    chrome.storage.local.set({
      mighty_login_detected: { href: window.location.href, ts: Date.now() }
    }, function() {
      if (chrome.runtime.lastError) {
        console.log('[Mighty] storage.set failed:', chrome.runtime.lastError.message);
      } else {
        console.log('[Mighty] mighty_login_detected written to storage ✓');
      }
    });
  }, 2000);
  // Stop polling after 3 minutes — if no login form by then, user is logged in
  setTimeout(function() { clearInterval(_loginPollId); }, 180000);

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
