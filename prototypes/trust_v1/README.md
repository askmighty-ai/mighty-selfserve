# Mighty Trust Prototype v1

Static HTML/CSS product prototype demonstrating Mighty redesigned around:

- `docs/TRUST_BY_DESIGN.md`
- `docs/FIRST_10_MINUTES.md`

**Not production.** No Flask routes, templates, or production CSS were modified.

## Open

Open `index.html` in a browser, or serve the folder:

```bash
cd prototypes/trust_v1
python3 -m http.server 8765
```

Then visit `http://localhost:8765`.

## Pages

| # | File | Primary question |
|---|------|------------------|
| 1 | `01-landing.html` | What is Mighty? |
| 2 | `02-create-account.html` | How do I begin safely? |
| 3 | `03-welcome.html` | Did it work — what happens now? |
| 4 | `04-how-mighty-works.html` | How does it work, and what won’t you do? |
| 5 | `05-connect-gmail.html` | Why Gmail — and what are the limits? |
| 6 | `06-discover-accounts.html` | Is something happening? |
| 7 | `07-review-accounts.html` | What did you find — what do I keep? |
| 8 | `08-home-waiting.html` | What do I do now? |
| 9 | `09-home-healthy.html` | Does anything need me? |
| 10 | `10-accounts.html` | What is Mighty watching? |
| 11 | `11-activity.html` | What happened, and can I review it? |
| — | `home-opportunity.html` | Bonus Home state: calm value opportunity |

## Design principles applied

1. Explain before you ask
2. One primary question / one primary CTA per screen
3. Least privilege, said out loud
4. User stays the operator
5. Empty teaches; all-clear is success
6. Show the work when stakes are high
7. Calm automation over urgency theater
