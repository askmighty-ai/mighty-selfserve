# Milestone 7 — Automatic Account Discovery and Enrollment

**Status:** Complete  
**Design note:** [ACCOUNT_DISCOVERY.md](../ACCOUNT_DISCOVERY.md)  
**Related:** [PRODUCT_MANIFESTO.md](../PRODUCT_MANIFESTO.md) · [ACCOUNT_STATE.md](../ACCOUNT_STATE.md)

## User capability

Accounts appear from the user’s existing digital life without bulk “Add account” rituals.

## Objective

Build a production discovery and enrollment capability that uses Gmail-derived evidence (and other mailbox scans) to identify provider relationships, assign confidence and provenance, and enroll appropriate accounts automatically — without collapsing discovery, enrollment, session, and extraction.

## PRs merged

| PR | Theme |
|----|--------|
| *(this PR)* | Design note, discovery policy/store/pipeline, Gmail/Outlook/IMAP wire-up, tests |

## Architecture changes

- Added pure `discovery_policy` (match + confidence + disposition)
- Added `account_discovery` store as discovery-fact SSoT
- Added `discovery_pipeline` reconcile + auto-enroll
- Added `discovery_enrollment` wrapping `_register_account_source`
- Replaced destructive `_store_suggestions` reset with reconcile projection
- Manual Add now enrolls credentials (not flag-only)
- Discovery metrics snapshot table

## Architecture Decisions

### AD-M7-1: Discovery Store owns facts; AccountState remains post-enrollment mirror

- **Decision:** Introduce `account_discovery` as the durable discovery fact; keep AccountState as the enrolled-account projector.  
- **Why:** Separation of discovery vs enrollment axes.  
- **Alternatives considered:** Overload `email_suggestions` alone; invent discovery inside AccountState.  
- **Long-term impact:** Clear reconcile/enroll seams.

### AD-M7-2: Registry config for aliases; pure policy for confidence

- **Decision:** Keep sender aliases in `SITE_SENDER_DOMAINS`; implement matching/confidence in pure `discovery_policy`.  
- **Why:** No provider-id switches in shared policy.  
- **Alternatives considered:** Keep Amex-only special-case in the Gmail callback.  
- **Long-term impact:** Expanding auto-enroll is a config/set change.

### AD-M7-3: Auto-enroll only for configured high-confidence providers

- **Decision:** Default auto-enroll set = `CUSTOMER_VISIBLE_PROVIDERS` (Amex today).  
- **Why:** Preserves alpha customer surface while delivering the architecture.  
- **Alternatives considered:** Auto-enroll all registry hits.  
- **Long-term impact:** Safe expansion path.

### AD-M7-4: Rescan must not reset dismiss/enroll intent

- **Decision:** Reconcile refreshes counts/`last_seen` and preserves dismissed/enrolled.  
- **Why:** Required for safe repeated scans.  
- **Alternatives considered:** Keep `added=0, dismissed=0` upsert.  
- **Long-term impact:** User intent survives rescans.

### AD-M7-5: Manual Add performs real enrollment

- **Decision:** `/api/email/suggestions/add` calls the canonical enroll path (credentials + stub), not merely `added=1`.  
- **Why:** “Add” must mean watched-account enrollment without implying session/data.  
- **Alternatives considered:** Leave Add as a flag-only bridge.  
- **Long-term impact:** Removes orphan `added=1` without credentials.

## Final production data flow

```text
Mailbox scan (headers/sender only)
  → email_scan suggestions {site_key, sender, email_count, …}
  → discovery_policy (match + confidence + disposition)
  → account_discovery reconcile (preserve dismiss/enroll)
  → auto-enroll eligible ∈ CUSTOMER_VISIBLE_PROVIDERS
       → _register_account_source (credentials + waiting stub)
  → email_suggestions projection (UI compatibility)
  → Home Waiting / Accounts (truthful; no fake data)
```

## Discovery fact and lifecycle

Table `account_discovery` keyed by `(user_id, provider)`.

Dispositions: `discovered` · `eligible` · `enrolled` · `ambiguous` · `dismissed` · `already_enrolled` · `ignored`.

## Provider-matching and confidence policy

- Exact then suffix match against `SITE_SENDER_DOMAINS`
- Confidence: 0.90 exact / 0.85 suffix; +0.05 if `email_count >= 3`
- Auto-enroll when confidence ≥ 0.85 and provider in auto-enroll set

## Enrollment and reconciliation behavior

- Idempotent enroll via credentials uniqueness
- Rescan does not clear dismiss/enroll
- Absent-on-scan → `ignored` (enrolled accounts never deleted for missing mail)
- Ambiguous (e.g. Delta while only Amex auto-enrolls) stays off Home interrupt spam

## Privacy decisions

- No message bodies stored on discovery facts
- `source_ref` is mailbox connection id (`gmail` / `outlook` / `imap`)
- `evidence_summary` is domain/method/count only

## Validation performed

- Inventory of scan/enroll paths at kickoff
- Pure matching/confidence golden tests
- Reconcile + dismiss preservation
- Auto-enroll Amex / ambiguous Delta
- Idempotent rescan, manual coexistence, dismiss block, failure isolation
- Gmail callback route still redirects Amex connect after auto-enroll

## Tests executed

```text
.venv/bin/pytest tests/test_discovery_*.py \
  tests/test_routes.py::test_gmail_callback_redirects_to_amex_connect \
  tests/test_alpha_readiness.py tests/test_account_lifecycle.py
→ discovery suites green; Gmail callback green
```

## Metrics and observability

| Signal | Where |
|--------|--------|
| discovered / enrolled / ambiguous / already_enrolled / dismissed / ignored | `discovery_metric_snapshot` + `discovery.metrics` log |
| `discovery.enrolled` | enrollment log line |

## Legacy or duplicate code removed

- Destructive `_store_suggestions` conflict reset replaced (wrapper delegates to pipeline)
- Gmail Amex hard-coded enroll block replaced by pipeline auto-enroll

## Technical debt

- Dual catalog drift (`SUPPORTED_SITES` vs `SITE_SENDER_DOMAINS` keys) still present
- `email_suggestions` remains a compatibility projection
- Auto-enroll set still Amex-only via CVP
- Discovery-to-first-real-data conversion metric not yet time-series
- Unrelated `test_customer_account_access` copy assertion may be fragile (pre-existing)

## Lessons learned

- Preserving dismiss/enroll across rescans was the highest-leverage fix over inventing a second scan stack.
- Keeping auto-enroll as a frozenset over registry matches avoided Amex-only policy forks in the matcher.

## Recommendation for the next milestone

**Milestone 8 — Natural-Session Coverage**

Focus on session capture through normal browsing (extension natural-session paths, freshness of PSS evidence, Worker glance). Keep expanding `CUSTOMER_VISIBLE_PROVIDERS` / auto-enroll set as a focused ops change when product-ready — not a separate milestone.
