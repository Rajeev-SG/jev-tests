# RESULTS.md — where to use Jev today

**Bottom line: Jev is worth using as a cheap prior or a confidence-gated
filter on two of the six use cases tested, and is not proven on the other four.
No case justified replacing a GLM call outright.**

Measured spend for the whole programme: **$0.11** (543 GLM-5.3-Flash calls,
OpenRouter). classifier.dev and Jev cost **$0.00** inside its free tier.
Budget was $5.

Model identity: classifier.dev's fast tier *is* Jev (`jev-1.13.0`). Direct Jev
(TypeSafe key) is not available on this machine, so every Jev figure here is
classifier.dev over HTTP; the direct-API cost is reported alongside as an
illustrative figure at $0.042 / 1M input tokens.

## Table

| # | Use case | Decision | Verdict | Measured quality | Jev latency | LLM latency | Cost per 1k decisions | Expensive calls avoided |
|---|---|---|---|---|---|---|---|---|
| 1 | adpi corpus sieve | worth synthesis vs not | **NOT PROVEN** | 0.882 accuracy vs 0.889 always-keep; drop precision 0.41; 2.6% false-negative | 6.1 ms/record (837 in one batch: 5.1 s) | GLM p50 3.6 s, p95 11.2 s per call | Jev $0.0014; GLM $0.106 | 3.8% of records, 59% of those useful |
| 2 | coding-agent tool routing | which tool family next | **NOT PROVEN as a router; USE AS PRIOR** | top-1 0.613, **top-2 0.870** vs majority 0.416, repeat-last 0.549, GLM 0.422 | ≈6 ms/decision amortised | GLM p50 5.3 s, p95 14.4 s | Jev $0.003; GLM $0.134 | none at 98% accuracy; 12.8% coverage at 90% accuracy |
| 3 | context pruning | KEEP / TRUNCATE / DROP per history unit | **NOT PROVEN** | 0% of context removed at a safe 0.7 gate; raw drops 17–27% but low-confidence | 585 ms/unit (smart tier) | GLM p50 seconds | Jev $0.006; GLM replay $0.013 for 5 calls | 0% |
| 4 | PR review gate | skip the reviewer? | **NOT PROVEN for skipping** | avoids 28.4% of reviews, recall 0.650, **72% of skips were wrong**; rules avoid 12.7% at 0.850 recall | batch, sub-second | GLM sample 197 prompt tokens/PR | Jev $0.001; GLM $0.0095 for 102 full reviews | 28% of reviews, at an unacceptable miss rate |
| 5 | retrieval → relevance gate | keep chunk or not | **NOT PROVEN — blocked** | 0 of 27 retrieval pools contained any file the session used; pools were 1–2 documents | n/a | n/a | n/a | unmeasurable |
| 6 | browser next-action | inspect / click / type / … | **USE AS GATE** | raw 0.680 vs majority 0.865; **≥0.7 confidence: 63.2% coverage at 97.2% accuracy** | 246 ms/decision | GLM 0.689 accuracy, seconds per call | Jev $0.00; GLM $0.10 measured for the sample | 63% of planner turns on the confident subset |

## Where to use it

### USE AS GATE — browser next-action routing (Test 6)

**Integration point:** in the browser-agent loop, immediately before the planner
call — `state → Jev (classifier.dev) → if confidence ≥ 0.7 and the predicted
action is inspect or click, execute it; otherwise call the planner`.

**Why:** 63.2% of next-action decisions can be taken at 97.2% accuracy, at
246 ms and $0 instead of seconds and ~$0.10 per 1k planner calls. The two
actions it handles well (`inspect` recall 0.70, `click` 0.65) are 93% of the
recorded real-work decisions.

**Do not** let it own rare deterministic actions: `type`, `wait` and `navigate`
recall were 0.00 in this sample.

### USE AS PRIOR — coding-agent tool routing (Test 2)

