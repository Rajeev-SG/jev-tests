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


def goal_state_from_verification(d: dict) -> bool:
    """Derive the TodoMVC-compat goal state from the stored verification block.

    Older artifacts predate the `goal_state_reached` field, so re-derive it here
    rather than trusting a flag that may be absent: two todos saved, both named
    correctly, and the Active filter applied.
    """
    v = d.get("verification") or {}
    saved = v.get("saved") or []
    items = [i["text"] for i in v.get("items", [])]
    return (
        len(saved) == 2
        and {x["title"] for x in saved} == {"Email supplier", "Review invoice"}
        and "active" in (v.get("url") or "")
        and sorted(items) == ["Email supplier", "Review invoice"]
    )


def clean_term_from_status(d: dict) -> bool:
    return d.get("status") == "done"


def classify(d: dict) -> str:
    if d.get("pass"):
        return "pass"
    v = d.get("verification") or {}
    got_goal = bool(d.get("goal_state_reached")) or goal_state_from_verification(d)
    if d.get("status") == "error":
        return ("policy_format_error" if d.get("error_kind") == "invalid_decision"
                else "transport_error")
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
        d["_goal"] = bool(d.get("goal_state_reached")) or goal_state_from_verification(d)
        d["_clean"] = bool(d.get("clean_termination")) or clean_term_from_status(d)
        d["_bucket"] = classify(d)
        arms[f"{m.group('policy')}+{m.group('backend')}"].append(d)

    out = {
        "test": "Test 6b — Jev fast path, live browser run (issue #9)",
        "scoring": {
            "pass": "goal state reached by the verifier AND the policy stopped with DONE",
            "goal_state_only": "verifier says the goal state was reached, but the policy never declared DONE",
            "no_goal_state": "policy stopped without reaching the goal state",
            "transport_error": "browser transport failed; not a policy result",
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
        out["arms"][arm] = {
            "runs": len(rows),
            "buckets": dict(buckets),
            "goal_state_reached": sum(1 for r in rows if r["_goal"]),
            "clean_termination": sum(1 for r in rows if r["_clean"]),
            "pass": buckets.get("pass", 0),
            "median_wall_s_policy_completed": round(
                statistics.median([r["wall_s"] for r in valid]), 1) if valid else None,
            "median_decision_p50_ms": round(statistics.median(p50)) if p50 else None,
            "median_actions": round(statistics.median([r["jev_decisions"] for r in valid]), 1) if valid else None,
            "decision_model": valid[0].get("decision_model") if valid else None,
            "files": [r["_file"] for r in rows],
        }
    OUT.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
