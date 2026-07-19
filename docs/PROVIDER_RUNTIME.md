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

#### Developer Amex expiration recorder

`browser-watch-text` can false-positive immediately because Amex global
navigation permanently contains “Log Out”. `browser-record-expiration` is a
developer-only alternative that does **not** treat dialog text as the trigger.
It keeps a rolling in-memory window of CDP browser evidence and saves that
window when **canonical** authentication transitions from `SIGNED_IN` to
`SIGNED_OUT`.

**State channels (do not conflate them):**

| Channel | Source | Used for lifecycle? |
| --- | --- | --- |
| Canonical | Fresh `ReadUserSession.v1` + current URL via `classify_amex` (same policy as `verify amex`, but **no navigation**) | Yes — start + completion |
| Browser observation | Passive Inspector / DOM / AX text classification (no session API) | No — diagnostic evidence only |

Browser observation may stay `LOGIN_UNKNOWN` while canonical is `SIGNED_IN`;
that is expected and must not prevent recording. Browser `SIGNED_OUT` alone
never completes the recorder.

**Startup:**

1. Run fresh canonical verification.
2. `SIGNED_IN` → start recording.
3. `SIGNED_OUT` → `initial_not_signed_in` (do not start).
4. `LOGIN_UNKNOWN` → retry up to ~10s at 1s intervals; if still unknown →
   `initial_authentication_unknown`.

**Completion:** only when this run previously saw canonical `SIGNED_IN` and a
later fresh canonical verification returns `SIGNED_OUT`. Canonical
`LOGIN_UNKNOWN` during the run is recorded and polling continues; the last
definitive canonical state is retained separately.

**Idle-session caveat:** `ReadUserSession.v1` is also the `SESSION_API`
keepalive action and **may refresh Amex idle timeout**. Therefore canonical
verification defaults to every **5 seconds**, while screenshots / browser
evidence continue at **1 second**. Do not set `--verification-interval-seconds`
to `1` for idle-expiration experiments unless you intentionally accept that
risk.

It does **not** start or stop a keepalive trial, does not click/navigate/reload,
does not use `page.evaluate` / `frame.evaluate`, does not invoke keepalive
actions, and runs outside the runtime lock so keepalive inspection can continue.

For the recommended one-command experiment workflow (keepalive strategy +
recorder + evidence ZIP), see **Developer Amex expiration experiment** below. The lower-level
`browser-record-expiration` command remains available when you need the recorder
alone.

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--interval-seconds` | `1` | Browser-evidence poll interval |
| `--timeout-seconds` | `900` | Exit cleanly if logout never occurs |
| `--rolling-window-seconds` | `90` | Retain only the trailing observation window |
| `--screenshot-every-seconds` | `1` | Viewport PNG cadence via `Page.captureScreenshot` |
| `--verification-interval-seconds` | `5` | Fresh canonical verification cadence (`ReadUserSession.v1`) |
| `--output-dir` | auto under diagnostics | Optional directory for the JSON + screenshots |

Documented outcomes: `logged_out`, `timeout`, `initial_not_signed_in`,
`initial_authentication_unknown`, `fatal_error`.

Default output:

```text
~/.mighty/provider_runtime/diagnostics/
  amex-expiration-recording-<UTC timestamp>/
    recording.json
    screenshots/
      0001-<UTC timestamp>.png
      ...
```

The CLI prints the final `recording.json` path. Each observation stores both
canonical and browser-observation auth fields. “Log Out” is intentionally not
searched. Credentials, cookies, headers, request/response bodies, full HTML,
and unbounded page text are never stored.

#### Developer Amex expiration experiment

`browser-run-expiration-experiment` is the recommended developer workflow for
idle-expiration evidence. It replaces the old three-terminal dance with one
command that:

1. checks that `serve` is already reachable (does **not** auto-launch it);
2. runs a fresh canonical Amex verification (`SIGNED_OUT` → bootstrap hint;
   `LOGIN_UNKNOWN` → retry up to ~10s; requires `SIGNED_IN` to continue);
3. starts a keepalive trial with the selected `--strategy` (default `NONE`);
4. immediately starts `browser-record-expiration` concurrently (HTTP client
   orchestration; does not hold the runtime lock while waiting);
5. waits for the recorder to finish;
6. if the recorder outcome is `logged_out`, polls `keepalive-status` once per
   second until the keepalive trial stops, with a dynamic cap of
   `min(keepalive_interval_seconds + 10, 60)` so the worker can complete its
   next naturally scheduled tick (the orchestrator never wakes or stops the
   trial during this wait);
7. collects final `keepalive-status`, writes experiment metadata (including
   selected strategy and recorder/keepalive timing fields), and builds one ZIP.

Recommended workflow:

```bash
# Terminal 1 — runtime (attach to already-bootstrapped Amex Chrome)
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py serve

# Terminal 2 — one-time login if needed
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py bootstrap amex

# Then, for each experiment (default strategy NONE):
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  browser-run-expiration-experiment amex

# Trial a keepalive strategy (e.g. SESSION_API):
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  browser-run-expiration-experiment amex \
  --strategy SESSION_API
```

Defaults:

| Option | Default |
| --- | --- |
| `--strategy` | `NONE` (`NONE`, `SESSION_API`, `PAGE_ACTIVITY`, `OVERVIEW_RELOAD`) |
| `--trial-duration-seconds` | `600` |
| `--keepalive-interval-seconds` | `30` |
| `--recording-timeout-seconds` | `900` |
| `--evidence-interval-seconds` | `1` |
| `--verification-interval-seconds` | `5` |
| `--rolling-window-seconds` | `90` |
| `--screenshot-every-seconds` | `1` |
| `--output-dir` | auto under diagnostics |

Output:

```text
~/.mighty/provider_runtime/diagnostics/
  amex-expiration-experiment-<UTC timestamp>/
    experiment-summary.json
    keepalive-status.json
    runtime-status.json
    recorder/
      recording.json
      screenshots/
        ...
    amex-expiration-experiment-<UTC timestamp>.zip
