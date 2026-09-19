#!/usr/bin/env python3
"""Extract long coding-agent sessions as ordered context chunks (Test 3).

For each selected session the extractor produces:
  - `chunks`: the ordered history units (tool call + result, assistant message,
    user message) that a pruning layer would have to judge;
  - `modified_paths`: the files the session actually changed, read out of the
    session's own edit/apply-patch records. This is the objective ground truth
    used for the replay question ("which files did this session modify?").

    python3 tests/03_context_pruning/extract.py --sessions 10
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02_tool_routing"))
import extract as routing  # noqa: E402

INDEX_DB = Path(os.path.expanduser("~/Library/Application Support/AgentSessions/index.db"))
DATA_DIR = jb.ROOT / "data" / "context"

CHUNK_CHARS = 1400
PATCH_PATH = re.compile(r"\*\*\* (?:Update|Add|Delete) File: (.+)")
REDIRECT_PATH = re.compile(r">>?\s*([^\s|&;]+)")
EDIT_TOOLS = {"edit", "write", "multiedit", "applypatch", "apply_patch",
              "str_replace_editor", "notebookedit", "patch"}


def is_edit(name: str, args) -> bool:
    key = (name or "").strip().lower()
    if key in EDIT_TOOLS:
        return True
    if key in routing.GENERIC_SHELL_TOOLS:
        command = routing.extract_command(args)
        return bool(PATCH_PATH.search(command) or REDIRECT_PATH.search(command))
    return False


def paths_from(name: str, args) -> list[str]:
    found: list[str] = []
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {}
    if isinstance(args, dict):
        for key in ("file_path", "path", "file", "filename"):
            value = args.get(key)
            if isinstance(value, str) and value:
                found.append(value)
    command = routing.extract_command(args)
    found.extend(m.group(1).strip().strip("'\"") for m in PATCH_PATH.finditer(command))
    found.extend(m.group(1).strip().strip("'\"") for m in REDIRECT_PATH.finditer(command))
    return [p for p in found if p and not p.startswith("/dev/")]


def session_chunks(path: Path, source: str):
    chunks, modified = [], set()
    pending = None
    notes = []
    for event in routing.event_stream(path, source):
        kind = event[0]
        if kind == "tool":
            name, args = event[1], event[2]
            if is_edit(name, args):
                modified.update(paths_from(name, args))
            label = f"tool: {name}"
            command = jb.redact(routing.extract_command(args))
            if command:
                label += f" | command: {jb.truncated(command.replace(chr(10), ' '), 300)}"
            pending = {"kind": "tool", "label": label, "text": label}
        elif kind == "result":
            text = jb.redact(str(event[1] or ""))
            if pending is not None:
                pending["text"] += "\nresult: " + jb.truncated(text.replace("\n", " "), CHUNK_CHARS)
                pending["result_chars"] = len(text)
                chunks.append(pending)
                pending = None
            else:
                chunks.append({"kind": "result", "label": "tool result",
                               "text": jb.truncated(text.replace("\n", " "), CHUNK_CHARS),
                               "result_chars": len(text)})
        else:
            text = str(event[1] or "")
            if text.strip():
                text = jb.redact(text)
                chunks.append({"kind": "assistant", "label": "assistant message",
                               "text": jb.truncated(text.replace("\n", " "), CHUNK_CHARS),
                               "result_chars": len(text)})
            notes.append(text)
    for index, chunk in enumerate(chunks):
        chunk["i"] = index
        chunk["tokens_approx"] = jb.approx_tokens(chunk["text"])
    return chunks, sorted(modified)


def select(sources, limit: int, min_bytes: int):
    connection = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    rows = []
    for source in sources:
        rows.extend(connection.execute(
            "select path, session_id, title, cwd, source from session_meta "
            "where source = ? and size > ? and path is not null order by size desc limit ?",
            (source, min_bytes, limit),
        ).fetchall())
    connection.close()
    return [r for r in rows if Path(r[0]).exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--min-bytes", type=int, default=400_000)
    parser.add_argument("--min-chunks", type=int, default=25)
    parser.add_argument("--sources", default="codex,claude,droid,pi")
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",")]
    out = []
    for path, session_id, title, cwd, source in select(sources, args.sessions * 3, args.min_bytes):
        if len(out) >= args.sessions:
            break
        chunks, modified = session_chunks(Path(path), source)
        if len(chunks) < args.min_chunks or not modified:
            continue
        out.append({
            "source": source,
            "session_id": session_id,
            "title": title,
            "cwd": cwd,
            "chunks": chunks,
            "modified_paths": modified,
            "total_tokens_approx": sum(c["tokens_approx"] for c in chunks),
        })
        print(f"{source:8s} chunks={len(chunks):4d} modified={len(modified):2d} "
              f"tokens={out[-1]['total_tokens_approx']:7d} {jb.truncated(str(title), 50)}")

    jb.write_jsonl(DATA_DIR / "sessions.jsonl", out)
    print(f"\nsessions={len(out)} -> {DATA_DIR / 'sessions.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())