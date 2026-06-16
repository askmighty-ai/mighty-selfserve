// Mighty Sync — API interceptor (runs in MAIN world at document_start)
// Hooks fetch + XHR to capture JSON responses that contain account data,
// then posts them to the ISOLATED world relay via window.postMessage.
// This runs in the page's JS context so it can override fetch/XHR before
// any page code runs.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';

  // Keywords: if a JSON response contains 2+ of these it's probably account data
  const KEYWORDS = [
    'miles', 'certificate', 'points', 'balance', 'status', 'expir',
    'award', 'companion', 'upgrade', 'medallion', 'wallet', 'credit',
    'voucher', 'ecredit', 'tier', 'loyalty', 'reward', 'frequent',
  ];

  function looksLikeAccountData(text) {
    if (!text || text.length < 80 || text.length > 800_000) return false;
    // Must be parseable JSON
    try { JSON.parse(text); } catch { return false; }
    const lower = text.toLowerCase();
    const hits = KEYWORDS.filter(k => lower.includes(k)).length;
    return hits >= 2;
  }

  function maybeForward(url, responseText, contentType) {
    if (!contentType || !contentType.includes('json')) return;
    if (!looksLikeAccountData(responseText)) return;
    try {
      window.postMessage({
        type: MSG_TYPE,
        url:  String(url || '').slice(0, 500),
        data: responseText.slice(0, 100_000),
      }, '*');
    } catch (_) {}
  }

  // ── Intercept fetch ──────────────────────────────────────────────────────────
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const resp = await _fetch.apply(this, args);
    try {
      const url  = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
      const ct   = resp.headers.get('content-type') || '';
      resp.clone().text().then(t => maybeForward(url, t, ct)).catch(() => {});
    } catch (_) {}
    return resp;
  };

  // ── Intercept XMLHttpRequest ─────────────────────────────────────────────────
  const _open = XMLHttpRequest.prototype.open;
  const _send = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__mighty_url = String(url || '');
    return _open.apply(this, [method, url, ...rest]);
  };

  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener('load', function () {
      try {
        const ct = this.getResponseHeader('content-type') || '';
        maybeForward(this.__mighty_url, this.responseText, ct);
      } catch (_) {}
    });
    return _send.apply(this, args);
  };
})();
