# Test 6b — Jev fast path, live browser run (issue #9)

**Verdict, plain English: on a real browser task, Jev did NOT improve browser
automation. It is faster per decision than GLM but it failed the task; GLM
completed it.**

## What was run

A real browser task, driven live, with one loop, one browser bridge, one
verifier. The only thing that changed between arms was who picks the next action:

| arm | decision model | text helper |
|---|---|---|
| Jev | classifier.dev fast tier = `jev-1.13.0` | `openai/gpt-4.1-mini` |
| baseline | `z-ai/glm-5.3-flash` | `openai/gpt-4.1-mini` |

Task: the TodoMVC microbenchmark, minus its one step that is impossible for BOTH
arms — "mark a todo complete". That checkbox is `opacity: 0` on the page, and the
action menu comes from the same `snapshot.js` for either model, so neither can be
offered it. Removing it makes the comparison fair instead of testing the harness.
Success = two todos added and the Active filter applied.

Reps: 5 per arm on Playwriter, 3 per arm on Browser Relay.

## Result

| arm | valid runs | passed | median wall | decision p50 | model |
|---|---|---|---|---|---|
| Jev + Playwriter | 3 | **0** | 27.9 s | 1220 ms | jev-1.13.0 |
| GLM + Playwriter | 5 | **3** | 20.6 s | 1891 ms | glm-5.3-flash |
| Jev + Browser Relay | 2 | **0** | 23.7 s | 1085 ms | jev-1.13.0 |
| GLM + Browser Relay | 3 | **1** | 17.8 s | 2356 ms | glm-5.3-flash |

**Jev: 0 of 5 valid runs passed. GLM: 4 of 8 passed.** Jev's decisions are
~1.5–2× faster (about 1.1–1.2 s vs 1.9–2.4 s each), but that speed does not
translate into a finished task.

## Why Jev fails

Every Jev failure has the same shape. It performs the right first actions, then
gets stuck re-issuing an action it has already satisfied and never declares the
task done:

```
fill "Email supplier"   ✓
fill "Review invoice"   ✓
fill e2 (the same field again)   ← no page change
click e5 (Active)       ✓
click e5 again           ← already on Active
click e5 again
click e5 again            → blocked at the step budget
```

GLM, on the same page and the same menu, stops and emits `DONE`. So the gap is
decision quality — specifically Jev not recognising "already done" and not
choosing DONE — not the transport, which worked for both.

## Caveats, stated plainly

- **Small n.** 5 and 3 reps per arm is a screen, not a trend. The direction is
  consistent across both transports, but these are small numbers.
- **One task.** This is TodoMVC, a controlled microbenchmark. It says nothing
  about richer pages yet.
- **Four Playwriter runs raised a 10 s transport timeout** (2 Jev, 0 GLM). Those
  are counted as errors and excluded from the valid-run columns; they are the
  harness, not the policy, and they are preserved in the raw JSON.
- **Jev was reached through classifier.dev**, whose fast tier is Jev
  (`jev-1.13.0`). This Mac has no TypeSafe key. The model is the same Jev the
  offline Test 6 numbers used; the prompt framing differs (one question instead
  of two), which is a real difference from the upstream TypeSafe path.
- **The "mark complete" step was removed** for fairness, as described above. On
  the unmodified task both arms fail, which is a finding about the shared action
  space, not about either model.

## Reproduce

```sh
export OPENROUTER_API_KEY=<key>          # GLM baseline + text helper
export JEV_USE_CACHE=0                   # measure real decision latency
JEV_BROWSER_BACKEND=playwriter PLAYWRITER_SESSION=<id> \
  uv run python scripts/live_jev_run.py --task todomvc --backend playwriter \
    --policy jev --fill-enter --compat --rep 1
```

Raw per-rep JSON: `results/browser-fastpath/live/`. Aggregate:
`results/browser-fastpath/live-summary.json`.
