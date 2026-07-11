# Product Account State

Canonical product session/login state shared by admin and customer surfaces.

**Related:** [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md)

---

## Resolver chain

```
Provider evidence (extension / probe / verification)
        ↓
provider_session_state
        ↓
login_truth.compute_current_account_access_rows   (Current Access)
        ↓
session_access.resolve_product_account_state      (canonical product object)
        ↓
┌───────────────────────────────────────────────────────────────┐
│ Dashboard · Accounts · Account Center · Popup                 │
│ /api/account-status · Admin Current Access / Session Evidence │
└───────────────────────────────────────────────────────────────┘
```

Surfaces must not re-interpret login/session state from `sync_status`,
`connection_status`, or lifecycle `needs_login`. Those fields remain written
for compatibility and non-login setup signals only.

---

## Canonical product contract

`mighty.session_access.ProductAccountState` (via `resolve_product_account_state`):

| Field | Values / meaning |
|-------|------------------|
| `session_state` | `connected` · `checking` · `signed_out` · `unknown` |
| `login_required` | `True` only when `session_state == signed_out` |
| `user_attention_required` | Session-level: same as `login_required` |
| `next_action_type` / `next_action_text` | From `PRODUCT_NEXT_ACTION[session_state]` |
| `current_access` | Admin Current Access vocabulary (may be `error`, mapped to product `signed_out`) |

Mapping from Current Access → product `session_state`:

| Current Access | Product session |
|----------------|-----------------|
| `connected_now` | `connected` |
| `checking` | `checking` |
| `signed_out` | `signed_out` |
| `error` | `signed_out` |
| `unknown` | `unknown` |

---

## Status consumer audit

| Surface | Status source | Canonical? | Notes |
|---------|---------------|------------|-------|
| Admin Current Account Access | `compute_current_account_access_rows` | Yes | Same Current Access as product bridge |
| Admin Session Evidence | `provider_session_state` timeline | Yes | Evidence detail only |
| `/api/account-status` | `load_all_account_statuses` → `resolve_product_account_state` | Yes | Shared by Dashboard poll + popup |
| Dashboard hero / health chips / login banner | `AccountStatus` from `build_account_status` | Yes | Buckets presentation only |
| Accounts sections / CTAs | `session_state` + `resolve_canonical_status` | Yes | Login CTA only for `signed_out` |
| Account Center login badge / CTA | `resolve_session_access_presentation` | Yes | Non-probe → `unknown` |
| Extension popup | `/api/account-status` `summary.access_loop` | Yes | No local login reinterpretation |
| Connect modal (`/api/extension/poll`) | Product `session_state` on poll payload | Yes | Lifecycle `needs_login` no longer drives modal |

### Intentionally out of this contract

| Area | Why |
|------|-----|
| Extension extraction gating (`connection_status`) | Operational sync gate — not product presentation |
| `account_state` data-health / session_health labels | Cached-data freshness vocabulary, not Current Access |
| Recommendation scoring / benefit banners | Benefit-driven, not session state |
| Provider verification / evidence writers | Write path — unchanged by this PR |

---

## Intentional presentation differences

These differ in **wording or detail only**. Underlying `session_state`,
`login_required`, and recommended next action stay aligned.

| Topic | Admin | Customer |
|-------|-------|----------|
| Session vocabulary | Current Access labels (`Connected now`, `Error`, evidence, source) | Product labels (`Connected`, `Needs sign in`, `Unable to verify`) |
| Login copy | “Sign into this account again.” | Accounts: “Needs login” / Account Center: “Needs sign in” |
| Checking copy | “Checking” + verification lifecycle | Accounts: “Checking now”; Access Loop: “Checking...” |
| Unknown copy | Evidence / source detail | “Unable to verify” / Accounts: “Not yet verified” |
| Setup vs session | N/A | “Still setting up” buckets cover waiting/updating — not login |
| Data errors (`ERROR`) | Cached Data / diagnostics | Health “Needs attention” (separate from session login) |

---

## Consistency tests

`tests/test_product_state_consistency.py` asserts that for every provider in
`{amex, delta, hilton, marriott, united, southwest, xfinity, pa_utilities}`
and every product session state, Admin Current Access, Dashboard, Accounts,
Account Center, `/api/account-status`, and the popup payload agree on the
canonical product contract.
