// Developer-only feasibility spike for a non-visible Amex execution context.
// Invoke from the Mighty service-worker console:
//   await runAmexInvisibleContextTest()
(() => {
  const OFFSCREEN_PATH = 'amex_invisible_context_offscreen.html';
  const OFFSCREEN_TARGET = 'mighty-amex-invisible-context-offscreen';
  const DEFAULT_URL = 'https://global.americanexpress.com/overview';
  const pending = new Map();
  let creatingOffscreenDocument = null;

  async function ensureAmexTestOffscreenDocument() {
    if (!chrome.offscreen) {
      throw new Error('chrome.offscreen is unavailable; reload after adding the offscreen permission');
    }
    const documentUrl = chrome.runtime.getURL(OFFSCREEN_PATH);
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
      documentUrls: [documentUrl],
    });
    if (contexts.length) return;

    if (!creatingOffscreenDocument) {
      creatingOffscreenDocument = chrome.offscreen.createDocument({
        url: OFFSCREEN_PATH,
        reasons: ['IFRAME_SCRIPTING'],
        justification: 'Developer feasibility test for invisible authenticated provider execution.',
      }).finally(() => {
        creatingOffscreenDocument = null;
      });
    }
    await creatingOffscreenDocument;
  }

  chrome.runtime.onMessage.addListener((message) => {
    if (message?.target !== 'mighty-service-worker'
        || message?.type !== 'AMEX_INVISIBLE_CONTEXT_TEST_RESULT') return;
    const waiter = pending.get(message.request_id);
    if (!waiter) return;
    pending.delete(message.request_id);
    clearTimeout(waiter.timeout);
    waiter.resolve(message.result);
  });

  globalThis.runAmexInvisibleContextTest = async function runAmexInvisibleContextTest(
    url = DEFAULT_URL,
  ) {
    const requestId = crypto.randomUUID();
    console.log('[Mighty Invisible Test] creating/reusing offscreen document', { requestId, url });
    await ensureAmexTestOffscreenDocument();

    const resultPromise = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        pending.delete(requestId);
        reject(new Error('Invisible Amex context test timed out waiting for offscreen result'));
      }, 30000);
      pending.set(requestId, { resolve, reject, timeout });
    });

    await chrome.runtime.sendMessage({
      target: OFFSCREEN_TARGET,
      type: 'RUN_AMEX_INVISIBLE_CONTEXT_TEST',
      request_id: requestId,
      url,
    });

    const result = await resultPromise;
    console.log('[Mighty Invisible Test] RESULT', result);
    await chrome.storage.local.set({
      amex_invisible_context_test_last_result: result,
    });
    return result;
  };

  globalThis.closeAmexInvisibleContextTest = async function closeAmexInvisibleContextTest() {
    if (!chrome.offscreen) return false;
    const documentUrl = chrome.runtime.getURL(OFFSCREEN_PATH);
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ['OFFSCREEN_DOCUMENT'],
      documentUrls: [documentUrl],
    });
    if (!contexts.length) return false;
    await chrome.offscreen.closeDocument();
    return true;
  };
})();
