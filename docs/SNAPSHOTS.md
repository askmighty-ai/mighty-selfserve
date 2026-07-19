# Snapshot Platform

**Status:** Implemented  
**Related:** [CONNECTORS.md](CONNECTORS.md) · [ACCOUNT_SNAPSHOTS.md](ACCOUNT_SNAPSHOTS.md) · [PROVIDER_RUNTIME.md](PROVIDER_RUNTIME.md)

Provider-independent persistence and change detection for canonical connector
`AccountSnapshot` objects. This platform records history and factual deltas
only. It never produces financial advice, rankings, or optimization.

```text
Provider Connector
        ↓
AccountSnapshot
        ↓
SnapshotStore
        ↓
SnapshotDiff
        ↓
FactGenerator
        ↓
Future Opportunity Engine
```

---

## Responsibility boundary

| Layer | Owns | Does not own |
|-------|------|--------------|
| **Provider Connector** | Read-only refresh → canonical `AccountSnapshot` | Persistence, diff, advice |
| **SnapshotStore** | Append-only immutable snapshot history | Mutations, overwrites, provider I/O |
| **SnapshotDiff** | Provider-independent facts between two snapshots | Summaries, recommendations |
| **FactGenerator** | Human-readable descriptive summaries | Advice, rankings, Opportunity Engine |
| **Future Opportunity Engine** | (Not implemented) Interpreting facts into opportunities | Snapshot persistence / connector I/O |

This document describes the **connector Snapshot Platform**
(`mighty/snapshot_store.py`, `mighty/snapshot_diff.py`, `mighty/fact_generator.py`).
It is separate from product UI snapshots in `mighty/account_snapshot.py`
(see [ACCOUNT_SNAPSHOTS.md](ACCOUNT_SNAPSHOTS.md)).

---

## Snapshot model

Each persisted record is an immutable envelope:

```text
StoredSnapshotRecord
  snapshot_id
  provider
  provider_customer_id   # opaque; never a credential
  observed_at
  verified_at
  connector_version
  extraction_summary     # counts / completeness only
  snapshot               # canonical AccountSnapshot
  stored_at
```

The inner `snapshot` is the connector canonical model:

- `accounts[]` — `FinancialAccount` (opaque `provider_account_id`)
- `rewards[]` — `RewardsBalance`
- `completeness`, `warnings`, `provider_metadata`

### Persistence rules

- Append-only. Never overwrite a prior `snapshot_id`.
- Local JSON store today: `~/.mighty/provider_runtime/snapshots/`
- Abstracted behind `SnapshotStore` so Railway/Postgres can replace the backend
  without changing Diff / FactGenerator.

---

## Diff model

`SnapshotDiff` compares two snapshots from the **same provider** (and same
opaque customer id when persisted).

Stable matching:

| Entity | Match key |
|--------|-----------|
| Accounts | `provider_account_id` (opaque) |
| Rewards | `program_name` |

Display names, last-four digits, and product labels are **never** match keys.
They may appear in explanations only.

### Fact types

| Fact type | Meaning |
|-----------|---------|
| `NEW_ACCOUNT` | Account id present after, absent before |
| `ACCOUNT_REMOVED` | Account id present before, absent after |
| `BALANCE_CHANGED` | Current balance amount changed |
| `AVAILABLE_CREDIT_CHANGED` | Available credit amount changed |
| `PAYMENT_DUE_CHANGED` | Payment due amount changed |
| `PAYMENT_DATE_CHANGED` | Payment due date changed |
| `REWARDS_CHANGED` | Rewards program balance changed |
| `ACCOUNT_RENAMED` | Display name changed for same account id |
| `PRODUCT_CHANGED` | Product name changed for same account id |
| `FIELD_BECAME_AVAILABLE` | Optional field went from null → value |
| `FIELD_BECAME_UNAVAILABLE` | Optional field went from value → null |
| `LAST_VERIFIED_CHANGED` | Snapshot `verified_at` changed |

---

## Fact model

```text
Fact
  fact_id
  snapshot_before
  snapshot_after
  provider
  account_id          # optional
  fact_type
  observed_at
  old_value
  new_value
  confidence
  explanation
```

Example explanation:

> Membership Rewards balance changed from 124,350 points to 125,120 points.

Facts are **descriptive only**. They must never say:

- “You should…”
- “We recommend…”
- “Pay…”
- “Redeem…”
- “Optimize…”

---

## Provider independence

Diff and FactGenerator operate solely on canonical connector models from
`mighty/provider_connector.py`. They must not import Amex (or any provider)
extractors, normalizers, DOM helpers, or raw payloads.

A Chase or Delta snapshot with the same canonical shape produces the same fact
types and summary style.

---

## Read-only philosophy

The Snapshot Platform is observational:

- Never submits payments, redemptions, transfers, or settings changes
- Never mutates provider state
- Never ranks cards or suggests optimization
- Never invents opportunities — that belongs to a future engine downstream

---

## CLI

```bash
PYTHONPATH=. .venv/bin/python scripts/provider_runtime.py connector-refresh amex --persist
```

Behavior:

```text
connector refresh
      ↓
snapshot persisted
      ↓
previous snapshot loaded
      ↓
diff computed
      ↓
facts generated
      ↓
summary printed
```

First refresh prints `First snapshot recorded.`  
Subsequent refreshes print `Changes since previous refresh` with bullets, or
`No changes since previous refresh.` when the facts list is empty.

### Telemetry (sanitized)

Recorded on persist (no sensitive values beyond canonical snapshot fields):

| Field | Meaning |
|-------|---------|
| `snapshot_duration` | Persist wall time (ms) |
| `snapshot_size` | Serialized record size (bytes) |
| `facts_generated` | Count of facts from diff |
| `previous_snapshot_found` | Whether a prior snapshot existed |
| `diff_duration` | Diff wall time (ms) |

---

## Future Opportunity Engine boundary

The Opportunity Engine (not built here) may consume facts as inputs. It must
remain a **separate** layer:

```text
Facts (descriptive, provider-independent)
        ↓
Opportunity Engine (future)
        ↓
Opportunities / advisories (explicitly out of scope for Snapshot Platform)
```

SnapshotStore, SnapshotDiff, and FactGenerator must stay free of opportunity
scoring, recommendations, and action CTAs.
