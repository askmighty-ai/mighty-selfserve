# Mighty Local Provider Runtime

The Provider Runtime is a local, isolated browser service that maintains
authenticated provider sessions without using the user's ordinary Chrome
windows or profiles.

The first implementation supports American Express.

## Lifecycle

```text
bootstrap amex
      |
      | opens dedicated native Chrome + CDP
      v
user signs in (MFA as needed)
      |
      | verify over CDP -> SIGNED_IN
      v
authenticated Chrome stays alive
      |
      | second terminal: serve attaches over CDP
      v
repeated verify calls reuse the same session
```

Credentials are entered directly into the provider's native page. Mighty does
not read or store passwords.

## Architecture

```text
Dashboard / extension / backend
             |
             | localhost commands
             v
Mighty Provider Runtime
             |
             | CDP attach (preferred) or headless launch
             v
Dedicated Amex Chrome process
             |
             v
Persistent Amex profile
```

The user sees a provider window during bootstrap login (and later for MFA,
CAPTCHA, consent, or other provider-required interaction). After a successful
bootstrap, that authenticated Chrome process remains running. `serve` attaches
to it over CDP instead of launching a second browser.

## 1. Bootstrap Amex

Run in a real terminal:

```bash
.venv/bin/python scripts/provider_runtime.py bootstrap amex
```

An isolated Chrome window opens. Sign in to Amex and complete any MFA. When the
authenticated account is visible, return to the terminal and press Enter.

The bootstrap verifies the session over Chrome DevTools Protocol. On
`SIGNED_IN`, it **does not** terminate Chrome and **does not** release the
profile. The authenticated browser stays alive with CDP enabled so `serve` can
attach to that exact process.

If verification is not `SIGNED_IN`, the browser is also left running for
diagnosis.

A successful bootstrap prints a result including:

```json
{
  "authentication_state": "SIGNED_IN"
}
```

Then follow the printed instruction to start `serve` in a second terminal.

## 2. Start the runtime

In a second terminal (while the authenticated Amex Chrome from bootstrap is
still running):

```bash
.venv/bin/python scripts/provider_runtime.py serve
```

Leave this process running. On start it:

1. Checks whether the configured CDP endpoint is already available.
2. If yes, attaches to that existing authenticated Chrome process and does not
   launch or terminate another browser.
3. If no live CDP endpoint exists, falls back to launching dedicated headless
   Chrome with the Mighty Amex profile.

It exposes:

- local control API: `http://127.0.0.1:8765`
- dedicated Amex Chrome CDP: `http://127.0.0.1:9223`

Profile directory:

```text
~/.mighty/provider_runtime/amex
```

## 3. Verify Amex

With `serve` running:

```bash
.venv/bin/python scripts/provider_runtime.py verify amex
```

Or directly:

```bash
curl -X POST http://127.0.0.1:8765/providers/amex/verify
```

Verification connects through Playwright `connect_over_cdp`, classifies the
session, then disconnects the Playwright client **without** calling
`browser.close()`, so the native Chrome process stays alive for the next verify.

Possible canonical results:

```text
SIGNED_IN
SIGNED_OUT
LOGIN_UNKNOWN
```

### Verification versus maintenance

| Concern | Owner |
| --- | --- |
| Classify whether Amex is `SIGNED_IN` / `SIGNED_OUT` / `LOGIN_UNKNOWN` | Verification (`POST /providers/amex/verify`) |
| Detect the Amex inactivity-expiration dialog and click **Continue** | Maintenance watcher |
| Confirm the session is still valid after Continue | Maintenance calls the same canonical verify path |
| Open, close, or restart Chrome | Neither path — Chrome stays attached over CDP |

Verification answers “is this session authenticated right now?” Maintenance keeps a
live authenticated Chrome session from being dropped by Amex’s inactivity dialog.

## 4. Automatic Amex session extension

When `serve` attaches to the live Amex Chrome process, it starts one daemon
maintenance watcher thread. That watcher:

- polls about every 3 seconds over the existing CDP endpoint;
- prefers an existing page whose hostname ends with `americanexpress.com`;
- never creates a page merely to watch, and never calls `browser.close()`;
- detects the genuine expiration dialog only when all of these are visible in
  the same dialog/modal:
  - text like “Your session is about to expire”;
  - language referring to session expiration or inactivity;
  - a **Continue** button inside that dialog;
- records `maintenance_started`, clicks Continue, waits up to 10 seconds for the
  dialog to disappear, then runs canonical verification;
- records success only when verification returns `SIGNED_IN`.

Maintenance outcomes:

```text
SESSION_EXTENDED
EXTENSION_CLICK_FAILED
DIALOG_DID_NOT_CLOSE
SESSION_NOT_CONFIRMED
WATCHER_ERROR
```

Watcher exceptions are recorded as `WATCHER_ERROR` and are never classified as
`SIGNED_OUT`. Concurrent verify and maintenance calls share a runtime lock so
they cannot drive the same page at once. Duplicate maintenance attempts are
locked out and debounced for at least 30 seconds.

### Developer maintenance check

Run one immediate inspection without starting a second watcher:

```bash
curl -X POST http://127.0.0.1:8765/providers/amex/maintenance/check
```

## 5. Inspect runtime status

```bash
.venv/bin/python scripts/provider_runtime.py status
```

`chrome_pid` / `chrome_running` reflect processes whose command line contains
the exact dedicated `--user-data-dir` path, even when `ProviderRuntime` did not
launch that Chrome process itself (for example after bootstrap attach).

Sanitized maintenance fields in status/state:

```text
maintenance_running
last_maintenance_attempt_at
last_maintenance_result
last_session_extended_at
maintenance_attempt_count
maintenance_success_count
```

These fields never store page HTML, account values, cookies, credentials,
request bodies, or query strings.

## 6. Stop the runtime

```bash
.venv/bin/python scripts/provider_runtime.py stop
```

Stopping ends the maintenance watcher, then terminates only Chrome processes
whose command line contains the exact dedicated Mighty Amex profile path.
Normal Chrome windows and profiles are never affected.

## Current scope

This is the first functional runtime boundary, not the final production service.

It currently provides:

- isolated persistent Amex profile;
- native visible login bootstrap that leaves Chrome alive;
- CDP attach from `serve` to the authenticated process;
- headless launch fallback when no CDP endpoint is live;
- CDP-based verification that disconnects without killing Chrome;
- automatic Amex inactivity-dialog session extension;
- localhost status, verification, maintenance-check, and shutdown commands;
- canonical authentication results;
- sanitized persisted runtime state (including maintenance counters).

It does not yet:

- start automatically at login;
- authenticate localhost requests;
- communicate with Railway;
- perform account-data extraction;
- recover from MFA or CAPTCHA;
- manage multiple providers.
