// Developer-only Amex invisible-context feasibility probe.
// Runs inside a Chrome offscreen document and reports sanitized observations only.
(() => {
  const TARGET = 'mighty-amex-invisible-context-offscreen';
  const DEFAULT_URL = 'https://global.americanexpress.com/overview';
  const TEST_TIMEOUT_MS = 15000;

  function nowIso() {
    return new Date().toISOString();
  }

  function sanitizeUrl(raw) {
    try {
      const url = new URL(raw);
      return `${url.origin}${url.pathname}`;
    } catch (_) {
      return String(raw || '').split('?')[0].split('#')[0];
    }
  }

  async function runTest(requestId, requestedUrl) {
    const url = requestedUrl || DEFAULT_URL;
    const startedAt = nowIso();
    const iframe = document.createElement('iframe');
    iframe.id = 'mighty-amex-invisible-frame';
    iframe.style.cssText = 'position:fixed;width:1px;height:1px;left:-10000px;top:-10000px;border:0;';
    iframe.referrerPolicy = 'no-referrer';

    let settled = false;
    let timeoutId;

    const finish = async (outcome, extra = {}) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);

      let frameUrl = null;
      let sameOriginDomAccessible = false;
      let frameReadyState = null;
      let frameTitle = null;
      try {
        frameUrl = sanitizeUrl(iframe.contentWindow?.location?.href || '');
        frameReadyState = iframe.contentDocument?.readyState || null;
        frameTitle = iframe.contentDocument?.title || null;
        sameOriginDomAccessible = true;
      } catch (_) {
        // Expected for a successfully loaded cross-origin provider page.
      }

      const resources = performance.getEntriesByType('resource')
        .filter((entry) => String(entry.name || '').includes('americanexpress.com'))
        .slice(-20)
        .map((entry) => ({
          name: sanitizeUrl(entry.name),
          initiator_type: entry.initiatorType || null,
          duration_ms: Math.round(entry.duration || 0),
          transfer_size: Number(entry.transferSize || 0),
        }));

      let credentialedFetch = null;
      try {
        const controller = new AbortController();
        const fetchTimeout = setTimeout(() => controller.abort(), 8000);
        const response = await fetch(url, {
          method: 'GET',
          credentials: 'include',
          redirect: 'follow',
          cache: 'no-store',
          signal: controller.signal,
        });
        clearTimeout(fetchTimeout);
        credentialedFetch = {
          ok: response.ok,
          status: response.status,
          redirected: response.redirected,
          final_url: sanitizeUrl(response.url),
          response_type: response.type,
        };
      } catch (error) {
        credentialedFetch = {
          ok: false,
          error_name: error?.name || 'Error',
          timed_out: error?.name === 'AbortError',
        };
      }

      iframe.remove();
      chrome.runtime.sendMessage({
        target: 'mighty-service-worker',
        type: 'AMEX_INVISIBLE_CONTEXT_TEST_RESULT',
        request_id: requestId,
        result: {
          outcome,
          started_at: startedAt,
          completed_at: nowIso(),
          requested_url: sanitizeUrl(url),
          iframe_load_event: outcome === 'IFRAME_LOAD_EVENT',
          iframe_error_event: outcome === 'IFRAME_ERROR_EVENT',
          same_origin_dom_accessible: sameOriginDomAccessible,
          frame_url: frameUrl,
          frame_ready_state: frameReadyState,
          frame_title: frameTitle,
          american_express_resource_count: resources.length,
          resources,
          credentialed_fetch: credentialedFetch,
          ...extra,
        },
      }).catch(() => {});
    };

    iframe.addEventListener('load', () => finish('IFRAME_LOAD_EVENT'), { once: true });
    iframe.addEventListener('error', () => finish('IFRAME_ERROR_EVENT'), { once: true });
    timeoutId = setTimeout(() => finish('IFRAME_TIMEOUT'), TEST_TIMEOUT_MS);
    document.body.appendChild(iframe);
    iframe.src = url;
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.target !== TARGET || message?.type !== 'RUN_AMEX_INVISIBLE_CONTEXT_TEST') return;
    runTest(message.request_id, message.url).catch((error) => {
      chrome.runtime.sendMessage({
        target: 'mighty-service-worker',
        type: 'AMEX_INVISIBLE_CONTEXT_TEST_RESULT',
        request_id: message.request_id,
        result: {
          outcome: 'OFFSCREEN_EXCEPTION',
          error_name: error?.name || 'Error',
          error_message: String(error?.message || error).slice(0, 300),
        },
      }).catch(() => {});
    });
  });
})();
