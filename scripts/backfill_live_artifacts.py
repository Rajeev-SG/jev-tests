#!/usr/bin/env python3
"""One-time backfill: stamp evidence fields onto artifacts written before the
dual-scoring change, using the SAME code the live run uses.

Why this exists: the first live artifacts recorded only the verifier output and a
combined `pass`, so the summarizer later had to guess goal state with a
task-specific predicate. That is exactly the drift the review flagged. This
script resolves each task through benchlib and calls the task's own `check` —
the identical predicate the live run used — so the stamped values cannot
disagree with a live run.

    uv run python scripts/backfill_live_artifacts.py

Safe to re-run; it only fills fields that are absent.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from pathlib import Path

MICROBENCH = Path("/Users/rajeev/Code/web-automation-microbench")
LIVE = Path("results/browser-fastpath/live")
PROBES = Path("results/browser-fastpath/probes")


def load_benchlib():
    bench_ext = MICROBENCH / "bench-ext"
    sys.path.insert(0, str(bench_ext))
    importlib.import_module("task_ingest")
    return importlib.import_module("benchlib")


def error_kind_from(message: str | None) -> str:
    """Classify an error message with the same rules the runner applies live."""
    if not message:
        return "ok"
    if "text helper" in message or "no value" in message:
        return "invalid_decision"
    if "timed out" in message or "Code execution timed out" in message:
        return "transport_error"
    return "terminal_error"


def load_runner():
    spec = importlib.util.spec_from_file_location("live_jev_run", Path(__file__).with_name("live_jev_run.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_jev_run"] = mod
    spec.loader.exec_module(mod)
    return mod


def termination_failure_from(d: dict) -> str | None:
    """Why the policy did not terminate cleanly, derived from the committed trace.

    `repeated_action` is the observed Jev failure: the same operation+target
    re-issued while the page did not change. Kept distinct from step-budget
    exhaustion so the summary states the cause, not just the symptom.
    """
    if d.get("clean_termination"):
        return None
    if d.get("error_kind") == "invalid_decision":
        return "policy_format_error"
    if d.get("error_kind") == "transport_error":
        return "transport_error"
    hist = d.get("history") or []
    repeats = 0
    prev = None
    for h in hist:
        sig = (h.get("kind"), h.get("choice"), h.get("text"))
        if h.get("page_changed") is False and sig == prev:
            repeats += 1
        else:
            repeats = 1
        prev = sig
    return "repeated_action" if repeats >= 2 else "step_budget"


def main() -> None:
    benchlib = load_benchlib()
    runner = load_runner()
    for path in sorted(list(LIVE.glob("*.json")) + list(PROBES.glob("*.json"))):
        d = json.loads(path.read_text())
        task = benchlib.get_task(d.get("task") or "todomvc")
        # Reuse the runner's own task resolution so the resumed predicate is the
        # one the run used (the compat variant when the run recorded `compat`).
        if d.get("compat") or "compat" not in d:
            task = runner._compat_task(benchlib, task)
        changed = False
        if "goal_state_reached" not in d:
            parsed = d.get("verification") or {}
            d["goal_state_reached"] = bool(task.check(parsed)) if parsed else False
            changed = True
        if "clean_termination" not in d:
            d["clean_termination"] = d.get("status") == "done"
            changed = True
        if "error_kind" not in d:
            d["error_kind"] = error_kind_from(d.get("error"))
            changed = True
        if "termination_failure" not in d:
            d["termination_failure"] = termination_failure_from(d)
            changed = True
        if changed:
            d["_backfilled"] = True
            path.write_text(json.dumps(d, indent=2, default=str))
            print("backfilled", path.name)


if __name__ == "__main__":
    main()
