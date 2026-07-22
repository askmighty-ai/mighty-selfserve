# Automatic Account Discovery and Enrollment — Milestone 7 Design Note

**Status:** Complete  
**Milestone report:** [milestones/MILESTONE_7.md](milestones/MILESTONE_7.md)  
**Related:** [PRODUCT_MANIFESTO.md](PRODUCT_MANIFESTO.md) · [ACCOUNT_STATE.md](ACCOUNT_STATE.md) · [HOME_EXPERIENCE.md](HOME_EXPERIENCE.md)

## User capability

Accounts appear from the user’s existing digital life without bulk “Add account” rituals.

## Objective

Use Gmail-derived (and other mailbox) sender evidence to identify provider relationships, assign confidence and provenance, and enroll high-confidence accounts automatically — without collapsing discovery, enrollment, session, and extraction into one “connected” flag.

## Inventory summary (kickoff)

| Existing | Role vs M7 |
|----------|------------|
| `mighty/email_scan.py` `SITE_SENDER_DOMAINS` | Sender→provider registry config — keep as matching input |
| `_store_suggestions` | Upsert that **resets** `added`/`dismissed` — replace reconcile |
| `_register_account_source` | Canonical enrollment write — wrap, do not fork |
| Gmail callback Amex special-case | Become generic high-confidence auto-enroll |
| `email_suggestions` | Compatibility projection for scan UI / lifecycle |
| `AccountState` | Post-enrollment mirror — **not** discovery SSoT |
| Attention / Recovery | Out of scope for discovery/matching |

## Ownership

| Responsibility | Owner |
|----------------|-------|
| Discovery facts | **Discovery Store** (`account_discovery`) |
| Provider matching + confidence | **Discovery Policy** (pure; registry config) |
| Enrollment writes | **`enroll_from_discovery` → `_register_account_source`** |
| Session / extraction | Access Manager / PSS / extractors (unchanged) |
| Product presentation | Home / Accounts / lifecycle (truthful waiting) |

## Canonical discovery fact

Table `account_discovery` (one row per `(user_id, provider)`):

| Field | Notes |
|-------|-------|
| `provider` | Normalized `site_key` |
| `source_type` | e.g. `gmail_sender` |
| `source_ref` | Mailbox provider id (`gmail` / `outlook` / `imap`) — not message bodies |
| `matched_domain` | Sender domain that matched |
| `match_method` | `exact` \| `suffix` |
| `confidence` | 0.0–1.0 deterministic |
| `email_count` | Aggregate estimate from scan |
| `disposition` | see below |
| `first_seen_at` / `last_seen_at` | |
| `evidence_summary` | Short non-body summary |
| `enrolled_at` | Set when enrollment succeeds |

**Dispositions:** `discovered` · `eligible` · `enrolled` · `ambiguous` · `dismissed` · `already_enrolled` · `ignored`

## Matching and confidence

- Match only via registry domains (`SITE_SENDER_DOMAINS`) — exact then suffix.  
- No shared-policy `if provider == …` branches; auto-enroll eligibility is a config set (defaults to `CUSTOMER_VISIBLE_PROVIDERS`).  
- Confidence: base 0.90 exact / 0.85 suffix; +0.05 if `email_count >= 3` (cap 1.0).  
- **Auto-enroll** when confidence ≥ 0.85, disposition would be eligible, provider in auto-enroll set, not dismissed, not already enrolled.

## Reconciliation

Rescans refresh counts and `last_seen_at`. Never clear `dismissed` or enrolled linkage solely because a scan re-ran. Absent-on-scan → `ignored` (keep history); do **not** delete enrolled accounts.

## Product integration

- High-confidence → enroll stub credentials + waiting connection status (no fake data).  
- Ambiguous / non-auto-enroll → stay on discovery / scan surfaces; no per-provider Attention spam.  
- Home Empty still offers Connect Gmail; after enroll, Waiting is truthful.  
- Accounts remains audit/repair.

## Implementation order

1. Design note + living report + roadmap  
2. Pure policy + store + enrollment + pipeline  
3. Wire Gmail/Outlook/IMAP + fix Add API + metrics  
4. Tests + docs close  

## Non-goals

Inbox management, unsubscribe UI, deals surfaces, credentials vault, login automation, Attention/Recovery redesign, admin dashboards, cutover cleanup.

## Success criteria

- One discovery fact owner; one enrollment write path  
- Deterministic match/confidence; idempotent auto-enroll  
- Axes preserved; isolation held; tests + living report complete  
