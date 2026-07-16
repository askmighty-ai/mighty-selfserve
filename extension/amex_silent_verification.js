// Silent American Express session verification strategy.
//
// This layer deliberately does not replace the existing provider-tab verifier.
// It first attempts a credentialed request from the extension service worker and
// falls back to the existing runSessionVerification implementation whenever the
// response is not conclusive.
(() => {
  const originalRunSessionVerification = runSessionVerification;
  const AMEX_SILENT_ENTRY = 'https://global.americanexpress.com/overview';
  const AMEX_SILENT_TIMEOUT_MS = 8000;
  let silentProbeInProgress = false;

  function countHits(text, markers) {
    return markers.reduce((count, marker) => count + (text.includes(marker) ? 1 : 0), 0);
  }

  function classifyAmexSilentResponse(response, bodyText) {
    const finalUrl = String(response?.url || AMEX_SILENT_ENTRY);
    const lowerUrl = finalUrl.toLowerCase();
    const sample = String(bodyText || '').toLowerCase().slice(0, 250000);

    const loginUrl = /\/(login|log-?in|signin|sign-?in)(?:[/?#]|$)/i.test(lowerUrl);
    const loginHits = countHits(sample, [
      'sign in to your account',
      'log in to your account',
      'user id',
      'show password',
      'forgot password',
    ]);
    const authenticatedHits = countHits(sample, [
      'membership rewards',
      'account home',
      'recent activity',
      'manage account',
      'statement balance',
      'card ending',
      'available credit',
      'payment due',
    ]);

    // Signed out requires affirmative provider-specific evidence. A generic 401,
    // 403, fetch failure, or unexpected page is never enough by itself.
    if (loginUrl || (loginHits >= 2 && authenticatedHits === 0)) {
      return {
        conclusive: true,
        authenticationState: 'SIGNED_OUT',
        finalUrl,
        evidence: {
          strategy: 'background_fetch',
          response_status: response?.status || 0,
          final_url_is_login: loginUrl,
          login_marker_count: loginHits,
          authenticated_marker_count: authenticatedHits,
        },
      };
    }

    // Require multiple authenticated markers. The overview URL alone is not proof:
    // Amex may return an app shell, marketing content, or an access-block page.
    if (response?.ok && authenticatedHits >= 2 && !loginUrl) {
      return {
        conclusive: true,
        authenticationState: 'SIGNED_IN',
        finalUrl,
        evidence: {
          strategy: 'background_fetch',
          response_status: response.status,
          final_url_is_login: false,
          login_marker_count: loginHits,
          authenticated_marker_count: authenticatedHits,
        },
      };
    }

    return {
      conclusive: false,
      authenticationState: 'LOGIN_UNKNOWN',
      finalUrl,
      evidence: {
        strategy: 'background_fetch',
        response_status: response?.status || 0,
        final_url_is_login: loginUrl,
        login_marker_count: loginHits,
        authenticated_marker_count: authenticatedHits,
      },
    };
  }

  async function runAmexSilentProbe(apiKey, verificationId, entryUrl) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AMEX_SILENT_TIMEOUT_MS);
    try {
      const response = await fetch(entryUrl || AMEX_SILENT_ENTRY, {
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
      return classifyAmexSilentResponse(response, bodyText);
    } catch (error) {
      return {
        conclusive: false,
        authenticationState: 'LOGIN_UNKNOWN',
        finalUrl: entryUrl || AMEX_SILENT_ENTRY,
        evidence: {
          strategy: 'background_fetch',
          failure_reason: error?.name === 'AbortError' ? 'background_fetch_timeout' : 'background_fetch_failed',
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

  async function submitConclusiveSilentResult(apiKey, verificationId, result) {
    const signedIn = result.authenticationState === 'SIGNED_IN';
    const payload = {
      provider: 'amex',
      url_visited: result.finalUrl || AMEX_SILENT_ENTRY,
      signed_in_detected: signedIn,
      private_data_detected: false,
      failure_reason: signedIn ? null : 'login_required',
      verification_id: verificationId,
      access_cycle_id: verificationId,
      verification_strategy: 'background_fetch',
      evidence_source: 'extension_service_worker',
      background_fetch_evidence: result.evidence,
    };

    await _postProviderAccessProbe(apiKey, payload, { skipDedup: true });
    await _completeSessionVerification(
      apiKey,
      verificationId,
      'completed',
      null,
      {
        terminalReason: signedIn ? 'authenticated' : 'signed_out',
        terminalSource: 'extension_background_fetch',
      },
    );
    _lastProcessedSessionVerificationId = verificationId;
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
      const result = await runAmexSilentProbe(apiKey, verificationId, entryUrl || AMEX_SILENT_ENTRY);
      console.log(
        '[Mighty] Amex background fetch result=',
        result.authenticationState,
        'conclusive=',
        result.conclusive,
        result.evidence,
      );

      if (result.conclusive) {
        await submitConclusiveSilentResult(apiKey, verificationId, result);
        return;
      }

      console.log('[Mighty] Amex background fetch inconclusive — falling back to provider tab');
      return originalRunSessionVerification(apiKey, provider, verificationId, entryUrl);
    } finally {
      silentProbeInProgress = false;
    }
  };
})();
