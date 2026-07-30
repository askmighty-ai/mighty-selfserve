# Founder Test Card — Invite Path

**Environment:** https://mighty-selfserve-production.up.railway.app  
**Build:** Invite-path beta · demo / research content off  
**You need:** Desktop Chrome · your Amex login · (optional after first insight) Gmail for extra discovery

**Product rule:** Time-to-first-insight is the only metric before discovery. The Chrome add-on is infrastructure. Anticipation, not configuration.

**Emotional path:** interesting → add Mighty → visit Amex → checking → “I didn’t know that.”

---

## 1. Open Mighty

1. Go to https://mighty-selfserve-production.up.railway.app  
2. **Create account** (email + password, 6+ characters)  
3. You should land on **Add Mighty to Chrome** (`/extension-setup`) — not Gmail

---

## 2. Add Mighty to Chrome

**Use a normal Chrome window — not Incognito or Guest.**

1. **Download Mighty** → unzip  
2. Chrome → `chrome://extensions` → **Developer mode** → **Load unpacked** → unzipped folder  
3. Click **I’ve added Mighty**  
4. Primary CTA should be **Visit American Express** (not “continue to insight”)  
5. If stuck: admin only — `/extension-setup?debug=1` while signed in as admin

**Important:** After a new build, reload or reinstall from a fresh download.

---

## 3. Test journey (in order)

1. **Visit American Express** — sign in on americanexpress.com (including 2FA); leave Mighty open  
2. Return to Mighty — expect **We’re checking your American Express account…**  
3. **See first insight** — only then should copy claim something was found  
4. **Optional:** **Find more accounts from Gmail** — only after first insight  
5. **Come back later** — You’re good or one ask (not a stack)

---

## 4. Factory reset (same email — no eng)

**Preferred:**

1. Go to https://mighty-selfserve-production.up.railway.app/beta/restart  
   (also linked when signup says the email already exists, and from Sign in → Factory reset)  
2. Enter that email + your **current Mighty password**  
3. Check the confirmation box → **Delete data and start over**  
4. You land on the **public landing** (signed out). Create account again with the same email.  
5. Chrome: **Reload** Mighty on `chrome://extensions` (or reinstall from `/extension-setup`).

**If you forgot the Mighty password:** try Forgot password on Sign in. If that fails, email Jonathan — operator wipe at `/admin/founder-reset`.

**Alternate while signed in:** Settings → Permanently delete account.

---

## 5. Report a blockage

Email Jonathan with: journey step (signup / add Mighty / Visit Amex / checking / first insight / optional Gmail / Home) · expected vs actual · screenshot · approximate time.

**Expected (not bugs):** Chrome first · Amex enrolled at signup · sideload install · desktop Chrome required · Gmail optional after first insight · no heartbeat/diagnostics on customer path · never type Amex password into Mighty
