# RESULTS.md — where to use Jev today

**Bottom line: Jev is worth using as a cheap prior or a confidence-gated
filter on two of the six use cases tested, and is not proven on the other four.
No case justified replacing a GLM call outright.**

Measured spend for the whole programme: **at least $0.11** of GLM-5.3-Flash
calls on OpenRouter — $0.104 of it visible in the per-test summaries
(`cost_usd_including_cache`) plus superseded rounds still in the response cache.
It is a lower bound: a retried call that was billed but not cached is not
counted. classifier.dev and Jev cost **$0.00** inside its free tier. Budget
was $5.

Model identity: classifier.dev's fast tier *is* Jev (`jev-1.13.0`). Direct Jev
(TypeSafe key) is not available on this machine, so every Jev figure here is
classifier.dev over HTTP; the direct-API cost is reported alongside as an
illustrative figure at $0.042 / 1M input tokens.

## Residual privacy risk, stated plainly

Session-derived inputs (task titles, working directories, tool result excerpts)
**are sent to two third-party endpoints**: classifier.dev and OpenRouter. The
protection is a redactor plus a scanner gate: every payload is scrubbed of
credential-shaped strings and then scanned, and a scanner hit raises
`SecretDetected` and fails the benchmark rather than sending. The scanner covers
OAuth tokens in query parameters, bearer headers, key=value secrets and the
known key prefixes.

That is pattern-based protection, not a guarantee. A credential in a shape
neither regex list knows about would pass through. This risk is accepted here
because the samples are truncated to a few hundred characters of decision
context and were screened with zero findings, but anyone running these
benchmarks on their own sessions should treat the residual risk as real.

## Table

<!-- BEGIN GENERATED TABLE -->

| # | Use case | Decision | Verdict | Measured quality | Jev latency | LLM latency | Cost | Expensive calls avoided |
|---|---|---|---|---|---|---|---|---|
| 1 | adpi corpus sieve | worth synthesis vs not | **NOT PROVEN** | 0.886 accuracy vs 0.889 always-keep; drop precision 0.47; 2.7% false-negative; GLM agrees with the label only 0.52; the accuracy gap vs the baseline is 2.0 records out of 837, inside the noise | 28.6 ms/decision (recorded run) | p50 3576 / p95 11194 ms (recorded run) | Jev $0.0018 direct-API equivalent; GLM $0.0249 measured; illustrative corpus pass $0.086 -> $0.082 | 4.5% of records (38 of 837) |
| 2 | coding-agent tool routing | which tool family next | **PRIOR ONLY** | top-1 0.61 / top-2 0.87 vs majority 0.42 / repeat-last 0.55 / GLM 0.42 over 1265 decisions (time-ordered subset, not a random sample: largest composition drift source:codex -47.4 points) | n/a | p50 5259 / p95 14372 ms (recorded run) | Jev $0.0229 direct-API equivalent; GLM $0.0187; $0.134 per 1k decisions illustrative | none at 98% accuracy; gate picked on a tuning half gives held-out 12.9% coverage at 0.87 (in-sample 12.8% at 0.90) |
| 3 | context pruning | KEEP / TRUNCATE / DROP per history unit | **NOT PROVEN** | 0.0% of context removed at a 0.7 gate; 309 kept / 0 truncated / 0 dropped over 309 chunks in 5 sessions | 585.5 ms/decision (recorded run) | p50 8188 / p95 31282 ms (recorded run) | Jev $0.00 measured; GLM $0.0384 recorded | 0% |
| 4 | PR review gate | skip the reviewer? | **NOT PROVEN for skipping** | 28.4% of reviews avoided, recall 0.65, 72% of skips wrong; rules avoid 12.7% at 0.85 recall over 102 PRs | 253.7 ms/decision (recorded run) | p50 5613 / p95 10870 ms (recorded run) | Jev $0.00 measured; GLM $0.0108; illustrative full review of all 102 PRs $0.0095 | 28.4%, at a 0.72 false-skip rate |
| 5 | retrieval -> relevance gate | keep chunk or not | **NOT PROVEN — BLOCKED** | 0 of 27 retrieval pools contained any file the session used; pool sizes [1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2] | n/a | n/a | $0.00, no calls needed to establish the blocker | unmeasurable |
| 6 | browser next-action | inspect / click / type / ... | **USE AS GATE** | raw 0.68 vs majority 0.86 and GLM 0.69; threshold 0.7 picked on a tuning half: held-out 63.3% coverage at 0.969 accuracy (ci95 0.92-0.99); in-sample was 63.2% at 0.972, over 400 scored decisions | 246.3 ms/decision (recorded run) | p50 4850 / p95 17397 ms (recorded run) | Jev $0.00; GLM $0.0114 | 63.2% of planner turns on the confident subset |

<!-- END GENERATED TABLE -->

Table generated from the committed `results/*/summary.json` and `cost.json` by
`python3 tests/render_results.py` (`--check` fails if it drifts).

## Where to use it

### USE AS GATE — browser next-action routing (Test 6)

**Integration point:** in the browser-agent loop, immediately before the planner
call — `state → Jev (classifier.dev) → if confidence ≥ 0.7 and the predicted
action is inspect or click, execute it; otherwise call the planner`.

**Why, and this is the out-of-sample number:** the 0.7 threshold was chosen on a
tuning half of the decisions and then applied to the disjoint held-out half,
where it covers **63.3%** of decisions at **96.9% accuracy** (95% CI
0.924–0.988). In-sample it was 63.2% at 97.2%, so the operating point survives
held-out validation rather than being an artefact of threshold selection. That
is 400 decisions in 7 batched calls, 246 ms per decision (recorded) and $0
instead of seconds per call and about $0.19 per 1,000 planner calls. The two
actions it handles well (`inspect` recall 0.70, `click` 0.65) are 96.4% of the
recorded real-work decisions.

