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
- uses Browser Inspector candidates plus the Amex expiration classifier;
- clicks **Continue** only inside the exact classified candidate;
- records `maintenance_started`, waits up to 10 seconds for the dialog to
  disappear, then runs canonical verification;
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

### Browser Inspector

The Browser Inspector is a reusable, provider-agnostic view of what the attached
browser is visibly rendering. It does **not** own provider-specific meaning.

#### Why Amex breaks Playwright `evaluate`

Amex monkey-patches `eval` in page JavaScript. Playwright’s
`frame.evaluate` / `page.evaluate` therefore fail with `Error: eval is disabled`
on authenticated Amex pages. Canonical verify still works because it uses
navigation, locators, title, `body.inner_text`, and response listeners — not
in-page evaluate.

Live Browser Inspector collection therefore uses **Chrome DevTools Protocol
(CDP)** only. No production inspection path calls `frame.evaluate` or
`page.evaluate`.

#### CDP-backed architecture

```text
Provider Runtime
    ↓
Browser Inspector (CDP)
    ├── Page.getFrameTree
    ├── DOM.getDocument(pierce=true)
    ├── Accessibility.getFullAXTree
    ├── bounded DOM/AX candidate extraction (Python)
    ├── CSS.getComputedStyleForNode + DOM.getBoxModel (visibility/geometry)
    └── sanitized screenshots/diagnostics metadata
```

Flow:

1. open a page-bound CDP session;
2. enable DOM / CSS / Accessibility domains;
3. take pierced DOM + full AX snapshots;
4. discover a bounded union of modal-like nodes (selectors, AX dialog roles,
   fixed/absolute containers) — not a full-page serialize;
5. build `InspectionCandidate` records in Python;
6. dedupe nested/overlapping candidates, preferring the outer useful container.

Open shadow roots and same-origin child-frame documents are included through
pierced DOM. Closed/inaccessible roots and cross-origin frames produce sanitized
diagnostics rather than fabricated content.

Responsibilities:

- select the best provider page with a generic hostname/preference helper;
- inspect the selected page’s main frame, nested child frames, and open shadow
  roots without failing the whole run when one context is inaccessible;
- emit bounded `InspectionCandidate` records for visible modal-like containers
  (not every page shell), including `role="dialog"`, `aria-modal`, AX
  dialog/alertdialog/alert nodes, substantial fixed/absolute overlays, high
  z-index layers, actionable controls, and modal-related text;
- deduplicate nested candidates so the outer useful container wins;
- keep text snippets ≤300 characters and mask runs of 6+ digits as
  `[REDACTED_NUMBER]`;
- never persist credentials, cookies, authorization headers, request/response
  bodies, full HTML, balances, card numbers, transaction values, or full query
  strings.

Developer diagnostics for failed CDP operations include frame URL/id, target id
when available, CDP method, exception class/message/traceback, failure phase,
failure scope (entire frame vs node), and same-origin/cross-origin when known.

#### Provider-specific classification boundary

Amex-specific expiration classification consumes inspector output in a separate
classifier. A candidate is the Amex expiration dialog only when:

- headline text is a close equivalent of “Your session is about to expire”;
- text mentions inactivity or expiration;
- visible actions include **Continue**;
- text/actions also include Log Out or equivalent session-ending language
  (for example “signed out”);
- the candidate is visible.

Classifier condition keys:

```text
headline_match
expiration_language_match
continue_action_match
logout_action_match
candidate_visible
classified_as_expiration_dialog
```

`role="dialog"` is not required.

#### Maintenance Continue click

After classification, maintenance retains a stable CDP identity for the matched
Continue control (`backendNodeId`, encoded as an internal continue token) and
clicks it with CDP `Input.dispatchMouseEvent` at the control’s box-model center.
It does not write marker attributes via JavaScript and does not use
`page.evaluate` / `frame.evaluate`.

#### Developer Browser Inspector API / CLI

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py browser-inspect amex
```

Optional screenshot (disabled by default; never used by automatic background
inspection):

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py browser-inspect amex \
  --capture-screenshot
```

```bash
curl -X POST http://127.0.0.1:8765/providers/amex/diagnostics/browser-inspection \
  -H 'Content-Type: application/json' \
  -d '{"capture_screenshot": false}'

curl http://127.0.0.1:8765/providers/amex/diagnostics/browser-inspection/latest
```

Screenshot warning: captured images may contain sensitive account information.
They are stored only under `~/.mighty/provider_runtime/diagnostics/`, are never
uploaded, and the API returns only the local path (never screenshot bytes).

Keepalive trials remain observation-only: they record
`expiration_dialog_detected` from the classifier and do not click Continue.
Canonical authentication still comes from verification / the latest committed
canonical state (`inspection_authentication_state_source`:
`LATEST_CANONICAL` | `FRESH_VERIFICATION` | `NONE`), not from DOM guessing.

#### Developer browser text watcher

The timeout dialog appears only briefly, so repeatedly running
`browser-find-text` by hand is impractical. `browser-watch-text` is a
developer-only diagnostic that polls CDP DOM/AX for configured substrings and
writes one sanitized JSON bundle at the first match.

It does **not** start a keepalive trial, does not click Continue (or any other
control), does not mutate the page, and does not use `page.evaluate` /
`frame.evaluate`. Intended workflow:

