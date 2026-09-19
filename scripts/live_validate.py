#!/usr/bin/env python3
"""Live validation for the Jev browser bridge (issue #9).

Runs the REAL upstream jev_ultrafast.agent.Agent loop with the REAL BridgeBrowser
against a REAL microbench task + verifier, substituting only the TypeSafe model
calls with a deterministic scripted policy. This isolates the transport +
observed-node contract, which is exactly what needs live validation before the
scored screening matrix (which needs a TypeSafe key that is absent on this Mac).

Usage:
  JEV_BROWSER_BACKEND=playwriter PLAYWRITER_SESSION=3 \
    uv run python scripts/live_validate.py --task todomvc --backend playwriter
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import time
from pathlib import Path

import jev_ultrafast.agent as jev_agent
import jev_ultrafast.model as jev_model

from jev_tests.bridge import BridgeBrowser

ROOT = Path(__file__).resolve().parents[1]


def load_benchlib(microbench_root: Path):
    bench_ext = microbench_root / "bench-ext"
    if not (bench_ext / "benchlib.py").exists():
        raise FileNotFoundError(f"benchlib.py not found under {bench_ext}")
    sys.path.insert(0, str(bench_ext))
    importlib.import_module("task_ingest")
    return importlib.import_module("benchlib")


# --- scripted policy: TodoMVC canonical task ---------------------------------
# Goal: add "Email supplier", "Review invoice"; complete only the first;
# click Active; verify only "Review invoice" shows with 1 item left.
class TodoMVCPlan:
    """Deterministic stand-in for the TypeSafe policy, driving the real browser."""

    def __init__(self):
        self.i = 0
        self.fills = ["Email supplier", "Review invoice"]

    def choose(self, page, goal, history):
        acts = page["actions"]
        def find(*, kind=None, needle=None, exact=False):
            for a in acts:
                if kind and a["kind"] != kind:
                    continue
                lbl = a.get("label", "")
                if needle is None or (lbl == needle if exact else needle.lower() in lbl.lower()):
                    return a
            return None

        step = self.i
        if step == 0 or step == 1:
            a = find(kind="fill", needle="What needs to be done?")
        elif step == 2:
            a = find(kind="click", needle="Email supplier")
        elif step == 3:
            a = find(kind="click", needle="Active")
        elif step == 4:
            a = None  # DONE
        else:
            a = None

        if step >= 4:
            choice = "DONE"
        elif a is None:
            choice = "BLOCKED"
        else:
            choice = a["id"]
        self.i += 1
        return {
            "choice": choice,
            "operation": "DONE" if choice == "DONE" else (a.get("kind", "").upper() if a else "BLOCKED"),
            "target": None,
            "confidence": 1.0,
            "probabilities": {choice: 1.0},
            "operation_probabilities": {},
            "target_probabilities": {},
            "target_confidence": None,
            "raw_answers": {},
            "model": "scripted",
            "usage": {},
            "latency_ms": 1.0,
            "request": {},
        }

    def next_text(self):
        v = self.fills.pop(0) if self.fills else None
        return v


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="todomvc")
    p.add_argument("--microbench-root", default="/Users/rajeev/Code/web-automation-microbench")
    p.add_argument("--backend", default="playwriter")
    p.add_argument("--fill-enter", action="store_true")
    args = p.parse_args()

    benchlib = load_benchlib(Path(args.microbench_root))
    task = benchlib.get_task(args.task)

    os.environ["JEV_BROWSER_BACKEND"] = args.backend
    if args.fill_enter:
        os.environ["JEV_FILL_ENTER"] = "1"

    plan = TodoMVCPlan()

    def scripted_choose(page, goal, history):
        return plan.choose(page, goal, history)

    def scripted_field_text(context):
        return plan.next_text(), {"model": "scripted", "latency_ms": 0.0}

    jev_model.choose = scripted_choose
    jev_model.field_text = scripted_field_text
    jev_agent.choose = scripted_choose
    jev_agent.field_text = scripted_field_text

    jev_agent.Browser = BridgeBrowser

    reset_js = None
    if task.id == "todomvc":
        reset_js = "localStorage.removeItem('react-todos')"
        os.environ["JEV_RESET_JS"] = reset_js

    started = time.perf_counter()
    error = None
    verify_parsed = None
    try:
        with jev_agent.Agent(task.url, task.instruction, screenshots=False) as agent:
            for _ in agent.run():
                pass
            wall_s = time.perf_counter() - started
            raw = agent.browser.evaluate(task.verify_js)
            verify_parsed = benchlib.parse_verify(raw if isinstance(raw, str) else json.dumps(raw))
            passed = bool(task.check(verify_parsed) and agent.state.get("status") == "done")
            state = agent.state
    except Exception as exc:
        wall_s = time.perf_counter() - started
        passed = False
        error = f"{type(exc).__name__}: {exc}"
        state = {}

    out = {
        "task": task.id,
        "backend": args.backend,
        "fill_enter_compat": args.fill_enter,
        "pass": passed,
        "wall_s": round(wall_s, 3),
        "jev_decisions": len(state.get("decisions", [])),
        "actions": len(state.get("history", [])),
        "text_calls": len(state.get("text_calls", [])),
        "status": state.get("status"),
        "error": error,
        "verification": verify_parsed,
        "history": [
            {"step": h.get("step"), "kind": h.get("kind"), "choice": h.get("choice"),
             "text": h.get("text"), "page_changed": h.get("page_changed")}
            for h in state.get("history", [])
        ],
    }
    print(json.dumps(out, indent=2, default=str))
    outdir = Path("results") / "browser-fastpath" / "validation"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / f"live-{task.id}-{args.backend}{'-enter' if args.fill_enter else ''}.json").write_text(
        json.dumps(out, indent=2, default=str)
    )


if __name__ == "__main__":
    main()
