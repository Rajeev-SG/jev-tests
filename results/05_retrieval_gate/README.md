# Test 5 — Jev relevance gate after Project Recall / Recoll retrieval

**Verdict: NO CLAIM — this is a harness failure, not a result about Jev.** The
experiment could not be built: the local Recoll index cannot produce the
candidate pools the design needs, so nothing was measured in either direction.
It is filed as a failure to fix, not as evidence against a relevance gate. No
synthetic corpus was substituted to manufacture a number.

## What was attempted

The stack used locally is the same one Project Recall drives: the Recoll index
at `~/.recoll`, queried with `recollq` over the folders it indexes (`recoll.conf`
`topdirs`: the CHANEL GRASP folders, the SharePoint/sitemap live-audit folder,
and the TradeHero work).

Ground truth: the files a real past session actually read or edited, taken from
that session's own tool calls in the AgentSessions index.

## What was measured

| | value |
|---|---|
| index-covered files sampled | 36 |
| sampled files with a matching historical session | 27 |
| queries returning any document | 27 of 27 |
| **pools containing at least one ground-truth file** | **0** |
| pool sizes observed | 1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2 |

Two distinct problems, both real:

1. **Result-set size.** Recoll's default AND semantics return 1–2 documents for
   realistic task queries. The design assumed 30–50 candidate chunks per query.
2. **Corpus coverage.** The sessions that could supply ground truth worked on
   files outside the folders the index covers (spreadsheet builds, Chrome
   profile JSON, workbook scripts), so the retrieved pool and the session's
   files barely intersect.

Because 0 of 27 pools contained a file the session used, there is nothing for
the gate to protect or to drop, and every metric in the issue — recall of useful
chunks, precision of retained, context tokens before/after, downstream cost
saved — is unmeasurable on this data.

## Cost

$0.00. No classifier or LLM calls were needed to establish the blocker;
`--dry-run` records the diagnostics in `summary.json` (`coverage_diagnostics`,
`blocked: true`).

## What would unblock it

- Build the candidate pool from a retriever with a real recall budget (the
  coding-agent search path, or a vector index over the same folders), rather
  than Recoll's AND search.
- Choose evaluation tasks from sessions whose working directory is *inside* the
  indexed folders, so the session's own files can appear in the pool.

Until then, the correct statement is: **we do not know whether Jev should sit
between retrieval and the coding model.** Nothing here argues for or against it.
The summary records `harness_failure: true` and the reason, so a reader scanning
the artefacts cannot mistake this for a measured negative.

## Reproduce

```sh
python3 tests/05_retrieval_gate/run.py --dry-run --sessions 6 --chunks 25
python3 tests/05_retrieval_gate/run.py --tier fast --sessions 6 --chunks 25
```