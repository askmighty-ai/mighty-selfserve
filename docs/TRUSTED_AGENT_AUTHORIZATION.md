# Trusted Agent Authorization

**Status:** Complete (Milestone 11)  
**Related:** [ATTENTION_COMPILER_AUTHORIZE.md](ATTENTION_COMPILER_AUTHORIZE.md) · [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md)

---

## User capability

Mighty is the trusted execution layer for AI agents: durable Actions, deterministic authorization policy, auditable execution, and verifiable receipts.

---

## Objective

Establish the trust boundary between AI agents and real-world actions — not agent workflows, marketing, or a second approval UI.

---

## Non-goals

- Building multi-step agent planners / workflows  
- A parallel approval queue beside Activity  
- Merging Recovery `ASK_HUMAN` (login) with agent authorize  
- Provider-specific branching in shared authorization policy  
- Replacing Attention ranking  

---

## Ownership

| Concern | Owner |
|---------|--------|
| **User Policy** | Durable user intent ([USER_POLICY.md](USER_POLICY.md)) |
| **Action + authorization facts** | Trusted Agent Authorization (`actions` + lifecycle) |
| **Immutable execution receipts** | `action_execution_receipts` |
| **Interruption** | Attention (`AuthorizeRow` → `agent_authorization`) |
| **History presentation** | Activity (reads `actions` + receipts) |
| **Access repair human** | Recovery (separate axis) |
| **Provider execution behavior** | Adapters / capability registry |

Authorization evaluates **Policy + facts**. Execution never bypasses Policy.

Authorization computes **trust decisions**. Execution performs **work**. Activity presents **history**. Attention decides **interruption**.

---

## Canonical Action lifecycle

| State | Meaning |
|-------|---------|
| `proposed` | Agent submitted; policy not yet applied |
| `awaiting_authorization` | Human decision required (Activity / Attention) |
| `authorized` | Human (or policy auto) granted |
| `denied` | Human or policy denied |
| `executing` | Execution in flight |
| `completed` | Execution succeeded; receipt written |
| `failed` | Execution failed; receipt written |
| `cancelled` | Cancelled before execution |
| `expired` | Timed out awaiting authorization |

Compatibility: Activity/Attention still understand legacy `pending` / `approved` / `denied` / `timeout` / `logged` via status mapping.

---

## Authorization policy (provider-independent)

Keys off `consequence_level` and action metadata — not provider id:

| Level | Default |
|-------|---------|
| `critical` / `consequential` | Require human authorization |
| `routine` | Require human authorization before execution |
| `informational` | Auto-authorize for record-only paths |

Also: deny when expired; suppress duplicate open fingerprints; capability registry enables executable action types.

---

## Receipt model

Append-only `action_execution_receipts`:

- originating Action id  
- requesting agent id  
- authorization decision + timestamp  
- execution result  
- proposal hash + receipt hash (integrity)  
- timestamps + provenance  

Never updated after insert.

---

## Flow

```text
Agent proposes Action
  → authorization_policy (pure)
  → actions lifecycle (awaiting_authorization | authorized | denied)
  → Attention emits agent_authorization while awaiting
  → Human decides (Activity / token / API)
  → execute (idempotent; adapter-capable)
  → immutable receipt
  → Attention clears (terminal lifecycle)
```

---

## Modules

| Module | Role |
|--------|------|
| `authorization_policy` | Pure trust decisions |
| `agent_action_store` | Durable Action lifecycle on `actions` |
| `execution_receipt` | Immutable receipts |
| `trusted_agent` | Propose / decide / execute coordinator |
| `agent_capability_registry` | Executable action-type enablement |
| `agent_auth_metrics` | Observability |
