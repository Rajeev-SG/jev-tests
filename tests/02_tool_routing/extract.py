#!/usr/bin/env python3
"""Extract real coding-agent tool-routing decision points from local sessions.

Sources (read from the AgentSessions index, read-only, then the session files):
  claude  - explicit tool_use blocks (Bash, Read, Edit, Write, Grep, Glob, WebFetch, Agent...)
  droid   - explicit tool_use blocks (Execute, Read, ApplyPatch, Grep, Glob, WebSearch, ...)
  pi      - explicit tool_execution_start records (read, bash, edit, write, grep...)
  codex   - function_call records; the tool is always exec_command, so the family is
            derived from the shell command with the rules in COMMAND_RULES. Commands
            that do not match a family are reported as `other` and excluded from the
            primary score.

At each decision point the state holds only what exists BEFORE the action: the
session title, the last assistant message, and the last few truncated tool
results. The observed next action is the label and is never placed in the state.

    python3 tests/02_tool_routing/extract.py --max-sessions 400
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

INDEX_DB = Path(os.path.expanduser("~/Library/Application Support/AgentSessions/index.db"))
DATA_DIR = jb.ROOT / "data" / "sessions"

# Tool name -> family, for the harnesses that name their tools explicitly.
TOOL_FAMILY = {
    # shell
    "bash": "shell", "shell": "shell", "execute": "shell", "run": "shell",
    "exec_command": "shell", "terminal": "shell", "run_command": "shell",
    # file read
    "read": "file_read", "readfile": "file_read", "read_file": "file_read",
    "view": "file_read", "notebookread": "file_read",
    # file edit
    "edit": "file_edit", "write": "file_edit", "multiedit": "file_edit",
    "applypatch": "file_edit", "apply_patch": "file_edit", "str_replace_editor": "file_edit",
    "notebookedit": "file_edit", "patch": "file_edit",
    # search / retrieval
    "grep": "search", "glob": "search", "ls": "search", "list": "search",
    "search": "search", "codebase_search": "search", "grep_search": "search",
    "list_directory": "search", "codereview": "search",
    # test / build
    "run_tests": "test_build", "pytest": "test_build", "build": "test_build",
    # browser / web
    "webfetch": "browser", "websearch": "browser", "fetchurl": "browser",
    "browser": "browser", "playwright": "browser", "browser_use": "browser",
    "browsernavigate": "browser", "browsersnapshot": "browser", "browserclick": "browser",
    # memory / recall
    "memory": "memory", "retrieve": "memory", "recall": "memory", "qmd": "memory",
    "project_recall": "memory", "read_thread": "memory", "list_threads": "memory",
    # git / github
    "github": "git", "gh": "git", "git": "git", "create_pull_request": "git",
    "gh_pr": "git",
    # done / handoff
    "exit_spec_mode": "done_handoff", "exitspecmode": "done_handoff",
    "task": "done_handoff", "agent": "done_handoff", "spawn_agent": "done_handoff",
    "todowrite": "planning", "todo_write": "planning", "update_plan": "planning",
    "askuser": "ask_user", "ask_user": "ask_user", "exit_plan_mode": "done_handoff",
}

# Shell command -> family, first matching rule wins. Only used where the harness
# reports a single generic tool (codex exec_command).
COMMAND_RULES = [
    (re.compile(r"^\s*(sudo\s+)?git\b"), "git"),
    (re.compile(r"^\s*gh\b"), "git"),
    (re.compile(r"^\s*(rg|grep|fd|ag|ack|find)\b"), "search"),
    (re.compile(r"^\s*(ls|tree)\b"), "search"),
    (re.compile(r"^\s*(pytest|python -m pytest|bun test|npm test|npm run|pnpm|yarn|make|cargo|go test|ruff|mypy|pyright|tsc|eslint|uv run)\b"), "test_build"),
    (re.compile(r"^\s*(cat|head|tail|bat|less|jq|sed\s+-n|awk)\b"), "file_read"),
    (re.compile(r"^\s*(apply_patch|sed\s+-i|tee|mv|cp|rm|mkdir|touch|chmod|ln)\b"), "file_edit"),
    (re.compile(r"^\s*(curl|wget|relay|browser-use|browser-use-real)\b"), "browser"),
    (re.compile(r"^\s*(qmd|recoll|project-recall)\b"), "memory"),
    (re.compile(r"^\s*(echo|pwd|wc|du|df|whoami|env|which|date|uname|printf)\b"), "shell"),
    (re.compile(r"^\s*(python3?|node|deno|bun|npm|pnpm|yarn|uv|poetry|docker|kubectl|osascript|open|launchctl|systemctl|ps|kill|sleep|time|nohup|source|export|set|for|while|if)\b"), "shell"),
    (re.compile(r"^\s*(python3?\s*<<|bash\s+-c|sh\s+-c|zsh\s+-c)"), "shell"),
]

# Families scored in the primary benchmark.
FAMILIES = ["shell", "search", "file_read", "file_edit", "test_build", "git", "browser", "memory"]


GENERIC_SHELL_TOOLS = {"exec_command", "bash", "shell", "run", "execute", "run_command",
                       "terminal", "execute_command", "run_terminal_cmd"}


def normalize_command(command: str) -> str:
    """Strip a leading `cd X &&` chain and env assignments so rules can match."""
    text = command.strip()
    while True:
        stripped = re.sub(r"^cd\s+[^\s&;|]+\s*&&\s*", "", text, count=1)
        if stripped != text:
            text = stripped.strip()
            continue
        stripped = re.sub(r"^[A-Z_][A-Z0-9_]*=[^\s]*\s+", "", text, count=1)
        if stripped != text:
            text = stripped.strip()
            continue
        break
    return text


def family_from_tool(name: str, args) -> tuple[str | None, str]:
    key = (name or "").strip().lower()
    if key in GENERIC_SHELL_TOOLS:
        # The harness exposes one generic shell tool; the family has to come from
        # the command that was actually run.
        command = extract_command(args)
        if command:
            command = normalize_command(command)
            for pattern, mapped in COMMAND_RULES:
                if pattern.match(command):
                    return mapped, "command_rule"
        return (("shell", "tool_name") if not command else (None, "other"))
    family = TOOL_FAMILY.get(key)
    if family in FAMILIES:
        return family, "tool_name"
    return None, "other"


def extract_command(args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return args.strip()
    if isinstance(args, dict):
        for key in ("cmd", "command", "script", "code"):
            value = args.get(key)
            if isinstance(value, str):
                return value.strip()
            if isinstance(value, list) and value:
                return " ".join(str(v) for v in value).strip()
    return ""


def content_blocks(message):
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, list) else []


def event_stream(path: Path, source: str):
    """Yield ('assistant', text) | ('tool', name, args) | ('result', text) in order."""
    opener = (lambda: open(path, errors="ignore")) if source != "hermes" else (lambda: open(path, errors="ignore"))
    try:
        handle = opener()
    except OSError:
        return
    with handle as fh:
        for line in fh:
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield from events_from_record(record, source)


def events_from_record(record: dict, source: str):
    if source == "codex":
        payload = record.get("payload") or {}
        if record.get("type") == "response_item" and isinstance(payload, dict):
            kind = payload.get("type")
            if kind == "function_call":
                yield ("tool", payload.get("name"), payload.get("arguments"))
            elif kind == "function_call_output":
                yield ("result", payload.get("output"))
            elif kind == "message" and payload.get("role") == "assistant":
                for block in content_blocks(payload):
                    if block.get("type") in ("output_text", "input_text") and block.get("text"):
                        yield ("assistant", block["text"])
        return

    if source == "pi":
        if record.get("type") == "custom":
            custom = record.get("customType")
            data = record.get("data") or {}
            if custom == "tool_execution_start":
                yield ("tool", data.get("toolName"), data.get("args"))
            elif custom == "tool_execution_end":
                yield ("result", json.dumps(data.get("result"))[:400])
        elif record.get("type") == "message":
            data = record.get("data") or record
            if data.get("role") == "assistant":
                text = data.get("content")
                if isinstance(text, str):
                    yield ("assistant", text)
        return

    # claude / droid: anthropic-style message records
    message = record.get("message")
    if isinstance(message, dict):
        for block in content_blocks(message):
            kind = block.get("type")
            if kind == "tool_use":
                yield ("tool", block.get("name"), block.get("input"))
            elif kind == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                yield ("result", content if isinstance(content, str) else "")
            elif kind == "text" and message.get("role") == "assistant" and block.get("text"):
                yield ("assistant", block["text"])


def build_state(title, cwd, assistant_notes, results, previous) -> str:
    lines = [f"task: {title or 'unknown task'}"]
    if cwd:
        lines.append(f"working directory: {cwd}")
    if previous:
        lines.append("actions taken so far: " + ", ".join(previous[-6:]))
    if assistant_notes:
        lines.append("last assistant note: " + jb.truncated(" ".join(assistant_notes[-2:]).replace("\n", " "), 400))
    for result in results[-3:]:
        lines.append("recent tool result: " + jb.truncated(str(result).replace("\n", " "), 200))
    return "\n".join(lines)


def discover(sources, per_source: int, min_bytes: int):
    connection = sqlite3.connect(f"file:{INDEX_DB}?mode=ro", uri=True)
    found = {}
    for source in sources:
        rows = connection.execute(
            "select path, session_id, title, cwd from session_meta "
            "where source = ? and size > ? and path is not null "
            "order by end_ts desc limit ?",
            (source, min_bytes, per_source),
        ).fetchall()
        found[source] = [r for r in rows if Path(r[0]).exists()]
    connection.close()
    return found


def extract_session(path: Path, source: str, session_id: str, title: str, cwd: str,
                    max_decisions: int):
    notes, results = [], []
    previous: list[str] = []
    decisions = []
    for event in event_stream(path, source):
        if event[0] == "assistant":
            notes.append(event[1] or "")
        elif event[0] == "result":
            results.append(event[1] or "")
        elif event[0] == "tool":
            name, args = event[1], event[2]
            if len(decisions) >= max_decisions:
                break
            family, how = family_from_tool(name, args)
            decisions.append({
                "source": source,
                "session_id": session_id,
                "title": title,
                "cwd": cwd,
                "step": len(decisions),
                "observed_tool": name,
                "label_how": how,
                "family": family,
                "previous_families": list(previous),
                "state": build_state(title, cwd, notes, results, previous),
            })
            if family:
                previous.append(family)
    return decisions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-sessions", type=int, default=60, help="per source")
    parser.add_argument("--max-decisions-per-session", type=int, default=25)
    parser.add_argument("--sources", default="claude,droid,pi,codex")
    parser.add_argument("--min-bytes", type=int, default=50_000)
    args = parser.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    discovered = discover(sources, args.max_sessions, args.min_bytes)
    rows = []
    excluded = []
    for source, entries in discovered.items():
        kept = 0
        for path, session_id, title, cwd in entries:
            decisions = extract_session(Path(path), source, session_id, title, cwd,
                                        args.max_decisions_per_session)
            for decision in decisions:
                decision["decision_id"] = f"{source}:{session_id}:{decision['step']}"
                if decision["family"] is None:
                    excluded.append(decision)  # ambiguous step: excluded from the primary score
                    continue
                rows.append(decision)
                kept += 1
        print(f"{source:8s} sessions={len(entries):4d} decisions={kept:6d}")

    jb.write_jsonl(DATA_DIR / "decisions.jsonl", rows)
    jb.write_jsonl(DATA_DIR / "excluded.jsonl", excluded)
    print(f"\ntotal decisions={len(rows)} excluded_ambiguous={len(excluded)}")
    print(f"-> {DATA_DIR / 'decisions.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())