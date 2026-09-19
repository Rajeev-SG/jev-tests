# Test 3 — Jev context pruning on long coding-agent sessions

**Verdict: NOT PROVEN.** With a confidence gate that protects against losing
evidence, Jev pruned **nothing at all** — 309 chunks in, 309 kept, 0% context
removed.

## Data

Five completed long codex sessions (selected from 19 candidates; the rest had no
ground-truth file inside the 12,000-token window). The window is the first
chunks of each session up to the budget — the old history a pruner would have to
judge. 309 history units, 59,051 tokens: tool call + result pairs, assistant
messages, results.

Ground truth for replay: the files the session's own edit records touched and
that appear in the window.

## Decision and gate

Each unit is classified KEEP / TRUNCATE / DROP. Anything below 0.7 confidence is
treated as KEEP, because dropping evidence on a guess is the expensive mistake.

| | value |
|---|---|
| decisions | 309 |
| keep | 309 |
| truncate | 0 |
| drop | 0 |
| context tokens A (full) | 59,413 |
| context tokens B (Jev-pruned) | 59,413 |
| context tokens C (recency baseline) | 59,413 |
| removed | **0.0%** |

**Why nothing was pruned:** Jev's *raw* answers said "drop" for 17–27% of chunks
per session, but almost none of those answers carried ≥0.7 confidence — the
share of confident decisions per session was 0.0, 0.0, 0.03, 0.13, 0.02. Jev's
own drop judgements are exactly the ones it is least sure about, which is the
opposite of what a pruning gate needs.

## Replay

GLM-5.3-Flash was asked, against contexts A, B and C, which files the history
modifies. It answered `{"paths": []}` even for the full context, and recall was
0.00 for all three — `sessions_losing_any=5` is an artefact of that, not of
pruning. The replay metric did not work on this data and the comparison is
inconclusive; it is reported here rather than hidden.

## Cost

| | value |
|---|---|
| Jev | 309 classifications, 8 requests, 585 ms/item amortised (smart tier; no fast-tier quota left today) |
| GLM replay | 7 calls across runs, ≈$0.038 recorded spend |

## What this means

The practical rule the issue asks about — "Jev-prune once history exceeds N
tokens" — is **not supported**. With a safe gate the saving is zero; without a
gate Jev would remove roughly a fifth of the history on decisions it is not
confident about. Either way the evidence does not justify putting Jev in front
of a coding model's context.

Two follow-ups would make this a fairer test: use the fast tier (this run had to
use the smart tier, which returned very low confidence on this task), and pick a
replay question the full-context baseline can actually answer.

## Reproduce

```sh
python3 tests/03_context_pruning/extract.py --sessions 8
python3 tests/03_context_pruning/run.py --tier fast --sessions 6 --budget 12000
```