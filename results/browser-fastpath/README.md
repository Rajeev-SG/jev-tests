# Test 6 — Jev fast path over Playwriter / Browser Relay

**State: implementation live-validated on the Mac; screening matrix not yet run.**

## What was validated here

The PR #8 harness was exercised live on this Mac against the real upstream
`jev_ultrafast.agent.Agent` loop, the real `BridgeBrowser`, the real
`web-automation-microbench` tasks and their independent verifiers.

Because no TypeSafe API key exists on this machine (`TYPESAFE_API_KEY` is unset
and absent from the login keychain), the scored matrix cannot run: Jev's own
policy calls `https://api.typesafe.ai/v1/systemone` and fails without the key.
To validate the *transport and observed-node contract* — the part that must be
proven before scoring — `validation/live_validate.py` substitutes a deterministic
scripted policy for the TypeSafe calls and leaves everything else (Agent loop,
snapshot.js, BridgeBrowser, transport, verifier) real.

## Live results

| check | result |
|---|---|
| `uv sync` with pinned `jev-ultrafast@452c1ad` | **fixed** — first failed; see Bugs |
| Playwriter: navigate + observe + fill | pass |
| Playwriter: Enter compatibility commits todos | pass (2 todos saved) |
| Browser Relay: navigate + observe + fill | pass |
| Browser Relay: Enter compatibility commits todos | pass (2 todos saved) |
| Observed-node identity, model never supplies selectors | pass |
| Stale/covered target fails closed and forces re-observation | pass |
| Real-work URLs load and observe (chanel, porsche, rajeevg ×2) | pass |

Raw validation JSON is committed under `validation/`.

## Bugs found and fixed live

1. **`uv sync` could not resolve.** `requires-python = ">=3.11"` conflicted with
   `jev-ultrafast`'s `>=3.12`. Fixed to `>=3.12`.
2. **hatchling rejected the build.** The pinned git dependency needs
   `[tool.hatch.metadata] allow-direct-references = true`. Added.
3. **Transport output formats differ** (Browser Relay prints bare strings / JSON;
   Playwriter prints a `__JEV_JSON__`-prefixed line). The existing
   `_decode_jsonish` already handled all three; regression tests added.

Focused tests for every bug above and for the observed-node contract live in
`tests/test_browser_fastpath.py`.

## Blockers before the scored matrix can run

1. **No TypeSafe API key on this Mac.** Jev's `choose()`/`field_text()` call
   TypeSafe directly; without `TYPESAFE_API_KEY` every decision raises. Prior
   Test 6 work used classifier.dev instead and reported Jev that way. Either
   provide a TypeSafe key or wire the classifier.dev client from
   `lib/jevbench.py` into `jev_ultrafast.model.post_json`.
2. **A todo-toggle blindness (independent of the Enter gap).** TodoMVC's
   per-item complete checkbox is `opacity: 0` (the visible box is a CSS
   pseudo-element). Jev's snapshot visibility filter uses
   `checkVisibility({checkOpacity:true})`, so **the toggle is never offered in
   the action space at all**. On the canonical TodoMVC task, `Mark ONLY
   "Email supplier" complete` is therefore impossible for *stock* Jev on any
   transport — a second, separate reason the canonical task fails beyond the
   documented missing-Enter action. Recorded here as evidence; do not hide it.
3. **Browser Relay needs a focused tab for keystrokes.** `key Enter` is dropped
   when the attached tab is backgrounded. With the tab focused, Enter commits
   reliably (3/3 plain and 3/3 with `--text "\r"`). The harness must call
   `browser-relay focus --tab <id>` before a compat run, or the compat row will
   under-report.
4. **Browser Relay needed a Chrome with the extension loaded.** No everyday-Chrome
   profile was connected during this run; validation used a dedicated Chrome for
   Testing instance with the relay extension loaded. Confirm the user's profile
   is open with the extension enabled before scoring.

## Still to do (requires the key + focused Chrome)

Screening matrix with 2 reps per row, promotion to 5 for plausible frontier rows,
and the measured metrics listed in `docs/browser-fastpath.md`.
