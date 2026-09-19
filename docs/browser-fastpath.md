# Browser fast-path benchmark contract

## Hypothesis

For repetitive DOM-heavy browser work, Jev can replace many GLM planner turns while Playwriter or Browser Relay retains access to the user's existing Chrome session.

## Architecture

```text
goal
 -> Jev choose(operation, observed indexed target)
 -> generated text helper only when TYPE_TEXT
 -> BridgeBrowser
 -> Playwriter | Browser Relay
 -> Chrome
 -> structured Jev snapshot
 -> repeat
```

Retain upstream `jev_ultrafast.agent.Agent`. Swap only the browser constructor for `BridgeBrowser`.

### Selector rule

Jev never emits selectors. `snapshot.js` retains actual DOM-node identity in `window.__jevFast.nodes`. The bridge validates the selected node, stamps a short-lived code-owned attribute, and the transport uses that generated selector for the native action.

Allowed: observed node id -> bridge-generated selector.

Forbidden: model-generated selector, coordinate, JavaScript or shell command.

## Benchmark source

Use `Rajeev-SG/web-automation-microbench` as the canonical task, reset and verifier source. Do not fork its benchmark framework.

### TodoMVC

Run:

- current Browser Relay + GLM baseline;
- current Playwriter + GLM baseline;
- stock Jev + Browser Harness control;
- stock Jev + Browser Relay;
- stock Jev + Playwriter;
- Jev+Enter + Browser Relay;
- Jev+Enter + Playwriter.

The `+Enter` rows are compatibility experiments, not stock Jev. Stock Jev lacks an arbitrary keypress action; the canonical TodoMVC task needs Enter to commit each item.

### Real-work corpus

Start with 3-5 tasks already known to work in thin DOM-capable harnesses. Prefer a spread of deterministic DOM, search/extract/act, dynamic UI, longer-horizon work and one safe logged-in-session task if reproducible.

Exclude canvas/vision-only tasks from the primary Jev claim.

## Measurement

Every row uses the same starting state and independent verifier. Record:

- pass/fail;
- wall time and setup time;
- action count;
- Jev decision count;
- decision p50/p95;
- text-helper count/latency;
- Browser Relay / Playwriter command count;
- stale-page retries;
- raw usage returned by Jev/TypeSafe;
- measured Jev/text-helper cost;
- GLM baseline token/cost;
- planner turns avoided;
- failure class.

## Replication

Follow the microbench policy: 2 reps to screen, 5 for plausible frontier candidates, 10 only for close/high-variance cases. Preserve failed runs.

## Required result

`RESULTS.md` must answer:

1. Which browser decisions should Jev own?
2. Which tasks still need the full reasoning model?
3. Does Jev improve Browser Relay, Playwriter, both or neither?
4. Wall-time and cost delta per successful task.
5. Planner turns eliminated.
6. Fallback frequency.
7. Whether the benefit grows with task length.

## Live validation status (issue #9)

The harness was exercised live on this Mac. The `BridgeBrowser` navigates,
observes, fills, clicks and scrolls against both Playwriter and Browser Relay;
Enter-commit works on both; the observed-node contract holds and stale/covered
targets fail closed.

Two blockers stop the scored matrix here:

1. **No TypeSafe key on this Mac.** `jev_ultrafast.model` posts to
   `https://api.typesafe.ai/v1/systemone` with `TYPESAFE_API_KEY`; unset, every
   decision raises. Prior Test 6 work used classifier.dev (`lib/jevbench.py`),
   so scoring needs either a TypeSafe key or the classifier.dev client wired
   into `jev_ultrafast.model.post_json`.
2. **Stock Jev cannot complete the canonical TodoMVC task** for two independent
   reasons: no arbitrary keypress action (documented), and the per-item complete
   checkbox is `opacity: 0`, so Jev's `checkOpacity:true` visibility filter drops
   it from the action space entirely.

Operational notes for scoring:

- Browser Relay drops `key` input when the attached tab is backgrounded. Focus
  the tab (`browser-relay focus --tab <id>`) before a compat run.
- The relay needs a Chrome with its extension loaded; a dedicated Chrome for
  Testing instance with the extension unpacked works for validation.
- `scripts/live_validate.py` replays the real Agent loop + bridge + verifier with
  a scripted policy (no key required), writing JSON to
  `results/browser-fastpath/validation/`.
