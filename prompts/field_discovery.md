---
id: field_discovery
version: "1.0.0"
description: Extract personalized account fields from scraped page text
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
CRITICAL — How to read structured data blocks:
=== EMBEDDED STATE === blocks contain JSON serialized directly into the page by the framework
(Next.js __NEXT_DATA__, Apollo __APOLLO_STATE__, Redux, etc.) before any API call fires.
This is the HIGHEST CONFIDENCE source — treat non-empty values here as ground truth.

=== API RESPONSE === blocks contain JSON from a specific network API call.
These are high-confidence but may be INCOMPLETE — a single API call often returns partial data.
If an API block shows empty arrays ([]) or null/empty for a field, that does NOT mean the user
has no data — the site loads different data from different endpoints.

In both cases: if the block shows empty/null but the page text clearly displays a value
(e.g. "Gold", "143,996 Points"), ALWAYS trust the displayed page text. A site may render
status from a different API call than the one captured. Never let an empty JSON field suppress
a value that is clearly visible on the page.

Page text:
{text}

Extract ONLY data that is SPECIFIC TO THIS USER's account — personalized numbers, statuses, dates, and benefits.

INCLUDE:
- Loyalty/rewards points, miles, or cash-back balance totals
- Tier or status level (Gold, Platinum, Diamond, A-List, etc.) — only meaningful named tiers, not generic labels like "Cardmember" or "Member"
- Progress toward next tier or status goal (e.g. "4 of 20 flights to A-List")
- Benefits the user CAN USE RIGHT NOW: certificates, upgrade awards, free nights, companion passes, lounge visits, travel credits, fee waivers — include the count or value and the expiry date if shown
- Expiration dates for points, status, or any benefit (even if the benefit itself is listed above)
- UPCOMING reservations or bookings — ONLY those with a future date (after today). If the trip date is in the past, REJECT it.
- Payment info: current balance owed, minimum payment due, payment due date, whether autopay is active, last payment received (date + amount), any past-due or overdue amount
- Personalized special offers with a specific deadline date AND a specific reward amount (e.g. "Earn 5,000 bonus points if you stay by Aug 31")

HARD EXCLUDE — never include these even if they appear on the page:
- Any value containing "log in", "sign in", "login to view", "sign in to see"
- Search or booking form fields (departure city, destination, travel dates, passenger count, cabin class)
- Site-wide availability windows (e.g. "Book travel through [date]", "Reservations Through: March 2027")
- "No match found", "None", "N/A", "–", empty values, or zero values ("0", "$0", "$0.00")
- Navigation labels, menu items, links, tab names, page headings with no data value
- Generic account-type labels that carry no meaningful tier information: "Cardmember", "Member", "Basic", "Standard", "Registered" — these tell the user nothing they don't already know
- PAST reservations, trips, or flights whose date has already occurred (before today) — these are history, not upcoming
- Generic marketing copy available to ALL users with no personalized quantity, deadline, or condition
- Contact and personal info: email addresses, phone numbers, mailing addresses, passport numbers — never useful on a dashboard
- Promotional offers with no specific personalized deadline AND no specific personalized reward quantity (if both are missing, REJECT)

CONCRETE REJECT EXAMPLES:
- "Points Balance Alert: Log in to view points balance" → REJECT (login wall)
- "Reservations Through: March 10, 2027" → REJECT (site-wide booking window, not a user reservation)
- "Depart Date: Fri, Jun 12, 2026" from a search widget → REJECT (search form)
- "Upcoming Flight: Jul 22, 2024" → REJECT (date is in the past)
- "Cardmember Status: Cardmember" → REJECT (generic label, tells the user nothing)
- "Membership Level: Member" → REJECT (redundant, not a meaningful tier)
- "Upcoming Trips: None" → REJECT (empty value)
- "Earn more points with our partners" → REJECT (generic marketing, no personalized amount or deadline)
- "Gift Cards Balance: 0" → REJECT (zero value)
- "Nights This Year: 0" → REJECT (zero value)
- "Primary Email Address: user@example.com" → REJECT (contact info)
- "Earn Up to 700 Points with Hertz" → REJECT (generic partner promotion, no personalized deadline or quantity)
- "Earn 2,000 Bonus Points Every Night" → REJECT (generic promotion, not a personalized offer)

