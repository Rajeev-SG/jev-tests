# Data sources and privacy

Every sample in this repo is real historical material. Nothing is synthetic.

| Path | Source | Committed? |
|---|---|---|
| `data/adpi/records.jsonl` | `Rajeev-SG/adpi-data` public export of ad-platform-intelligence (837 capability records, generated 2026-09-17) | yes — public vendor data |
| `data/adpi/dataset.json` | the same export as downloaded | no — 750 KB, `build_dataset.py` fetches it |
| `data/sessions/decisions.jsonl` | next-action decision points parsed from local coding-agent sessions (codex, pi, claude, droid) | **no — real work paths, client names and tool output** |
| `data/context/sessions.jsonl` | long sessions chunked for the pruning test | **no — same reason** |
| `data/browser/decisions.jsonl` | next-action decisions parsed from `web-automation-microbench` trace artifacts | yes — derived from that repo's recorded traces |
| `data/prs/prs.jsonl` | PR metadata from openreview, ad-platform-intelligence, codex-home, project-recall, jev-tests | **no — private repos** |
| `results/.cache/` | raw classifier.dev and OpenRouter responses | **no — third-party responses** |

`.gitignore` enforces the "no" rows. The extractor scripts are committed so any
of them can be regenerated locally.

## Third-party calls

Two services receive text:

- **classifier.dev** (Jev) — used for every Jev measurement.
- **OpenRouter / GLM-5.3-Flash** — used for the LLM comparison arms.

Both `lib/jevbench.py` clients redact credential-shaped strings
(`sk-…`, `ghp_…`, JWTs, private-key blocks, `key = value` style secrets) before
sending. Session-derived samples are truncated to the fields a decision needs
(task title, working directory, action names, short result excerpts).

Per the programme rules, no new crawling was done and no private or client
content was sent deliberately; the one place private material is involved (PR
metadata, session traces) stays on disk.