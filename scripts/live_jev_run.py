#!/usr/bin/env python3
"""Run the REAL Jev Agent loop over a live microbench task (issue #9).

Unlike `live_validate.py` (scripted policy, transport proof only), this drives
the actual Jev decision model — via classifier.dev, whose fast tier is Jev — so
task success and speed are real measurements, not a scripted rehearsal.

    JEV_BROWSER_BACKEND=playwriter PLAYWRITER_SESSION=3 \
      uv run python scripts/live_jev_run.py --task todomvc

Writes results/browser-fastpath/live/<task>-<backend>[-enter]-<rep>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

import jev_ultrafast.agent as jev_agent
import jev_ultrafast.model as jev_model

from jev_tests import classifier_policy, glm_policy
from jev_tests.bridge import BridgeBrowser


def load_benchlib(root: Path):
    bench_ext = root / "bench-ext"
    if not (bench_ext / "benchlib.py").exists():
        raise FileNotFoundError(f"benchlib.py not found under {bench_ext}")
    sys.path.insert(0, str(bench_ext))
    importlib.import_module("task_ingest")
    return importlib.import_module("benchlib")


# Goal achievable inside the shared action space: two todos plus the Active
# filter. The original task's "mark complete" step is impossible for BOTH
# policies here (the checkbox is opacity:0 and the action menu comes from the
# same snapshot.js for Jev and the baseline), so including it would test the
# harness, not the policy. Nothing is completed, so the Active filter shows both.
JEV_COMPAT_GOAL = (
    'Add exactly two todos: "Email supplier" then "Review invoice". '
    "Then click the Active filter. Do not clear completed and do not delete anything. "
    "Work efficiently with ONE logical UI action per response; observe before next action. "
    "Finish only after observing the final state."
)


def _compat_task(benchlib, task):
    """Same page and verifier, minus the checkbox Jev structurally cannot see.

    Success = two todos saved and the Active filter showing only "Review invoice",
    which is the original task's requirement with the impossible step removed.
    """
    def check(parsed):
        saved = parsed.get("saved", [])
        items = [i["text"] for i in parsed.get("items", [])]
        return (
            len(saved) == 2
            and {x["title"] for x in saved} == {"Email supplier", "Review invoice"}
            and "active" in (parsed.get("url") or "")
            and sorted(items) == ["Email supplier", "Review invoice"]
        )

    return benchlib.Task(
        id=task.id,
        instruction=JEV_COMPAT_GOAL,
        url=task.url,
        observe_js=task.observe_js,
        verify_js=task.verify_js,
        check=check,
        capabilities=task.capabilities,
        provenance={**(task.provenance or {}), "variant": "no-complete-step"},
    )


def run(root: Path, task_id: str, rep: str, backend: str, fill_enter: bool,
        policy: str = "jev", compat: bool = False) -> dict:
    benchlib = load_benchlib(root)
    task = benchlib.get_task(task_id)
    if compat:
        task = _compat_task(benchlib, task)

    os.environ["JEV_BROWSER_BACKEND"] = backend
    if fill_enter:
        os.environ["JEV_FILL_ENTER"] = "1"
    else:
        os.environ.pop("JEV_FILL_ENTER", None)
    if task.id == "todomvc":
        os.environ["JEV_RESET_JS"] = "localStorage.removeItem('react-todos')"
    else:
        os.environ.pop("JEV_RESET_JS", None)

    # Either real Jev decisions (via classifier.dev) or the GLM baseline, both
    # over the identical BridgeBrowser so only the policy differs.
    chosen = classifier_policy if policy == "jev" else glm_policy
    jev_model.choose = chosen.choose
    jev_model.field_text = chosen.field_text
    jev_agent.choose = chosen.choose
    jev_agent.field_text = chosen.field_text

    previous_browser = jev_agent.Browser
    jev_agent.Browser = BridgeBrowser
    started = time.perf_counter()
    verify_raw = verify_parsed = error = None
    passed = False
    try:
        with jev_agent.Agent(task.url, task.instruction, screenshots=False) as agent:
            for _ in agent.run():
                pass
            wall_s = time.perf_counter() - started
            state = agent.state
            try:
                verify_raw = agent.browser.evaluate(task.verify_js)
                raw = verify_raw if isinstance(verify_raw, str) else json.dumps(verify_raw)
                verify_parsed = benchlib.parse_verify(raw)
                passed = bool(task.check(verify_parsed) and state.get("status") == "done")
            except Exception as exc:
                error = f"verify: {type(exc).__name__}: {exc}"
            summary = _summary(state, wall_s)
    except Exception as exc:
        wall_s = time.perf_counter() - started
        error = f"{type(exc).__name__}: {exc}"
        summary = {"status": "error", "wall_s": round(wall_s, 3), "jev_decisions": 0,
                   "actions": 0, "text_calls": 0, "decision_p50_ms": None,
                   "decision_p95_ms": None, "history": [], "decisions": []}
        state = {}
    finally:
        jev_agent.Browser = previous_browser

    out = {
        "task": task.id, "rep": rep, "backend": backend,
        "policy": policy,
        "condition": f"{policy}+{backend}" + ("+enter" if fill_enter else ""),
        "fill_enter_compat": fill_enter,
        "pass": passed, "verification": verify_parsed, "error": error,
        "source_repo": "Rajeev-SG/web-automation-microbench",
        **summary,
    }
    outdir = Path("results") / "browser-fastpath" / "live"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{task.id}-{policy}-{backend}{'-enter' if fill_enter else ''}-{rep}.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    out["artifact"] = str(path)
    return out


def _summary(state: dict, wall_s: float) -> dict:
    decisions = state.get("decisions", [])
    dms = [float(d["latency_ms"]) for d in decisions if d.get("latency_ms") is not None]
    ordered = sorted(dms)
    total_in = total_out = total_reason = 0
    for d in decisions:
        u = (d.get("usage") or {})
        total_in += u.get("prompt_tokens") or u.get("input_tokens") or 0
        total_out += u.get("completion_tokens") or u.get("output_tokens") or 0
        total_reason += (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    text_ms = sum(float(h.get("text_latency_ms") or 0) for h in state.get("history", []))
    return {
        "status": state.get("status"),
        "wall_s": round(wall_s, 3),
        "jev_decisions": len(decisions),
        "actions": len(state.get("history", [])),
        "text_calls": len(state.get("text_calls", [])),
        "decision_p50_ms": round(statistics.median(dms), 1) if dms else None,
        "decision_p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1) if dms else None,
        "decision_model": decisions[0].get("model") if decisions else None,
        "decision_tokens": {"input": total_in, "output": total_out, "reasoning": total_reason},
        "text_helper_ms_total": round(text_ms, 1),
        "history": [
            {"step": h.get("step"), "kind": h.get("kind"), "choice": h.get("choice"),
             "text": h.get("text"), "page_changed": h.get("page_changed")}
            for h in state.get("history", [])
        ],
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="todomvc")
    p.add_argument("--microbench-root", default="/Users/rajeev/Code/web-automation-microbench")
    p.add_argument("--backend", default="playwriter")
    p.add_argument("--rep", default="1")
    p.add_argument("--policy", default="jev", choices=["jev", "glm"])
    p.add_argument("--compat", action="store_true",
                   help="Jev-compatible variant: same page, minus the invisible checkbox")
    p.add_argument("--fill-enter", action="store_true")
    args = p.parse_args()
    result = run(Path(args.microbench_root), args.task, args.rep, args.backend,
                 args.fill_enter, args.policy, args.compat)
    print(json.dumps({k: result.get(k) for k in (
        "task", "backend", "policy", "condition", "pass", "wall_s", "jev_decisions",
        "actions", "text_calls", "decision_p50_ms", "decision_p95_ms",
        "status", "error", "artifact")}, indent=2, default=str))


if __name__ == "__main__":
    main()
