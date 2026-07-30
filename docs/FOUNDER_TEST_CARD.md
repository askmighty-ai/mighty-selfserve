# Founder Test Card — Invite Path

**Environment:** https://mighty-selfserve-production.up.railway.app  
**Build:** Invite-path beta · demo / research content off  
**You need:** Desktop Chrome · your Amex login · (optional after first insight) Gmail for extra discovery

---

## 1. Open Mighty

1. Go to https://mighty-selfserve-production.up.railway.app  
2. **Create account** (email + password, 6+ characters)  
3. You should land on **Set up Mighty in Chrome** (`/extension-setup`) — not Gmail

---

## 2. Install Mighty in Chrome

**Use a normal Chrome window — not Incognito or Guest.** Same window for Mighty and `chrome://extensions`.

1. Confirm **I’m in a normal Chrome window**  
2. Click **Download Mighty in Chrome** → unzip  
3. Chrome → `chrome://extensions` → turn on **Developer mode**  
4. **Load unpacked** → select the unzipped `mighty-in-chrome` folder  
5. Pin the extension · if it was already installed, click **Reload**  
6. Click **I’ve installed Mighty** and wait for connected (or use **Continue to Home** if you need to leave)  
7. If not detected: open **Beta detection diagnostics** on the same page — note the first incomplete stage and recent events (send to Jonathan)

**Important:** After pulling a new build, remove the old extension and Load unpacked from a fresh **Download Mighty in Chrome** zip so you get service-worker + diagnostics fixes (v1.3.22+).

---

## 3. Test journey (in order)

1. **Continue to Home** — Amex should already be enrolled for watching  
2. **Visit American Express** — sign in on americanexpress.com (including 2FA)  
3. **See first insight** on Home (account intelligence card, or Checking / Visit prompt)  
4. **Optional:** **Find more accounts from Gmail** — only after first insight; skip if not testing mail discovery  
5. **Come back later** — open Home again; You’re good or one ask (not a stack)

---

## 4. Factory reset (same email — no eng)

**Preferred:**

1. Go to https://mighty-selfserve-production.up.railway.app/beta/restart  
   (also linked when signup says the email already exists, and from Sign in → Factory reset)  
2. Enter that email + your **current Mighty password**  
3. Check the confirmation box → **Delete data and start over**  
4. You land on the **public landing** (signed out), with a short “account deleted” note. Click **Create account** / **Get Started** with the same email — first-time onboarding starts over.  
5. Chrome: on `chrome://extensions`, click **Reload** on Mighty (or remove and reinstall from `/extension-setup`) so it picks up the new account. If setup doesn’t say connected, use **Continue to Home** and keep going.

**If you forgot the Mighty password:** try Forgot password on Sign in. If that fails, email Jonathan with the account email — operator wipe at `/admin/founder-reset`, then Create account again.

**Alternate while signed in:** Settings → Permanently delete account (same outcome: public landing).

---

## 5. Report a blockage

Email Jonathan with: journey step (signup / Chrome / Visit Amex / first insight / optional Gmail / Home) · expected vs actual · screenshot · approximate time.

**Expected (not bugs):** Chrome-first · Amex enrolled at signup · sideload install · desktop Chrome required · Gmail optional after first insight · sparse Opportunities · no weekly digest · never type Amex password into Mighty
