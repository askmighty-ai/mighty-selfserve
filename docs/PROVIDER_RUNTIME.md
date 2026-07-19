# Mighty Local Provider Runtime

The Provider Runtime is a local, isolated browser service that maintains
authenticated provider sessions without using the user's ordinary Chrome
windows or profiles.

The first implementation supports American Express.

## Recommended operational interface

For day-to-day development, use the **Mighty Access Control Center**. It is the
long-running console that proves Mighty can continuously maintain authenticated
access — not a campaign UI, and not a one-shot probe.

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py control-center amex
```

On start it:

1. ensures Provider Runtime (`serve`) is healthy (starts it when needed);
2. ensures the managed Amex browser is available;
3. prompts for login/MFA once if the session is not already `SIGNED_IN`;
4. starts the Access Supervisor loop;
5. redraws a live console from a provider-independent `AccessState`.

Authenticate once, leave it running, and return hours later to see whether
access stayed healthy. Keyboard shortcuts while the console is up:

| Key | Action |
| --- | --- |
| `v` | Verify authentication now |
| `k` | Run keepalive now |
| `r` | Run connector refresh |
| `l` | Login / recover now |
| `q` | Quit Control Center |

Default browser cleanup is `leave-open` so an authenticated managed Chrome
session survives quitting the console. Runtime started by Control Center is
stopped on quit; a preexisting runtime is left alone.

See [Access Control Center](#access-control-center) below for architecture.

## Lifecycle

```text
control-center amex          (recommended)
      |
      | ensure serve + managed Chrome
      v
user signs in once (MFA as needed)
      |
      | Access Supervisor maintains access
      v
live console shows AccessState for hours

— or the lower-level path —

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

`campaign amex` is the first-class end-to-end keepalive comparison command.
It ensures Provider Runtime is running, manages the dedicated Amex browser,
runs the default trial matrix, packages one ZIP, and stops `serve` only when
this command started it.

Recommended low-interaction workflow (walk away; return only for Amex MFA):

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py campaign amex \
  --trial SESSION_API:30 \
  --trial PAGE_ACTIVITY:30 \
  --trial OVERVIEW_RELOAD:30 \
  --analyze \
  --unattended \
  --notify \
  --prevent-sleep
```

Amex MFA cannot be automated. With `--unattended` and `--notify`, the campaign
pauses safely with no auth timeout, brings the dedicated Mighty Amex Chrome
window forward, sends a macOS desktop notification, and waits indefinitely for
Enter after you finish signing in. Reminder notifications repeat every 15
minutes by default (`--auth-reminder-minutes`; minimum 5).

That command:

1. checks runtime health and auto-starts `serve` when needed;
2. launches or reuses the dedicated managed Amex Chrome window
   (`launch_native_chrome` + Mighty profile + CDP port `9223`);
3. prompts for login/MFA when required (with optional desktop notifications);
4. runs the selected trials without per-trial confirmation;
5. prints minute heartbeats while each trial runs;
6. packages `campaign-summary.json` / `.csv` / `.md` plus trial evidence into one ZIP;
7. runs offline analysis when `--analyze` is set;
8. stops owned `caffeinate` / `serve` only when this command started them.

Default full matrix (when no `--trial` is passed):

   - `NONE:30`
   - `SESSION_API:30`
   - `SESSION_API:5`
   - `PAGE_ACTIVITY:30`
   - `OVERVIEW_RELOAD:30`

Authentication prompt when needed (repeatable for every trial that logs out):

```text
Authentication required for trial 2 of 3.

The campaign is paused safely and will wait indefinitely.
Sign in and complete MFA in the dedicated Mighty Amex window.
Wait until the account overview is fully loaded.
Return here and press Enter when ready.
```

macOS notification example:

```text
Title: Mighty Amex authentication required
Body:  Trial 2 of 3 is waiting. Sign in to Amex and return to Terminal.
```

After Enter the campaign prints `Input received.` / `Verifying authentication...`
and continues only when canonical verification is `SIGNED_IN`. Failed
verification re-prompts without failing the pending trial or stopping `serve`.
While paused, the manifest records `waiting_for_authentication` plus pending
trial metadata so `--resume` can continue without rerunning completed trials.

Resume an interrupted campaign (skips completed trials via the campaign
manifest):

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py campaign amex \
  --resume ~/.mighty/provider_runtime/diagnostics/amex-expiration-campaign-<UTC>/
```

Example console:

```text
Provider Runtime not running.
Starting Provider Runtime serve...
Provider Runtime ready.
Preventing sleep with owned caffeinate process...
Checking managed Amex browser...
No managed browser found.
Launching dedicated Mighty Amex Chrome...
Browser ready.

Authentication required for trial 1 of 3.

The campaign is paused safely and will wait indefinitely.
Sign in and complete MFA in the dedicated Mighty Amex window.
Wait until the account overview is fully loaded.
Return here and press Enter when ready.
```

