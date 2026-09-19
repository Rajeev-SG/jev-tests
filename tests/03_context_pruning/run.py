#!/usr/bin/env python3
"""Test 3 - Jev context pruning on long coding-agent sessions.

For each real session the history window (the first chunks up to a token
budget) is rebuilt three ways and the same diagnostic question is asked of
GLM-5.3-Flash against each:

  A full window
  B Jev-pruned window   (KEEP in full / TRUNCATE / DROP, confidence-gated)
  C recency baseline    (same token budget, most recent chunks kept whole)

The question is objectively scorable: "which files does this history modify?",
checked against the paths the session's own edit records touched.

    python3 tests/03_context_pruning/run.py --budget 12000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

SESSIONS = jb.ROOT / "data" / "context" / "sessions.jsonl"
OUT = jb.ROOT / "results" / "03_context_pruning"

LABELS = ["keep in full", "truncate to one line", "drop as no longer useful"]
KEEP, TRUNCATE, DROP = LABELS
THRESHOLD = 0.7

INSTRUCTIONS = (
    "This is one unit of a coding agent's history: a tool call with its result, "
    "or a message. The agent is still working on the same task. Decide whether "
    "this unit must survive in full because it holds evidence the agent still "
    "needs (file contents, error messages, decisions, identifiers), whether one "
    "line is enough, or whether it can be dropped."
)
GLM_SYSTEM = "You read a coding agent's history and answer precisely."
GLM_PROMPT = """Below is the history of a coding-agent session.

{context}

