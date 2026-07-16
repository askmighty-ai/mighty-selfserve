# Amex Persistent Browser Proof of Concept

This is a developer-only feasibility test for Mighty’s intended account-access
model:

1. The user signs in once through a dedicated visible Amex browser window.
2. Mighty stores only browser profile/session state in a dedicated local profile.
3. Later checks reopen the same profile without a visible window.
4. The script reports whether Amex still treats that profile as authenticated.

It does **not** capture credentials, bypass MFA/CAPTCHA, or publish account data.

## Install the browser

From the repository virtual environment:

```bash
python -m playwright install chrome
```

Playwright may already find locally installed Chrome when using `--channel chrome`.
The install command is useful when the channel is unavailable.

## Step 1: establish the session

```bash
python scripts/amex_persistent_runtime.py login
```

An ordinary installed Google Chrome process opens with a dedicated Mighty Amex
profile. Playwright is not attached during login. Sign in normally, complete any
MFA, and confirm that the Amex account is authenticated. Then return to the
terminal and press Enter. The script closes only the dedicated Mighty Chrome
process group; it does not close or modify the user's regular Chrome windows or
profiles.

Profile data is stored at:

```text
~/.mighty/provider_runtime/amex
```

Do not point this spike at your everyday Chrome profile. Chromium does not allow
multiple processes to use the same user-data directory, and Mighty needs its own
isolated provider profile.

The earlier Playwright-controlled login diagnostics remain in the repository
for historical analysis, but native login mode does not create or update them.

## Step 2: verify without a visible window

```bash
python scripts/amex_persistent_runtime.py verify
```

The process exits `0` only when it finds authenticated evidence. It prints and
writes a sanitized result to:

```text
~/.mighty/provider_runtime/amex_last_result.json
```

## Step 3: restart test

Run the verification command, quit it, then run it again:

```bash
python scripts/amex_persistent_runtime.py verify
python scripts/amex_persistent_runtime.py verify
```

This tests whether the dedicated profile survives runtime restart.

## Diagnostic headed verification

When a headless verification is inconclusive, compare it with:

```bash
python scripts/amex_persistent_runtime.py verify --headed
```

A difference between headed and headless behavior is itself an important Amex
compatibility result.

## Success criteria

The spike succeeds only when:

- interactive login reaches `AUTHENTICATED`;
- later headless verification reaches `AUTHENTICATED`;
- the result remains authenticated after the runtime is stopped and restarted;
- routine verification does not open a visible window;
- session loss is reported as `SIGNED_OUT` or `INCONCLUSIVE`, never fabricated
  as authenticated.
