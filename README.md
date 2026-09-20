# jev-tests

Cheap, empirical tests of Jev / classifier.dev against Rajeev's real workloads.

## Plain-English answer: does Jev improve browser automation?

**On the one real task we measured, yes — but the sample is small, so treat it as
a strong signal rather than a proven result.**

| arm | runs | passed | median wall (all runs) | per decision |
|---|---|---|---|---|
| Jev + Playwriter | 5 | **4** | **16.1 s** | 1.4 s |
| GLM + Playwriter | 5 | 4 | 27.1 s | 2.6 s |
| Jev + Browser Relay | 5 | **5** | 13.1 s | 1.2 s |
| GLM + Browser Relay | 5 | 1 | 13.4 s | 2.5 s |

**Jev passed 9 of 10 runs; GLM passed 5 of 10.** Same loop, same browser bridge,
same verifier — only the decision model changed. Jev decides about twice as fast
(1.2–1.4 s vs 2.5–2.6 s per decision), and on Playwriter it finishes ~40% sooner
(16.1 s vs 27.1 s).

**Where the speed claim does not hold:** on Browser Relay the medians are level
(13.1 s vs 13.4 s). GLM is marginally *quicker* on the runs it completes
(12.8 s over completed runs vs Jev's 13.1 s) — it just fails most of them there.
So "Jev is faster" is true on Playwriter and roughly a tie on Relay; the robust
difference on Relay is reliability (5/5 vs 1/5), not speed.

**A correction, and it matters.** An earlier version of this page reported Jev
failing 0/5, and I said Jev could not finish tasks. That was wrong — it was a bug
in my test harness. I had merged Jev's two questions (which operation, then which
element) into one, which removed the element decision and made the policy loop.
With Jev's real two-question framing it passes. Both run sets are committed —
corrected in `results/browser-fastpath/live/`, broken in
`results/browser-fastpath/live-onequestion-shim/` — so you can check rather than
take my word for it.

**Where it still loses:** one Playwriter run in five looped until the step
budget. So the failure mode is real but rare here, not eliminated.

**Caveats, stated up front:**

- **Jev was reached through classifier.dev** (`jev-1.13.0` *is* Jev); this Mac has
  no TypeSafe key. Its scores are normalised per question and validated exactly as
  upstream validates TypeSafe's, so this is Jev's decision quality on a different
  endpoint — not a claim about TypeSafe.
- **Small n:** 5 reps per arm per transport, one controlled task. One flipped run
  changes Jev to 8/10.
- **No cost comparison:** GLM reports token usage, Jev does not, so the claim is
  wall time and pass rate only.
- **One step was removed for fairness:** TodoMVC's "mark complete" box is
  `opacity: 0`, so the shared menu never offers it to *either* policy.

Full write-up and raw per-rep JSON:
[results/browser-fastpath/README.md](results/browser-fastpath/README.md).

**Where it stands:** Jev looks worth using for repetitive, well-observed browser
actions where speed matters. Keep a supervisor on final "am I done?" decisions,
because it can occasionally loop or stop early.

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