Which files does this history modify? Use the paths exactly as they appear in \
the history. Reply with JSON only:
{{"paths": ["path", "path"]}}"""


def load_sessions():
    with SESSIONS.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def window(session, budget):
    chunks, total = [], 0
    for chunk in session["chunks"]:
        if total + chunk["tokens_approx"] > budget and chunks:
            break
        chunks.append(chunk)
        total += chunk["tokens_approx"]
    return chunks, total


def ground_truth_paths(session, chunks):
    text = "\n".join(c["text"] for c in chunks)
    found = []
    for path in session["modified_paths"]:
        name = Path(path).name
        if name and name in text:
            found.append(path)
    return found


def render(chunks, decisions):
    parts = []
    for chunk, decision in zip(chunks, decisions):
        if decision == DROP:
            continue
        text = chunk["text"]
        if decision == TRUNCATE:
            text = jb.truncated(text, 220)
        parts.append(text)
    return "\n---\n".join(parts)


def render_recency(chunks, budget):
    parts, total = [], 0
    for chunk in reversed(chunks):
        if total + chunk["tokens_approx"] > budget and parts:
            break
        parts.append(chunk["text"])
        total += chunk["tokens_approx"]
    return "\n---\n".join(reversed(parts))


def parse_paths(text):
    match = re.search(r"\{.*\}", text or "", re.S)
    payload = None
    if match:
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            payload = None
    if not isinstance(payload, dict):
        return None
    paths = payload.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list):
        return None
    return [p for p in paths if isinstance(p, str)]


def path_score(answer_paths, truth_paths):
    if answer_paths is None or not truth_paths:
        return None, None
    hits = sum(1 for t in truth_paths if any(Path(t).name == Path(a).name for a in answer_paths))
    return hits / len(truth_paths), len(answer_paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--budget", type=int, default=12000, help="history window in approx tokens")
    parser.add_argument("--sessions", type=int, default=8)
    parser.add_argument("--no-glm", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--tier", default="fast", choices=["fast", "smart"])
    args = parser.parse_args()
    use_cache = not args.refresh

    sessions = load_sessions()[: args.sessions]
    fast_client = jb.ClassifierDev(tier=args.tier, use_cache=use_cache)
    glm = jb.GLM(use_cache=use_cache)

    per_session = []
    totals = {"A_tokens": 0, "B_tokens": 0, "C_tokens": 0, "window_tokens": 0,
              "kept": 0, "truncated": 0, "dropped": 0, "chunks": 0}
    scores = {"A": [], "B": [], "C": []}
    answer_sizes = {"A": [], "B": [], "C": []}

    for session in sessions:
        chunks, window_tokens = window(session, args.budget)
        truth = ground_truth_paths(session, chunks)
        if not truth or not chunks:
            print(f"skip {session['session_id'][:12]}: no ground-truth path in window")
            continue
        results = fast_client.classify([c["text"] for c in chunks], LABELS,
                                       instructions=INSTRUCTIONS)
        decisions, confident, raw_decisions = [], 0, []
        for result in results:
            label, confidence = result["label"], result["confidence"]
            raw_decisions.append(label)
            if confidence is not None and confidence >= THRESHOLD:
                confident += 1
                decisions.append(label)
            else:
                decisions.append(KEEP)  # uncertain -> keep, never lose evidence
        context_a = render(chunks, [KEEP] * len(chunks))
        context_b = render(chunks, decisions)
        budget_b = jb.approx_tokens(context_b)
        context_c = render_recency(chunks, budget_b)

        row = {
            "session_id": session["session_id"],
            "source": session["source"],
            "title": session["title"],
            "chunks_in_window": len(chunks),
            "window_tokens": window_tokens,
            "ground_truth_paths": truth,
            "decisions": {KEEP: decisions.count(KEEP), TRUNCATE: decisions.count(TRUNCATE),
                          DROP: decisions.count(DROP)},
            "confident_fraction": confident / len(chunks),
            "raw_drop_share": raw_decisions.count(DROP) / len(chunks),
            "tokens": {"A_full": jb.approx_tokens(context_a),
                       "B_jev_pruned": budget_b,
                       "C_recency": jb.approx_tokens(context_c)},
            "replay": {},
        }
        totals["window_tokens"] += window_tokens
        totals["A_tokens"] += row["tokens"]["A_full"]
        totals["B_tokens"] += row["tokens"]["B_jev_pruned"]
        totals["C_tokens"] += row["tokens"]["C_recency"]
        totals["kept"] += decisions.count(KEEP)
        totals["truncated"] += decisions.count(TRUNCATE)
        totals["dropped"] += decisions.count(DROP)
        totals["chunks"] += len(chunks)

        for name, context in (("A", context_a), ("B", context_b), ("C", context_c)):
            if args.no_glm:
                continue
            response = glm.chat(GLM_PROMPT.format(context=context), system=GLM_SYSTEM,
                                max_tokens=800, json_object=True)
            paths = parse_paths(response["text"])
            recall, said = path_score(paths, truth)
            row["replay"][name] = {
                "recall": recall, "paths_said": said,
                "prompt_tokens": response["prompt_tokens"],
                "completion_tokens": response["completion_tokens"],
                "cost_usd": response["cost_usd"],
                "elapsed_ms": response["elapsed_ms"],
            }
            if recall is not None:
                scores[name].append(recall)
                answer_sizes[name].append(said)
        per_session.append(row)
        print(f"{session['source']:7s} chunks={len(chunks):4d} "
              f"drop={row['decisions'][DROP]:4d} trunc={row['decisions'][TRUNCATE]:4d} "
              f"tokens A={row['tokens']['A_full']:6d} B={row['tokens']['B_jev_pruned']:6d} "
              f"C={row['tokens']['C_recency']:6d} recall A/B/C="
              f"{fmt(row['replay'].get('A', {}).get('recall'))}/"
              f"{fmt(row['replay'].get('B', {}).get('recall'))}/"
              f"{fmt(row['replay'].get('C', {}).get('recall'))}")

    summary = {
        "test": "03_context_pruning",
        "data": {
            "sessions": len(per_session),
            "token_budget": args.budget,
            "scope": "first chunks of each session up to the token budget",
            "ground_truth": "files the session's own edit records touched and that appear in the window",
        },
        "decision": "KEEP / TRUNCATE / DROP per history chunk, confidence-gated at 0.7 (uncertain -> KEEP)",
        "context_tokens": totals,
        "removed_fraction_B": 1 - (totals["B_tokens"] / totals["A_tokens"]) if totals["A_tokens"] else None,
        "removed_fraction_C": 1 - (totals["C_tokens"] / totals["A_tokens"]) if totals["A_tokens"] else None,
        "replay_recall": {
            name: {"mean": (sum(v) / len(v)) if v else None, "n": len(v),
                   "min": min(v) if v else None,
                   "sessions_losing_any": sum(1 for x in v if x < 1.0)}
            for name, v in scores.items()
        },
        "replay_paths_said": {name: (sum(v) / len(v)) if v else None for name, v in answer_sizes.items()},
        "per_session": per_session,
        "classifier_dev_fast": fast_client.stats(),
        "glm": glm.stats() if not args.no_glm else None,
        "provenance": jb.provenance(
            False,
            "inputs are chunks of long local codex sessions, which contain real work paths "
            "and client names",
            "python3 tests/03_context_pruning/extract.py (reads the local AgentSessions index)"),

    }
    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", per_session)
    report(summary)
    return 0


def fmt(value):
    return "n/a" if value is None else f"{value:.2f}"


def report(summary):
    print("=" * 72)
    print("TEST 3 - Jev context pruning")
    print("=" * 72)
    t = summary["context_tokens"]
    print(f"sessions={summary['data']['sessions']} chunks={t['chunks']} window_tokens={t['window_tokens']}")
    print(f"decisions: keep={t['kept']} truncate={t['truncated']} drop={t['dropped']}")
    print(f"context tokens A={t['A_tokens']} B={t['B_tokens']} C={t['C_tokens']}")
    print(f"removed: Jev {100 * (summary['removed_fraction_B'] or 0):.1f}%  "
          f"recency {100 * (summary['removed_fraction_C'] or 0):.1f}%")
    print("\nreplay recall (files correctly named):")
    for name, metrics in summary["replay_recall"].items():
        print(f"  {name}: mean={fmt(metrics['mean'])} n={metrics['n']} min={fmt(metrics['min'])} "
              f"sessions_losing_any={metrics['sessions_losing_any']}")
    print(f"\nmean paths said: {json.dumps(summary['replay_paths_said'])}")
    fast = summary["classifier_dev_fast"]
    print(f"Jev: {fast['classifications']} classifications, "
          f"{fmt(fast['ms_per_item_amortised'])} ms/item amortised")
    if summary.get("glm"):
        print(f"GLM: {summary['glm']['requests']} calls, "
              f"{summary['glm']['prompt_tokens']}+{summary['glm']['completion_tokens']} tokens, "
              f"${summary['glm']['cost_usd_from_usage']:.4f}")


if __name__ == "__main__":
    raise SystemExit(main())