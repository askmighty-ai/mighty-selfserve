# Canonical Account Snapshots

**Status:** Implemented (PR #94); Change Intelligence on diffs (Milestone 9)  
**Related:** [PRODUCT_ACCOUNT_STATE.md](PRODUCT_ACCOUNT_STATE.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md) · [ACCESS_FLOW.md](ACCESS_FLOW.md) · [FRESHNESS_CHANGE.md](FRESHNESS_CHANGE.md)

---

## Architecture

### Before

```
Verification → Extraction → Customer UI
                              ↑
                    reads live account_data.items
                    (and in-progress extraction state)
```

Customer surfaces mixed **lifecycle** (checking / extracting) with **presentation data**.
A failed or running extraction could starve the UI of previously good fields.

### After

```
Verification
    → Extraction
    → Normalization
    → Account Snapshot (immutable persist)
    → Customer UI
```

| Layer | Owns | Customer may depend on? |
|-------|------|-------------------------|
| Verification lifecycle | Access cycles, checking | Status labels only |
| Extraction / `account_data` | Raw capture + working blob | **No** (ops / adapters only) |
| **Account Snapshot** | Normalized successful result | **Yes — source of truth** |
| Readiness / AccountStatus | Connected / Checking / Sign in | Status labels only |

---

## Snapshot schema

```text
AccountSnapshot
  snapshot_id
  user_id
  provider
  account_identifier
  verified_at
  created_at
  schema_version          # SNAPSHOT_SCHEMA_VERSION = 1
  provider_version
  confidence
  correlation_id
  access_cycle_id
  accounts[]
  benefits[]
  rewards[]
  credits[]
  offers[]
  travel[]
  warnings[]
  metadata{}
  evidence_refs[]         # pointers — never raw payloads
  normalized_fields[]     # provider-independent display items
```

Provider-specific JSON is **not** exposed to product surfaces. Normalization
happens when the snapshot is created (`mighty.account_snapshot`).

### Evidence references

```json
{
  "kind": "account_data",
  "provider": "amex",
  "synced_at": "2026-07-12T…",
  "field_keys": ["points_balance"],
  "pipeline_run_id": "…",
  "access_cycle_id": "…"
}
```

---

## Selection rules

Always serve the **latest successful** snapshot.

Ignore (do not activate):

- failed extraction
- partial / empty normalization
- running extraction

If extraction or normalization fails, or verification succeeds while extraction
is still running → **retain the previous successful snapshot**.

Never replace a good snapshot with an incomplete state.

---

## Customer surfaces

Dashboard, Home (embedded in Dashboard), Accounts, popup, and `/api/account-status`
all resolve field/data presentation from the same latest snapshot identity
(`snapshot_id` on `AccountStatus`).

Status/readiness still comes from the PR #93 readiness layer (Connected =
fresh session + correlated private data). Snapshot is the **data** contract;
readiness is the **access** contract.

---

## Persistence

Table: `account_snapshots` (append-only).

- Insert only — never `UPDATE` an existing snapshot row.
- Newest successful row is active.
- Older rows remain queryable.
- On each successful persist, Milestone 9 diffs previous→new into
  `account_changes` (meaningful deltas + fingerprint dedupe). See
  [FRESHNESS_CHANGE.md](FRESHNESS_CHANGE.md).

---

## Migration notes

1. `ensure_account_snapshot_tables()` runs from `init_db()`.
2. Successful extractions create snapshots via:
   - `/api/data/sync`
   - `apply_adapter_payload`
   - `apply_amex_membership_rewards_extraction`
3. Optional backfill: `maybe_backfill_snapshot_from_account_data()` when a
   provider has `extraction_status=complete` but no snapshot yet.
4. Customer UI does **not** fall back to live extraction items once the
   snapshot layer is active — empty snapshot ⇒ empty product fields
   (status may still show Checking / Unable to verify).

---

## Admin / internal

| Surface | Purpose |
|---------|---------|
| `/admin/account-snapshots` | Viewer: provider, verified time, id, schema, fields, evidence refs |
| `GET /api/admin/account-snapshots` | Metadata (and optional full payload) for internal tooling |

---

## Module

`mighty/account_snapshot.py` — model, normalize, persist, select, backfill.
