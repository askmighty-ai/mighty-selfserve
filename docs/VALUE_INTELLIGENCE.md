# Value Intelligence

**Status:** Complete (Milestone 10)  
**Related:** [ACCOUNT_SNAPSHOTS.md](ACCOUNT_SNAPSHOTS.md) · [FRESHNESS_CHANGE.md](FRESHNESS_CHANGE.md) · [ATTENTION_COMPILER_BENEFIT.md](ATTENTION_COMPILER_BENEFIT.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md)

---

## User capability

Mighty understands which opportunities are genuinely valuable and computes them as durable opportunity facts. Attention may later decide whether they deserve interruption.

---

## Objective

Convert normalized account data (Account Snapshots) into durable, provider-independent **opportunity facts** — not notifications, not ranking, not marketing.

---

## Non-goals

- A parallel Home recommendation / hero ranker  
- Notifications or delivery  
- Provider-specific branching in shared engines  
- Computing value inside Freshness, Recovery, or Discovery  
- Replacing Attention ranking  

---

## Ownership

| Concern | Owner |
|---------|--------|
| Normalized account fields | Account Snapshots |
| Meaningful field deltas | Change Intelligence (facts about *change*, not value) |
| **Opportunity facts** | **Value Intelligence** |
| Interruption / ranking | Attention (consumes facts later via existing Benefit path) |
| Access repair | Recovery |

Value Intelligence **computes value**. Attention **decides interruption**.

---

## Canonical Opportunity model

```text
Opportunity
  opportunity_id
  user_id / provider
  kind                 # expiring_credit | unused_benefit | …
  field_key / label / value / field_type
  score                # 0–100 from score_opportunity (durable)
  urgency              # urgent | soon | info
  days_left / exp_date
  value_estimate       # optional dollar estimate
  fingerprint          # dedupe key
  lifecycle_state      # discovered | active | consumed | expired | dismissed
  snapshot_id          # provenance
  summary
  created_at / updated_at
```

---

## Value policy (provider-independent)

Policies key off snapshot field `_type` and parsed expiry — not provider id.

| Kind | When |
|------|------|
| `expiring_credit` | cash/travel credit with days_left in window |
| `expiring_certificate` | certificate with days_left in window |
| `unused_benefit` | actionable credit/cert with score ≥ entry threshold |
| `upgrade_opportunity` | certificate whose label indicates upgrade |
| `expiring_points` | points_balance with days_left in window |
| `elite_qualification_risk` | progress_toward near threshold (ratio ≥ config) |
| `duplicated_benefit` | same type+normalized label appears more than once |
| `payment_due` / `renewal` | needs-attention types (durable value-at-risk facts) |

Provider-specific knowledge (program names, scrape hints) stays in adapters / `SOURCE_CAPABILITIES` / optional capability registry — not in the shared policy engine.

---

## Lifecycle

| State | Meaning |
|-------|---------|
| `discovered` | First seen this reconcile |
| `active` | Open and still evidenced by latest snapshot |
| `consumed` | User completed / redeemed (intent from action_items when present) |
| `expired` | Past exp_date or days_left &lt; 0 |
| `dismissed` | User dismissed (preserved across reconcile) |

Reconcile is deterministic: same snapshot fields + same `now` → same fingerprints and states.

---

## Lifecycle flow

```text
Successful Account Snapshot
  → Value Policy (pure candidates)
  → opportunity_store reconcile (dedupe + lifecycle)
  → optional action_items bridge (Attention input compatibility)
  → Attention may compile BenefitSignals later (unchanged ranker)
```

---

## Observability

| Signal | Meaning |
|--------|---------|
| generated | New/updated active opportunities |
| suppressed | Below score threshold / unsupported kind |
| expired | Transitioned to expired |
| duplicates_suppressed | Same fingerprint already current |
| value_at_risk_total | Sum of value estimates for VAR-like kinds |

---

## Modules

| Module | Role |
|--------|------|
| `expiry` | Shared expiry day parsing |
| `value_policy` | Pure candidate computation |
| `opportunity_store` | Durable records + lifecycle |
| `value_intelligence` | Coordinator after snapshot |
| `value_metrics` | Counters |
| `value_capability_registry` | Optional per-provider kind enablement (config) |
