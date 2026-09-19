"""Run Jev fast-path conditions against web-automation-microbench tasks."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

import jev_ultrafast.agent as jev_agent

from .bridge import BridgeBrowser

UPSTREAM_BROWSER = jev_agent.Browser


def _load_benchlib(root: Path):
    bench_ext = root / "bench-ext"
    if not (bench_ext / "benchlib.py").exists():
        raise FileNotFoundError(f"benchlib.py not found under {bench_ext}")
    sys.path.insert(0, str(bench_ext))
    benchlib = importlib.import_module("benchlib")
    # Register harvested tasks if available. benchlib also lazy-loads on get_task().
    try:
        importlib.import_module("task_ingest")
    except Exception:
        pass
    return benchlib


def _summary(state: dict, wall_s: float) -> dict:
    decisions = state.get("decisions", [])
    history = state.get("history", [])
    dms = [float(d["latency_ms"]) for d in decisions if d.get("latency_ms") is not None]
    p95 = None
    if dms:
        ordered = sorted(dms)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    return {
        "status": state.get("status"),
        "wall_s": round(wall_s, 3),
        "jev_decisions": len(decisions),
        "actions": len(history),
        "text_calls": len(state.get("text_calls", [])),
        "decision_p50_ms": round(statistics.median(dms), 3) if dms else None,
        "decision_p95_ms": round(p95, 3) if p95 is not None else None,
        "history": history,
        "decisions": decisions,
        "text_call_details": state.get("text_calls", []),
    }


def run_one(root: Path, task_id: str, rep: str, backend: str, fill_enter: bool) -> dict:
    benchlib = _load_benchlib(root)
    task = benchlib.get_task(task_id)

    os.environ["JEV_BROWSER_BACKEND"] = backend
    if fill_enter:
        os.environ["JEV_FILL_ENTER"] = "1"
    else:
        os.environ.pop("JEV_FILL_ENTER", None)

    # Match the canonical TodoMVC reset used by the microbench adapters.
    if task.id == "todomvc":
        os.environ["JEV_RESET_JS"] = "localStorage.removeItem('react-todos')"
    else:
        os.environ.pop("JEV_RESET_JS", None)

    if backend == "browser-harness":
        class BenchmarkUpstreamBrowser(UPSTREAM_BROWSER):
            def __init__(self, url: str):
                super().__init__(url)
                reset_js = os.environ.get("JEV_RESET_JS")
                if reset_js:
                    self.evaluate(reset_js)
                    self.call("Page.navigate", url=url)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if self.evaluate("document.readyState") == "complete":
                            break
                        time.sleep(0.05)

        jev_agent.Browser = BenchmarkUpstreamBrowser
    else:
        jev_agent.Browser = BridgeBrowser
    started = time.perf_counter()
    verify_raw = None
    verify_parsed = None
    error = None

    try:
        with jev_agent.Agent(task.url, task.instruction, screenshots=False) as agent:
            for _ in agent.run():
                pass
            wall_s = time.perf_counter() - started
            state = agent.state
            try:
                verify_raw = agent.browser.evaluate(task.verify_js)
                raw_for_parser = (
                    verify_raw
                    if isinstance(verify_raw, str)
                    else json.dumps(verify_raw)
                )
                verify_parsed = benchlib.parse_verify(raw_for_parser)
                passed = bool(task.check(verify_parsed) and state.get("status") == "done")
            except Exception as exc:
                passed = False
                error = f"verify: {type(exc).__name__}: {exc}"
            result = _summary(state, wall_s)
    except Exception as exc:
        wall_s = time.perf_counter() - started
        passed = False
        error = f"{type(exc).__name__}: {exc}"
        result = {
            "status": "error",
            "wall_s": round(wall_s, 3),
            "jev_decisions": 0,
            "actions": 0,
            "text_calls": 0,
            "decision_p50_ms": None,
            "decision_p95_ms": None,
            "history": [],
            "decisions": [],
            "text_call_details": [],
        }

    result.update(
        {
            "task": task.id,
            "rep": rep,
            "backend": backend,
            "condition": f"jev+{backend}" + ("+enter" if fill_enter else ""),
            "fill_enter_compat": fill_enter,
            "pass": passed,
            "verification_raw": verify_raw,
            "verification": verify_parsed,
            "error": error,
            "source_repo": "Rajeev-SG/web-automation-microbench",
        }
    )

    outdir = (
        Path("results")
        / "browser-fastpath"
        / dt.date.today().isoformat()
        / task.id
        / result["condition"]
    )
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{rep}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    result["artifact"] = str(path)
    return result


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--microbench-root", default="../web-automation-microbench")
    p.add_argument("--task", default="todomvc")
    p.add_argument("--rep", default="1")
    p.add_argument(
        "--backend",
        required=True,
        choices=["browser-harness", "browser-relay", "playwriter"],
    )
    p.add_argument("--fill-enter", action="store_true")
    args = p.parse_args()

    result = run_one(
        Path(args.microbench_root).expanduser().resolve(),
        args.task,
        args.rep,
        args.backend,
        args.fill_enter,
    )
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "task",
                    "rep",
                    "condition",
                    "pass",
                    "wall_s",
                    "jev_decisions",
                    "actions",
                    "text_calls",
                    "decision_p50_ms",
                    "decision_p95_ms",
                    "error",
                    "artifact",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
