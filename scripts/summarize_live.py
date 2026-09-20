#!/usr/bin/env python3
"""Regenerate results/browser-fastpath/live-summary.json from the per-rep JSONs.

One summarizer, derived from the raw files, so the aggregate cannot drift from
what was actually run. Buckets every run explicitly instead of silently
excluding it:

    pass                   goal state reached AND policy stopped with DONE
    goal_state_only        goal state reached, policy never declared DONE
    no_goal_state          policy stopped (blocked/DONE) without reaching the goal
    transport_error        the browser transport failed (not the policy)
    policy_format_error    the policy emitted an invalid action (e.g. empty fill)

Runs with `condition` not ending in a policy pair are reported but never mixed
into an arm's pass rate.
"""
from __future__ import annotations

import collections
import json
import re
import statistics
from pathlib import Path

LIVE = Path("results/browser-fastpath/live")
OUT = Path("results/browser-fastpath/live-summary.json")

NAME = re.compile(r"^(?P<task>.+?)-(?P<policy>jev|glm)-(?P<backend>browser-relay|playwriter)"
                  r"(?P<enter>-enter)?-(?P<rep>\d+)\.json$")


INFRA_MARKERS = ("Code execution timed out", "extension_not_connected",
                 "no attached tab", "chrome", "Playwriter", "browser-relay")
# A policy that declines to supply a field value is a policy failure, not the
# transport: it must not be counted as infrastructure noise.
POLICY_FORMAT_MARKERS = ("did not supply a field value", "returned no value",
                         "text helper returned no value", "returned empty content")


def is_infra_failure(d: dict) -> bool:
    """A transport/infrastructure fault, not a policy decision.

    These runs never invoked the model, so counting them against an arm's pass
    rate would bias the comparison by whichever way the flakes fell.
    """
    msg = d.get("error") or ""
    return d.get("status") == "error" and any(m.lower() in msg.lower() for m in INFRA_MARKERS)


def classify(d: dict) -> str:
    """Bucket one run. Every bucket is defined in the summary legend."""
    if d.get("pass"):
        return "pass"
    if "goal_state_reached" not in d:
        # Never guess. A run written before the dual-scoring change must be
        # backfilled with the real predicate (scripts/backfill_live_artifacts.py)
        # rather than re-derived here from task-specific literals.
        raise SystemExit(
            f"{d.get('_file')} has no goal_state_reached field; run "
            "scripts/backfill_live_artifacts.py first")
    got_goal = bool(d["goal_state_reached"])

    if d.get("status") == "error":
        msg = (d.get("error") or "").lower()
        if any(m.lower() in msg for m in POLICY_FORMAT_MARKERS):
            return "policy_format_error"
        if is_infra_failure(d):
            return "infra_error"
        if d.get("error_kind") == "invalid_decision":
            return "policy_format_error"
        # A crash with no decisions and no actions never reached the policy, so
        # it is a harness fault, not a model-arm failure.
        if not d.get("jev_decisions") and not d.get("actions"):
            return "terminal_error"
        return "transport_error"

    if got_goal:
        return "goal_state_only"
    return "no_goal_state"


def main() -> None:
    arms: dict[str, list] = collections.defaultdict(list)
    skipped = []
    for path in sorted(LIVE.glob("*.json")):
        m = NAME.match(path.name)
        if not m or not m.group("enter"):
            skipped.append(path.name)
            continue
        d = json.loads(path.read_text())
        d["_file"] = path.name
        if "goal_state_reached" not in d or "clean_termination" not in d:
            raise SystemExit(
                f"{path.name} predates dual scoring; run scripts/backfill_live_artifacts.py")
        d["_goal"] = bool(d["goal_state_reached"])
        d["_clean"] = bool(d["clean_termination"])
        d["_bucket"] = classify(d)
        arms[f"{m.group('policy')}+{m.group('backend')}"].append(d)

    out = {
        "test": "Test 6b — Jev fast path, live browser run (issue #9)",
        "scoring": {
            "pass": "goal state reached by the verifier AND the policy stopped with DONE",
            "goal_state_only": "verifier says the goal state was reached, but the policy never declared DONE",
            "no_goal_state": "policy stopped without reaching the goal state",
            "infra_error": "browser/transport infrastructure fault (timeout, no attached tab); excluded from policy rates",
    "terminal_error": "harness-side crash before the policy produced a decision; excluded from policy rates",
    "transport_error": "browser transport failed mid-run; not a policy result",
            "policy_format_error": "policy emitted an invalid action (e.g. a fill with no value)",
        },
        "note": "Only `+enter` runs are scored (the Enter-compat condition). Timed-out or "
                "format-error runs are kept in their buckets, never dropped.",
        "arms": {},
        "not_scored_non_enter_runs": skipped,
    }
    for arm, rows in sorted(arms.items()):
        buckets = collections.Counter(r["_bucket"] for r in rows)
        valid = [r for r in rows if r["_bucket"] in ("pass", "goal_state_only", "no_goal_state")]
        p50 = [r["decision_p50_ms"] for r in valid if r.get("decision_p50_ms")]
        for r in valid:
            a, b = r.get("decision_p50_ms"), r.get("decision_p95_ms")
            if a is not None and b is not None and b < a:
                raise SystemExit(
                    f"{r.get('_file')} has p95 {b} < p50 {a}; latency percentiles are invalid")
        out["arms"][arm] = {
            "runs": len(rows),
            "buckets": dict(buckets),
            "goal_state_reached": sum(1 for r in rows if r["_goal"]),
            "clean_termination": sum(1 for r in rows if r["_clean"]),
            "pass": buckets.get("pass", 0),
            "excluded_from_policy_rates": (
                buckets.get("infra_error", 0) + buckets.get("terminal_error", 0)),
            "infra_errors_excluded": buckets.get("infra_error", 0),
            # Both denominators, so a fast failure cannot flatter an arm by being
            # dropped: all runs, and runs that actually reached the browser.
            "median_wall_s_all_runs": round(
                statistics.median([r["wall_s"] for r in rows]), 1) if rows else None,
            "median_wall_s_policy_completed": round(
                statistics.median([r["wall_s"] for r in valid]), 1) if valid else None,
            "median_decision_p50_ms": round(statistics.median(p50)) if p50 else None,
            "median_actions": round(statistics.median([r["jev_decisions"] for r in valid]), 1) if valid else None,
            "decision_model": valid[0].get("decision_model") if valid else None,
            "termination_failures": dict(collections.Counter(
                r["termination_failure"] for r in rows if r.get("termination_failure"))),
            "files": [r["_file"] for r in rows],
        }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