**Integration point:** before a reasoning turn, give the model Jev's top-2
families as a hint, not as a decision. Top-2 is 0.870 against a 0.416 majority
baseline, at 6 ms and $0.003 per 1k decisions.

**Do not** use it as a router that skips the model: the ≥98% accuracy criterion
the issue set is not met anywhere on the confidence curve (best 93.6% at 3.7%
coverage).

## Where not to use it

1. **In front of the adpi synthesis layer.** It removes 3.8% of records and gets
   59% of the removals wrong. The ground-truth label itself is weak — GLM agreed
   with it only 52% of the time.
2. **As a PR review skip gate.** 72% of its skips needed review. The
   deterministic rules are safer (12.7% avoided, 0.85 recall) and a full review
   costs $0.0001 per PR anyway.
3. **To prune coding-agent context.** At a safe gate it prunes nothing; its drop
   judgements are its least confident ones.
4. **Between retrieval and the coding model** — unmeasured, because the local
   Recoll index cannot supply real candidate pools (see Test 5).

## Cross-cutting findings

- **Calibrated confidence is Jev's real value, not raw accuracy.** On three of
  six tests the majority class beat Jev on overall accuracy, yet the
  confidence-gated slice was consistently strong (0.97 on Test 6; 0.90 at 13%
  coverage on Test 2; 0.96 at 54% coverage on Test 1). The gate pattern — act
  only when confident, escalate the rest — is what the data supports.
- **GLM-5.3-Flash is a weak cheap classifier.** It lost to Jev on Test 2
  (0.422 vs 0.613) and to the majority class on Test 6, and was only 26%
  accurate on the items Jev was unsure about. "Escalate the unsure ones to GLM"
  does not rescue a weak classifier on these tasks.
- **The free tier's daily cap is an operational constraint.** classifier.dev
  free allows 20,000 fast classifications per IP per day. A tool-routing
  workload at 3,000 decisions/day fits; a rerun-heavy workflow does not, and the
  cap was hit during this programme. For production volume, budget for Pro or a
  direct TypeSafe key.
- **Cost only matters where the replaced call is expensive.** The PR gate saves
  $0.0095 in total. The browser and routing cases save seconds and cents per
  thousand. At these volumes the saving that matters is wall-clock, not dollars.

## Reproduce everything

```sh
python3 tests/01_corpus_sieve/build_dataset.py && python3 tests/01_corpus_sieve/run.py
python3 tests/02_tool_routing/extract.py && python3 tests/02_tool_routing/run.py
python3 tests/03_context_pruning/extract.py && python3 tests/03_context_pruning/run.py
python3 tests/04_pr_gate/collect.py && python3 tests/04_pr_gate/run.py
python3 tests/05_retrieval_gate/run.py --dry-run
python3 tests/06_browser_routing/extract.py && python3 tests/06_browser_routing/run.py
```

Every third-party response is cached under `results/.cache/`, so a rerun
reproduces the numbers without new spend. Prices come from
`pricing/pricing-2026-09-19.json`, read from OpenRouter and classifier.dev on
2026-09-19. Session-derived samples and private-repo PR data stay local
(`.gitignore`); aggregate results are committed.

## Honest limitations

- Test 2 is a **partial run**: 1,265 of 2,983 decisions, because the free tier's
  daily cap was spent mid-programme. Codex sessions are under-represented (15 of
  1,450 there).
- Test 3 used the smart tier rather than fast, for the same reason.
- Test 4's positive set rests on CI failures plus a coarse file-overlap rework
  signal; `changes_requested` and `follow_up_fixes` were both zero across the
  102 PRs, so the base rate and recall figures are approximate.
- Test 5 could not be run at all: it is a blocked experiment, not a negative
  result.
- Test 6 is offline replay of recorded traces; **no live browser runs were made**.
- Absolute-cost figures use measured per-item token counts from samples; a real
  review or extraction call sends more context than the sample did, so the
  illustrative numbers are lower bounds.