**Do not** let it own rare deterministic actions: `type`, `wait` and `navigate`
recall were 0.00 in this sample.

### USE AS PRIOR — coding-agent tool routing (Test 2)

**Integration point:** before a reasoning turn, give the model Jev's top-2
families as a hint, not as a decision. Top-2 is 0.870 against a 0.416 majority
baseline; the whole 1,265-decision run cost $0.023 in direct-API terms, about
$0.018 per 1,000 decisions.

**Two caveats that matter, both measured:**

1. **The evaluated subset is not a random sample.** It is the first 1,265 of
   2,983 decisions, cut off where the classifier.dev daily cap ran out. Its
   composition drifts from the full set by up to 47 points (codex share −47.4,
   pi +25.2, claude +18.4), so this is a partial result, not a validated
   estimate.
2. **Threshold selection costs accuracy.** A 90% auto-accuracy gate is met at
   90.1% in-sample but only **86.7% on the held-out half** (12.9% coverage, CI
   0.778–0.924) when the threshold is chosen on a tuning split.

**Do not** use it as a router that skips the model: the ≥98% accuracy criterion
the issue set is not met anywhere on the curve.

## Where not to use it

1. **In front of the adpi synthesis layer.** It removes 4.5% of records and gets
   53% of the removals wrong. Its accuracy (0.886) is *below* the always-keep
   baseline (0.889) — a gap of 2 records out of 837, i.e. inside the noise — and
   the ground-truth label itself is weak: GLM agreed with it only 52% of the
   time.
2. **As a PR review skip gate.** 72% of its skips needed review (21 of 29).
   The deterministic rules are less bad (12.7% avoided, 0.85 recall, but 9 of
   13 skips wrong), and a full review costs about $0.0001 per PR anyway — there
   is almost nothing to save and a defect to miss.
3. **To prune coding-agent context.** At a safe gate it prunes nothing; its drop
   judgements are its least confident ones.
4. **Between retrieval and the coding model** — no claim either way. Test 5 was
   a **harness failure**: the retrieval step could not build a usable candidate
   pool (0 of 27 pools contained a file the session had used), so nothing was
   measured about Jev. That is an experiment to fix, not evidence against the
   gate.

## How thresholds are validated

Every operating point is reported twice. The threshold sweep and calibration
table in each summary are **in-sample** — computed on the same items used to
report accuracy — so the summaries also carry a `heldout_gate_*` block per
target: the threshold is chosen on a deterministic tuning half and scored on a
disjoint held-out half, with a 95% Wilson interval on the accuracy.

That check changes two numbers materially and leaves one standing:

| test | in-sample | held-out |
|---|---|---|
| Test 6 browser gate (≥95% target) | 63.2% coverage at 97.2% | **63.3% at 96.9%** (CI 0.924–0.988) |
| Test 2 routing gate (≥90% target) | 12.8% coverage at 90.1% | 12.9% at 86.7% (CI 0.778–0.924) |

Test 6's verdict holds. Test 2's in-sample figure was optimistic by about three
points, which is why its verdict is "prior only" and not a routing decision.

## Cross-cutting findings

- **Calibrated confidence is Jev's real value, not raw accuracy.** On three of
  six tests the majority class beat Jev on overall accuracy, yet the
  confidence-gated slice was consistently strong (0.97 on Test 6; 0.90 at 13%
  coverage on Test 2; 0.94 at 55% coverage on Test 1). The gate pattern — act
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

## How latency and cost figures are produced

Both clients keep this-run measurements and earlier-run recordings apart, and
each summary carries fields that say which is which:

- **classifier.dev** (`classifier_dev_*`): `batch_latency` and
  `ms_per_item_amortised` cover cold calls made in the process that wrote the
  artefact. `batch_latency_historical_from_cache` and
  `ms_per_item_amortised_historical` hold cold measurements recorded by an
  earlier run, with `historical_meaning` spelling that out. Cache hits
  contribute no latency (`warm_cache_hits_excluded_from_latency`).
- **GLM** (`glm`): `latency` is this run's cold calls;
  `latency_historical_from_cache` is what earlier runs recorded. Where a
  regenerated summary had no cold calls, the human-readable tables quote the
  historical field and say so.
- **Cost**: `cost_usd_including_cache` sums the billed amounts recorded on every
  call that was made, including ones later served from cache. That is spend that
  really happened, and it is a lower bound because a billed failed call is not
  cached.

Every number in this file and in `results/*/README.md` comes from the committed
`summary.json` / `cost.json` at this commit. Where a figure had to come from a
recorded earlier run rather than the regeneration, the text says "recorded".

The table above is generated: `python3 tests/render_results.py` prints it and
`--check` exits non-zero if RESULTS.md disagrees with the artefacts, so the two
cannot drift apart silently.

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

## Reproducibility, stated per test

Each `summary.json` carries a `provenance` block saying whether a third party can
re-derive its numbers:

| test | reproducible from the repo? | why |
|---|---|---|
| 1 corpus sieve | **yes** | inputs come from the public `Rajeev-SG/adpi-data`; `build_dataset.py` re-downloads them |
| 2 tool routing | no | inputs are parsed from local agent sessions (real work paths, client names) |
| 3 context pruning | no | same |
| 4 PR gate | no | PR metadata from private repositories |
| 5 retrieval gate | no | local Recoll index + local session ground truth |
| 6 browser routing | no | parsed from the private `web-automation-microbench` traces |

For the five that are not committed, the extractor is committed and the summary
says so; the numbers are not independently checkable by a third party, only the
method is. The OpenRouter key is read from `OPENROUTER_API_KEY` (portable) with a
macOS keychain fallback; classifier.dev needs no key at all.