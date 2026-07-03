---
id: field_discovery_v2
version: "2.0.0"
description: Field discovery v2 — balance-first ordering for playground comparison
variables:
  - site
  - text
  - today
  - category_hint
---

You are analyzing one or more pages from a user's {site} account.
Pages may be separated by === URL === markers.
Today's date: {today}.
{category_hint}

Read === EMBEDDED STATE === and === API RESPONSE === blocks as high-confidence JSON when present.
If JSON is empty but visible page text shows a value, trust the visible text.

Page text:
{text}

Extract ONLY personalized account data for THIS user — balances, tiers, benefits, dates, bookings.

HARD EXCLUDE: login walls, search forms, generic marketing, past trips, zero/empty values,
contact info, generic labels ("Member", "Cardmember"), site-wide booking windows.

Return JSON: {{"fields": [...]}} where each field has:
key, label, value, value_type, confidence (0–1), source_snippet (≤15 words).
Max 25 fields. Skip uncertain fields (confidence < 0.70).

ORDERING v2 — balance and spendable value FIRST:
1. Primary points/miles/cash-back balance (hero field)
2. Named elite status tier (Gold, Platinum, etc. — not generic "Member")
3. Spendable benefits with counts (certificates, credits, awards)
4. Benefit or points expiration dates
5. Tier progress (e.g. "4 of 20 flights")
6. Future reservations only (date after today)
7. Payment due / autopay / past-due amounts
8. Account metadata last (member since, loyalty ID)

The first field MUST be the primary balance or most spendable benefit — never a member ID.
