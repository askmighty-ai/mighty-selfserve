# User Policy — Trust, Privacy, and Governance

**Status:** Complete (Milestone 12)  
**Related:** [TRUSTED_AGENT_AUTHORIZATION.md](TRUSTED_AGENT_AUTHORIZATION.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md)

---

## User capability

Users can understand, inspect, govern, and control what Mighty knows, what it can do, and why it behaves the way it does.

---

## Objective

One canonical **Policy** model for durable user intent — privacy, approval, execution, monitoring, opportunities, notifications, retention — consumed by Authorization. No parallel settings system.

---

## Ownership

| Concern | Owner |
|---------|--------|
| **Policy** | Durable user intent (`user_policies` + Settings bridge) |
| **Authorization** | Evaluates Policy + Action facts |
| **Execution** | Performs work only after Authorization |
| **Activity / Receipts** | History and inspectable provenance |
| **Attention** | Interruption only — never stores Policy |

---

## Canonical Policy model

```text
UserPolicy
  user_id
  require_human_at_or_above   # informational|routine|consequential|critical
  auto_execute_informational
  auto_execute_routine
  monitor_providers
  suppress_opportunity_kinds[]
  minimal_logging
  delete_raw_after_extract
  retention_days              # null = until account delete
  notify_email / notify_push / notify_ntfy
  alert_expiry_emails
  notification_pref
  provider_overrides{}        # config map; evaluation stays provider-agnostic
  updated_at / version
```

Settings Privacy / Notifications remain the write UX. Writers sync into Policy; readers of Authorization use Policy only.

---

## Evaluation

```text
Action proposal facts + UserPolicy
  → policy_evaluation (pure)
  → AuthorizationDecision + explanation
  → trusted_agent lifecycle
  → receipt carries explanation provenance
```

Conflict resolution (deterministic): **deny > require_human > auto_authorize**.  
Provider overrides apply as config lookups (more restrictive wins), not `if provider ==` branches in shared engines.

---

## Explainability

Every authorization decision includes a human-readable explanation citing Policy fields + facts (expiry, duplicate, consequence level, overrides).

Trace chain: Policy → Authorization → Receipt → Activity.

---

## Modules

| Module | Role |
|--------|------|
| `user_policy` | Policy dataclass + defaults + legacy projection |
| `policy_store` | Durable `user_policies` + sync from `users` |
| `policy_evaluation` | Pure evaluate + explain + conflicts |
| `authorization_policy` | Consumes optional Policy |
| `policy_metrics` | Observability |
| `trusted_agent` | Loads Policy on propose |
