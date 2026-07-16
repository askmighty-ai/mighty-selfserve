// Silent American Express session verification strategy.
//
// The extension collects browser observations only. It never decides SIGNED_IN,
// SIGNED_OUT, or LOGIN_UNKNOWN. The existing backend provider-access decision path
// receives the evidence and returns the canonical verification decision. A
// provider tab is opened only when that backend decision is inconclusive.
(() => {
  const originalRunSessionVerification = runSessionVerification;
  const AMEX_SILENT_ENTRY = 'https://global.americanexpress.com/overview';
  const AMEX_SILENT_TIMEOUT_MS = 8000;
  let silentProbeInProgress = false;

  function countHits(text, markers) {
    return markers.reduce((count, marker) => count + (text.includes(marker) ? 1 : 0), 0);
  }

  function collectAmexSilentEvidence(response, bodyText, requestedUrl) {
    const finalUrl = String(response?.url || requestedUrl || AMEX_SILENT_ENTRY);
    const sample = String(bodyText || '').toLowerCase().slice(0, 250000);
    const finalUrlIsLogin = /\/(login|log-?in|signin|sign-?in)(?:[/?#]|$)/i.test(finalUrl);
    const loginMarkerCount = countHits(sample, [
      'sign in to your account',
      'log in to your account',
      'user id',
      'show password',
      'forgot password',
    ]);
    const authenticatedMarkerCount = countHits(sample, [
      'membership rewards',
      'account home',
      'recent activity',
      'manage account',
      'statement balance',
      'card ending',
      'available credit',
      'payment due',
    ]);

    return {
      finalUrl,
      observations: {
        strategy: 'background_fetch',
        response_status: response?.status || 0,
        response_ok: !!response?.ok,
        redirected: !!response?.redirected,
        final_url_is_login: finalUrlIsLogin,
        login_marker_count: loginMarkerCount,
        authenticated_marker_count: authenticatedMarkerCount,
        network_error: false,
      },
    };
  }

  async function runAmexSilentProbe(entryUrl) {
    const requestedUrl = entryUrl || AMEX_SILENT_ENTRY;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AMEX_SILENT_TIMEOUT_MS);
    try {
      const response = await fetch(requestedUrl, {
        method: 'GET',
        credentials: 'include',
        redirect: 'follow',
        cache: 'no-store',
        signal: controller.signal,
        headers: {
          'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8',
        },
      });
      const bodyText = await response.text();
      return collectAmexSilentEvidence(response, bodyText, requestedUrl);
    } catch (error) {
      return {
        finalUrl: requestedUrl,
        observations: {
          strategy: 'background_fetch',
          response_status: 0,
          response_ok: false,
          redirected: false,
          final_url_is_login: false,
          login_marker_count: 0,
          authenticated_marker_count: 0,
          network_error: true,
          failure_reason: error?.name === 'AbortError'
            ? 'background_fetch_timeout'
            : 'background_fetch_failed',
          error_name: error?.name || 'Error',
        },
      };
    } finally {
      clearTimeout(timeout);
    }
  }

  async function markVerificationRunning(apiKey, verificationId) {
    try {
      await fetch(`${MIGHTY_URL}/api/extension/session-verification/running`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Mighty-Key': apiKey,
        },
        body: JSON.stringify({ verification_id: verificationId }),
      });
    } catch (error) {
      console.warn('[Mighty] silent Amex running mark failed:', error?.message);
    }
  }

  function canonicalDecision(posted) {
    const data = posted?.data || {};
    const decision = String(data.verification_decision || '').toLowerCase();
    if (decision === 'connected' || decision === 'signed_in') return 'SIGNED_IN';
    if (decision === 'signed_out') return 'SIGNED_OUT';
    if (decision === 'inconclusive' || decision === 'login_unknown') return 'LOGIN_UNKNOWN';

    // Provider Access Manager also returns the canonical public enum. Consume it
    // before legacy auth_state so a definitive backend result does not
    // unnecessarily fall back to opening a provider tab.
    const authenticationState = String(data.authentication_state || '').toUpperCase();
    if (authenticationState === 'SIGNED_IN') return 'SIGNED_IN';
    if (authenticationState === 'SIGNED_OUT') return 'SIGNED_OUT';
    if (authenticationState === 'LOGIN_UNKNOWN') return 'LOGIN_UNKNOWN';

    // Compatibility only: consume backend auth_state when older deployments do
    // not yet return verification_decision. These are backend results, not local
    // extension classifications.
    const authState = String(data.auth_state || '').toLowerCase();
    if (authState === 'authenticated_no_private_data' || authState === 'private_data_visible') {
      return 'SIGNED_IN';
    }
    if (authState === 'login_page' || authState === 'session_expired') return 'SIGNED_OUT';
    return 'LOGIN_UNKNOWN';
  }

  async function submitSilentEvidence(apiKey, verificationId, result) {
    const observations = result.observations || {};
    const payload = {
      provider: 'amex',
      url_visited: result.finalUrl || AMEX_SILENT_ENTRY,
      // These are raw observed booleans used by the existing backend evidence
      // classifier. They are not canonical authentication decisions.
      signed_in_detected: observations.authenticated_marker_count >= 2,
      private_data_detected: false,
      verification_id: verificationId,
      access_cycle_id: verificationId,
      verification_strategy: 'background_fetch',
      evidence_source: 'extension_service_worker',
      background_fetch_evidence: observations,
      page_diagnostics: {
        final_url: result.finalUrl || AMEX_SILENT_ENTRY,
        body_exists: true,
        body_text_length: null,
      },
    };
    return _postProviderAccessProbe(apiKey, payload, { skipDedup: true });
  }

  async function finishBackendDecision(apiKey, verificationId, decision) {
    if (decision !== 'SIGNED_IN') {
      // The provider-access endpoint owns signed-out terminalization. Avoid a
      // competing extension-side terminal conclusion.
      return;
    }
    // A silent fetch has no page from which to extract. The backend has already
    // made the canonical SIGNED_IN decision; finish this access-only cycle rather
    // than leaving it in session_verified/extracting.
    await _completeSessionVerification(
      apiKey,
      verificationId,
      'completed',
      null,
      {
        terminalReason: 'authenticated',
        terminalSource: 'backend_decision_background_fetch',
      },
    );
  }

  runSessionVerification = async function runSessionVerificationWithSilentAmex(
    apiKey,
    provider,
    verificationId,
    entryUrl,
  ) {
    if (provider !== 'amex' || !verificationId || silentProbeInProgress) {
      return originalRunSessionVerification(apiKey, provider, verificationId, entryUrl);
    }

    silentProbeInProgress = true;
    try {
      console.log('[Mighty] Amex verification strategy=background_fetch');
      await markVerificationRunning(apiKey, verificationId);
      const result = await runAmexSilentProbe(entryUrl || AMEX_SILENT_ENTRY);
      console.log('[Mighty] Amex background fetch observations=', result.observations);

      const posted = await submitSilentEvidence(apiKey, verificationId, result);
      const decision = canonicalDecision(posted);
      console.log('[Mighty] Amex backend verification decision=', decision, posted?.data || {});

      if (decision === 'SIGNED_IN' || decision === 'SIGNED_OUT') {
        await finishBackendDecision(apiKey, verificationId, decision);
        _lastProcessedSessionVerificationId = verificationId;
        return;
      }

      console.log('[Mighty] Amex backend decision inconclusive — falling back to provider tab');
      return originalRunSessionVerification(apiKey, provider, verificationId, entryUrl);
    } finally {
      silentProbeInProgress = false;
    }
  };
})();
