# Mighty Alpha / Invite cohort — American Express

**For invite-only testers.** One vertical: **Install Mighty in Chrome → Visit Amex → see your first insight.** Gmail discovery is an optional enhancement after that first insight — not required for onboarding.

**App:** https://mighty-selfserve-production.up.railway.app  
**Founder one-pager:** [docs/FOUNDER_TEST_CARD.md](docs/FOUNDER_TEST_CARD.md)  
**Limitations:** See [docs/BETA_INVITE_BRIEF.md](docs/BETA_INVITE_BRIEF.md) and [docs/BETA_MLP.md](docs/BETA_MLP.md) §7.

---

## Before you start

1. **Desktop Chrome** (not mobile Safari — Mighty in Chrome is required for updates).
2. **Sign up** at the app URL with the email you were invited with.
3. Keep this guide open for the Chrome install steps (also shown in-product on `/extension-setup`).
4. Have your **American Express** login ready (User ID + password + 2FA as required).

Gmail is **not** required to complete the Amex beta path.

---

## The flow (~10–15 minutes)

### 1. Set up Mighty in Chrome

- After signup you land on **Set up Mighty in Chrome** (`/extension-setup`).
- Confirm you’re in a **normal Chrome window** (not Incognito or Guest).
- Download → unzip → `chrome://extensions` → Developer mode → **Load unpacked** → pin Mighty.
- Click **I’ve installed Mighty** and wait until it confirms connected.

**What should happen:** American Express is already enrolled for watching. Chrome is ready before your first visit.

### 2. Visit American Express

- Continue to **Home**. Primary ask: **Visit American Express**.
- Open Amex in Chrome and sign in on americanexpress.com (including 2FA).
- Keep the Mighty tab available in the background.

**What should happen:** Mighty discovers useful account information (for example rental-car status benefits) and shows your first insight on Home. You are not asked for an Amex password inside Mighty.

### 3. See your first insight

- Return to Home. Expect a clear insight card (or “Checking American Express…” while a refresh runs).
- If refresh fails, previous information stays; use **Refresh from American Express** if offered.

### 4. Optional — Find more from Gmail

- Only after your first insight, Home may offer **Find more accounts from Gmail**.
- Open `/email-scan` if you want mail-based discovery of other programs.
- Skip this entirely if Amex is all you care about for the beta.

### 5. Return later

- Open Home again. Expect either **You’re good** or **one clear ask** — not a stack of equal primaries.

---

## If something stalls

| Symptom | Try |
|---------|-----|
| Extension not detected | Finish install steps on `/extension-setup`, reload that page |
| Home asks to visit Amex | Sign in at americanexpress.com in the same Chrome profile with Mighty loaded |
| Optional Gmail OAuth cancel | Return later via Home’s optional CTA or `/email-scan` — not required |
| Locked out of Mighty password | Contact beta support (invite brief) — do not expect reset if mailer is down |

**Support:** See invite brief for the named channel.
