# Milestone 5 — Autonomous Attention

**Status:** Complete  
**Design note:** [ATTENTION_AUTONOMOUS.md](../ATTENTION_AUTONOMOUS.md)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](../AUTHENTICATION_ATTENTION_PLATFORM.md)

## Objective

Strengthen the production Attention Platform by improving trust, delivery, observability, and operational robustness without changing the core architecture established in Milestones 1–4.

This milestone makes the platform production-ready rather than architecturally different.

## PRs merged

| PR | Theme |
|----|--------|
| [#138](https://github.com/askmighty-ai/mighty-selfserve/pull/138) | Design note |
| [#139](https://github.com/askmighty-ai/mighty-selfserve/pull/139) | Runtime Trust producer + thin `runtime_access_state` |
| [#140](https://github.com/askmighty-ai/mighty-selfserve/pull/140) | Supervisor reopen + delivery retry / SLA breach |
| [#141](https://github.com/askmighty-ai/mighty-selfserve/pull/141) | Production metrics |
| [#142](https://github.com/askmighty-ai/mighty-selfserve/pull/142) | Opt-in legacy compare + cutover retirement criteria |

## Architecture changes

Strengthened only — no ranking/View/consumer redesign.

- Rank-1 `trust` producer from Runtime publications (mutual exclusion with `needs_human` → auth)
- Thin `runtime_access_state` store for AuthTruth/Attention (not Control Center)
- Supervisor logs `attention.reopened` on in_flight timeout
- Delivery retries failed push with backoff/caps; logs `attention.sla_breached`
- Product metrics on supervisor heartbeat
- Legacy compare probes opt-in via `ATTENTION_SHADOW_COMPARE`

## Final production data flow

```text
runtime_access_state / PSS / actions / action_items / users.extension_* / account_state
  → loaders (incl. TrustSignal)
  → compile_attention_candidates
  → compose_attention
  → AttentionState → AttentionView → Home / Worker
  → AttentionDelivery (retry + receipts)
  → AttentionSupervisor (timeout / GC / reopen)
  → attention_metrics snapshot (coverage, false silence/interrupt, delivery SLA)
```

## Validation performed

- Design note recorded emit rules, mutual exclusion, metrics definitions, cutover retirement criteria
- Trust producer golden tests + engine replay for managed_runtime `awaiting_user`
- Supervisor reopen + delivery retry/backoff tested
- Metrics computed offline from fixtures (not on GET hot path)
- Cutover: AttentionView works with `legacy=None` when compare is off

## Tests executed

- `pytest tests/test_attention*.py tests/test_runtime_access_state.py` at M5 close → **225 passed**
- Notable: `tests/test_attention_compiler_trust.py`, `tests/test_runtime_access_state.py`, `tests/test_attention_metrics.py`, cutover opt-in coverage

## Metrics added

| Metric | Definition / storage |
|--------|----------------------|
| `autonomous_coverage` | Healthy managed_runtime accounts without trust/auth_blocker / eligible |
| `false_silence_rate` | Undelivered blocker primaries aged ≥ 60s / push-eligible blockers |
| `false_interruption_rate` | Unexpected visible ranks 1–4 / visible blockers |
| `delivery_sla_rate` | Delivered within 60s of first attempt / attempted receipts |
| Persistence | `attention_metric_snapshot` (scope=`global`) |
| Logs | `attention.reopened`, `attention.sla_breached`, `attention.metrics …` |

See [ATTENTION_METRICS.md](../ATTENTION_METRICS.md).

## Technical debt

- Full Runtime focus bridge still gated (no Runtime control API auth yet)
- Cutover env flags retained until 7-day soak criteria met ([ATTENTION_CUTOVER_RETIREMENT.md](../ATTENTION_CUTOVER_RETIREMENT.md))
- `test_auth_truth.py` still imports fuller publication helpers beyond the thin store
- Unexpected-human-minutes is a rate of unexpected blockers, not a time-series yet

## Lessons learned

- AuthTruth’s soft-import made a thin `runtime_access_state` the right unlock without restoring Control Center
- Making legacy compare opt-in removed hot-path probe cost while keeping rollback/soak available
- Keeping metrics on the supervisor heartbeat preserved the “never block Home/Worker” invariant

## Recommendation for the next milestone

Milestone 6 candidates:

1. Runtime focus CTA bridge after Runtime API auth exists  
2. Hard-delete cutover flags after soak criteria pass  
3. Admin metrics dashboard over `attention_metric_snapshot`  
4. Unexpected-human-minutes time series  
5. Reconcile/expand AuthTruth tests against the thin Runtime store  
