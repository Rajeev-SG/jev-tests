#!/usr/bin/env python3
"""Test 5 - Jev relevance gate between retrieval and the coding model.

Stack used locally: Project Recall's Recoll index (~/.recoll) queried with
recollq, over the same folders it indexes.

Ground truth: for each real past session, the files that session actually read
or edited. A retrieved chunk counts as genuinely useful when its file is one of
those. Recall is therefore measured over the retrieval stack's own candidate
pool — which is exactly the question the gate has to answer.

    python3 tests/05_retrieval_gate/run.py --tier smart --sessions 12 --chunks 25
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02_tool_routing"))
import extract as routing  # noqa: E402

# 03_context_pruning/extract.py holds the file-path extraction rules; load it
# under a distinct module name so it is not shadowed by 02's extract.py.
import importlib.util as _importlib_util  # noqa: E402

_ctx_spec = _importlib_util.spec_from_file_location(
    "ctx_extract", Path(__file__).resolve().parents[1] / "03_context_pruning" / "extract.py")
ctx = _importlib_util.module_from_spec(_ctx_spec)
_ctx_spec.loader.exec_module(ctx)
is_edit = ctx.is_edit
paths_from = ctx.paths_from

INDEX_DB = Path(os.path.expanduser("~/Library/Application Support/AgentSessions/index.db"))
RECOLLQ = "/Applications/recoll.app/Contents/MacOS/recollq"
RECOLL_CONF = str(Path.home() / ".recoll")
OUT = jb.ROOT / "results" / "05_retrieval_gate"

TOP_DIRS = [
    "/Users/rajeev/Work/PHD/CHANEL/grasp",
    "/Users/rajeev/Work/PHD/CHANEL/outputs/grasp_live_implementation_20260904",
    "/Users/rajeev/Work/PHD/CHANEL/outputs/sitemap-v2/sharepoint-live-audit-20260904",
    "/Users/rajeev/Work/Singulyr/singulyr.com/src/prototypes/tradehero",
    "/Users/rajeev/Work/TradeHero/NewGen_Proposal",
    "/Users/rajeev/tradehero-mobile-web-parity-audit",
]
CWD_PREFIXES = ["/Users/rajeev/Work/PHD/CHANEL", "/Users/rajeev/Work/Singulyr",
                "/Users/rajeev/Work/TradeHero", "/Users/rajeev/tradehero-mobile-web-parity-audit"]

LABELS = ["directly answers the task", "useful background", "not relevant"]
THRESHOLD = 0.7
OK = {"directly answers the task", "useful background"}
INSTRUCTIONS = (
    "A coding agent asked a question and a local search index returned this "
    "document. Decide whether the document is directly useful for the task, "
    "useful background, or not relevant at all."
)
CHUNK_CHARS = 900


def is_read(name, args) -> bool:
    key = (name or "").strip().lower()
    if key in ("read", "readfile", "read_file", "view", "notebookread"):
        return True
    if key in routing.GENERIC_SHELL_TOOLS:
        command = routing.extract_command(args)
        return bool(re.match(r"^\s*(cat|head|tail|bat|less|jq|sed\s+-n|awk|rg|grep|find|fd)\b", command))
    return False


def touched_paths(path: Path, source: str) -> set[str]:
    found: set[str] = set()
    for event in routing.event_stream(path, source):
        if event[0] != "tool":
            continue
        name, args = event[1], event[2]
        if is_read(name, args) or is_edit(name, args):
            for candidate in paths_from(name, args):
                if candidate.startswith("/") or candidate.startswith("~/") or "/" in candidate:
                    found.add(candidate)
            command = routing.extract_command(args)
            for match in re.finditer(r"[\w./~-]+/[\w./-]+\.\w{1,6}", command):
                found.add(match.group(0))
    return found


def indexed_files(limit: int):
    """Sample real documents from the folders the local Recoll index covers."""
    found = []
    for top in TOP_DIRS:
        root = Path(top)
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in (".md", ".csv", ".json", ".txt", ".xlsx"):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def task_for_file(name: str):
    """Find a real historical session that worked with this file."""
    connection = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    rows = connection.execute(
        "select s.session_id, m.title, m.cwd, m.source, m.path from session_search s "
        "join session_meta m on m.session_id = s.session_id "
        "where s.text like ? and m.path is not null and m.size > 1000000 limit 5",
        (f"%{name}%",)).fetchall()
    connection.close()
    for session_id, title, cwd, source, path in rows:
        if Path(path).exists() and (title or "").strip():
            return session_id, title, cwd, source, path
    return None


STOPWORDS = {"the","a","an","and","or","of","to","in","for","on","with","is","it","this",
              "that","be","as","at","by","from","we","i","you","my","me","our","use","using"}


def query_terms(title: str):
    strip_chars = ".,;:()[]#*"
    words = [w.strip(strip_chars) for w in (title or "").split()]
    keep = [w for w in words if len(w) > 2 and w.lower() not in STOPWORDS and not w.startswith("http")]
    return keep


def recoll_best(terms, max_results: int):
    """Recoll defaults to AND over terms, so try longest query first, then trim."""
    if not terms:
        return [], ""
    for size in (6, 4, 3, 2):
        if len(terms) < size:
            continue
        query = " ".join(terms[:size])
        docs = recoll(query, max_results)
        if docs:
            return docs, query
    for term in terms[:8]:
        docs = recoll(term, max_results)
        if docs:
            return docs, term
    return [], ""


def recoll(query: str, max_results: int):
    if not Path(RECOLLQ).exists():
        raise RuntimeError(f"recollq not found at {RECOLLQ}")
    proc = subprocess.run([RECOLLQ, "-c", RECOLL_CONF, "-m", str(max_results), "-t", "-A", query],
                          capture_output=True, text=True, timeout=120)
    docs, current = [], None
    for line in proc.stdout.splitlines():
        if "[file://" in line:
            match = re.search(r"\[file://([^\]]+)\]", line)
            current = {"url": match.group(1) if match else "", "abstract": ""}
            docs.append(current)
        elif current is not None and line.startswith("abstract = "):
            current["abstract"] = line[len("abstract = "):]
        elif current is not None and line.startswith("filename = "):
            current["filename"] = line[len("filename = "):]
    return docs


def under_top_dir(path: str) -> bool:
    return any(path.startswith(d) or d in path for d in TOP_DIRS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="fast", choices=["fast", "smart"])
    parser.add_argument("--sessions", type=int, default=12)
    parser.add_argument("--chunks", type=int, default=25)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="skip the classifier; validate retrieval and ground truth")
    args = parser.parse_args()
    use_cache = not args.refresh

    client = jb.ClassifierDev(tier=args.tier, use_cache=use_cache)
    rows, totals = [], {"before_tokens": 0, "after_tokens": 0, "chunks": 0,
                        "useful_chunks": 0, "kept_useful": 0, "kept": 0}
    kept_scores = []
    diagnostics = {"files_sampled": 0, "tasks_matched": 0, "pools_with_ground_truth": 0,
                   "pools_empty": 0, "pool_sizes": [], "ground_truth_hits": []}

    for doc_path in indexed_files(args.sessions * 6):
        if len(rows) >= args.sessions:
            break
        diagnostics["files_sampled"] += 1
        match = task_for_file(doc_path.name)
        if not match:
            continue
        diagnostics["tasks_matched"] += 1
        session_id, title, cwd, source, session_path = match
        touched = touched_paths(Path(session_path), source)
        truth_names = {Path(p).name for p in touched if p} | {doc_path.name}
        if len(truth_names) < 2:
            continue
        terms = query_terms(title) or [doc_path.stem]
        try:
            docs, query = recoll_best(terms, args.chunks)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"recoll failed: {exc}", file=sys.stderr)
            break
        if not docs:
            diagnostics["pools_empty"] += 1
            continue
        diagnostics["pool_sizes"].append(len(docs))
        chunks, useful = [], []
        for doc in docs:
            name = Path(doc.get("url", "")).name
            text = f"query: {query}\nfile: {name}\ncontent: {jb.truncated(doc.get('abstract') or '', CHUNK_CHARS)}"
            chunks.append(text)
            useful.append(name in truth_names)
        if sum(useful) < 1:
            # Retrieval did not surface a single file the session actually used,
            # so there is nothing for the gate to protect or drop.
            continue
        diagnostics["pools_with_ground_truth"] += 1
        diagnostics["ground_truth_hits"].append(sum(useful))

        if args.dry_run:
            results = [{"label": LABELS[0], "confidence": 1.0} for _ in chunks]
        else:
            results = client.classify(chunks, LABELS, instructions=INSTRUCTIONS)
        decided, confident = [], 0
        for result in results:
            confidence = result["confidence"]
            if confidence is not None and confidence >= THRESHOLD:
                confident += 1
                decided.append(result["label"] in OK)
            else:
                decided.append(True)  # uncertain -> keep
        before = sum(jb.approx_tokens(c) for c in chunks)
        after = sum(jb.approx_tokens(c) for c, keep in zip(chunks, decided) if keep)
        kept_useful = sum(1 for keep, good in zip(decided, useful) if keep and good)
        totals["before_tokens"] += before
        totals["after_tokens"] += after
        totals["chunks"] += len(chunks)
        totals["useful_chunks"] += sum(useful)
        totals["kept_useful"] += kept_useful
        totals["kept"] += sum(decided)
        kept_scores.append(kept_useful / max(1, sum(useful)))
        rows.append({
            "session_id": session_id, "source": source, "title": title, "cwd": cwd,
            "query": query,
            "retrieved": len(chunks), "useful_in_pool": sum(useful),
            "kept": sum(decided), "kept_useful": kept_useful,
            "recall_of_useful": kept_useful / max(1, sum(useful)),
            "precision_of_kept": kept_useful / max(1, sum(decided)),
            "tokens_before": before, "tokens_after": after,
            "confident_fraction": confident / len(chunks),
        })
        print(f"{source:7s} q='{query[:40]}' pool={len(chunks)} useful={sum(useful)} "
              f"kept={sum(decided)} recall={rows[-1]['recall_of_useful']:.2f} "
              f"tokens {before}->{after}")

    summary = {
        "test": "05_retrieval_gate",
        "data": {
            "sessions": len(rows),
            "retrieval": "local project-recall Recoll index via recollq",
            "top_dirs": TOP_DIRS,
            "ground_truth": "files the session actually read or edited, matched against the retrieved pool",
        },
        "tier": args.tier,
        "threshold": THRESHOLD,
        "totals": totals,
        "recall_of_useful_chunks": totals["kept_useful"] / totals["useful_chunks"] if totals["useful_chunks"] else None,
        "precision_of_kept": totals["kept_useful"] / totals["kept"] if totals["kept"] else None,
        "context_token_reduction": 1 - (totals["after_tokens"] / totals["before_tokens"])
        if totals["before_tokens"] else None,
        "worst_session_recall": min(kept_scores) if kept_scores else None,
        "coverage_diagnostics": diagnostics,
        "blocked": len(rows) == 0,
        "blocker": ("the local Recoll index covers a narrow folder set and its default AND "
                    "query semantics return very small result pools, so it cannot supply "
                    "the 30-50 candidate chunks this design assumes") if len(rows) == 0 else None,
        "per_session": rows,
        "classifier": client.stats(),
    }
    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", rows)
    report(summary)
    return 0


def report(summary):
    print("=" * 72)
    print("TEST 5 - Jev relevance gate after retrieval")
    print("=" * 72)
    t = summary["totals"]
    print(f"sessions={summary['data']['sessions']} chunks={t['chunks']} "
          f"useful_in_pool={t['useful_chunks']}")
    print(f"recall of useful chunks: {fmt(summary['recall_of_useful_chunks'])} "
          f"(worst session {fmt(summary['worst_session_recall'])})")
    print(f"precision of kept: {fmt(summary['precision_of_kept'])}")
    print(f"kept {t['kept']}/{t['chunks']} chunks; tokens "
          f"{t['before_tokens']} -> {t['after_tokens']} "
          f"({100 * (summary['context_token_reduction'] or 0):.1f}% removed)")


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())