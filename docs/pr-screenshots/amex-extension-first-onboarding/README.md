# Amex first-insight onboarding — PR screenshots

Anticipation path: Add Mighty → Visit Amex → checking → first insight. Infrastructure stays quiet. Gmail is optional after first intelligence.

**Do not embed these images in chat.** Open files from the repository.

| File | What it shows |
|------|----------------|
| `attention.png` | Add Mighty to Chrome (`/extension-setup`) — install means, Visit Amex next |
| `all-clear.png` | Home — Visit American Express (no insight claimed yet) |
| `opportunity.png` | First insight on Home plus optional Find more accounts from Gmail |

## Notes

- Capture script: `scripts/capture_amex_extension_first_onboarding_screenshots.py`
- Signup redirects to `/extension-setup` and auto-enrolls Amex
- No heartbeat / diagnostics on the customer path (`?debug=1` admin only)
- CTA language stays action-true: Visit American Express — never “continue to your first insight”
