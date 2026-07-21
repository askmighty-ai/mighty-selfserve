# Milestone 4 — Intelligent Attention

**Status:** Complete  
**Design note:** [ATTENTION_INTELLIGENT.md](../ATTENTION_INTELLIGENT.md)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](../AUTHENTICATION_ATTENTION_PLATFORM.md)

## Objective

Expand the Attention Platform with new producers and delivery capabilities while preserving the architecture established in Milestones 1–3.

This milestone extends the platform. It does not redesign it.

## PRs merged

| PR | Theme |
|----|--------|
| [#131](https://github.com/askmighty-ai/mighty-selfserve/pull/131) | Design note |
| [#132](https://github.com/askmighty-ai/mighty-selfserve/pull/132) | `data_gap` producer |
| [#133](https://github.com/askmighty-ai/mighty-selfserve/pull/133) | Worker → `system` |
| [#134](https://github.com/askmighty-ai/mighty-selfserve/pull/134) | Benefit → `value_at_risk` / `opportunity` |
| [#135](https://github.com/askmighty-ai/mighty-selfserve/pull/135) | AttentionSupervisor |
| [#136](https://github.com/askmighty-ai/mighty-selfserve/pull/136) | Delivery + receipts + HTTP commands |
| [#137](https://github.com/askmighty-ai/mighty-selfserve/pull/137) | Replay/e2e + milestone docs |

## Architecture changes

Extended the existing platform only — no ranking/View/consumer redesign.

- New producers plug into `compile_attention_candidates`
- New loaders feed the engine
- Supervisor owns `in_flight` timeout + orphan GC
- Delivery targets `AttentionState.primary` only
- HTTP routes are thin Store adapters

## Final production data flow

```text
account_state / PSS / actions / action_items / users.extension_*
  → attention_loaders
  → compile_attention_candidates (gather only)
  → compose_attention (overlays + ranking)
  → AttentionState
  → AttentionView(surface) → Home / Worker
  → AttentionDelivery (primary push + receipts)
  → AttentionSupervisor (30m in_flight clear + orphan GC)
```

## Validation performed

- Design note reviewed against architectural invariants before producer PRs
- Each producer PR included golden/unit tests and engine wiring
- Multi-producer ranking + CTA/timeout/delivery/silence lifecycle covered in replay tests
- Self-review before each merge: focused PR, tests green, docs updated, no duplicate policy

## Tests executed

- `pytest tests/test_attention*.py` at M4 close → **209 passed** (at time of completion; suite grew in M5)
- Notable: `tests/test_attention_m4_replay.py`, compiler suites for data_gap / worker / benefit, supervisor, delivery, commands

## Metrics added

- Delivery receipts table (`attention_delivery_receipt`) with `delivered` / `failed` / `skipped`
- Supervisor/delivery heartbeat logs (`in_flight_cleared`, `orphans_deleted`, `delivery_attempts`)
- Structured `attention.delivered` / `attention.delivery_failed` log lines

## Technical debt

- No first-class product analytics events table (logs + receipts only) — addressed partially in M5 metrics
- Opportunity source is `action_items` only (not partnerships / `_generate_opportunities`)
- Cutover shadow/compare flags retained from M3
- Benefit View copy still relatively generic (improved slightly in hardening)
- Trust producer deferred to M5

## Lessons learned

- Reusing AccountState once for AuthTruth + data_gap + worker enrollment count kept the engine thin
- Loader “unknown → no signal” for missing `users` rows avoided inventing SYSTEM blockers in fixtures
- Keeping delivery off the account-status GET path preserved the failure-isolation invariant

## Recommendation for the next milestone

Milestone 5 — Autonomous Attention:

1. Trust producer + Runtime focus CTA (RFC P5), gated on Runtime API auth  
2. Product metrics: false silence, snooze return, delivery SLA  
3. Activity surface filter for authorize items  
4. Retire M3 shadow/compare once production confidence is permanent  
5. Optional: richer benefit copy from `label`/`value`/`days_left` in AttentionView only  
