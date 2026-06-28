# Mighty Mobile — Architecture

## How sync works

Each loyalty/travel account is represented as a `MobileSite` entry in `lib/sites.ts`. When a user taps "Sync" for a site, the app opens a full-screen `SyncWebView` modal that loads the site's login URL in an embedded WebView.

The user logs in normally (no credentials are stored by the app — the WebView handles the session just like Safari would). Once the login URL changes away from a login-path pattern, the component automatically navigates to each URL in `accountPages` sequentially. After each page finishes loading, it injects a small JavaScript snippet that reads `document.body.innerText` (capped at 15 000 characters) and posts the text back via `ReactNativeWebView.postMessage`.

Each captured page is immediately pushed to the Flask server at `POST /api/extension/capture` in the same format the Chrome extension uses: `{ source, page_text, page_url, synced_at }`. The server's existing extraction pipeline (Gemini + regex connectors) then parses the text into structured fields.

## Auth model

The API key is entered once on the login screen and validated by hitting `GET /api/me`. It is stored in the device keychain via `expo-secure-store` — never in AsyncStorage or plaintext. All API requests attach the key as the `X-Mighty-Key` header.

## Why Expo managed workflow

Managed workflow ships without a native build step, making it straightforward to deploy and update. The only native module required (`react-native-webview`) is supported out of the box by Expo's managed build service. No ejection is needed.

## iOS background sync limitation

iOS strictly limits background execution for apps that use WebViews. Sync is active-only — the user must have the app in the foreground. A "Sync All" button lets users queue all sites in one session.

## Roadmap

- **Official API integrations** — replace WebView scraping with OAuth or official loyalty APIs as they become available (e.g. Delta SkyMiles API, Marriott Bonvoy API).
- **Push notifications** — alert users when points are about to expire or a credit goes unused.
- **Background refresh** — use `expo-background-fetch` + lightweight health-check endpoint to show staleness warnings even when full sync isn't possible.
- **Biometric lock** — gate app open behind Face ID / Touch ID for additional key security.
