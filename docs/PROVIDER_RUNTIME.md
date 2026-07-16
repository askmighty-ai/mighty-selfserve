# Mighty Local Provider Runtime

The Provider Runtime is a local, isolated browser service that maintains
authenticated provider sessions without using the user's ordinary Chrome
windows or profiles.

The first implementation supports American Express.

## Architecture

```text
Dashboard / extension / backend
             |
             | localhost commands
             v
Mighty Provider Runtime
             |
             | CDP
             v
Dedicated headless Chrome
             |
             v
Persistent Amex profile
```

The user sees a provider window only during initial login, MFA, CAPTCHA, consent,
or another provider-required interaction. Routine verification runs through the
headless runtime.

Credentials are entered directly into the provider's native page. Mighty does
not read or store passwords.

## 1. Bootstrap Amex

Run in a real terminal:

```bash
.venv/bin/python scripts/provider_runtime.py bootstrap amex
```

An isolated Chrome window opens. Sign in to Amex and complete any MFA. When the
authenticated account is visible, return to the terminal and press Enter.

The bootstrap verifies the session over Chrome DevTools Protocol, then shuts
down only Chrome processes using Mighty's dedicated Amex profile. Other Chrome
windows and profiles are untouched.

A successful bootstrap prints:

```json
{
  "authentication_state": "SIGNED_IN"
}
```

## 2. Start the runtime

```bash
.venv/bin/python scripts/provider_runtime.py serve
```

Leave this process running. It starts:

- local control API: `http://127.0.0.1:8765`
- dedicated Amex Chrome CDP: `http://127.0.0.1:9223`

The browser is headless and uses:

```text
~/.mighty/provider_runtime/amex
```

## 3. Verify Amex

In a second terminal:

```bash
.venv/bin/python scripts/provider_runtime.py verify amex
```

Or directly:

```bash
curl -X POST http://127.0.0.1:8765/providers/amex/verify
```

Possible canonical results:

```text
SIGNED_IN
SIGNED_OUT
LOGIN_UNKNOWN
```

## 4. Inspect runtime status

```bash
.venv/bin/python scripts/provider_runtime.py status
```

## 5. Stop the runtime

```bash
.venv/bin/python scripts/provider_runtime.py stop
```

Stopping terminates only Chrome processes whose command line contains the exact
dedicated Mighty profile path.

## Current scope

This is the first functional runtime boundary, not the final production service.

It currently provides:

- isolated persistent Amex profile;
- native visible login bootstrap;
- headless long-running Chrome;
- CDP-based verification;
- localhost status, verification, and shutdown commands;
- canonical authentication results;
- sanitized persisted runtime state.

It does not yet:

- start automatically at login;
- authenticate localhost requests;
- communicate with Railway;
- perform account-data extraction;
- recover from MFA or CAPTCHA;
- manage multiple providers.
