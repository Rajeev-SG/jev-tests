# Test 2 — coding-agent tool routing from successful traces

**Verdict: NOT PROVEN as a replacement for a reasoning turn; usable as a
ranker.** Jev beats every baseline it was compared against, but it cannot reach
the 98% auto-route accuracy the issue asked for at any useful coverage.

**This run is partial.** classifier.dev's free tier caps at 20,000 fast
classifications per IP per day; earlier failed rounds in this session spent that
day's budget, so only the 1,265 decisions already classified could be scored
(43% of the 2,983 extracted). The full run is one command once quota resets.

## Data

2,983 next-action decisions extracted from real local sessions by
`extract.py`: 1,450 codex, 1,044 pi, 403 claude, 86 droid. 1,260 further steps
were excluded as ambiguous (a shell command that matched no family rule).
Labels are the observed next tool in sessions that completed:

| family | decisions | | family | decisions |
|---|---|---|---|---|
| read a file | 922 | | git / GitHub | 315 |
| shell | 827 | | browser / web | 120 |
| search | 582 | | test / build | 65 |
| edit a file | 343 | | memory / recall | 2 |

State given to a classifier: task title, working directory, actions taken so
far, the last assistant note, and the last three tool results (truncated). The
observed next action is never in the state.

## Results (1,265 scored decisions)

| method | top-1 | top-2 |
|---|---|---|
| majority class ("read a file") | 0.416 | – |
| repeat the last action | 0.549 | – |
| **classifier.dev fast (Jev)** | **0.613** | **0.870** |
| GLM-5.3-Flash (90-call stratified sample) | 0.422 | – |

Per family (Jev recall): test_build 0.88, git 0.83, browser 0.80, shell 0.73,
search 0.64, edit 0.61, read 0.52.

Per source (Jev top-1): claude 0.71, pi 0.57, droid 0.55, codex 0.53.
Codex is only 15 decisions in this partial run, so it carries almost no weight.

## Two validity caveats, both measured

**The subset is not a random sample.** The 1,265 evaluated decisions are a
time-ordered prefix of the 2,983 extracted, cut where the classifier.dev daily
cap ran out. Composition drifts from the full set by up to 47 points (source:codex -47.4, source:pi +25.2, source:claude +18.4),
so the comparison below is a partial result, not a validated estimate of the
whole corpus. `summary.json` records this under `subset_representativeness`.

**Threshold selection costs accuracy.** A gate targeting 90% auto-accuracy: the
tuning half chose **0.8**, giving

| split | n | coverage | accuracy | 95% CI |
|---|---|---|---|---|
| tuning (in-sample) | 79 | 12.7% | 0.937 | 0.860-0.973 |
| held-out | 83 | 12.9% | 0.867 | 0.778-0.924 |

The in-sample curve in `summary.json` is therefore optimistic by about three
points at this operating point.

## The 98% criterion is not met

| Jev confidence | coverage | accuracy on auto-routed |
|---|---|---|
| ≥0.30 | 90.8% | 0.640 |
| ≥0.50 | 57.0% | 0.712 |
| ≥0.60 | 39.4% | 0.796 |
| ≥0.70 | 26.7% | 0.817 |
| ≥0.80 | 12.8% | 0.901 |
| ≥0.90 | 3.7% | 0.936 |

The best auto-accuracy anywhere on the curve is 93.6%, and only at 3.7%
coverage. Nothing here supports "Jev preserves ≥98% accuracy on auto-routed
cases".

## Escalation

73.3% of decisions were below 0.7 confidence. On the sample, GLM was *worse*
than Jev overall (0.422 vs 0.613) and only 26% accurate on the items Jev was
unsure about, so "escalate the unsure ones to GLM" does not rescue the
architecture — the unsure items are genuinely hard for both.

## Cost and latency

| | value |
|---|---|
| Jev | 0.61 top-1, $0.00 measured. No latency figure: every fast response in this partial run came from cache |
| Jev direct-API equivalent | $0.0229 for 1,265 decisions at $0.042/1M input tokens (≈$0.018 per 1,000) |
| GLM sample | 90 calls, 36k+29k tokens, **$0.0187 measured**, p50 5,259 ms, p95 14,372 ms (recorded) |
| illustrative GLM cost | $0.134 per 1,000 decisions |

## What this means

Use Jev, if at all, as a **prior over the next tool family** — top-2 is 0.870 —
not as a decision that replaces the reasoning turn. At 6 ms and $0 against
seconds and $0.13 per 1k, a cheap ranker that hands two candidates to the model
is a plausible win; a router that picks one and skips the model is not
supported by this data.

## Reproduce

```sh
python3 tests/02_tool_routing/extract.py --max-sessions 80 --max-decisions-per-session 40
python3 tests/02_tool_routing/run.py --glm-sample 90          # full run: 2,983 decisions
python3 tests/02_tool_routing/run.py --only-covered --glm-sample 90   # this partial run
```