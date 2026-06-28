import React, { useCallback, useRef, useState } from 'react';
import {
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  ActivityIndicator,
} from 'react-native';
import { WebView, WebViewNavigation } from 'react-native-webview';

interface Props {
  source: string;
  loginUrl: string;
  accountPages: string[];
  siteName: string;
  onData: (text: string) => void;
  onDone: () => void;
  onSkip: () => void;
}

type Phase = 'login' | 'capturing' | 'done';

// JS injected after each account page loads to extract body text
const EXTRACT_SCRIPT = `
(function() {
  var text = document.body ? document.body.innerText : '';
  if (text.length > 15000) text = text.slice(0, 15000);
  window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'page_text', text: text, url: window.location.href }));
})();
true;
`;

// Check if the current URL looks like a login page
function looksLikeLoginPage(url: string, loginUrl: string): boolean {
  try {
    const loginHost = new URL(loginUrl).hostname.replace(/^www\./, '');
    const currentHost = new URL(url).hostname.replace(/^www\./, '');
    const currentPath = new URL(url).pathname.toLowerCase();

    // If we're on a different host that is the login host, it's a login page
    if (currentHost !== loginHost) return false;

    return /\/(sign-?in|log-?in|login|auth|sso|account\/login|member\/login)(\/|$|\?|$)/i.test(
      currentPath
    );
  } catch {
    return false;
  }
}

function hostMatches(url: string, loginUrl: string): boolean {
  try {
    const loginHost = new URL(loginUrl).hostname.replace(/^www\./, '');
    const currentHost = new URL(url).hostname.replace(/^www\./, '');
    // Allow subdomains of the login host
    return currentHost === loginHost || currentHost.endsWith('.' + loginHost);
  } catch {
    return false;
  }
}

export default function SyncWebView({
  source,
  loginUrl,
  accountPages,
  siteName,
  onData,
  onDone,
  onSkip,
}: Props) {
  const webViewRef = useRef<WebView>(null);
  const [phase, setPhase] = useState<Phase>('login');
  const [currentUrl, setCurrentUrl] = useState(loginUrl);
  const [pageIndex, setPageIndex] = useState(0);
  const [capturedCount, setCapturedCount] = useState(0);
  const [webviewLoading, setWebviewLoading] = useState(true);

  // After login is detected, start navigating account pages
  const startCapturing = useCallback(() => {
    if (accountPages.length === 0) {
      setPhase('done');
      onDone();
      return;
    }
    setPhase('capturing');
    setPageIndex(0);
    webViewRef.current?.injectJavaScript(
      `window.location.href = ${JSON.stringify(accountPages[0])};true;`
    );
  }, [accountPages, onDone]);

  const handleNavigationChange = useCallback(
    (navState: WebViewNavigation) => {
      const url = navState.url;
      setCurrentUrl(url);

      if (phase === 'login') {
        // Detect when user has successfully logged in:
        // They're on the site's domain but no longer on a login path
        const onSite = hostMatches(url, loginUrl);
        const onLoginPage = looksLikeLoginPage(url, loginUrl);

        if (onSite && !onLoginPage && !navState.loading) {
          // Give the page a moment to settle, then start capture
          setTimeout(() => startCapturing(), 1200);
        }
      }
    },
    [phase, loginUrl, startCapturing]
  );

  const handleLoadEnd = useCallback(() => {
    setWebviewLoading(false);

    if (phase === 'capturing') {
      // Inject extraction script after each account page loads
      webViewRef.current?.injectJavaScript(EXTRACT_SCRIPT);
    }
  }, [phase]);

  const handleMessage = useCallback(
    (event: { nativeEvent: { data: string } }) => {
      try {
        const msg = JSON.parse(event.nativeEvent.data);
        if (msg.type === 'page_text' && msg.text) {
          onData(msg.text);
          setCapturedCount((n) => n + 1);

          const nextIndex = pageIndex + 1;
          if (nextIndex < accountPages.length) {
            setPageIndex(nextIndex);
            setWebviewLoading(true);
            webViewRef.current?.injectJavaScript(
              `window.location.href = ${JSON.stringify(accountPages[nextIndex])};true;`
            );
          } else {
            setPhase('done');
            onDone();
          }
        }
      } catch {
        // Ignore malformed messages
      }
    },
    [accountPages, onData, onDone, pageIndex]
  );

  const onLoginPhase = phase === 'login';
  const progressPct =
    accountPages.length > 0
      ? Math.round((capturedCount / accountPages.length) * 100)
      : 0;

  return (
    <View style={styles.container}>
      {/* Header */}
      <View style={styles.header}>
        <View style={styles.headerLeft}>
          <Text style={styles.siteName}>{siteName}</Text>
          <Text style={styles.phaseLabel}>
            {phase === 'login'
              ? 'Log in to continue'
              : phase === 'capturing'
              ? `Capturing ${capturedCount + 1} / ${accountPages.length}`
              : 'Done'}
          </Text>
        </View>
        <TouchableOpacity style={styles.skipButton} onPress={onSkip}>
          <Text style={styles.skipText}>Skip</Text>
        </TouchableOpacity>
      </View>

      {/* Login prompt banner */}
      {onLoginPhase && (
        <View style={styles.loginBanner}>
          <Text style={styles.loginBannerText}>
            Please log in to {siteName}. Once logged in, Mighty will automatically capture your account data.
          </Text>
        </View>
      )}

      {/* Progress bar */}
      {phase === 'capturing' && accountPages.length > 0 && (
        <View style={styles.progressTrack}>
          <View style={[styles.progressFill, { width: `${progressPct}%` }]} />
        </View>
      )}

      {/* WebView */}
      <WebView
        ref={webViewRef}
        source={{ uri: loginUrl }}
        style={styles.webview}
        onNavigationStateChange={handleNavigationChange}
        onLoadEnd={handleLoadEnd}
        onLoadStart={() => setWebviewLoading(true)}
        onMessage={handleMessage}
        javaScriptEnabled
        domStorageEnabled
        sharedCookiesEnabled
        thirdPartyCookiesEnabled
        allowsBackForwardNavigationGestures
        userAgent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
      />

      {/* Loading overlay */}
      {webviewLoading && phase === 'capturing' && (
        <View style={styles.loadingOverlay}>
          <ActivityIndicator size="large" color="#6366f1" />
          <Text style={styles.loadingText}>Loading page {pageIndex + 1}…</Text>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#fff' },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 56,
    paddingBottom: 12,
    paddingHorizontal: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#f3f4f6',
  },
  headerLeft: { flex: 1 },
  siteName: { fontSize: 16, fontWeight: '700', color: '#111827' },
  phaseLabel: { fontSize: 12, color: '#6b7280', marginTop: 2 },
  skipButton: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    backgroundColor: '#f3f4f6',
    borderRadius: 8,
  },
  skipText: { fontSize: 13, fontWeight: '600', color: '#374151' },
  loginBanner: {
    backgroundColor: '#eff6ff',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#dbeafe',
  },
  loginBannerText: { fontSize: 13, color: '#1d4ed8', lineHeight: 18 },
  progressTrack: {
    height: 3,
    backgroundColor: '#e5e7eb',
  },
  progressFill: {
    height: 3,
    backgroundColor: '#6366f1',
  },
  webview: { flex: 1 },
  loadingOverlay: {
    position: 'absolute',
    top: 120,
    left: 0,
    right: 0,
    alignItems: 'center',
    paddingTop: 40,
    backgroundColor: 'rgba(255,255,255,0.85)',
  },
  loadingText: {
    marginTop: 12,
    fontSize: 13,
    color: '#6b7280',
  },
});
