#!/usr/bin/env python3
"""Run the Jev browser fast-path screening matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jev_tests.microbench import run_one


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config/browser-fastpath.tasks.json")
    p.add_argument("--microbench-root", default="../web-automation-microbench")
    p.add_argument("--task", action="append", help="Run only selected task id(s)")
    p.add_argument("--backend", action="append", help="Run only selected backend(s)")
    args = p.parse_args()

    config = json.loads(Path(args.config).read_text())
    wanted_tasks = set(args.task or [])
    wanted_backends = set(args.backend or [])
    root = Path(args.microbench_root).expanduser().resolve()

    rows = []
    for spec in config["tasks"]:
        task_id = spec["id"]
        if wanted_tasks and task_id not in wanted_tasks:
            continue
        for backend in config["jev_backends"]:
            if wanted_backends and backend not in wanted_backends:
                continue
            compat_values = [False]
            if spec.get("run_fill_enter_compat") and backend != "browser-harness":
                compat_values.append(True)
            for compat in compat_values:
                for rep in config["screen_reps"]:
                    result = run_one(root, task_id, rep, backend, compat)
                    rows.append(
                        {
                            k: result.get(k)
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
                        }
                    )
                    print(json.dumps(rows[-1]))

    Path("results/browser-fastpath/screening-summary.json").parent.mkdir(
        parents=True, exist_ok=True
    )
    Path("results/browser-fastpath/screening-summary.json").write_text(
        json.dumps(rows, indent=2)
    )


if __name__ == "__main__":
    main()
