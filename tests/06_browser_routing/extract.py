#!/usr/bin/env python3
"""Extract next-action decisions from recorded web-automation-microbench traces.

Source: Rajeev-SG/web-automation-microbench artifacts. Each trace file records
one real or instrumented browser task run: per step it stores the goal context,
the action the model issued (`code`) and the page state it saw (`observation`).

Decision point N:
    state  = page observation from step N-1 (plus task, url and actions so far)
    label  = the first browser action inside step N's code

Action vocabulary: navigate, click, type, press, scroll, wait, inspect, done.

Task classification: the microbench README is explicit that the TodoMVC round is
a latency instrument, not real work, so its traces are tagged `instrument` and
scored separately from `real_work` traces.

    python3 tests/06_browser_routing/extract.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

MICROBENCH = Path.home() / "Code" / "web-automation-microbench"
DATA_DIR = jb.ROOT / "data" / "browser"

# Ordered by priority when a snippet contains several actions; the label is the
# action that appears EARLIEST in the snippet, which is the first thing that ran.
ACTIONS = [
    ("navigate", re.compile(r"(page\.goto|navigate\(|goto\(|\.get\(['\"]http|open_url|browser_navigate)", re.I)),
    ("click", re.compile(r"(trusted_click|\.click\(|click\(|browser_click|click_text|\bclick\b)", re.I)),
    ("type", re.compile(r"(type_text|fill\(|\.fill\(|insert_text|type\(|browser_type|set_value)", re.I)),
    ("press", re.compile(r"(press_key|\.press\(|press\(|keys\(|key_press|keyboard)", re.I)),
    ("scroll", re.compile(r"(scroll|mouse_wheel)", re.I)),
    ("wait", re.compile(r"(\bwait\b|wait_for|sleep\(|timeout)", re.I)),
    ("inspect", re.compile(r"(\beval\b|snapshot|screenshot|read_page|get_text|Runtime\.evaluate|"
                           r"document\.|outerHTML|innerText|bsk\b|webctl|js\()", re.I)),
    ("done", re.compile(r"(\bdone\b|finish|complete|report_result)", re.I)),
]
INSTRUMENT_TASKS = ("todomvc",)
OBS_CHARS = 1200


def label_for(code: str):
    best = None
    for name, pattern in ACTIONS:
        match = pattern.search(code)
        if match and (best is None or match.start() < best[1]):
            best = (name, match.start())
    return best[0] if best else None


def task_kind(task: str, payload) -> str:
    blob = (task or "").lower()
    if any(k in blob for k in INSTRUMENT_TASKS):
        return "instrument"
    first_obs = ""
    events = payload.get("events") or []
    if events and isinstance(events[0], dict):
        first_obs = str(events[0].get("observation") or "")
    if "todomvc" in first_obs.lower() or "demo.playwright.dev" in first_obs.lower():
        return "instrument"
    return "real_work"


def traces():
    out = []
    for pattern in ("**/*.json",):
        for path in MICROBENCH.glob(pattern):
            if "node_modules" in str(path):
                continue
            try:
                payload = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(payload, dict):
                continue
            events = payload.get("events")
            if not isinstance(events, list) or not events or not isinstance(events[0], dict):
                continue
            if "code" not in events[0]:
                continue
            out.append((path, payload))
    return out


def main() -> int:
    rows = []
    seen = set()
    for path, payload in traces():
        task = payload.get("task") or payload.get("id") or path.stem
        kind = task_kind(task, payload)
        contender = payload.get("contender") or payload.get("tool") or "unknown"
        model = payload.get("model") or "unknown"
        observation = ""
        actions = []
        for event in payload["events"]:
            code = str(event.get("code") or "")
            label = label_for(code)
            if label and code.strip():
                rows.append({
                    "trace": str(path.relative_to(MICROBENCH)),
                    "task": task,
                    "task_kind": kind,
                    "contender": contender,
                    "model": model,
                    "step": event.get("step"),
                    "label": label,
                    "actions_so_far": list(actions),
                    "state": build_state(task, kind, observation, actions),
                    "code_chars": len(code),
                    "observation_chars": len(str(event.get("observation") or "")),
                })
                actions.append(label)
            observation = str(event.get("observation") or "") or observation
    jb.write_jsonl(DATA_DIR / "decisions.jsonl", rows)
    kinds = {}
    for row in rows:
        kinds[row["task_kind"]] = kinds.get(row["task_kind"], 0) + 1
    labels = {}
    for row in rows:
        labels[row["label"]] = labels.get(row["label"], 0) + 1
    print(f"decisions={len(rows)} traces={len({r['trace'] for r in rows})}")
    print(f"by kind: {kinds}")
    print(f"by label: {sorted(labels.items(), key=lambda kv: -kv[1])}")
    return 0


def build_state(task, kind, observation, actions) -> str:
    parts = [f"task: {task}", f"task kind: {kind}"]
    if actions:
        parts.append("actions so far: " + ", ".join(actions[-8:]))
    if observation:
        parts.append("current page state: " + jb.truncated(observation.replace("\n", " "), OBS_CHARS))
    return "\n".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())