// Mighty Sync — API interceptor (runs in MAIN world at document_start)
// Hooks fetch + XHR to capture JSON responses that contain account data,
// then posts them to the ISOLATED world relay via window.postMessage.
// This runs in the page's JS context so it can override fetch/XHR before
// any page code runs.

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';

  // Broad keyword list — program-specific names + generic account terms.
  // A single hit on a response ≥500 chars is enough to forward.
  const KEYWORDS = [
    // Generic account data
    'miles', 'points', 'balance', 'status', 'tier', 'reward', 'loyalty',
    'certificate', 'award', 'companion', 'upgrade', 'credit', 'voucher',
    'ecredit', 'wallet', 'expir', 'medallion', 'frequent', 'elite',
    // Program-specific tokens (camelCase and snake_case both hit on lowercase)
    'skymiles', 'hhonors', 'bonvoy', 'mileageplus', 'rapidrewards',
    'aadvantage', 'trueblue', 'worldofhyatt', 'ihgrewards', 'wyndhamrewards',
    'thankyoupoints', 'ultimaterewards', 'membershiprewards',
    // Account/billing fields
    'autopay', 'amountdue', 'minimumpayment', 'statementbalance',
    'accountsummary', 'loyaltynumber', 'membernumber', 'memberid',
  ];

  function looksLikeAccountData(text) {
    if (!text || text.length < 80 || text.length > 1_000_000) return false;
    // Must be parseable JSON
    try { JSON.parse(text); } catch { return false; }
    const lower = text.toLowerCase();
    const hits = KEYWORDS.filter(k => lower.includes(k)).length;
    // 1 hit is enough for larger responses; tiny responses need 2+ to avoid noise
    return text.length >= 500 ? hits >= 1 : hits >= 2;
  }

  function maybeForward(url, responseText, contentType) {
    // Accept any JSON-ish content-type (application/json, application/graphql+json, text/json, etc.)
    const ct = (contentType || '').toLowerCase();
    if (!ct.includes('json') && !ct.includes('graphql')) return;
    if (!looksLikeAccountData(responseText)) return;
    try {
      window.postMessage({
        type: MSG_TYPE,
        url:  String(url || '').slice(0, 500),
        data: responseText.slice(0, 120_000),
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
