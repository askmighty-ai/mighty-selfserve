# Mighty Alpha — American Express

**For trusted testers only.** This guide walks you through the one flow we're validating: Gmail → Amex discovery → login → real data on Home.

**App:** https://mighty-selfserve-production.up.railway.app

---

## Before you start

1. **Desktop Chrome** (not mobile Safari — the extension is required).
2. **Install Mighty Sync** — load the unpacked extension from the repo's `extension/` folder (ask Jonathan for the latest build if needed).
3. **Sign up** at the app URL above.
4. **Configure the extension once:** after login, visit **Settings → Setup Chrome Extension** (or go to `/extension-setup`). The page auto-configures your API key.

You need a Gmail account that receives **American Express** emails (statements, alerts, marketing — any message from `@americanexpress.com` counts).

---

## The flow (~5 minutes)

### 1. Connect Gmail

- Go to **Accounts** → **Scan Gmail to find accounts** (or `/email-scan`).
- Click **Gmail** and approve read-only access.

**What should happen:** Mighty scans your inbox, finds Amex, and **automatically** opens the Amex connect screen. You should **not** need to click Add or pick Amex from a list.

### 2. Connect American Express

- The connect modal opens for **American Express**.
- Click **Open in Chrome →** and log into Amex normally (User ID + password, 2FA if prompted).
- Keep the Mighty tab open in the background.

**What should happen:** The modal moves from "Waiting for extension…" → **Connected** → redirects to **Home**.

### 3. See your real data on Home

- On **Home** (`/dashboard`), find the **American Express** account card.
- It should show **Membership Rewards Points** with your actual balance (e.g. `142,500`).
- The Daily Brief at the top should show **your real data** — no fake Marriott free nights, demo dollar totals, or "Demo" tags.

**What should happen:** Home reloads automatically within ~8 seconds of extraction. You should not need to click Sync or refresh manually.

---

## What we're testing

| Step | Success looks like |
|------|-------------------|
| Gmail scan | Redirects straight to Amex connect |
| Amex login | Modal shows "Connected" without entering password into Mighty |
| Extraction | MR points appear on the account card |
| Home | Truthful brief — no placeholder/demo content |
| Auto-update | Home refreshes when extraction completes |

---

## If something breaks

**Gmail card is greyed out**  
OAuth isn't configured on the server. Tell Jonathan — don't use IMAP for this test.

**"Waiting for extension…" never resolves**  
- Confirm the extension is installed and you visited `/extension-setup` while logged in.
- Chrome → Extensions → Mighty Sync → **Reload** after any update.

**Amex modal says "Needs login"**  
Finish logging in on the Amex tab. Make sure you're on `americanexpress.com`, not a third-party wallet app.

**Connected but no points on Home**  
- Visit your Amex account home or Membership Rewards page while logged in.
- Wait ~30 seconds; Home should auto-reload.
- If still empty, note the Amex URL you landed on and send a screenshot to Jonathan.

**Home shows demo content (Marriott cert, $1,240, "Demo" tag)**  
That's a bug — screenshot and report. Real data should suppress all demo content.

---

## What to send back

After completing the flow, reply with:

1. **Did the magic moment land?** (yes/no — MR points on Home without manual refresh)
2. **Your MR balance** as shown (or "missing")
3. **Any step that confused you**
4. **Screenshots** of Home and the connect modal if anything failed

---

## Out of scope for this alpha

- Other providers (Delta, Marriott, Chase, etc.)
- Mobile app
- Credits, balances, or payment due dates (only MR points for now)
- Notifications or Activity feed

Thank you for testing.