1. Start a `NONE` keepalive trial.
2. In another terminal, start `browser-watch-text`.
3. Leave both running; the watcher captures the dialog automatically.

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  browser-watch-text amex \
  --terms "expire,Your session,Continue,Log Out" \
  --interval-seconds 1 \
  --timeout-seconds 600 \
  --stop-after-first-match
```

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--terms` | `expire,Your session,Continue,Log Out` | Comma-separated case-insensitive substrings |
| `--interval-seconds` | `1` | CDP poll interval |
| `--timeout-seconds` | `600` | Exit cleanly if no match |
| `--stop-after-first-match` / `--no-stop-after-first-match` | stop | Whether to exit after the first capture |
| `--output-file` | auto under diagnostics | Optional explicit JSON path |

Default output path:

```text
~/.mighty/provider_runtime/diagnostics/amex-text-watch-<UTC timestamp>.json
```

The CLI prints the exact saved path. The bundle includes `started_at`,
`matched_at`, `completed_at`, configured/matched terms, selected page URL,
canonical authentication state + source, per-term `browser-find-text` results,
a normal Browser Inspector snapshot, and bounded `errors`. Snippets stay
bounded with long-number redaction; credentials, cookies, headers,
request/response bodies, and full HTML are never stored.

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

## 6. Developer-only Amex keepalive trials (experiment)

This is an **experiment** to learn which controlled background action, if any,
resets Amex’s inactivity timer. It is **not** automatic production keepalive.
Do not enable a strategy as always-on behavior until trials prove it works.

Keepalive trials:

- require `SIGNED_IN` before start;
- attach to the existing live CDP Amex browser (no new Chrome process/tab);
- run only one trial at a time;
- put the maintenance watcher into observation-only mode (detect dialog, do
  **not** click Continue) while a trial is active;
- never convert keepalive exceptions into `SIGNED_OUT`;
- always terminalize with canonical verification and a sanitized persisted
  result.

### Strategies

| Strategy | Behavior |
| --- | --- |
| `NONE` | No maintenance action; baseline expiration timing |
| `SESSION_API` | Periodic read-only `ReadUserSession.v1` fetch from the Amex page context |
| `PAGE_ACTIVITY` | Harmless focus + small scroll/return (no clicks/forms) |
| `OVERVIEW_RELOAD` | Reload/navigate to the Amex overview page (experimental) |

**Current limitation:** `SESSION_API` and `PAGE_ACTIVITY` still use
`page.evaluate` and are incompatible with Amex’s eval monkey-patch. They were
not migrated in the CDP Browser Inspector change. Prefer `NONE` /
`OVERVIEW_RELOAD` for Amex trials until a non-evaluate keepalive path exists.
Keepalive trials remain observation experiments and must not silently change
behavior.

Defaults: `duration_seconds=1800` (30 minutes), `interval_seconds=60`.
Developer trials may use shorter values such as 8 minutes / 30 seconds.

### CLI

```bash
.venv/bin/python scripts/provider_runtime.py keepalive-start amex \
  --strategy SESSION_API \
  --duration-seconds 1800 \
  --interval-seconds 60

.venv/bin/python scripts/provider_runtime.py keepalive-status amex

.venv/bin/python scripts/provider_runtime.py keepalive-stop amex
```

### Localhost API

```bash
curl -X POST http://127.0.0.1:8765/providers/amex/keepalive/start \
  -H 'Content-Type: application/json' \
  -d '{"strategy":"SESSION_API","duration_seconds":1800,"interval_seconds":60}'

curl http://127.0.0.1:8765/providers/amex/keepalive/status

curl -X POST http://127.0.0.1:8765/providers/amex/keepalive/stop
```

Trial state includes counters, dialog/logout flags, final canonical
authentication state/reason, and a bounded sanitized event list. Events never
store credentials, cookies, authorization headers, request/response bodies,
account values, full query strings, or page HTML.

Persisted trial result path:

```text
~/.mighty/provider_runtime/amex_keepalive_last_trial.json
```

## 7. Stop the runtime

```bash
.venv/bin/python scripts/provider_runtime.py stop
```

Stopping ends any active keepalive trial, stops the maintenance watcher, then
terminates only Chrome processes whose command line contains the exact dedicated
Mighty Amex profile path. Normal Chrome windows and profiles are never affected.

## Current scope

This is the first functional runtime boundary, not the final production service.

It currently provides:

- isolated persistent Amex profile;
- native visible login bootstrap that leaves Chrome alive;
- CDP attach from `serve` to the authenticated process;
- headless launch fallback when no CDP endpoint is live;
- CDP-based verification that disconnects without killing Chrome;
- reusable Browser Inspector (pages/frames/shadow/modal candidates);
- automatic Amex inactivity-dialog session extension via inspector + classifier;
- developer-only Amex keepalive trials (experiment, not production keepalive);
- localhost status, verification, maintenance-check, browser-inspect,
  browser-find-text, browser-watch-text, keepalive, and shutdown commands;
- canonical authentication results;
- sanitized persisted runtime state (including maintenance counters and trial results).

It does not yet:

- start automatically at login;
- authenticate localhost requests;
- communicate with Railway;
- perform account-data extraction;
- recover from MFA or CAPTCHA;
- manage multiple providers;
- enable automatic keepalive as production behavior.
