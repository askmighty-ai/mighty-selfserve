# Trust Prototype V1 — Product review screenshots

Full-page captures at approximately **1440×900** viewport width from the static prototype in `prototypes/trust_v1/`.

Review these files locally or from the repository — they are not embedded in chat.

| Screenshot | Prototype HTML | Primary user question | Primary CTA |
|------------|----------------|-----------------------|-------------|
| `01-landing.png` | `01-landing.html` | What is Mighty, and is it worth my time? | **Get started** |
| `02-create-account.png` | `02-create-account.html` | How do I begin safely? | **Create account** |
| `03-welcome.png` | `03-welcome.html` | Did it work — and what happens now? | **Continue** |
| `04-how-mighty-works.png` | `04-how-mighty-works.html` | How does Mighty work, what will you ask for, and what won’t you do? | **Connect Gmail** |
| `05-connect-gmail.png` | `05-connect-gmail.html` | Why Gmail — and what are the limits? | **Continue to Google** |
| `06-discovering-accounts.png` | `06-discover-accounts.html` | Is something happening — and how are you finding accounts? | None while scanning (wait); prototype link to view results |
| `07-review-accounts.png` | `07-review-accounts.html` | What did you find — and what do I keep? | **Start watching these accounts** |
| `08-home-waiting.png` | `08-home-waiting.html` | What do I do now — and is Mighty working? | **Set up Mighty in Chrome** |
| `09-home-healthy.png` | `09-home-healthy.html` | Did it work — and do I need to do anything else? | None required (optional: **View accounts**) |
| `10-accounts.png` | `10-accounts.html` | What is Mighty watching? | **Find more accounts** |
| `11-activity.png` | `11-activity.html` | What happened, and can I review it? | None required (reviewable history) |

## Source docs

- `docs/TRUST_BY_DESIGN.md`
- `docs/FIRST_10_MINUTES.md`

## How to view the live prototype

```bash
cd prototypes/trust_v1
python3 -m http.server 8770 --bind 127.0.0.1
```

Then open `http://127.0.0.1:8770/`.
