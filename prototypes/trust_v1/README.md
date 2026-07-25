# Mighty Trust Prototype v1

Static HTML/CSS/JS product prototype demonstrating Mighty redesigned around:

- `docs/TRUST_BY_DESIGN.md`
- `docs/FIRST_10_MINUTES.md`
- `docs/MIGHTY_VISUAL_SYSTEM_V1.md`

**Not production.** No Flask routes, templates, production CSS, or extension code were modified.

## Visual identity

**The Quiet Field** — accounts rest as steady points in a calm atmospheric plane; Mighty’s work is ambient motion beneath that field. When nothing needs you, the field stays still.

## Open

```bash
cd prototypes/trust_v1
python3 -m http.server 8770 --bind 127.0.0.1
```

Then visit `http://127.0.0.1:8770`.

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

## Motion

`prototype.js` advances discovery steps and reveals review rows. Honors `prefers-reduced-motion`.
