# Milestone 8 — Natural-Session Coverage

**Status:** Complete  
**Design note:** [NATURAL_SESSION.md](../NATURAL_SESSION.md)  
**Related:** [ACCESS_FLOW.md](../ACCESS_FLOW.md)

## User capability

When a user naturally visits a supported provider, Mighty detects the session, validates it, refreshes when freshness policy requires, and quietly keeps the account current.

## Objective

Build a unified Natural Session pipeline connecting browser activity, PAM, Recovery, AuthTruth, AccountState, and Attention — maximizing passive freshness and minimizing interruption.

## PRs merged

| PR | Theme |
|----|--------|
| [#150](https://github.com/askmighty-ai/mighty-selfserve/pull/150) | Design note, Natural Session policy/coordinator, ensure-due + observe wire-up, extension emitter, tests |

## Architecture changes

- Added pure `natural_session_policy` (skip / enqueue / defer / unsupported)
- Added `natural_session` coordinator executing only through PAM
- Ensure-due routes through Natural Session (Recovery deferral)
- New `POST /api/extension/natural-session/observe` for `provider_page_observed`
- Extension emits observe on known provider account navigations
- Natural Session metrics snapshot + logs
- `has_active_recovery_for_provider` Recovery Store helper

## Architecture Decisions

### AD-M8-1: Natural Session decides; PAM executes; Recovery owns failures

- **Decision:** Coordinator above PAM chooses skip / enqueue / defer-recovery.  
- **Why:** Unify passive coverage without a parallel sync system.  
- **Alternatives considered:** Extension-only policy; Attention-scheduled sessions.  
- **Long-term impact:** One passive-coverage seam.

### AD-M8-2: Defer to Recovery when a case is active

- **Decision:** Active Recovery case for a provider blocks Natural Session enqueue.  
- **Why:** No competing owners.  
- **Alternatives considered:** Parallel `scheduled_recheck` during recovery.  
- **Long-term impact:** Measurable `defer_recovery` handoffs.

### AD-M8-3: Reuse existing freshness policy

- **Decision:** Use `session_state_needs_verification` unchanged.  
- **Why:** Single scheduling freshness clock.  
- **Alternatives considered:** AuthTruth 24h TTL as schedule gate.  
- **Long-term impact:** Deterministic skip vs enqueue.

### AD-M8-4: Natural Session does not mutate AuthTruth or rank Attention

- **Decision:** Refresh improves Attention only via PSS → AuthTruth → Recovery close → escalate gate.  
- **Why:** Architectural constraints.  
- **Alternatives considered:** Direct overlay clears on observe.  
- **Long-term impact:** Axes remain clean.

## Final production data flow

```text
Natural browse (supplement domains) | keepalive ensure-due
  → Natural Session policy
       unsupported | defer_recovery | skip_fresh | enqueue_verify
  → PAM ensure_if_stale (trigger_source=provider_page_observed|scheduled_recheck|…)
  → session_verification → extension runSessionVerification → probe → PSS
  → AuthTruth / AccountState (existing projections)
  → Recovery may succeed/close; Attention only if escalated
```

## Natural Session lifecycle

| Action | Meaning |
|--------|---------|
| `unsupported` | No verification entry URL capability |
| `defer_recovery` | Active Recovery case owns the provider |
| `skip_fresh` | Evidence within freshness / revalidation policy |
| `enqueue_verify` | PAM ensure_if_stale with natural trigger |

## Validation performed

- Kickoff inventory of session/sync/recovery paths  
- Pure policy golden tests  
- Coordinator: skip fresh, enqueue stale, defer recovery, unsupported, failure isolation  
- Ensure-due still records `scheduled_recheck`  
- Boundary audits updated for Natural Session ownership  

## Tests executed

```text
.venv/bin/pytest tests/test_natural_session*.py \
  tests/test_read_command_verification_boundary.py::test_scheduled_trigger_records_scheduled_recheck \
  tests/test_read_command_verification_boundary.py::test_ensure_stale_callers_classified
→ green
```

## Metrics

| Signal | Where |
|--------|--------|
| detections / enqueued / skipped_fresh / deferred_recovery | `natural_session_metric_snapshot` |
| `natural_session.enqueue` / `.skip_fresh` / `.defer_recovery` | logs |
| `passive_coverage_rate` | (skipped+enqueued) / capable detections |

## Technical debt

- Hourly sync alarm / legacy probes still exist (not extended; demotion deferred)  
- Verification entry URLs still Amex-only for enqueue capability  
- Worker “session glance” UI not added (Attention Worker remains install/reachability)  
- AuthTruth 24h stale vs scheduling 120s/15m clocks remain intentionally split  

## Lessons learned

- Emitting the already-defined `provider_page_observed` trigger was higher leverage than inventing a second scheduler.  
- Recovery deferral at the Natural Session boundary removed the ensure-due race without changing Recovery policy.

## Recommendation for the next milestone

**Milestone 9 — Freshness and Change Intelligence**

Surface what changed and that data is current without status-dashboard noise. Build on Natural Session passive coverage + AccountState/history; keep sync-marathon demotion as supporting work inside that capability if needed.
