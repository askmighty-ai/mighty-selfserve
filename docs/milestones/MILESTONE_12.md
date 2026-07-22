# Milestone 12 — Trust, Privacy, and User Governance

**Status:** Complete  
**Design note:** [USER_POLICY.md](../USER_POLICY.md)  
**Related:** [TRUSTED_AGENT_AUTHORIZATION.md](../TRUSTED_AGENT_AUTHORIZATION.md) · [PRODUCT_ARCHITECTURE.md](../PRODUCT_ARCHITECTURE.md)

## User capability

Users can understand, inspect, govern, and control what Mighty knows, what it can do, and why it behaves the way it does.

## Objective

Build a unified governance layer that exposes durable user intent, privacy preferences, execution policies, and decision provenance — without introducing parallel policy or settings systems.

## PRs merged

| PR | Theme |
|----|--------|
| [#158](https://github.com/askmighty-ai/mighty-selfserve/pull/158) | User Policy model/store/evaluation, Authorization integration, Settings bridge, API, tests, living report |

## Architecture changes

- Added canonical `UserPolicy` model + defaults + legacy `users` projection
- Added durable `user_policies` store with Settings sync (no parallel settings)
- Added pure `policy_evaluation` with explanations and conflict resolution
- Authorization consumes optional Policy; explanations stored on Actions + Receipts
- `GET/PATCH /api/policy` for inspect/update governance knobs
- Settings privacy/notifications/alert-expiry sync into Policy
- Value Intelligence honors opportunity suppression + provider monitoring Policy
- Added `policy_metrics` (explainability coverage, overrides, outcomes)

## Architecture Decisions

### AD-M12-1: Policy projects existing Settings — no parallel settings system

- **Decision:** `user_policies` is the governance SSoT; Settings writers update `users` and sync Policy.  
- **Why:** Required constraint; avoid dual knobs.  
- **Alternatives considered:** Replace `users` columns immediately.  
- **Long-term impact:** Gradual migration with live Settings UX.

### AD-M12-2: Authorization evaluates Policy + facts

- **Decision:** `evaluate_authorization_policy(..., user_policy=)` delegates to Policy evaluation when present.  
- **Why:** Every auth decision explainable from Policy + facts.  
- **Alternatives considered:** Keep consequence-only forever.  
- **Long-term impact:** User-governed auto-execute ceilings.

### AD-M12-3: Conflict resolution is deny > require_human > auto

- **Decision:** Deterministic precedence; document in explanations.  
- **Why:** Safety over convenience when Policy knobs conflict.  
- **Alternatives considered:** Auto wins when explicitly set.  
- **Long-term impact:** Predictable governance.

### AD-M12-4: Provider overrides are config lookups

- **Decision:** `provider_overrides` map consulted by key; no `if provider ==` in shared engines.  
- **Why:** Provider-independent evaluation invariant.  
- **Alternatives considered:** Provider-specific policy modules.  
- **Long-term impact:** Safe multi-provider governance.

### AD-M12-5: Attention does not own Policy

- **Decision:** Policy is Settings/governance; Attention still only interrupts awaiting Actions.  
- **Why:** Ownership constraints.  
- **Alternatives considered:** Policy-driven Attention ranking.  
- **Long-term impact:** Axes remain clean.

### AD-M12-6: Provenance chain Policy → Auth → Receipt → Activity

- **Decision:** Persist `decision_explanation` on Actions; copy into receipt detail.  
- **Why:** Inspectable “why” for every execution.  
- **Alternatives considered:** Logs only.  
- **Long-term impact:** Trust UI can render explanations.

## Final production data flow

```text
Settings /api/policy
  → user_policies (+ users column sync)
  → Agent proposes Action
  → load UserPolicy
  → policy_evaluation → AuthorizationDecision + explanation
  → actions lifecycle (+ decision_explanation)
  → Attention interrupt if awaiting
  → execute → receipt (policy_explanation in detail)
  → Activity history
```

## Policy model

`UserPolicy`: approval threshold, auto-execute flags, monitoring, opportunity suppression, privacy, retention, notifications, provider_overrides.

## Governance model

| Layer | Role |
|-------|------|
| Policy | Durable user intent |
| Authorization | Evaluates Policy + facts |
| Execution | Work after authorization |
| Activity / Receipts | History + provenance |
| Attention | Interruption only |

## Validation performed

- Inventory of settings, privacy audit, auth, dismissals  
- Pure Policy evaluation + conflict + override + replay tests  
- Store sync from/to users  
- Authorization-by-policy  
- E2E policy → auto-authorize → execute → receipt explanation  
- E2E policy → require human → decide  
- Explainability coverage metrics  
- M11 auth regression green  

## Tests executed

```text
.venv/bin/pytest tests/test_user_policy.py \
  tests/test_authorization_policy.py \
  tests/test_trusted_agent.py \
  tests/test_value_intelligence.py \
  tests/test_value_policy.py
→ green
```

## Metrics

| Signal | Where |
|--------|--------|
| evaluations / require_human / auto / deny | `policy_metric_snapshot` |
| overrides / suppressed_executions / conflicts | counters |
| explainability_coverage | explained+refs / evaluations |

## Technical debt

- `approved_domains` still unwired (Policy provider_overrides is the governance path; domain table demotion deferred)  
- Retention enforcement job not implemented (`retention_days` stored only)  
- Opportunity dismiss HTTP API still unwired (Policy suppress kinds works at reconcile)  
- Privacy audit log not yet unified with Action receipts in one UI  
- Extension still uses hard-coded domain lists for capture  

## Lessons learned

- Bridging Settings into Policy beat inventing a second preferences surface.  
- Putting explanations on Actions/Receipts made “why” inspectable without Attention changes.  
- Explicit conflict precedence removed ambiguity when auto-execute and thresholds collide.

## Recommendations for post-roadmap evolution

1. Enforce `retention_days` with a periodic purge job + audit events.  
2. Unify Privacy audit + Action receipts into one inspectable Trust surface.  
3. Wire extension capture allowlists to Policy / resurrect or delete `approved_domains`.  
4. Expose Opportunity dismiss API and bridge Attention durable dismiss.  
5. Customer-facing Policy explanation UI on Activity detail cards.  
6. Consider signed Policy version pins on receipts for stronger non-repudiation.
