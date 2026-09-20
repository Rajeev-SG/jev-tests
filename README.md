# jev-tests

Cheap, empirical tests of Jev / classifier.dev against Rajeev's real workloads.

## Plain-English answer: does Jev improve browser automation?

**Not proven. Jev decides about twice as fast and reaches the goal state as often
as GLM on Playwriter, but it never once stopped on its own — so it never actually
finished a task. Its failure is specific and repeatable: it re-clicks something
it has already done.**

| arm | runs | reached goal state | stopped itself (DONE) | why it failed | median wall | per decision |
|---|---|---|---|---|---|---|
| Jev + Playwriter | 5 | **3** | **0** | repeated action ×3, transport ×2 | 27.9 s | 1.2 s |
| GLM + Playwriter | 5 | **3** | 4 | step budget ×1 | 20.6 s | 1.9 s |
| Jev + Browser Relay | 3 | 0 | 0 | repeated action ×2, empty fill ×1 | 23.7 s | 1.1 s |
| GLM + Browser Relay | 3 | 1 | 3 | — | 17.8 s | 2.4 s |

Two different questions, answered separately:

1. **Goal state reached?** Jev tied GLM on Playwriter — 3 of 5, and 3 of the 3
   runs not killed by a transport timeout. It clicked the filter and left the
   page in exactly the state GLM's passing runs ended in.
2. **Did it finish?** No, in any run. Every Jev non-termination has the same
   cause: it re-issued an action it had already satisfied until the step cap, and
   never chose DONE. GLM, same page, same action menu, stopped itself in 4 of 5
   and 3 of 3.

So the only claim the data supports is **Jev decides about twice as fast**
(1.1–1.2 s vs 1.9–2.4 s). The pass-rate difference is not statistically
meaningful at n=5 and n=3 on one task — read it as inconclusive, not as Jev
losing the task.

**Two caveats that matter, stated up front:**

- **This tests classifier.dev's one-question re-framing, not upstream Jev.**
  Upstream Jev asks two questions per step (operation, then target).
  classifier.dev returns one classification, so they collapse into one menu
  choice. The extra termination signal upstream gets from its second question is
  **not exercised here — and termination is exactly what failed.** Do not read
  this as a verdict on upstream Jev's stopping behaviour.
- **One step was removed for fairness.** TodoMVC's "mark a todo complete" box is
  `opacity: 0`, so the shared action menu never offers it to *either* policy;
  leaving it in would test the harness, not the model.

Full write-up, per-run buckets and raw JSON:
[results/browser-fastpath/README.md](results/browser-fastpath/README.md).

**The transport works**: Jev can drive your real Chrome through both Playwriter
and Browser Relay — navigate, observe, click, type, scroll, and it refuses stale
targets.

**What to try next:** upstream Jev's real two-question framing (or confirm
classifier.dev can express it) and re-run, since the missing termination signal
is the observed failure. A task with a long run of repetitive non-terminal
actions remains the most likely place a fast decider pays off.

## Question

Where should a fast decision model replace or gate ordinary LLM calls in Rajeev's coding-agent and intelligence pipelines?

Every test must answer in plain English:

1. **What decision is Jev making?**
2. **Is it accurate enough on our own data?**
3. **How much faster is it?**
4. **What does it cost?**
5. **What would the equivalent LLM path cost?**
6. **What percentage of expensive LLM calls/tokens can we safely avoid?**
7. **Where should we use it, and where should we not?**

## Common benchmark rules

- Use **real historical data** from Rajeev's repos / agent traces, not synthetic toy examples.
- Keep tests cheap. Prefer offline replay and small stratified samples.
- classifier.dev fast is free within its published limits; use it where simple text classification is sufficient.
- Use direct Jev where Choice / Score / Noul, shared-state multi-question decisions, or calibrated confidence are the point of the test.
- Compare against the **actual replacement path**, normally GLM-5.3-Flash, plus a trivial deterministic baseline where useful.
- Do not force a decision. Test confidence thresholds and **Jev → LLM escalation**.
- Record raw timing, token counts, provider/model, request count, correctness and confidence.
- Calculate costs from a versioned pricing snapshot committed to this repo. Show both measured spend and illustrative equivalent spend.
- Current reference prices at repo creation (2026-09-19):
  - Jev: **$0.042 / 1M input tokens, output free**.
  - GLM-5.3-Flash on OpenRouter: **$0.075 / 1M input, $0.25 / 1M output**.
  - classifier.dev: **$0** within free per-IP limits.
