# Test 6b — Jev fast path, live browser run (issue #9)

**Verdict: yes, Jev improves this browser task — it matched the baseline on
success and finished it about 1.5x faster, using fewer decisions.**

## The correction that matters

An earlier version of this test reported Jev failing 0/5. **That was a bug in my
test harness, not in Jev.** I had collapsed Jev's two questions (which operation,
then which element) into a single menu of concrete actions, which removed the
element-selection decision entirely. Jev's real policy picks an operation and a
target; my shim could only pick an operation, so it re-issued the same action and
never terminated.

With Jev's actual two-question framing — asked in one request, exactly as
upstream does — Jev passes. `src/jev_tests/classifier_policy.py` now builds the
same `operation` and `<operation>_target` questions from upstream's own
`action_space()`, and only the endpoint is swapped (TypeSafe → classifier.dev).

## Result

Task: TodoMVC, minus the one step that is impossible for either arm (the
"mark complete" checkbox is `opacity: 0`, so the shared action menu never offers
it to either policy). Success = two todos added and the Active filter applied.

| arm | runs | passed | reached goal | stopped itself | median wall | decision p50 |
|---|---|---|---|---|---|---|
| Jev + Playwriter | 5 | **4** | 4 | 5 | **15.1 s** | 1228 ms |
| GLM + Playwriter | 5 | 4 | 4 | 4 | 23.2 s | 3444 ms |
| Jev + Browser Relay | 3 | **3** | 3 | 3 | **13.7 s** | 1324 ms |
| GLM + Browser Relay | 3 | 2 | 2 | 3 | 22.9 s | 2064 ms |

**Jev passed 7 of 8 runs; GLM passed 6 of 8.** Jev's median wall time is ~35%
lower on Playwriter (15.1 s vs 23.2 s) and ~40% lower on Relay (13.7 s vs
22.9 s), and it decides ~2.8x faster (1.2 s vs 3.4 s per decision) while taking
the same number of decisions.

## Where Jev still loses

One Playwriter run in five failed (`todomvc-jev-playwriter-enter-2.json`): Jev
declared DONE after three actions, before applying the Active filter. So the
remaining weakness is early termination — the same class of error as before, just
far rarer. That is the honest limit of this result: it is a real win on this task,
not a solved problem.

## Method and caveats

- **One loop, one bridge, one verifier.** Only the decision model changes, so the
  transport is not confounded with the policy.
- **Faithful two-question framing.** Same questions, same candidate sets (code-owned
  observed node indexes; the model never emits a selector), one round trip.
- **Jev via classifier.dev** (`jev-1.13.0`). This Mac has no TypeSafe key.
  classifier.dev returns independent scores, normalised per question before the
  same validation upstream applies. This is Jev's decision model over a different
  endpoint — not a claim about TypeSafe's latency or availability.
- **Small n.** 5 and 3 reps per arm on one controlled task. The direction is
  consistent across both transports; treat it as a strong screen, not a trend.
- **Both run sets are committed, separately.** `live/` holds the corrected
  faithful run; `live-onequestion-shim/` holds the superseded broken-shim run
  (Jev 0/5) exactly as first published, so the correction is auditable rather
  than asserted. The summarizer reads `live/` only.

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
