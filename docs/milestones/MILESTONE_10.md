# Milestone 10 — Value Intelligence

**Status:** Complete  
**Design note:** [VALUE_INTELLIGENCE.md](../VALUE_INTELLIGENCE.md)  
**Related:** [ACCOUNT_SNAPSHOTS.md](../ACCOUNT_SNAPSHOTS.md) · [ATTENTION_COMPILER_BENEFIT.md](../ATTENTION_COMPILER_BENEFIT.md)

## User capability

Mighty understands which opportunities are genuinely valuable to the user and computes them as durable opportunity facts. Attention may later decide whether those opportunities deserve interruption.

## Objective

Build a provider-independent Value Intelligence pipeline that converts normalized account data into durable opportunity facts — not notifications, not ranking, not marketing.

## PRs merged

| PR | Theme |
|----|--------|
| [#154](https://github.com/askmighty-ai/mighty-selfserve/pull/154) | Design note, value policy/store/coordinator/metrics, snapshot wire-up, tests, living report |

## Architecture changes

- Added shared `expiry.parse_expiry_days` (Action Center delegates to it)
- Added pure `value_policy` (OpportunityCandidate computation)
- Added `value_capability_registry` (config-only kind enablement)
- Added durable `account_opportunities` store with lifecycle
- Added `value_intelligence` coordinator after successful snapshot persist
- Added `value_metrics` snapshot counters
- Snapshot create path reconciles opportunities (failure-isolated)

## Architecture Decisions

### AD-M10-1: Opportunities are facts; Attention remains the only ranker

- **Decision:** Persist `account_opportunities`; do not emit AttentionItems from Value Intelligence.  
- **Why:** Required separation — compute value vs decide interruption.  
- **Alternatives considered:** Promote opportunities directly into Home recommendation ranking.  
- **Long-term impact:** Benefit Attention can later load from opportunities without a second ranker.

### AD-M10-2: Snapshots are the input; no parallel recommendation engine

- **Decision:** Compute from Account Snapshot `normalized_fields`.  
- **Why:** Consolidated with M9 change foundation; avoids `_generate_opportunities` as SSoT.  
- **Alternatives considered:** Keep only `action_items` writer as value owner.  
- **Long-term impact:** One normalized field plane feeds change + value.

### AD-M10-3: Provider-independent kinds; registry for enablement only

- **Decision:** Policy keys off field `_type` + expiry + thresholds; registry only enables kinds.  
- **Why:** Shared engines must not branch on provider id.  
- **Alternatives considered:** Provider-specific scoring inside value_policy.  
- **Long-term impact:** New providers inherit kinds via classification + config.

### AD-M10-4: Lifecycle preserves user intent

- **Decision:** Reconcile expires missing open rows; never resurrect `dismissed` / `consumed`.  
- **Why:** Matches Action Center dismiss semantics.  
- **Alternatives considered:** Soft-delete only; reset on every sync.  
- **Long-term impact:** Stable quiet behavior under re-extract.

### AD-M10-5: Reuse score_opportunity; persist score on the fact

- **Decision:** Call existing `score_opportunity` / urgency helpers; store score on the row.  
- **Why:** Consolidate scoring; avoid a second score engine.  
- **Alternatives considered:** New value-only score.  
- **Long-term impact:** Action Center and Value facts share urgency math.

### AD-M10-6: Fingerprint dedupe per provider

- **Decision:** UNIQUE `(user_id, provider, fingerprint)` where fingerprint = provider|kind|field_key|exp_date.  
- **Why:** Required duplicate suppression with deterministic replay.  
- **Alternatives considered:** field_key-only uniqueness.  
- **Long-term impact:** Same opportunity updates in place across snapshots.

## Final production data flow

```text
Successful extraction
  → Account Snapshot persist
  → Freshness/Change observe (M9)
  → Value Policy (pure candidates from normalized_fields)
  → account_opportunities reconcile (lifecycle + dedupe)
  → Attention unchanged (may later load BenefitSignals from opportunities)
```

## Opportunity model

Durable `OpportunityRecord`: kind, field identity, score, urgency, expiry, value_estimate, fingerprint, lifecycle_state, snapshot provenance, summary.

Kinds: `expiring_credit`, `unused_benefit`, `expiring_certificate`, `elite_qualification_risk`, `upgrade_opportunity`, `expiring_points`, `duplicated_benefit`, `payment_due`, `renewal`.

## Value policy model

Provider-independent rules over field type + parsed expiry + score threshold + progress ratio. Capability registry enables kinds per provider without embedding provider branches in the engine.

## Validation performed

- Inventory of action_items, Benefit Attention, classify/scoring, advisors, snapshots  
- Pure policy golden tests (credits, certs, upgrade, elite risk, duplicates, replay)  
- Lifecycle: expire missing, preserve dismiss, fingerprint upsert  
- E2E snapshot → opportunities  
- Failure isolation  
- Attention independence (no new ranker)  
- Provider capability config  
- Snapshot regression suite green  

## Tests executed

```text
.venv/bin/pytest tests/test_value_policy.py \
  tests/test_value_intelligence.py \
  tests/test_account_snapshots.py \
  tests/test_freshness_change.py
→ green
```

## Metrics

| Signal | Where |
|--------|--------|
| generated / suppressed / expired | `value_metric_snapshot` + logs |
| duplicates_suppressed | reconcile counters |
| active | open opportunity count |
| value_at_risk_total | sum of estimates for VAR-like kinds |

## Technical debt

- `action_items` still populated separately on sync (bridge to load BenefitSignals from opportunities deferred)  
- `_generate_opportunities` / benefit_advisor remain parallel ephemeral paths (demotion deferred)  
- Elite risk uses progress ratio only — program-specific thresholds stay in advisors/registry for later  
- No customer UI surface for opportunity list yet (Attention Recommendation remains existing path)  
- Consumed state not yet auto-wired from action_items `completed_at`  

## Lessons learned

- Computing value from the same snapshot plane as change kept axes clean.  
- Persisting score on the fact removes the “urgency-only” amnesia of action_items.  
- Keeping Attention untouched prevented a second recommendation engine at the exact moment value facts landed.

## Recommendation for the next milestone

**Milestone 11 — Trusted Agent Authorization**

Agents act only with verified, inspectable human approval. Value Intelligence facts (and Attention) can inform *what* is worth acting on; authorization must own *whether* an agent may act — without collapsing value computation into agent permissions.
