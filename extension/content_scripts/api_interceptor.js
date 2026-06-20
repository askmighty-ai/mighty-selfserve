// Mighty Sync — API interceptor (runs in MAIN world at document_start)
// Hooks fetch + XHR to capture JSON responses that contain account data,
// then posts them to the ISOLATED world relay via window.postMessage.
// This runs in the page's JS context so it can override fetch/XHR before
// any page code runs.
//
// Acquisition tiers:
//   Tier 1 — fetch/XHR API responses (JSON content-type)
//   Tier 2 — embedded page state (__NEXT_DATA__, __APOLLO_STATE__, etc.)
//   Tiers 3–4 — handled server-side (structured DOM, AI extraction)

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
    // Embedded state keys common on loyalty sites
    'hhonorsnumber', 'loyaltytier', 'memberlevel', 'elitestatus',
    'frequentflyer', 'programaccountsummary', 'pointsbalance',
  ];

  function looksLikeAccountData(text) {
    if (!text || text.length < 80 || text.length > 1_000_000) return false;
    // Must be parseable JSON
    try { JSON.parse(text); } catch { return false; }
    const lower = text.toLowerCase();
    // Skip pure auth/token responses — they're never loyalty data and JWTs
    // can accidentally match keywords in their base64 payload
    if (lower.includes('"token_type"') || lower.includes('"access_token"') ||
        lower.includes('"id_token"') || lower.includes('"refresh_token"')) return false;
    const hits = KEYWORDS.filter(k => lower.includes(k)).length;
    // 1 hit is enough for larger responses; tiny responses need 2+ to avoid noise
    return text.length >= 500 ? hits >= 1 : hits >= 2;
  }

  function forward(url, text) {
    try {
      window.postMessage({
        type: MSG_TYPE,
        url:  String(url || '').slice(0, 500),
        data: text.slice(0, 120_000),
      }, '*');
    } catch (_) {}
  }

  function maybeForward(url, responseText, contentType) {
    // Accept any JSON-ish content-type (application/json, application/graphql+json, text/json, etc.)
    const ct = (contentType || '').toLowerCase();
    if (!ct.includes('json') && !ct.includes('graphql')) return;
    if (!looksLikeAccountData(responseText)) return;
    forward(url, responseText);
  }

  // ── Tier 1: Intercept fetch ──────────────────────────────────────────────────
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

  // ── Tier 1: Intercept XMLHttpRequest ────────────────────────────────────────
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

  // ── Tier 2: Embedded page state ──────────────────────────────────────────────
  // Many modern sites (Next.js, Apollo, Redux) serialize all data into a window
  // global before any API call fires. Capturing this is more reliable than
  // intercepting individual API responses because:
  //   • The data is already structured and complete
  //   • It captures state from all APIs in one shot
  //   • No timing issues with hydration or lazy loading

  // Known embedded state keys across major frameworks
  const EMBEDDED_KEYS = [
    '__NEXT_DATA__',        // Next.js — serialized page props + server state
    '__APOLLO_STATE__',     // Apollo Client — normalized GraphQL cache
    '__APOLLO_CLIENT__',    // Apollo Client (older versions)
    '__INITIAL_STATE__',    // Redux / generic initial state
    '__APP_STATE__',        // Various SPA frameworks
    '__REDUX_STATE__',      // Redux
    '__STORE_STATE__',      // MobX / other stores
    '__PRELOADED_STATE__',  // Redux Toolkit
    'digitalData',          // Adobe Experience Cloud / CEDDL analytics layer
    '__nuxt__',             // Nuxt.js
    '__NEXT_REDUX_STORE__', // Next.js + Redux
  ];

  function checkWindowState() {
    for (const key of EMBEDDED_KEYS) {
      try {
        const val = window[key];
        if (!val) continue;
        // Stringify if needed (Apollo stores plain objects, not strings)
        const text = typeof val === 'string' ? val : JSON.stringify(val);
        if (looksLikeAccountData(text)) {
          // Prefix with "embedded:" so the server knows this is Tier 2 data
          forward(`embedded:${key}@${location.href}`, text);
        }
      } catch (_) {}
    }
  }

  // Watch for <script id="__NEXT_DATA__"> injected into the document at parse time.
  // This script tag contains the full Next.js server-side props as inline JSON and
  // is available before DOMContentLoaded in most cases.
  const _mo = new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const n of m.addedNodes) {
        if (n.nodeName !== 'SCRIPT') continue;
        const isNextData = n.id === '__NEXT_DATA__' || n.getAttribute?.('data-next-page') != null;
        if (!isNextData) continue;
        try {
          const t = n.textContent || '';
          if (looksLikeAccountData(t)) {
            forward(`embedded:__NEXT_DATA__@${location.href}`, t);
          }
        } catch (_) {}
        // Once found, no need to keep observing for Next.js script tag
        _mo.disconnect();
      }
    }
  });
  _mo.observe(document.documentElement, { childList: true, subtree: true });

  // Check at DOMContentLoaded (Apollo/Redux may be set by this point)
  document.addEventListener('DOMContentLoaded', checkWindowState);

  // Check after full load (catches anything set by async scripts)
  window.addEventListener('load', checkWindowState);

  // Safety net: check again after 3 seconds for slow-hydrating SPAs
  setTimeout(checkWindowState, 3_000);

})();