Advanced users may override the default matrix with repeatable `--trial` options
and/or call the internal helper `browser-run-expiration-campaign` against an
already-running `serve`.

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
| `--trial STRATEGY:INTERVAL` | Override the default trial matrix (repeatable) |
| `--unattended` | Low-interaction mode: no trial confirmations, indefinite auth waits, progress heartbeats |
| `--notify` | macOS desktop notifications for auth pauses, trial completion, and campaign end (nonfatal on failure) |
| `--prevent-sleep` | Start an owned `caffeinate` process for the campaign duration (macOS) |
| `--auth-reminder-minutes N` | In `--unattended`, repeat auth notifications every N minutes (default 15, minimum 5) |
| `--browser-cleanup close-on-completion` | Default. Closes only a browser this campaign launched; never closes a preexisting managed browser or ordinary Chrome |
| `--browser-cleanup leave-open` | Leave the managed browser running after the campaign |
| `--continue-on-error` | Record a failed trial and continue (with auth recovery if needed) |
| `--skip-completed` | Resume an interrupted campaign; skip trials already completed for the same strategy + interval in `campaign-manifest.json` |
| `--output-dir` | Choose/resume a specific campaign directory |
| `--analyze` | After packaging, run offline campaign analysis into `campaign-analysis.json` / `.csv` / `.md` |

Ctrl+C finishes writing the current trial’s partial evidence, writes campaign
summary files for completed/partial trials, creates a partial campaign ZIP,
leaves the managed browser open by default, stops owned `caffeinate` and
`serve` only when this command started them, and never touches ordinary Chrome.

The CLI prints only the campaign result and final ZIP path.

#### Analyze an existing campaign

Offline analysis reads saved trial evidence only. It does not start `serve` or
Chrome and never mutates the managed browser, runtime state, authentication
profile, or original trial artifacts.

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  analyze-campaign <campaign-directory-or-zip>
```

Examples:

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  analyze-campaign \
  ~/.mighty/provider_runtime/diagnostics/amex-expiration-campaign-<UTC>/

PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  analyze-campaign \
  ~/.mighty/provider_runtime/diagnostics/amex-expiration-campaign-<UTC>/amex-expiration-campaign-<UTC>.zip
```

Outputs written beside the campaign directory (or beside a ZIP):

```text
campaign-analysis.json
campaign-analysis.csv
campaign-analysis.md
```

#### Run and analyze in one step

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  campaign amex --analyze
```

If analysis fails after a successful campaign, the campaign ZIP and trial
evidence are preserved; analysis prints a clear error and returns a distinct
nonzero status without rewriting campaign success as packaging failure.

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
| `SESSION_API` | Periodic read-only `ReadUserSession.v1` via browser `context.request` (no `page.evaluate`) |
| `PAGE_ACTIVITY` | Harmless bring-to-front + tiny mouse-wheel scroll/restore (no clicks/forms/navigation) |
| `OVERVIEW_RELOAD` | Reload/navigate to the Amex overview page (experimental) |

Amex monkey-patches `eval`, so keepalive strategies must not use
`page.evaluate` / `frame.evaluate`. `SESSION_API` uses the same credentialed
`context.request.get(ReadUserSession.v1)` path as canonical verification.
`PAGE_ACTIVITY` uses Playwright input APIs. Keepalive trials remain developer
observation experiments.

Defaults: `duration_seconds=1800` (30 minutes), `interval_seconds=60`.
Developer trials may use shorter values such as 8 minutes / 30 seconds.

### Keepalive probe (preflight)

Before a long trial, prove one strategy attempt executes. No manual `serve`,
`bootstrap`, or browser setup is required:

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  keepalive-probe amex --strategy SESSION_API

PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  keepalive-probe amex --strategy PAGE_ACTIVITY

PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py \
  keepalive-probe amex --strategy OVERVIEW_RELOAD
```

Like `campaign amex`, the probe reuses the shared managed-session preflight
(`prepare_managed_amex_session_for_command`):

1. checks runtime health and auto-starts `serve` when needed;
2. launches or reuses the dedicated Mighty Amex Chrome window
   (`launch_native_chrome` + Mighty profile + configured CDP port);
3. prompts for login/MFA until canonical verification is `SIGNED_IN`;
4. runs exactly one keepalive strategy attempt;
5. writes sanitized evidence under diagnostics;
6. stops `serve` only when this command started it;
7. closes only a browser this probe launched when
   `--browser-cleanup close-on-completion` (default); never closes a preexisting
   managed browser or ordinary Chrome.

Example fresh-machine console:

```text
Provider Runtime not running.
Starting Provider Runtime serve...
Provider Runtime ready.

Checking managed Amex browser...
No managed browser found.
Launching dedicated Mighty Amex Chrome...
Browser ready.

Authentication required.

A dedicated Mighty Amex Chrome window has been opened.
Sign in and complete MFA.
Wait until the account overview is fully loaded.
Press Enter here when ready.

Input received.
Verifying authentication...
Authentication verified.
Running keepalive probe: SESSION_API...

Keepalive probe: SESSION_API
Result: SUCCESS
Reason: success
Duration: 120ms
Target: https://functions.americanexpress.com/ReadUserSession.v1
HTTP status: 200
Evidence: <path>

Stopping Provider Runtime started by this probe...
```

