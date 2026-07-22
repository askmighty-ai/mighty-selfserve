# Milestone 6 — Autonomous Recovery

**Status:** Complete  
**Design note:** [ATTENTION_AUTONOMOUS_RECOVERY.md](../ATTENTION_AUTONOMOUS_RECOVERY.md)  
**Module doc:** [RECOVERY_PLANNER.md](../RECOVERY_PLANNER.md)  
**RFC / related:** [AUTHENTICATION_ATTENTION_PLATFORM.md](../AUTHENTICATION_ATTENTION_PLATFORM.md) · [ACCESS_FLOW.md](../ACCESS_FLOW.md)

## User capability

When something breaks, Mighty attempts every safe and appropriate recovery path before asking the user for help.

## Objective

Establish one production recovery flow that converts provider and access failures into autonomous recovery attempts, and produces user attention only when recovery is exhausted or the required step is genuinely human-only.

Build on Provider Access Manager, AuthTruth, and Attention. Consolidate — do not invent a parallel recovery system.

## PRs merged

| PR | Theme |
|----|--------|
| [#146](https://github.com/askmighty-ai/mighty-selfserve/pull/146) | Capability-led roadmap (M6–M12) |
| [#147](https://github.com/askmighty-ai/mighty-selfserve/pull/147) | Design note + living report kickoff |
| *(this PR)* | Recovery Planner, store, executor, supervisor, Attention gate, metrics, tests |

## Architecture changes

- Added pure `recovery_planner` (deterministic capability ranking)
- Added `recovery_store` as sole recovery lifecycle owner (case + attempts)
- Added `recovery_executor` → Access Manager (`internal_recovery`)
- Added `recovery_supervisor` heartbeat (`ENABLE_RECOVERY_SUPERVISOR`)
- Attention auth / trust / access_degraded gated on Recovery `escalated`
- Recovery metrics snapshot (`autonomous_recovery_coverage`, `unexpected_interruption_rate`)

## Architecture Decisions

### AD-M6-1: Invent Recovery Planner above PAM; do not put strategy in Attention

- **Decision:** Add a Recovery Planner + lifecycle store that executes through Access Manager and gates Attention emission on escalation.  
- **Why:** PAM already defers recovery to callers; Attention must remain ranking/presentation only; no planner existed.  
- **Alternatives considered:** Encode retries in AttentionSupervisor; delay AuthTruth `needs_human`; restore full Runtime Control Center as planner.  
- **Long-term impact:** One deterministic recovery owner; AuthTruth stays a pure projection; Attention interrupts only after autonomy is exhausted or human-only.

### AD-M6-2: Suppress auth/trust/access_degraded Attention until case is `escalated`

- **Decision:** Compilers emit those human blockers only when Recovery Store lists the provider as escalated. Active/open recovery and “no case yet” suppress interrupt.  
- **Why:** User interruption is the final capability; AuthTruth may still project `needs_human` while recovery runs.  
- **Alternatives considered:** Withhold `needs_human` inside AuthTruth; allow trust `awaiting_user` immediately.  
- **Long-term impact:** Clear seam — recovery owns time-to-interrupt; Attention owns ranking after escalation.

### AD-M6-3: Supporting ops stay inside capability milestones

- **Decision:** Cutover flag deletion, AuthTruth test reconciliation, and admin dashboards are not standalone product milestones.  
- **Why:** Capability-led roadmap.  
- **Alternatives considered:** Bundle flag hard-delete as M6 success criterion.  
- **Long-term impact:** M6 stayed focused on autonomous recovery.

### AD-M6-4: Fail closed on Attention gate read errors

- **Decision:** If Recovery Store cannot be read, treat `recovery_attention_allowed` as empty (no auth/trust/degraded interrupt).  
- **Why:** Prefer false silence over unexpected interruption when recovery state is unknown; supervisor will heal.  
- **Alternatives considered:** Fail open (emit Attention without escalation).  
- **Long-term impact:** Aligns with “interrupt is last”; monitor recovery.metrics / supervisor errors.

### AD-M6-5: Unavailable capabilities recorded as skipped, not provider-branched policy

- **Decision:** Capability availability is fact flags (`supports_*`); planner skips via attempt history without provider-id switches.  
- **Why:** Shared policy must not branch on provider identity.  
- **Alternatives considered:** Hard-code Amex-only deep probe paths.  
- **Long-term impact:** New providers plug in via capability flags from adapters.

## Final production data flow

```text
PSS / Runtime publications / AuthTruth / TrustSignal
  → Recovery Supervisor (heartbeat)
  → claim RecoveryCase (one active owner per user/provider/root_cause)
  → plan_recovery (pure)
  → recovery_executor → Access Manager (internal_recovery) / bounded_wait / skip
  → attempt history
  → succeeded → close (no Attention)
  → escalated → list_escalated_providers
  → compile_attention_candidates (auth/trust/degraded gated)
  → AttentionState → AttentionView → Home / Worker / Delivery
```

## Recovery lifecycle

| State | Meaning |
|-------|---------|
| `open` | Claimed; ready to plan |
| `running` | Capability execution in flight |
| `waiting` | Bounded backoff |
| `succeeded` | Failure cleared |
| `escalated` | Human interrupt allowed |
| `cancelled` | Closed without escalation |

Capabilities (order): `session_verify` → `silent_reauth` → `account_resync` → `navigation_gap_fill` → `deep_probe` → `bounded_wait` → `ask_human`. Human-only interruptions (`mfa` / `captcha` / `consent` / `unknown_human`) escalate immediately.

## Validation performed

- Inventory of existing failure/recovery/attention paths at kickoff  
- Pure planner golden tests (human-only, order, exhaustion, determinism)  
- Store single-owner + Attention gate semantics  
- Supervisor: start without Attention, MFA escalate → Attention, success clears, executor failure isolation  
- Attention suite updated for recovery gate (215 tests green)

## Tests executed

```text
.venv/bin/pytest tests/test_recovery_*.py tests/test_attention_*.py tests/test_runtime_access_state.py
→ 215 passed
```

## Metrics added

| Metric | Storage |
|--------|---------|
| `autonomous_recovery_coverage` | `recovery_metric_snapshot` (succeeded / terminals) |
| `unexpected_interruption_rate` | escalations not `human_only:*` / escalations |
| Logs | `recovery.succeeded`, `recovery.escalated`, `recovery.metrics` |

## Legacy code removed

- No deletion of `login_truth` / legacy needs_login in this milestone (diagnostic surfaces; not extended). Parallel **interrupt policy** for product Home is gated via Recovery rather than removed wholesale.

## Technical debt

- `silent_reauth` / `navigation_gap_fill` skip until credential/gap actuators exist  
- Managed_runtime repair actuators still thin (bound-wait → escalate)  
- Full Runtime Control Center still absent  
- `test_auth_truth.py` Control Center imports still unresolved (ops)  
- Cutover flag hard-delete still waiting soak criteria  

## Lessons learned

- Gating Attention on escalation (rather than mutating AuthTruth) preserved projection purity and made the recovery seam testable.  
- Pre-recording unavailable capabilities as `skipped` kept the pure planner free of I/O while still producing attempt history.  
- Fail-closed Attention on store errors matches “interrupt last” better than fail-open.

## Recommendation for the next milestone

**Milestone 7 — Automatic Account Discovery and Enrollment**

Promote automatic discovery/enrollment as the next user capability. Keep Runtime focus bridge, cutover hard-delete, and AuthTruth test reconciliation as supporting work inside the milestones they enable (or focused ops PRs), not as product milestones.