- Do not put secrets, private credentials or sensitive work/client content into third-party APIs. Use public repo/project data, coding-agent traces, and redacted/local-only material.
- All results must be reproducible from scripts in this repo.

## Tests

Verdicts and numbers: **[RESULTS.md](RESULTS.md)**. Each test's full write-up is
in `results/<test>/README.md`.

| # | Issue | Test | Real data | Decision being tested | Verdict |
|---|---|---|---|---|---|
| 1 | #2 | Corpus relevance sieve | adpi-data, 837 real records | Which harvested records deserve expensive synthesis? | not proven |
| 2 | #3 | Coding-agent tool routing | 2,983 decisions from real sessions | Which tool/tool-family should be used next? | prior only |
| 3 | #4 | Agent context pruning | 5 long codex sessions | KEEP / TRUNCATE / DROP prior context | not proven |
| 4 | #5 | PR review gate | 102 real PRs x 5 repos | Does this change need expensive review? | not proven |
| 5 | #6 | Retrieval relevance gate | local Recoll index + session ground truth | Which retrieved chunks enter context? | blocked |
| 6 | #7 | Browser next-action routing | 946 recorded microbench decisions | Which browser action runs next? | use as gate |
| 7 | #9 | Jev fast path over Playwriter / Browser Relay | live Chrome, real microbench tasks | Can Jev drive your own browser instead of leaning on the model? | plumbing works; benefit unmeasured (no TypeSafe key) |

## How to run

```sh
python3 tests/<test>/extract.py   # or build_dataset.py / collect.py
python3 tests/<test>/run.py       # writes results/<test>/
```

Standard library only. `lib/jevbench.py` holds the two clients, the response
cache, redaction and the metrics. classifier.dev needs no key; GLM needs an
OpenRouter key in `OPENROUTER_API_KEY` or the login keychain (`openrouter`).

## Required output

Each test writes:

- `results/<test>/raw.jsonl`
- `results/<test>/summary.json`
- `results/<test>/README.md` with a short plain-English verdict
- one CSV suitable for quick inspection
- charts only where they improve understanding

The repo root should ultimately contain a one-page `RESULTS.md` answering:

> **Where should Rajeev use Jev today, what should stay on an LLM, and what are the measured savings?**

No marketing language. If a test fails, say so.


## Test 6 architecture: Jev fast path over Playwriter / Browser Relay

The browser experiment uses the existing `web-automation-microbench` task/verifier contract rather than inventing a second benchmark.

```text
coding / reasoning agent
        |
        | high-level browser goal
        v
   Jev Ultrafast policy
        |
        | indexed operation + observed node
        v
 transport adapter
   |             |
Playwriter   Browser Relay
   |             |
   +------> existing Chrome
```

The reasoning model owns the goal. Jev owns repetitive next-action selection. A small text helper is used only when text must be generated.

The model never invents CSS selectors, coordinates, JavaScript or shell commands. Jev chooses from observed indexed DOM nodes; the bridge validates the selected node and creates any transport selector internally.

Upstream Jev Ultrafast is pinned to `browser-use/jev-ultrafast@452c1ad2dd628008f1d5608f28158d76e49e6cc0`, the functional 7-second-demo commit.

### Conditions

Compare the same task/verifier under:

1. current GLM-5.3-Flash + Browser Relay baseline;
2. current GLM-5.3-Flash + Playwriter baseline;
3. stock Jev Ultrafast + Browser Harness control;
4. Jev Ultrafast + Browser Relay;
5. Jev Ultrafast + Playwriter;
6. where required, a separately-labelled Jev + Enter compatibility condition.

Stock Jev currently has no arbitrary keypress operation, while the existing TodoMVC benchmark requires Enter to commit each todo. A stock failure is evidence and must not be hidden by the compatibility row.

Use TodoMVC for latency plus 3-5 replayable tasks from the harvested microbench corpus. Measure independent pass/fail, wall time, Jev p50/p95 decision latency, action/decision/text-helper counts, browser calls, stale retries, cost and planner turns eliminated.

Implementation contract: [docs/browser-fastpath.md](docs/browser-fastpath.md).
