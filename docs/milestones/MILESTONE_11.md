# Milestone 11 — Trusted Agent Authorization

**Status:** Complete  
**Design note:** [TRUSTED_AGENT_AUTHORIZATION.md](../TRUSTED_AGENT_AUTHORIZATION.md)  
**Related:** [ATTENTION_COMPILER_AUTHORIZE.md](../ATTENTION_COMPILER_AUTHORIZE.md) · [PRODUCT_ARCHITECTURE.md](../PRODUCT_ARCHITECTURE.md)

## User capability

Mighty becomes the trusted execution layer for AI agents by introducing a durable Action model, deterministic authorization policy, auditable execution, and verifiable receipts.

## Objective

Build a provider-independent authorization and execution pipeline that transforms proposed agent actions into durable Action records, routes them through authorization policy, executes only appropriately authorized actions, and records immutable receipts — establishing the trust boundary without building agent workflows.

## PRs merged

| PR | Theme |
|----|--------|
| [#156](https://github.com/askmighty-ai/mighty-selfserve/pull/156) | Design note, auth policy/store/receipts/coordinator, API wire-up, Attention/Activity integration, tests |

## Architecture changes

- Extended `actions` with lifecycle, agent_id, fingerprint, proposal_hash, auth_channel
- Added pure `authorization_policy` (require human / auto / deny)
- Added `agent_capability_registry` (executable action types)
- Added immutable `action_execution_receipts`
- Added `trusted_agent` coordinator (propose / decide / execute)
- Added `agent_auth_metrics`
- Wired `/api/authorize`, `/api/decide`, `/api/execute`, `/api/record`, `/api/log-decision`, approve token
- Attention loader emits for awaiting authorization; Activity badges cover lifecycle

## Architecture Decisions

### AD-M11-1: Extend `actions`; do not invent a parallel approval system

- **Decision:** Canonical lifecycle lives on `actions`; receipts are an append-only child table.  
- **Why:** Activity + Attention already treat `actions` as the authorize store (RFC D5).  
- **Alternatives considered:** New `agent_approvals` ledger.  
- **Long-term impact:** One approval surface remains Activity.

### AD-M11-2: Authorization policy is consequence-level based

- **Decision:** Human required for critical/consequential/routine; informational/record-only auto-authorize.  
- **Why:** Provider-independent trust decisions.  
- **Alternatives considered:** Provider-specific approval rules in shared engine.  
- **Long-term impact:** Safe default for consequential agent work.

### AD-M11-3: Immutable receipts with integrity hash

- **Decision:** Append-only receipts with `proposal_hash` + `receipt_hash`; UNIQUE(action_id, attempt).  
- **Why:** Verifiable audit; idempotent execution.  
- **Alternatives considered:** Mutable `outcome` column only.  
- **Long-term impact:** Inspectable trust trail for agents.

### AD-M11-4: Attention interrupts; does not authorize

- **Decision:** Keep `AuthorizeRow` → `agent_authorization`; clear when lifecycle leaves awaiting.  
- **Why:** No second prioritization system.  
- **Alternatives considered:** Approval inside AttentionStore.  
- **Long-term impact:** Axes remain clean.

### AD-M11-5: Recovery ASK_HUMAN stays separate

- **Decision:** Login/human-for-access remains Recovery; agent authorize remains `actions`.  
- **Why:** Different trust problems.  
- **Alternatives considered:** Unify all human gates.  
- **Long-term impact:** Avoids conflating session repair with agent execution.

### AD-M11-6: Default executor is record-only

- **Decision:** Shared execute path writes receipts; provider adapters inject later.  
- **Why:** Trust boundary ships before provider booking adapters.  
- **Alternatives considered:** Block milestone on Amex booking adapter.  
- **Long-term impact:** Clear seam for M12+ execution capabilities.

## Final production data flow

```text
Agent proposes Action
  → authorization_policy
  → actions lifecycle (awaiting | authorized | denied)
  → Attention agent_authorization while awaiting
  → Human decides (Activity / token / chat API)
  → execute (idempotent; adapter-capable)
  → action_execution_receipts (immutable)
  → Attention clears; Activity shows history
```

## Action model

Lifecycle: proposed · awaiting_authorization · authorized · denied · executing · completed · failed · cancelled · expired.  
Legacy status synced for Activity (`pending` / `approved` / …).

## Authorization model

Pure `evaluate_authorization_policy` over consequence level, expiry, duplicate fingerprint, record-only flag, and executable capability.

## Receipt model

`action_execution_receipts`: action_id, agent_id, authorization decision/time/channel, execution result, proposal_hash, receipt_hash, prev hash, detail, timestamps.

## Validation performed

- Inventory of Activity, authorize APIs, Attention authorize, Recovery boundary  
- Pure policy golden + replay tests  
- E2E propose → Attention → decide → execute → receipt integrity  
- Duplicate suppression, deny blocks execute, expire awaiting  
- Idempotent re-execute, failed execution receipts  
- Attention authorize compiler regression  

## Tests executed

```text
.venv/bin/pytest tests/test_authorization_policy.py \
  tests/test_trusted_agent.py \
  tests/test_attention_compiler_authorize.py \
  tests/test_attention_engine.py \
  tests/test_attention_view.py
→ green
```

## Metrics

| Signal | Where |
|--------|--------|
| proposed / approvals_requested / granted / denied | `agent_auth_metric_snapshot` |
| executions / failures / retries | counters |
| duplicates_suppressed / expired | counters |

## Technical debt

- Provider booking/redeem adapters not wired (record-only executor default)  
- Chat `/api/decide` still allows approval without Activity UI inspection (documented trust tradeoff)  
- Unified Home `Action.APPROVAL_REQUEST` builders still unused  
- Cryptographic signatures / external notarization not added (hash integrity only)  
- Home `Action` unified model still unused for approvals presentation  

## Lessons learned

- Extending `actions` preserved Attention/Activity without a second queue.  
- Separating lifecycle from legacy status kept old surfaces working during cutover.  
- Idempotent receipts made retries safe without duplicate side effects.

## Recommendation for the next milestone

**Milestone 12 — Trust, Privacy, and Control**

Users understand and control what Mighty can see and do. Build on durable Actions/receipts + privacy flags (`minimal_logging`, audit) so authorization history, data access, and agent permissions are inspectable and user-governed — without collapsing privacy into Attention ranking.
