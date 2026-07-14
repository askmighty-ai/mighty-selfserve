# Contributing to Mighty — Product Guide

This guide explains how engineers should use the [Product Manifesto](docs/PRODUCT_MANIFESTO.md) when building, reviewing, and shipping work in this repository.

The manifesto is not marketing copy. It is a decision framework. When a feature, UI change, or API shape feels ambiguous, the manifesto breaks the tie.

---

## Before you build

1. **Read the manifesto.** Skim the principles table if you are short on time; read the full document before designing a new surface or flow.
2. **Name the user problem in manifesto terms.** Example: "User has 40 Gmail-discovered accounts and does not know which need login" maps to *Action only when blocked* and *Separate axes*.
3. **Check the anti-patterns list.** If your proposal requires something under "What we do not build," stop and reconsider—or explicitly document why an exception is warranted.

---

## During implementation

### UI and copy

- **Default to read-mostly.** New CTAs should appear only when the user is blocked (extension missing, session expired, extraction failed).
- **Use plain language.** Surface states like "Needs login" or "Updated yesterday," not internal enum names or component roles ("worker," `session_expired`).
- **Prefer one clear action over many.** Home shows a featured item, not a grid of equal-weight cards.
- **Canonical copy lives in `mighty/user_copy.py`.** Add or update strings there rather than scattering product language across templates.

### Backend and extension

- **Respect the three axes:** enrollment (discovery), session (logged in?), extraction (have data?). Do not infer session state from stale extracted data.
- **Extension-owned session truth.** The server records session events from the extension; it does not guess login state.
- **Silent success.** Background extraction completion should not require a user notification unless it resolves a previously blocked state or surfaces new actionable value.
- **Bump `extension/manifest.json` version whenever any file under `extension/` changes.** The Truth Dashboard version diagnostic only works if every shipped extension change gets a new version. Tests and `scripts/check_extension_version_bump.py` enforce a strict increase vs the merge base.

### Agent authorization

- **Full detail in authorization requests.** Fields submitted for approval become the permanent record. Summaries are not enough for emails, purchases, or file operations.
- **Consumer onboarding ≠ agent setup.** API keys and MCP configuration belong in Settings, not the primary account-connecting path.

---

## Pull requests

Every product-facing PR should use the [pull request template](.github/pull_request_template.md) and explicitly list which manifesto principles the change supports.

**Good PR framing:**

> **User problem:** After Gmail scan, users still click "Add" for each of 30 discovered accounts.  
> **Principles:** Zero bulk onboarding, Works quietly  
> **After:** High-confidence matches auto-enroll; dashboard shows "Tracking" until first natural visit.

**Weak PR framing (avoid):**

> Added sync button to account cards for convenience.

That contradicts *Natural-session capture* and *Action only when blocked* unless the sync is gated to failed extraction or expired session only.

---

## Design review checklist

Ask these questions before merging UI or flow changes:

1. Does this add a manual step the manifesto says Mighty should own?
2. Does this expose internal state the user cannot act on?
3. Would a healthy user with 50 synced accounts see this every day? (If yes, it probably belongs on Accounts or in a dismissible banner—not Home.)
4. If we ship this, could demo or placeholder data appear when real data exists?
5. Which principle would we violate if we removed this feature?

If you cannot answer (5) with a specific principle name, the change may not be justified.

---

## When principles conflict

Rare conflicts should be resolved in this order:

1. **Truthful by default** — never show fake data to paper over a gap.
2. **Action only when blocked** — do not hide real blockers to reduce noise.
3. **Works quietly** — prefer silent background behavior over user-visible process.
4. **Zero bulk onboarding** — reduce setup friction even if engineering is harder.

Document tradeoffs in the PR when you cannot satisfy every principle.

---

## Updating the manifesto

The manifesto should change infrequently. Propose manifesto updates when:

- The team agrees on a durable product shift (not a one-off shortcut).
- Repeated PR debates reveal an unstated principle worth naming.
- A principle is consistently ignored because it no longer matches reality.

Manifesto PRs should be labeled and reviewed with product intent in mind—not bundled silently into feature work.

---

## Related documents

| Document | Purpose |
|----------|---------|
| [docs/PRODUCT_MANIFESTO.md](docs/PRODUCT_MANIFESTO.md) | Product north star |
| [ALPHA.md](ALPHA.md) | Alpha tester flow and success criteria |
| [mighty/user_copy.py](mighty/user_copy.py) | Canonical user-facing strings |
| [.github/pull_request_template.md](.github/pull_request_template.md) | PR structure including manifesto principles |
