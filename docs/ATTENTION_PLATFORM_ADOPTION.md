# Attention Platform Adoption — Milestone 3 Design Note

**Status:** In progress  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) P3/P4  
**Depends on:** Milestone 2 ([ATTENTION_ENGINE.md](ATTENTION_ENGINE.md))

## Objective

Make `AttentionState` / `AttentionView` the single source of truth for attention decisions on Home and Worker, with shadow validation, agreement metrics, safe rollback, then deletion of superseded Home-local ranking.

## Current state (origin/main at M3 start)

| Path | Behavior |
|------|----------|
| Attention Engine | `read_attention` → loaders → compile → overlays → `AttentionState` |
| Home | Calls `resolve_home_state` (six-state ranking) then renders Truth Dashboard from `CapabilityView`; shadow records engine output only |
| Worker | `GET /api/account-status` builds per-account status + `access_loop` summary; popup ranks interruption from `needs_sign_in` / `needs_attention` counts; shadow records engine output only |
| AttentionView | Not implemented |
| Agreement metrics | Not implemented |

## Attention decision paths to retire

1. **Home** — `resolve_home_state` priority: LOGIN → EMPTY → WAITING → UPDATE → RECOMMENDATION → ALL_CLEAR (`mighty/home_state.py`). Hero selection / recommendation ranking is attention policy and must leave Home.
2. **Worker** — Popup `_summaryNeedsUserAction` / `_dotForLoop` deriving interrupt urgency from account-status counts (`extension/popup.js`). Worker must consume the same attention result.

**Not attention (keep):** Enrollment Empty UX, Truth/Capability access diagnostics, AccountState per-account rows, Control Center / AccessState.

## Target data flow

```text
DB facts
  → Attention Engine → AttentionState
  → AttentionView(surface)  # window + resolve copy/CTA URLs; no ranking
  → Home render / Worker popup / account-status.attention
```

Failure rule: engine/view errors never break Home or Worker; fall back to non-attention presentation until cutover confidence is adequate, then degrade to silence/all-clear rather than inventing a second policy.

## Rollout sequence

| Step | PR theme | Default | Rollback |
|------|----------|---------|----------|
| 1 | `AttentionView` + copy/CTA resolution | n/a | revert PR |
| 2 | Shadow comparison + agreement metrics table | always record | stop writes / revert |
| 3 | Home cutover behind `ATTENTION_CUTOVER_HOME` | `shadow` | set `off` or `shadow` |
| 4 | Worker cutover behind `ATTENTION_CUTOVER_WORKER` | `shadow` | set `off` or `shadow` |
| 5 | Defaults → `on`; remove Home ranking + Worker count-based interrupt policy | `on` | env `shadow`/`off` briefly, then delete flags |
| 6 | Docs, obsolete flags/tests, green suite on main | — | — |

Flag values: `off` | `shadow` | `on`.

## Agreement taxonomy (minimum)

Recorded per user×surface when shadow/cutover runs:

| Code | Meaning |
|------|---------|
| `exact_agreement` | Same active/silent class and same primary identity (or both silent) |
| `same_class_diff_primary` | Both active same attention class, different primary id/provider |
| `old_silent_new_active` | Legacy silent; Attention has visible ranks 1–5 |
| `old_active_new_silent` | Legacy active; Attention silent / no ranks 1–5 |
| `both_active_diff_provider_or_reason` | Both active, different provider or reason |
| `platform_failure` | Engine timeout/exception |

Legacy signal for comparison (read-only probe, not a second permanent policy):

- Home: `HomeState.LOGIN` or capability `action_required` with sign-in → active auth; else silent for attention compare.
- Worker: `needs_login_count > 0` (or access_loop `needs_sign_in > 0`) → active auth; else silent.

Prioritize investigation: false silence, unnecessary interruption, unstable primary, provider divergence, stale/missing projection.

## Architectural invariants (unchanged)

- AttentionState is SSoT for attention decisions.
- Home renders; does not rank.
- Worker consumes the same result; no separate policy.
- Domain compilers own domain logic; gather only concatenates.
- Engine only composes loaders/compilers/overlays/selection.
- No provider-specific branching in shared ranking policy.
- Attention failures must not take down Home/Worker.

## Non-goals (this milestone)

No notifications, no new attention producers, no new UX concepts, no Recovery Platform work, no push cutover.
