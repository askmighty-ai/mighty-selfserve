# Access Flow — Provider Access Manager (Phase 1)

**Status:** Phase 1 complete — canonical production boundary established.  
**Related:** [PRODUCT_ACCOUNT_STATE.md](PRODUCT_ACCOUNT_STATE.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md)

---

## North star

Minimize minutes of user effort per connected account. Access acquisition should:

1. Prefer natural browser sessions over Mighty-initiated logins.
2. Write session truth to one place: `provider_session_state` (PSS).
3. Re-verify in the background without asking the user.
4. Treat every user interruption as a categorized bug.

---

## Canonical production path

```
Command (Check now / ensure-due / recovery)
  → Provider Access Manager.request_provider_verification(trigger_source=…)
      → session_verification (schedule)
      → extension GET /pending (read-only claim) → runSessionVerification
      → provider_access_probe (classify)
      → provider_session_state (persist definitive evidence only)
      → Amex: session_verified → extracting → correlated /amex/extract
      → access cycle completed only after extraction success (or signed_out)
```

Customer GETs never enqueue or expire. Timeout ownership is command-side only:

- Server verification-maintenance heartbeat (every 60s, plus startup sweep)
- `POST /api/extension/session-verification/maintain` (extension 1-minute alarm)
- `ensure-due` / Check now still expire before enqueue

Extension keepalive POSTs `ensure-due` with `trigger_source=scheduled_recheck`
independently of Dashboard traffic (enqueue path; not the sole timeout owner).

---

## Canonical Access Manager interface

Module: `mighty/provider_access_manager.py`

| Function | Role |
|----------|------|
| `request_provider_verification` | **Canonical** enqueue — requires explicit `trigger_source` |
| `request_provider_access_check` | Compatibility wrapper → `request_provider_verification` |
| `ensure_provider_access_check_if_stale` | Enqueue only when PSS evidence is stale (command/scheduled) |
| `ensure_stale_provider_access_checks` | Scheduled/command trigger for all stale probe providers |
| `run_verification_maintenance` | Per-user expire of overdue active rows (command-side) |
| `run_all_verification_maintenance` | Global expire used by the server heartbeat / startup |
| `mark_provider_access_check_running` | Extension claimed the job |
| `finish_provider_access_check` | Terminal verification lifecycle (no PSS write alone) |
| `complete_provider_access_check` | Record probe result + finish verification/manual jobs |
| `fail_provider_access_check` | Finish jobs when probe payload evaluation fails |
| `record_provider_access_evidence` | Canonical PSS write |
| `record_session_evidence_from_probe` | Probe → PSS (inconclusive = no write) |
| `record_amex_extension_connected` | Definitive Amex connected evidence |
| `record_amex_extension_needs_login` | Definitive Amex signed_out evidence |
| `record_extension_login_required` | Passive / sync-failure signed_out |
| `record_extension_session_connected` | Passive / login-cleared connected |

**Read/command boundary:** customer-facing GETs (`/dashboard`, `/api/account-status`,
`/api/extension/session-verification/pending`, `/sync/status`, `/api/latest-sync`,
`/dashboard/has-pending`) must never call `ensure_stale_*`,
`request_provider_verification`, or `run_verification_maintenance`. Background
rechecks use `POST /api/extension/session-verification/ensure-due`
(`trigger_source=scheduled_recheck`). Explicit user checks use
`POST /api/providers/amex/check` (`trigger_source=user_check_now`). Independent
timeout maintenance uses `POST /api/extension/session-verification/maintain`
and the server scheduler (`run_all_verification_maintenance`).

Allowed `trigger_source` values: `user_check_now`, `scheduled_recheck`,
`extension_startup`, `provider_page_observed`, `internal_recovery`, `admin_debug`.
Forbidden: `dashboard_reload`, `account_status_poll`.

This module is the **only** production entry point for an active session/access check.

---

## Approved `provider_session_state` writers

### A. Canonical production writers

| Path | Notes |
|------|-------|
| Access Manager evidence helpers | Active verification + explicit extension evidence |
| Passive definitive evidence via Access Manager | Authenticated session, login page, session API 200 / 401 / 403 |

### B. Compatibility wrappers

These still exist for older imports but **must** call the Access Manager
(they do not call `upsert_provider_session_state` directly):

- `mighty.provider_session_state.record_amex_extension_connected`
- `mighty.provider_session_state.record_amex_extension_needs_login`
- `mighty.provider_session_state.record_extension_login_required`
- `mighty.provider_session_state.record_extension_session_connected`
- `mighty.provider_session_state.record_session_evidence_from_probe`

### C. Debug-only

| Path | Notes |
|------|-------|
| Manual provider access probe | Admin + extension; not a product trigger |
| Bootstrap trace / live session comparison | Admin diagnostics |

### D. Legacy (retained, do not extend)

Marked in code with `LEGACY ACCESS PATH — DO NOT EXTEND`:

| Path | Location |
|------|----------|
| `probeAmexConnectionState` | `extension/background.js` |
| Sync-time `runProviderAccessProbes` | `extension/background.js` |
| Amex FSM session-truth role | `mighty/connection_state.py` |
| Cloud `scrape_amex` access | `scrape.py` |

Scheduled for redirect/removal in Phase 2/3. **Not deleted in Phase 1.**

---

## Guardrails

- New production code must not call `upsert_provider_session_state(...)` outside:
  - `mighty/provider_access_manager.py`
  - `mighty/provider_session_state.py` (storage implementation only)
- Static test: `tests/test_provider_access_manager.py`
- Cached private data alone must not set current session state
- Active verification writes PSS only on definitive `connected` / `signed_out`

---

## Probe providers (same interface)

Amex · Delta · Hilton · Marriott · United (`PROBE_PROVIDERS`)

---

## Phase 1 completion criteria

- [x] `mighty/provider_access_manager.py` exists as thin orchestration boundary
- [x] Stale-session scheduling requests Access Manager
- [x] Extension verification completion returns through Access Manager
- [x] Explicit connected / signed-out evidence routes through Access Manager
- [x] Passive definitive evidence preserved via Access Manager
- [x] Manual probe remains debug-only
- [x] Legacy paths marked, not removed
- [x] Static upsert guardrail test
- [x] Behavior preserved (enqueue / throttle / timeout / API contracts)

---

## Phase 2 / 3 (not in this PR)

- Redirect sync Amex probe + automatic probes into verification queue
- Quarantine / remove `probeAmexConnectionState` session side-effects
- Narrow Amex FSM to onboarding-only
- Stop treating cloud scrape as any form of access check
