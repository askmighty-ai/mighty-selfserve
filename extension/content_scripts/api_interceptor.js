// Mighty Sync — API interceptor (runs in MAIN world at document_start)
// Hooks fetch + XHR to capture JSON responses that contain account data,
// then posts them to the ISOLATED world relay via window.postMessage.
//
// Network Intelligence (Phase 2):
//   • JSON / GraphQL / REST responses with account-relevant payloads
//   • Skips static assets, analytics, telemetry, uploads, auth tokens
//   • Redacts sensitive keys before forwarding

(function () {
  'use strict';

  const MSG_TYPE = '__mighty_api__';

  const KEYWORDS = [
    'balance', 'points', 'miles', 'status', 'tier', 'trip', 'reservation',
    'account', 'payment', 'statement', 'transactions', 'rewards', 'member',
    'reward', 'loyalty', 'certificate', 'award', 'companion', 'upgrade',
    'credit', 'voucher', 'ecredit', 'wallet', 'expir', 'medallion', 'frequent',
    'elite', 'membership', 'skymiles', 'hhonors', 'bonvoy', 'mileageplus',
    'rapidrewards', 'aadvantage', 'trueblue', 'worldofhyatt', 'ihgrewards',
    'wyndhamrewards', 'thankyoupoints', 'ultimaterewards', 'membershiprewards',
    'autopay', 'amountdue', 'minimumpayment', 'statementbalance',
    'accountsummary', 'loyaltynumber', 'membernumber', 'memberid',
    'hhonorsnumber', 'loyaltytier', 'memberlevel', 'elitestatus',
    'frequentflyer', 'programaccountsummary', 'pointsbalance',
    'pagination', 'nextpage', 'cursor', 'offset', 'pageinfo',
  ];

  const SKIP_URL_RE = /\/(?:static|assets|asset|dist|bundle|bundles|chunks|chunk|analytics|telemetry|tracking|track|metrics|beacon|pixel|ads|advert|doubleclick|googletagmanager|gtm|segment|upload|uploads|multipart|oauth2?|token|auth\/token|login\/token|api\/auth|signin\/token|refresh|\.well-known)(?:\/|$|\?)/i;
  const SKIP_EXT_RE = /\.(?:js|css|png|jpg|jpeg|gif|svg|webp|ico|woff2?|ttf|map)(?:\?|$)/i;
  const AUTH_TOKEN_RE = /"(token_type|access_token|id_token|refresh_token)"/i;
  const SENSITIVE_KEY_RE = /"(access_token|refresh_token|id_token|password|secret|authorization|cookie|csrf|session_token|session_id|sessionid|set-cookie)"/i;
  const MAX_NETWORK_BLOCK_CHARS = 120_000;

  function shouldSkipUrl(url) {
    const u = String(url || '');
    if (!u || u.startsWith('embedded:')) return false;
    const lower = u.toLowerCase();
    if (lower.startsWith('data:') || lower.startsWith('blob:')) return true;
    if (SKIP_URL_RE.test(lower) || SKIP_EXT_RE.test(lower)) return true;
    if (/[?&](?:format=(?:png|jpg|gif|webp|svg|css|js)|content-type=image)/i.test(lower)) return true;
    return false;
  }

  function isGraphqlPayload(text, contentType) {
    const ct = (contentType || '').toLowerCase();
    if (ct.includes('graphql')) return true;
    if (!text) return false;
    try {
      const payload = JSON.parse(text);
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return false;
      if (Array.isArray(payload.errors)) return true;
      return payload.data != null && typeof payload.extensions === 'object';
    } catch (_) {
      return false;
    }
  }

  function redactSensitiveJson(text) {
    if (!text || !SENSITIVE_KEY_RE.test(text)) return text;
    try {
      const payload = JSON.parse(text);
      const walk = (value) => {
        if (Array.isArray(value)) return value.map(walk);
        if (value && typeof value === 'object') {
          const out = {};
          for (const [key, item] of Object.entries(value)) {
            out[key] = SENSITIVE_KEY_RE.test('"' + key + '"') ? '[REDACTED]' : walk(item);
          }
          return out;
        }
        return value;
      };
      return JSON.stringify(walk(payload));
    } catch (_) {
      return text;
    }
  }

  function looksLikeAccountData(text, contentType) {
    if (!text || text.length < 80 || text.length > 1_000_000) return false;
    try { JSON.parse(text); } catch { return false; }
    if (AUTH_TOKEN_RE.test(text)) return false;
    const lower = text.toLowerCase();
    const hits = KEYWORDS.filter(k => lower.includes(k)).length;
    if (isGraphqlPayload(text, contentType)) return hits >= 1 || text.length >= 300;
    return text.length >= 500 ? hits >= 1 : hits >= 2;
  }

  function forward(url, text, meta) {
    try {
      window.postMessage({
        type: MSG_TYPE,
        url: String(url || '').slice(0, 500),
        data: redactSensitiveJson(text).slice(0, MAX_NETWORK_BLOCK_CHARS),
        graphql: !!meta?.graphql,
        contentType: meta?.contentType || '',
      }, '*');
    } catch (_) {}
  }

  function maybeForward(url, responseText, contentType) {
    if (shouldSkipUrl(url)) return;
    const ct = (contentType || '').toLowerCase();
    const graphqlCt = ct.includes('graphql');
    const jsonCt = ct.includes('json') || graphqlCt || ct.includes('javascript');
    if (!jsonCt && !isGraphqlPayload(responseText, ct)) return;
    if (!looksLikeAccountData(responseText, ct)) return;
    forward(url, responseText, {
      graphql: graphqlCt || isGraphqlPayload(responseText, ct),
      contentType: ct,
    });
  }

  // ── Tier 1: Intercept fetch ──────────────────────────────────────────────────
  const _fetch = window.fetch;
  window.fetch = async function (...args) {
    const resp = await _fetch.apply(this, args);
    try {
      const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
      const ct = resp.headers.get('content-type') || '';
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
  const EMBEDDED_KEYS = [
    '__NEXT_DATA__', '__APOLLO_STATE__', '__APOLLO_CLIENT__', '__INITIAL_STATE__',
    '__APP_STATE__', '__REDUX_STATE__', '__STORE_STATE__', '__PRELOADED_STATE__',
    'digitalData', '__nuxt__', '__NEXT_REDUX_STORE__',
  ];

  function checkWindowState() {
    for (const key of EMBEDDED_KEYS) {
      try {
        const val = window[key];
        if (!val) continue;
        const text = typeof val === 'string' ? val : JSON.stringify(val);
        if (looksLikeAccountData(text, '')) {
          forward(`embedded:${key}@${location.href}`, text, { graphql: false });
        }
      } catch (_) {}
    }
  }

  const _mo = new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const n of m.addedNodes) {
        if (n.nodeName !== 'SCRIPT') continue;
        const isNextData = n.id === '__NEXT_DATA__' || n.getAttribute?.('data-next-page') != null;
        if (!isNextData) continue;
        try {
          const t = n.textContent || '';
          if (looksLikeAccountData(t, '')) {
            forward(`embedded:__NEXT_DATA__@${location.href}`, t, { graphql: false });
          }
        } catch (_) {}
        _mo.disconnect();
      }
    }
  });
  _mo.observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('DOMContentLoaded', checkWindowState);
  window.addEventListener('load', checkWindowState);
  setTimeout(checkWindowState, 3_000);

})();
