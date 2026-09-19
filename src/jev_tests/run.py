"""Run upstream Jev Agent with the backend bridge."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time

import jev_ultrafast.agent as jev_agent

from .bridge import BridgeBrowser


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


def run(url: str, goal: str) -> dict:
    # Preserve upstream Agent/model/text logic; replace browser execution only.
    # Restore the upstream class afterwards so this is not a permanent global patch.
    previous_browser = jev_agent.Browser
    jev_agent.Browser = BridgeBrowser
    started = time.perf_counter()
    try:
        return _run_agent(url, goal, started)
    finally:
        jev_agent.Browser = previous_browser


def _run_agent(url: str, goal: str, started: float) -> dict:
    with jev_agent.Agent(url, goal, screenshots=False) as agent:
        last = None
        for last in agent.run():
            pass
        state = agent.state
        history = state.get("history", [])
        decisions = state.get("decisions", [])
        decision_ms = [
            float(d["latency_ms"])
            for d in decisions
            if d.get("latency_ms") is not None
        ]
        return {
            "backend": os.environ.get("JEV_BROWSER_BACKEND", "browser-relay"),
            "fill_enter_compat": os.environ.get("JEV_FILL_ENTER") == "1",
            "status": state.get("status"),
            "wall_s": round(time.perf_counter() - started, 3),
            "jev_decisions": len(decisions),
            "actions": len(history),
            "text_calls": len(state.get("text_calls", [])),
            "decision_p50_ms": round(statistics.median(decision_ms), 3)
            if decision_ms
            else None,
            "decision_p95_ms": round(_p95(decision_ms), 3) if decision_ms else None,
            "history": history,
            "decisions": decisions,
            "final": last,
        }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--goal", required=True)
    p.add_argument("--backend", choices=["browser-relay", "playwriter"])
    p.add_argument(
        "--fill-enter",
        action="store_true",
        help="Compatibility condition: press Enter after each TYPE_TEXT action",
    )
    args = p.parse_args()
    if args.backend:
        os.environ["JEV_BROWSER_BACKEND"] = args.backend
    if args.fill_enter:
        os.environ["JEV_FILL_ENTER"] = "1"
    print(json.dumps(run(args.url, args.goal), indent=2))


if __name__ == "__main__":
    main()
