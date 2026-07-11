# Product Account State

Canonical product session/login state shared by admin and customer surfaces.

**Related:** [PRODUCT_ARCHITECTURE.md](PRODUCT_ARCHITECTURE.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md)

---

## Two layers

Product surfaces combine **two independent layers**. Do not collapse them.

### A. Session layer — `ProductAccountState`

Source: `provider_session_state` → Current Access → `resolve_product_account_state()`.

| Field | Values / meaning |
|-------|------------------|
| `session_state` | `connected` · `checking` · `signed_out` · `unknown` |
| `login_required` | `True` **only** when `session_state == signed_out` |
| `user_attention_required` | Session-level only: same as `login_required` (data `ERROR` attention is separate) |
| `next_action_type` / `next_action_text` | From **one** table: `PRODUCT_NEXT_ACTION[session_state]` |
| `current_access` | Admin vocabulary (`connected_now`, `error`, …); product maps `error` → `signed_out` |

Current Access → product `session_state`:

| Current Access | Product session |
|----------------|-----------------|
| `connected_now` | `connected` |
| `checking` | `checking` |
| `signed_out` | `signed_out` |
| `error` | `signed_out` |
| `unknown` | `unknown` |

Session next-action is defined only in `mighty.session_access.PRODUCT_NEXT_ACTION`.
Admin Current Access calls `login_truth.next_action_for_current_access()`, which
delegates to that table via `to_product_session_state`. There is no second policy table.

Customer UIs must consume `login_required` for sign-in CTAs (not re-check
`session_state == "signed_out"`). Wording may differ via presentation helpers.

### B. Update / data layer — `AccountStatus.status`

Source: `resolve_canonical_status()` (lifecycle + sync + session).

| Status | Meaning |
|--------|---------|
| `up_to_date` | Connected (or equivalent) with usable data posture |
| `updating` | Active sync for this source |
| `checking` | Session verification in progress |
| `needs_login` | Session `signed_out` only |
| `waiting_for_extension` | Still setting up / first visit |
| `error` | Non-login sync/data failure |

Dashboard health chips and Accounts sections bucket this layer
(“Still setting up”, “Needs attention”). That is **not** session policy.

---

## Resolver chain

```
Provider evidence (extension / probe / verification)
        ↓
provider_session_state
        ↓
login_truth.compute_current_account_access_rows   (Current Access)
        ↓
session_access.resolve_product_account_state      (session contract)
        ↓
account_status.build_account_status               (session + update status)
        ↓
surface presentation (wording / layout only)
```

Surfaces must not re-interpret login from `sync_status`, `connection_status`,
or lifecycle `needs_login`.

---

## Status consumers (accurate wiring)

| Surface | How it gets session truth | Direct `resolve_product_account_state`? |
|---------|---------------------------|----------------------------------------|
| Admin Current Account Access | `compute_current_account_access_rows` + `next_action_for_current_access` | Via next-action helper |
| Admin Session Evidence | PSS timeline | N/A (evidence) |
| `/api/account-status` | `build_account_status` | **Yes** (inside builder) |
| Dashboard hero / health | `AccountStatus` from `build_account_status` | **Transitive** |
| Accounts sections | `session_state` from product | Via `to_product_session_state` / product map |
| Accounts login CTA | `ProductAccountState.login_required` | **Yes** (call site resolves product) |
| Account Center login CTA | `ProductAccountState.login_required` | **Yes** (inside `build_card_view`) |
| Extension popup | `/api/account-status` summary | Server-side only |
| Connect modal | `/api/extension/poll` `product` | **Yes** on poll endpoint |

### Intentionally out of the session contract

| Area | Why |
|------|-----|
| Extension extraction gating (`connection_status`) | Operational sync gate |
| `account_state` data-health labels | Cached-data vocabulary |
| Recommendation scoring | Benefit-driven |
| Provider verification / evidence writers | Write path |

---

## Provider onboarding

To add a provider (e.g. Alaska Airlines) without changing Dashboard / Accounts /
Popup / Account Center presentation code:

1. **Catalog entry** — `SUPPORTED_SITES` (and login / entry URL maps) so the
   provider can be discovered and connected.
2. **Credentials / account row** — user must have `account_credentials` (and
   usually `account_data`) for that source.
3. **Evidence writer** — persist session evidence into `provider_session_state`
   (raw `upsert_provider_session_state`, or a generic extension writer).
4. **Optional automatic verification** — add an entry URL to
   `SESSION_VERIFICATION_ENTRY_URLS` if background `checking` should enqueue.
5. **Extraction adapter** — only if account field data is required (out of
   session-contract scope).

Once (2) + (3) exist, the product **read** pipeline treats the provider like any
other credentialed source.

### Current write / verification gates

| Gate | Today |
|------|-------|
| `PROBE_PROVIDERS` | `{amex, delta, hilton, united, marriott}` — extension `record_extension_*` helpers no-op outside this set |
| `SESSION_VERIFICATION_ENTRY_URLS` | Amex only — automatic verification enqueue |
| `upsert_provider_session_state` | **Not** probe-gated — any provider key may store PSS |

---

## Intentional presentation differences

Wording/detail only. Underlying `session_state`, `login_required`, and session
next-action stay aligned.

| Topic | Admin | Customer |
|-------|-------|----------|
| Session vocabulary | Current Access labels + evidence | Product labels (`Connected`, `Needs sign in`, …) |
| Login copy | “Sign into this account again.” | Accounts: “Needs login” / Account Center: “Needs sign in” |
| Checking copy | Checking + verification lifecycle | Accounts: “Checking now”; Access Loop: “Checking...” |
| Unknown copy | Evidence / source detail | “Unable to verify” / Accounts: “Not yet verified” |
| Setup vs session | N/A | “Still setting up” = update layer |
| Data errors | Cached Data diagnostics | Health “Needs attention” (update layer) |

---

## Consistency tests

`tests/test_product_state_consistency.py` — providers
`{amex, delta, hilton, marriott, united, southwest, xfinity, pa_utilities}`
× session states.

`tests/test_product_account_state_adoption.py` — login CTA consumes
`login_required`; dashboard client cannot let legacy `sync_status` override
canonical session; next-action defined in one place.
