# jev-tests

Cheap, empirical tests of Jev / classifier.dev against Rajeev's real workloads.

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