CONCRETE INCLUDE EXAMPLES:
- "Gold Medallion" status → INCLUDE as {{"key":"elite_status","label":"Elite Status","value":"Gold Medallion","confidence":0.99,"source_snippet":"SkyMiles Gold Medallion status"}}
- "Gold" tier displayed prominently on account home page (e.g. "Gold\\nWelcome back, Jonathan") → INCLUDE as {{"key":"elite_status","label":"Elite Status","value":"Gold","confidence":0.97,"source_snippet":"Gold Welcome back"}}
- "24,617 Rapid Rewards points" → INCLUDE as {{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617","confidence":0.97,"source_snippet":"Rapid Rewards Points Balance 24,617"}}
- "0 of 20 flights" in A-List section → INCLUDE as {{"key":"alist_progress","label":"A-List Flights Progress","value":"0 of 20","confidence":0.93,"source_snippet":"A-List status 0 of 20 qualifying flights"}}
- "$2,472.20 Total Payment Due" → INCLUDE as {{"key":"balance_due","label":"Balance Due","value":"$2,472.20","confidence":0.99,"source_snippet":"Total Payment Due $2,472.20"}}
- "Minimum Payment Due: $35 by Jul 12, 2026" → INCLUDE as {{"key":"min_payment_due","label":"Minimum Payment Due","value":"$35 by Jul 12, 2026","confidence":0.98,"source_snippet":"Minimum Payment Due $35 by Jul 12, 2026"}}
- "Past Due Amount: $150" → INCLUDE as {{"key":"past_due_amount","label":"Past Due Amount","value":"$150","confidence":0.99,"source_snippet":"Past Due Amount $150"}}
- "Last payment: $2,472.20 received Jun 11, 2026" → INCLUDE as {{"key":"last_payment","label":"Last Payment Received","value":"$2,472.20 on Jun 11, 2026","confidence":0.97,"source_snippet":"last payment $2,472.20 received Jun 11"}}
- "AutoPay: Enrolled" → INCLUDE as {{"key":"autopay_status","label":"Auto Pay Status","value":"Enrolled","confidence":0.98,"source_snippet":"AutoPay Enrolled"}}
- "Free Night Award — expires Dec 31, 2026" → INCLUDE as {{"key":"free_night_award","label":"Free Night Award Expiry","value":"Dec 31, 2026","confidence":0.96,"source_snippet":"Free Night Award expires Dec 31, 2026"}}
- "2 Suite Night Awards available" → INCLUDE as {{"key":"suite_night_awards","label":"Suite Night Awards","value":"2 available","confidence":0.95,"source_snippet":"2 Suite Night Awards available"}}
- "Annual travel credit: $187 remaining" → INCLUDE as {{"key":"travel_credit_remaining","label":"Travel Credit Remaining","value":"$187","confidence":0.94,"source_snippet":"Annual travel credit $187 remaining"}}
- "Earn 5,000 bonus miles — book by Jul 15" → INCLUDE as {{"key":"bonus_miles_offer","label":"Bonus Miles Offer Deadline","value":"Jul 15, 2026","confidence":0.92,"source_snippet":"Earn 5,000 bonus miles book by Jul 15"}}
- "Global Upgrade Certificate — 1 available, expires Dec 31, 2026" → INCLUDE as {{"key":"upgrade_certificates","label":"Global Upgrade Certificates","value":"1 (exp Dec 31, 2026)","confidence":0.97,"source_snippet":"Global Upgrade Certificate 1 available expires Dec 31"}}
- "Companion Certificate — valid through Jan 15, 2027" → INCLUDE as {{"key":"companion_certificate","label":"Companion Certificate","value":"Valid through Jan 15, 2027","confidence":0.98,"source_snippet":"Companion Certificate valid through Jan 15, 2027"}}
- "Regional Upgrade Certificates: 4 available" → INCLUDE as {{"key":"regional_upgrade_certs","label":"Regional Upgrade Certificates","value":"4 available","confidence":0.97,"source_snippet":"Regional Upgrade Certificates 4 available"}}
- "Priority Pass membership — unlimited lounge visits" → INCLUDE as {{"key":"priority_pass","label":"Priority Pass Lounge Access","value":"Unlimited visits","confidence":0.90,"source_snippet":"Priority Pass membership unlimited lounge visits"}}
- "Free checked bag on all Delta flights" → INCLUDE as {{"key":"free_checked_bag","label":"Free Checked Bag Benefit","value":"All Delta flights","confidence":0.89,"source_snippet":"Free checked bag on all Delta flights"}}
- "Upcoming flight: SFO → JFK, Aug 14, 2026" → INCLUDE as {{"key":"upcoming_flight","label":"Upcoming Flight","value":"SFO → JFK, Aug 14, 2026","confidence":0.98,"source_snippet":"Upcoming flight SFO to JFK Aug 14 2026"}}

LABELING: write labels that make sense without knowing the site (no abbreviations, no page jargon). Labels should say what the value IS, not repeat the site name.

Return ONLY a JSON array, no other text:
[{{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617","value_type":"points","confidence":0.97,"source_snippet":"Rapid Rewards Points Balance 24,617"}}]

Rules:
- key: snake_case, 1-4 words
- label: 2-5 words, self-explanatory out of context
- value: exact current value — if empty, zero, or a login prompt, skip the field entirely
- value_type: one of points, currency, date, status, text, certificate, credit, booking, payment, progress, other
- confidence: float 0.0–1.0 — how certain you are this is a real personalized user fact (not generic copy). Aim for >0.85 on solid data; below 0.70 means you are guessing.
- source_snippet: verbatim excerpt (≤15 words) from the page text that most directly supports this value
- Optional metadata when clearly present: expiry_date, points, currency (strings)
- Each concept ONCE, no duplicates
- Max 25 fields
- If you find zero fields that pass the hard-exclude test, return an empty array []

ORDERING — sort fields in this exact priority order (most important first):
1. Account status or tier (Gold, Platinum, Diamond, A-List — only meaningful named tiers)
2. Primary balance, points, or miles total
3. Available benefits (certificates, credits, awards with quantities)
4. Expiration dates for benefits, points, or status
5. Progress toward next tier goal
6. Upcoming reservations or bookings (future dates only)
7. Payment info (balance due, due date, past-due amount, autopay status)
8. Account metadata (member since, loyalty ID/member number — these go LAST)

The FIRST field in the array becomes the hero display — make it the single most meaningful thing about this account.
CRITICAL: Member numbers, account IDs, and loyalty IDs must NEVER be first. Status tier or primary balance must always lead.
CRITICAL: Generic account labels ("Cardmember", "Member") must NEVER be included — only include named tiers with real meaning.
CRITICAL: Past reservations (date already occurred) must NEVER be included as "upcoming".