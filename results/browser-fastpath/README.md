# Test 6b — Jev fast path, live browser run (issue #9)

**Verdict: the speed advantage is real; task improvement is not proven. Jev
reached the goal state as often as GLM but never declared the task done.**

## What was run

A real browser task driven live: one loop, one browser bridge, one verifier. The
only difference between arms is who picks the next action.

| arm | decision model | text helper |
|---|---|---|
| Jev | classifier.dev fast tier = `jev-1.13.0` | `openai/gpt-4.1-mini` |
| baseline | `z-ai/glm-5.3-flash` | `openai/gpt-4.1-mini` |

Task: TodoMVC, minus the one step impossible for BOTH arms — "mark a todo
complete". That checkbox is `opacity: 0`, and the action menu comes from the same
`snapshot.js` for either model, so neither policy can be offered it. Success =
two todos added and the Active filter applied.

Reps: 5 per arm on Playwriter, 3 per arm on Browser Relay. Scored runs are the
`+enter` condition only.

## Result

Two separate questions, reported separately — a policy that reaches the goal but
never stops is not the same as one that never reached the goal.

| arm | runs | reached goal state | stopped itself (DONE) | median wall | decision p50 |
|---|---|---|---|---|---|
| Jev + Playwriter | 5 | **3** | **0** | 27.9 s | 1220 ms |
| GLM + Playwriter | 5 | **3** | 4 | 20.6 s | 1891 ms |
| Jev + Browser Relay | 3 | 0 | 0 | 23.7 s | 1085 ms |
| GLM + Browser Relay | 3 | 1 | 3 | 17.8 s | 2356 ms |

Termination cause per arm (`termination_failure` in each raw file):

| arm | causes |
|---|---|
| Jev + Playwriter | repeated_action ×3, transport_error ×2 |
| Jev + Browser Relay | repeated_action ×2, policy_format_error ×1 |
| GLM + Playwriter | step_budget ×1 |
| GLM + Browser Relay | none |

Every Jev failure to terminate is the same cause: the policy re-issued an action
it had already satisfied while the page did not change. That is now detected and
named in each artifact rather than only showing up as a slow wall time.

Buckets are explicit and every run is counted (`scripts/summarize_live.py`):

- `jev+playwriter`: 3 `goal_state_only`, 2 `transport_error`
- `jev+browser-relay`: 2 `no_goal_state`, 1 `transport_error`
- `glm+playwriter`: 3 `pass`, 2 `no_goal_state`
- `glm+browser-relay`: 1 `pass`, 2 `no_goal_state`

**The one claim the data supports: Jev decides about twice as fast** (~1.1–1.2 s
vs ~1.9–2.4 s per decision).

**The claim it does not support: that Jev improves task success.** Jev reached
the goal state 3 of 5 on Playwriter — the same as GLM — but its three successful
runs are scored `goal_state_only`, not pass, because Jev never chose DONE. On
Browser Relay Jev reached the goal state 0 of 3 (one run died on a transport
timeout, two stalled). At n=5 and n=3 on one task the difference is not
statistically meaningful; treat it as inconclusive.

## Why Jev never finishes

Both models reach the same page states, but Jev re-issues an action it has
already satisfied and never terminates:

```
fill "Email supplier"   ✓
fill "Review invoice"   ✓
fill e2 (the same field again)   ← no page change
click e5 (Active)       ✓        ← goal state reached here
click e5 again
click e5 again            → step cap; never says DONE
```

GLM, on the same page and menu, stops and emits DONE. The gap is termination
judgement, not the transport.

## Limitations, in order of importance

1. **This is Jev-derived, one-question framing — not upstream Jev.** Upstream
   Jev asks two questions per step (operation, then target). classifier.dev
   returns a single classification, so the two collapse into one menu choice. The
   extra "is the goal met?" signal upstream gets from its second question is not
   exercised here — and that is precisely where Jev failed. Nothing in this
   result should be read as a verdict on upstream Jev's termination behaviour.
2. **One step removed for fairness** (the invisible complete-checkbox), as above.
3. **Small n.** 5 and 3 reps per arm is a screen. The direction is consistent
   across both transports, but the pass-rate comparison is inconclusive.
4. **Transport noise.** Two Jev Playwriter runs died on a 10 s transport timeout
   (bucket `transport_error`); one Jev Browser Relay run died because the policy
   requested a fill with no value, which is now rejected before it reaches the
   browser and bucketed separately (`policy_format_error`). Every run is
   preserved in its own bucket, never silently dropped. No measured wall or
   decision latencies for these runs are included in any median.
5. **The summary is generated** by `scripts/summarize_live.py` from the per-rep
   JSONs, and every bucket is traceable: older artifacts were stamped with the
   run's own verifier predicate by `scripts/backfill_live_artifacts.py`, so goal
   state is never re-derived from a task-specific literal. The summarizer refuses
   to score a run that lacks the field rather than guessing.

## Reproduce

```sh
export OPENROUTER_API_KEY=<key>          # GLM baseline + text helper
export JEV_USE_CACHE=0                   # measure real decision latency, not cache hits
JEV_BROWSER_BACKEND=playwriter PLAYWRITER_SESSION=<id> \
  uv run python scripts/live_jev_run.py --task todomvc --backend playwriter \
    --policy jev --fill-enter --compat --rep 1
uv run python scripts/summarize_live.py
```

Raw per-rep JSON: `results/browser-fastpath/live/`. Aggregate:
`results/browser-fastpath/live-summary.json`.