Failed post-Enter verification re-prompts without failing the probe. Ctrl+C
preserves evidence, leaves the managed browser open by default, stops `serve`
only when this command started it, and never touches ordinary Chrome.

Useful flags:

| Option | Behavior |
| --- | --- |
| `--browser-cleanup close-on-completion` | Default. Closes only a browser this probe launched |
| `--browser-cleanup leave-open` | Leave the managed browser running after the probe |

`campaign amex` runs this probe before each active-strategy trial. A failed
preflight records `OPERATIONALLY_FAILED` / `preflight_failed` and skips the
long observation window (`NONE` has no probe).

### CLI

```bash
.venv/bin/python scripts/provider_runtime.py keepalive-start amex \
  --strategy SESSION_API \
  --duration-seconds 1800 \
  --interval-seconds 60

.venv/bin/python scripts/provider_runtime.py keepalive-probe amex \
  --strategy SESSION_API

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

## Access Control Center

The Control Center is the recommended long-running operational interface for
Provider Runtime development. It answers: “Has Mighty continuously maintained
authenticated access?”

### Architecture

```text
control-center CLI
        |
        | ownership (serve + managed browser + initial auth)
        v
Access Supervisor  ----updates---->  AccessState
        |                                |
        | verify / overview /            | read-only
        | keepalive / repair             v
        |                          live console
        v
   EventHistory (last 100)
```

- **AccessState** — provider-independent snapshot. All verification and repair
  paths update this object. The console renders only `AccessState`.
- **Access Supervisor** — every `--interval-seconds` (default 60): verify auth,
  verify browser, ensure overview surface, run keepalive when due, repair when
  degraded, append events. It does **not** wait for connector refreshes to
  trigger maintenance.
- **EventHistory** — in-memory rolling buffer (default last 100 events):
  verification success/failure, keepalive success/failure, recovery
  started/completed, browser restart, user interruption, connector refresh.
- **Recovery Planner status** — Control Center surface for repair state
  (`idle` / `recovering` / `awaiting_user` / `failed`). Browser restarts run
  automatically; authentication loss sets `awaiting_user` until `l` (login).

### Console fields

| Section | Fields |
| --- | --- |
| SYSTEM | Runtime, Browser, Recovery Planner, Scheduler |
| PROVIDER | Authentication, Access health, Session age, Last verification, Last keepalive, Current strategy, Recoveries, User interruptions, Ready for extraction, Ready for connector |
| RECENT EVENTS | Rolling sanitized history |

### CLI

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py control-center amex

PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py control-center amex \
  --interval-seconds 60 \
  --keepalive-interval-seconds 300 \
  --strategy SESSION_API \
  --browser-cleanup leave-open
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--interval-seconds` | `60` | Supervisor tick interval |
| `--keepalive-interval-seconds` | `300` | Minimum gap between automatic keepalive probes |
| `--strategy` | `SESSION_API` | Keepalive strategy for supervisor probes |
| `--browser-cleanup` | `leave-open` | Close only a browser this command launched |

Implementation: `mighty/provider_runtime_control_center.py`.

## Current scope

This is the first functional runtime boundary, not the final production service.

It currently provides:

- **Access Control Center** (`control-center`) as the recommended ops console;
- isolated persistent Amex profile;
- native visible login bootstrap that leaves Chrome alive;
- CDP attach from `serve` to the authenticated process;
- headless launch fallback when no CDP endpoint is live;
- CDP-based verification that disconnects without killing Chrome;
- reusable Browser Inspector (pages/frames/shadow/modal candidates);
- automatic Amex inactivity-dialog session extension via inspector + classifier;
- developer-only Amex keepalive trials (experiment, not production keepalive);
- Access Supervisor continuous maintenance (verify / overview / keepalive / repair);
- localhost status, verification, maintenance-check, browser-inspect,
  browser-find-text, browser-watch-text, browser-record-expiration,
  browser-run-expiration-experiment, campaign (end-to-end Amex keepalive
  comparison), browser-run-expiration-campaign (internal helper),
  browser-open-latest-expiration-experiment, keepalive, connector-refresh,
  control-center, and shutdown commands;
- production connector support APIs:
  `ensure_usable_session`, `ensure_provider_surface`,
  `execute_readonly_extraction` (Amex overview accounts + rewards);
- canonical authentication results;
- sanitized persisted runtime state (including maintenance counters and trial results).

Account-data extraction for production reads lives in the **connector layer**
(see [CONNECTORS.md](./CONNECTORS.md)). Runtime owns session/surface/extract
transport; connectors own normalization and the public refresh contract.

It does not yet:

- start automatically at login;
- authenticate localhost requests;
- communicate with Railway;
- automate MFA or CAPTCHA (operator interruption only);
- manage multiple providers in Runtime itself (connectors are multi-provider);
- enable automatic keepalive as always-on production behavior outside Control Center;
- extract transactions, offers, statements, or perform account mutations.