```

The CLI prints a concise final result including the absolute Evidence ZIP path.
Upload that single ZIP after the run.

Ctrl+C stops orchestration only: it preserves any recorder output already
created, collects current keepalive status, writes a partial ZIP with
`outcome: interrupted`, and does **not** kill the authenticated Amex Chrome or
stop `serve`.

Convenience (macOS Finder):

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  browser-open-latest-expiration-experiment amex
```

#### Developer Amex expiration campaign

`browser-run-expiration-campaign` runs multiple existing expiration experiments
sequentially and packages one comparison artifact. It reuses
`browser-run-expiration-experiment` orchestration as the unit of execution.
The campaign also ensures a dedicated managed Amex Chrome window exists by
reusing `launch_native_chrome` with the Mighty profile + CDP port (never the
user’s normal Chrome profile).

Recommended workflow:

```bash
# Terminal 1 — keep serve running as the safety boundary
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py serve

# Terminal 2 — campaign launches/reuses managed Chrome and prompts for login
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  browser-run-expiration-campaign amex \
  --campaign-name amex-keepalive-comparison \
  --trial NONE:30 \
  --trial SESSION_API:30 \
  --trial SESSION_API:5 \
  --trial PAGE_ACTIVITY:30 \
  --trial OVERVIEW_RELOAD:30
```

You do **not** need to run `bootstrap amex` first. At startup the campaign:

1. checks runtime health (`serve` must already be running);
2. classifies managed CDP health via `/json/version` + `/json/list`;
3. reuses a healthy managed browser, launches one when absent, or restarts only
   the Mighty Amex profile Chrome when unhealthy (zero page targets);
4. performs canonical verification;
5. if `SIGNED_OUT` / `LOGIN_UNKNOWN`, brings the managed window forward (macOS)
   and prompts:

```text
Authentication required.

A dedicated Mighty Amex Chrome window has been opened.
Sign in and complete MFA.
Press Enter here when the account overview page is visible.
```

After Enter, it verifies again and continues only when `SIGNED_IN`. Between
trials, logout or a zero-target browser state triggers recovery +
reauthentication automatically.

Example console:

```text
Checking managed Amex browser...
No managed browser found.

Launching dedicated Mighty Amex Chrome...
Browser ready.

Authentication required.

A dedicated Mighty Amex Chrome window has been opened.
Sign in and complete MFA.
Press Enter here when the account overview page is visible.
```

Output:

```text
~/.mighty/provider_runtime/diagnostics/
  amex-expiration-campaign-<UTC timestamp>/
    campaign-summary.json
    campaign-summary.csv
    campaign-report.md
    campaign-manifest.json
    trials/
      001-none-30s/
      002-session-api-30s/
      ...
    amex-expiration-campaign-<UTC timestamp>.zip
```

`campaign-summary.json` also records managed-browser ownership fields:
`managed_browser_preexisting`, `managed_browser_launched_by_campaign`,
`managed_browser_restarted_by_campaign`, `browser_cleanup_policy`,
`managed_browser_closed_at_completion`, `managed_cdp_port`, and
`managed_profile_path`.

Useful flags:

| Option | Behavior |
| --- | --- |
| `--browser-cleanup close-on-completion` | Default. Closes only a browser this campaign launched; never closes a preexisting managed browser or ordinary Chrome |
| `--browser-cleanup leave-open` | Leave the managed browser running after the campaign |
| `--continue-on-error` | Record a failed trial and continue (with auth recovery if needed) |
| `--skip-completed` | Resume an interrupted campaign; skip trials already completed for the same strategy + interval in `campaign-manifest.json` |
| `--output-dir` | Choose/resume a specific campaign directory |

Ctrl+C finishes writing the current trial’s partial evidence, writes campaign
summary files for completed/partial trials, creates a partial campaign ZIP,
leaves the managed browser open by default, and does **not** stop `serve` or
touch ordinary Chrome.

The CLI prints only the campaign result and final ZIP path.

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

Trial state includes counters, dialog/logout flags, latest observed
authentication fields (updated each tick), final canonical authentication
state/reason (set only at finalization), and a bounded sanitized event list.

Latest versus final authentication:

| Field | When set |
| --- | --- |
| `keepalive_latest_authentication_state` | After every completed tick inspection |
| `keepalive_latest_authentication_state_source` | Same tick |
| `keepalive_latest_reason` | Same tick (`inspection` / `logged_out` / …) |
| `keepalive_latest_observed_at` | Same tick |
| `keepalive_final_authentication_state` | Only when the trial finalizes |
| `keepalive_final_reason` | Only when the trial finalizes |

Compatibility: the generic `authentication_state` field maps to
`keepalive_latest_authentication_state` while a trial is running, and to
`keepalive_final_authentication_state` after finalization. Prefer the explicit
`keepalive_latest_*` / `keepalive_final_*` fields in new code.

Events never store credentials, cookies, authorization headers, request/response
bodies, account values, full query strings, or page HTML.

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
  browser-find-text, browser-watch-text, browser-record-expiration,
  browser-run-expiration-experiment, browser-run-expiration-campaign,
  browser-open-latest-expiration-experiment, keepalive, and shutdown commands;
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
