# Test 4 — Jev gate before expensive PR/code review

**Verdict: NOT PROVEN for autonomous skipping.** The gate avoids 28% of full
reviews but misses 33% of the PRs that actually needed review. Its false-skip
rate is 72%. Use it at most to *prioritise* a review queue.

## Data

102 real PRs from openreview (30), ad-platform-intelligence (30), codex-home
(30), project-recall (11) and jev-tests (1), collected through the GitHub API.

Outcome signals — none of which is "merged":

- CI failure on the PR
- a review requesting changes
- later PRs that reference this one and start with fix/revert
- **rework**: a later fix/revert PR touching at least one of the same files

`needed_review` is the OR of those: **60 of 102 PRs (58.8%)**.

Caveat, stated plainly: `changes_requested` was 0 across all 102 PRs (this
workflow merges without formal change requests) and `follow_up_fixes` was 0, so
the positive set rests on CI failures (6) plus the file-overlap rework signal.
That signal is coarse — it will count unrelated later fixes that happened to
touch the same file — so both the base rate and the recall figures are
approximate. They are the objective signals available, and merged status was
deliberately not used as proof of safety.

## Results

| method | reviews avoided | recall of needed review | false skips |
|---|---|---|---|
| always review (baseline) | 0 | 1.000 | – |
| deterministic rules (size + path patterns) | 13 (12.7%) | 0.850 | 69% (9 of 13) |
| **Jev gate, ≥0.7** | **29 (28.4%)** | **0.650** | **72%** |
| GLM-5.3-Flash gate (59-PR sample) | 21 (35.6%) | 0.644 | 100% |

Missed examples at the ≥0.7 setting include `ad-platform-intelligence#50, #49,
#42, #40, #38, #37, #35, #28` and `codex-home#117, #116`. The cheap rule
baseline is less bad — recall 0.85 against 0.65 — but it still gets 9 of its 13
skips wrong, so it is not a skip gate either; it is a shortcut for changes that
are obviously safe by construction (docs, one-line edits).

Full gate curve is in `summary.json` under `jev_gate_curve`; it is monotone in
the wrong direction for a skip decision — raising the confidence threshold
removes few decisions and only slowly improves recall.

## Cost and latency

- Jev: 204 classifications (two narrow questions per PR), $0.00 measured.
- GLM sample: 59 calls, ≈197 prompt tokens per PR, **$0.0108 recorded spend**.
- Illustrative full review of all 102 PRs at this token count: **$0.0095** —
  a lower bound, because a real reviewer reads the patch, not just the diff
  summary.

The economics are the point against the gate here: at this PR size the full
review is already fractions of a cent, so avoiding 28% of reviews saves
essentially nothing while risking a missed defect.

## What this means

Do not skip review on Jev's say-so at this false-skip rate. The honest options
are (a) keep the deterministic rules for obvious docs/one-line changes, which
already give 12.7% avoidance at 0.85 recall, and (b) use Jev to order the queue,
not to empty it. A Jev gate would become interesting only if a stronger
outcome signal showed the current recall estimate is pessimistic.

## Reproduce

```sh
python3 tests/04_pr_gate/collect.py --per-repo 30     # uses gh; data/prs is gitignored
python3 tests/04_pr_gate/run.py --tier fast --glm-sample 60
```