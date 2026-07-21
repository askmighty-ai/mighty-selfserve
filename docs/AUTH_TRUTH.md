# AuthTruth — projection, not authority

**Status:** Implemented (PR1 / Milestone 4)  
**RFC:** [AUTHENTICATION_ATTENTION_PLATFORM.md](AUTHENTICATION_ATTENTION_PLATFORM.md) §3

## Why AuthTruth is a projection

Authentication facts are written once, by the adapters that observe sessions:

1. **Access Manager** → `provider_session_state` (browser_session)
2. **Provider Runtime** → `runtime_access_state` publications (managed_runtime)

`AuthTruth` is a **derived read model** over those publications for the account’s **primary** `access_method` only. Replaying the same source rows (with a fixed clock) rebuilds an identical `AuthTruth`. Nothing in the product stack invents terminals into AuthTruth; there is no `record_auth_evidence` product API.

Persisting `auth_truth` is materialization of the projection for cheap reads — not a second ledger. If the projection store is wiped, source publications remain the authority and the projector reconstructs AuthTruth.

## Ownership

| Role | Owner |
|------|--------|
| Write auth evidence | Access Manager / Runtime only |
| Choose primary `access_method` | AccountState / enrollment (config input) |
| Project primary-method product auth | AuthTruth projector |
| Rank / notify humans | Attention Engine ([ATTENTION_ENGINE.md](ATTENTION_ENGINE.md)); not this module |

## Interface gap (documented, not extended)

RFC §3.4 requires Runtime publications to carry `needs_human`, `needs_human_reason`, and `interruption_expected`. AccessState publication schema v2 does not yet emit or validate those fields. The projector **reads them when present** on the stored payload and otherwise leaves `needs_human=false`. It does **not** re-derive human-need from `recovery_state` / `awaiting_user`.
