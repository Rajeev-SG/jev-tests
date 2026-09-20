# Test 6b — Jev fast path, live browser run (issue #9)

**Verdict: on this task Jev is faster and at least as reliable, but the samples
are small — read it as a strong signal, not a proven general result.**

## The correction that matters

An earlier version of this test reported Jev failing 0/5. **That was a bug in my
harness, not in Jev.** `classifier_policy.py` had collapsed Jev's two questions
(which operation, then which element) into one menu of concrete actions. That
deleted the element decision, so the policy could only restate the same
operation — exactly the "repeat-action loop" I had blamed on Jev.

With upstream's real two-question framing — asked in one request, as
`jev-ultrafast` does — Jev passes. `tests/test_upstream_conformance.py` now pins
that framing against upstream's own `action_space()`, so the next divergence
fails CI instead of silently producing a wrong result.

## Result (balanced: 5 reps per arm per transport, 20 runs)

Task: TodoMVC, minus the one step impossible for either arm (the "mark complete"
checkbox is `opacity: 0`, so the shared menu never offers it to either policy).
Success = two todos added and the Active filter applied.

| arm | runs | passed | median wall | decision p50 |
|---|---|---|---|---|
| Jev + Playwriter | 5 | **4** | **16.1 s** | 1350 ms |
| GLM + Playwriter | 5 | 4 | 27.1 s | 2619 ms |
| Jev + Browser Relay | 5 | **5** | 13.1 s | 1218 ms |
| GLM + Browser Relay | 5 | 1 | 12.8 s | 2453 ms |

**Jev passed 9 of 10; GLM passed 5 of 10.** Jev decides roughly 2x faster
(1.2–1.4 s vs 2.5–2.6 s per decision) and its median wall time is ~40% lower on
Playwriter (16.1 s vs 27.1 s).

On relay the medians are level (13.1 s vs 12.8 s) but the pass rates are not:
GLM failed 4 of 5 relay runs, three of them by stopping without applying the
filter and one by declining to supply a field value. Jev passed 5 of 5.

## Where Jev still loses

One Playwriter run in five (`todomvc-jev-playwriter-enter-3.json`) looped on an
already-satisfied action for 27 decisions and hit the step budget. So the failure
mode is real but rare here, and it is the same class of error as before.

## Method and caveats

- **One loop, one bridge, one verifier.** Only the decision model changes.
- **Faithful two-question framing**, pinned by a conformance test against
  upstream's `action_space()`; the model never emits a selector.
- **Jev via classifier.dev** (`jev-1.13.0`). This Mac has no TypeSafe key.
  classifier.dev's independent scores are normalised per question before
  upstream's own validation. Single-candidate heads are answered locally, since
  classifier.dev rejects fewer than two labels. This measures Jev's decisions
  over a different endpoint — not TypeSafe's latency or availability.
- **Not token-parity.** GLM reports token usage and Jev does not, so cost could
  not be compared; the claim here is wall time and pass rate only.
- **Small n.** 5 reps per arm per transport, one controlled task. A strong
  screen, not a trend; a single flip changes Jev to 8/10.
- **Bucket discipline.** Infrastructure faults (timeouts, no attached tab),
  policy-format faults (declining to supply a value) and goal-state failures are
  counted in separate buckets; `scripts/summarize_live.py` asserts p95 >= p50 and
  refuses to score runs missing required fields.
- **Both run sets are committed.** `live/` holds this corrected run;
  `live-onequestion-shim/` holds the superseded broken run, copied byte-identical
  from commit `9892447` so the correction can be checked rather than trusted.

## Reproduce

```sh
export OPENROUTER_API_KEY=<key>          # GLM baseline + text helper
export JEV_USE_CACHE=0                   # measure real decision latency
JEV_BROWSER_BACKEND=playwriter PLAYWRITER_SESSION=<id> \
  uv run python scripts/live_jev_run.py --task todomvc --backend playwriter \
    --policy jev --fill-enter --compat --rep 1
uv run python scripts/summarize_live.py
```

Raw per-rep JSON: `results/browser-fastpath/live/`. Aggregate:
`results/browser-fastpath/live-summary.json`.
