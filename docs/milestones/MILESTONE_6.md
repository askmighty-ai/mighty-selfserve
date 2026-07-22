# Milestone 6 — Autonomous Recovery

**Status:** In progress  
**Design note:** [ATTENTION_AUTONOMOUS_RECOVERY.md](../ATTENTION_AUTONOMOUS_RECOVERY.md)  
**RFC / related:** [AUTHENTICATION_ATTENTION_PLATFORM.md](../AUTHENTICATION_ATTENTION_PLATFORM.md) · [ACCESS_FLOW.md](../ACCESS_FLOW.md)

## User capability

When something breaks, Mighty attempts every safe and appropriate recovery path before asking the user for help.

## Objective

Establish one production recovery flow that converts provider and access failures into autonomous recovery attempts, and produces user attention only when recovery is exhausted or the required step is genuinely human-only.

Build on Provider Access Manager, AuthTruth, and Attention. Consolidate — do not invent a parallel recovery system.

## PRs merged

| PR | Theme |
|----|--------|
| *(pending)* | Design note + living report + roadmap current |

## Architecture changes

*(updated as PRs merge)*

## Architecture Decisions

### AD-M6-1: Invent Recovery Planner above PAM; do not put strategy in Attention

- **Decision:** Add a Recovery Planner + lifecycle store that executes through Access Manager and gates Attention emission on escalation.  
- **Why:** PAM already defers recovery to callers; Attention must remain ranking/presentation only; no planner exists today.  
- **Alternatives considered:** Encode recovery retries inside AttentionSupervisor; teach AuthTruth to delay `needs_human`; restore full Runtime Control Center as planner.  
- **Long-term impact:** One deterministic recovery owner; AuthTruth stays a pure projection; Attention interrupts only after autonomy is exhausted or human-only.

### AD-M6-2: Suppress auth/trust/access_degraded Attention until case is `escalated`

- **Decision:** Existing compilers emit human blockers only when Recovery Store status for that provider is `escalated`. Active/open recovery suppresses interrupt.  
- **Why:** User interruption is the final capability, not the default; AuthTruth may still project `needs_human` from evidence while recovery runs.  
- **Alternatives considered:** Change AuthTruth to withhold `needs_human` until escalation (couples projection to recovery); allow trust `awaiting_user` immediately (violates capability).  
- **Long-term impact:** Clear seam — recovery owns time-to-interrupt; Attention owns ranking after escalation.

### AD-M6-3: Supporting ops stay inside M6, not new milestones

- **Decision:** Cutover flag deletion, AuthTruth test reconciliation, and admin dashboards are not M6 scope unless required for the recovery capability; track in technical debt / ops.  
- **Why:** Capability-led roadmap.  
- **Alternatives considered:** Bundle flag hard-delete as M6 success criterion.  
- **Long-term impact:** M6 stays focused on autonomous recovery.

## Final production data flow

*(filled at completion; target in design note)*

## Recovery lifecycle

*(filled at completion; target in design note)*

## Validation performed

- Inventory of failure/recovery/attention paths at kickoff (see design note)

## Tests executed

*(updated as suites land)*

## Metrics added

*(pending)*

## Technical debt

*(updated as discovered)*

## Lessons learned

*(at completion)*

## Recommendation for the next milestone

*(at completion — Milestone 7 Automatic Account Discovery and Enrollment)*
