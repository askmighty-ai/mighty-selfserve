# Home OS — Remaining simulation gaps (Migration P1)

**Status:** Living inventory for staging Home OS  
**Branch:** `feat/home-os-migration-p1`

Every remaining simulation must appear here. Tags below are emitted on
`data-simulation-tags` and stored in session for tests.

---

## Real sources (wired in P1)

| Domain | Source | Adapter |
|--------|--------|---------|
| Work Queue | Attention platform (`read_attention`) | `adapters.attention_items_to_work_items` |
| Coverage | `AccountState` via `load_account_states_for_attention` | `adapters.account_states_to_coverage` |
| Proof | Meaningful `account_changes` via `change_alerts_from_store` | `adapters.change_alerts_to_proof` |
| Ranking / projection | `mighty.workitem.project_home` | none (canonical) |

Authenticated staging users with a real `users` row consume these paths.

---

## Remaining simulations

| Tag | What is simulated | Why |
|-----|-------------------|-----|
| `ephemeral_marriott_scenario` | Full Marriott Interrupt + Coverage + seed Proof for `/research/home-os` preview sessions | No customer DB row; moderated demo still needs a coherent story |
| `ephemeral_future_preview` | Deterministic fully-operational household for `/research/home-os/future` (Coverage, Proof, Approval, Opportunities) | Review-only evaluation of Home OS at scale; no customer DB row |
| `auth_repair_completion_simulated` | Confirming primary repair action does not perform live provider authentication | Safe staging completion; live session capture not yet Home-centered |
| `session_local_proof_overlay` | Proof earned from staged repair is held in session until real change events exist | Lifecycle Proof is earned in-engine but not yet written to `account_changes` |
| `session_local_coverage_override` | Coverage auth flipped to signed-in after staged repair | Real AuthTruth / AccountState not mutated by Home OS commands yet |
| `no_authenticated_user` | (reserved) used when compose runs without a user | Edge diagnostic |

### Explicit non-simulations

- Ranking order is never simulated — WorkItem ranking owns it.
- Attention candidate *existence* for authenticated users is not simulated — it comes from the Attention engine.
- Proof from `account_changes` is not fabricated.

---

## Closure criteria (future)

A simulation tag may be removed only when:

1. A real owning-domain write path replaces it, and  
2. Tests assert the real path, and  
3. This document is updated in the same change.
