#!/usr/bin/env python3
"""Test 4 - Jev gate before expensive PR/code review.

102 real historical PRs from openreview, ad-platform-intelligence, codex-home,
project-recall and jev-tests, with outcome signals taken from GitHub metadata
plus a rework signal computed from later PRs that fix the same files.

Compares: always run a full review, cheap deterministic rules, and a Jev gate
(two narrow questions) with GLM-5.3-Flash escalation. Primary metric is recall
of PRs that actually needed substantive review.

    python3 tests/04_pr_gate/run.py --glm-sample 80
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

PRS = jb.ROOT / "data" / "prs" / "prs.jsonl"
OUT = jb.ROOT / "results" / "04_pr_gate"

TRIVIAL_LABELS = ["mechanically trivial", "needs a semantic reviewer"]
RISK_LABELS = ["touches a high-risk surface", "does not touch a high-risk surface"]
THRESHOLD = 0.7
THRESHOLDS = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]

HIGH_RISK = re.compile(
    r"(auth|login|session|token|secret|credential|payment|billing|migration|"
    r"schema|\.sql$|deploy|infra|docker|ci\.yml|workflows?/|security|permission)",
    re.I)
DOCS_ONLY = re.compile(r"(^|/)(docs?|README|CHANGELOG|LICENSE|\.github/ISSUE_TEMPLATE)", re.I)

TRIVIAL_INSTRUCTIONS = (
    "You are gating a pull request before an expensive semantic review. Decide "
    "whether the change is mechanically trivial (a rename, a version bump, a "
    "comment, a docs edit, a formatting change, a one-line obvious fix) or "
    "whether a reviewer reading it could find a real defect."
)
RISK_INSTRUCTIONS = (
    "Decide whether this change touches a high-risk surface: authentication, "
    "authorisation, sessions, secrets, payments, billing, database migrations "
    "or schemas, deployment or infrastructure, CI configuration, or security."
)
GLM_SYSTEM = "You triage a pull request diff summary. Answer with JSON only."
GLM_PROMPT = """Pull request summary:

{summary}

Should a semantic reviewer read this diff? Say true only if reading it could \
find a real defect. Reply with JSON only:
{{"needs_review": true, "confidence": 0.0, "reason": "short"}}"""


def load_rows():
    with PRS.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def add_rework(rows):
    by_repo = collections.defaultdict(list)
    for row in rows:
        by_repo[row["repo"]].append(row)
    positives = 0
    for repo, group in by_repo.items():
        for row in group:
            files = set(row["file_paths"])
            rework = []
            for other in group:
                if other["number"] <= row["number"]:
                    continue
                title = (other["title"] or "").strip().lower()
                if not re.match(r"^(fix|revert|hotfix|repair)", title):
                    continue
                if files & set(other["file_paths"]):
                    rework.append(other["number"])
            row["rework_prs"] = rework
            row["needed_review"] = int(
                row["changes_requested"] > 0 or row["ci_failure"] > 0
                or bool(row["follow_up_fixes"]) or bool(rework))
            positives += row["needed_review"]
    return positives


def summary_text(row):
    lines = [f"repo: {row['repo']}", f"title: {row['title']}",
             f"changed files: {row['changed_files']}",
             f"lines added: {row['additions']}, lines deleted: {row['deletions']}",
             f"commits: {row['commits']}"]
    paths = (row["file_paths"] or [])[:25]
    if paths:
        lines.append("paths: " + ", ".join(paths))
    return "\n".join(lines)


def rule_route(row):
    """Deterministic baseline: lightweight / normal / high-risk."""
    files = row["file_paths"] or []
    total = row["additions"] + row["deletions"]
    if any(HIGH_RISK.search(f) for f in files):
        return "high_risk"
    if total <= 20 and row["changed_files"] <= 2 and all(DOCS_ONLY.search(f) for f in files or [""]):
        return "lightweight"
    if total <= 8 and row["changed_files"] == 1:
        return "lightweight"
    return "normal"


def parse_glm(text):
    match = re.search(r"\{.*\}", text or "", re.S)
    payload = None
    if match:
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, None
    if not isinstance(payload, dict):
        return None, None
    value = payload.get("needs_review")
    if isinstance(value, str):
        value = value.strip().lower() in ("true", "yes", "1")
    if not isinstance(value, bool):
        return None, None
    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    return value, confidence


def gate_metrics(rows, skip_flags):
    """skip_flags[i] True means the gate skipped the full review for PR i."""
    truth = [r["needed_review"] for r in rows]
    skipped = [i for i, s in enumerate(skip_flags) if s]
    kept = [i for i, s in enumerate(skip_flags) if not s]
    missed = [i for i in skipped if truth[i] == 1]
    return {
        "reviews_avoided": len(skipped),
        "reviews_avoided_fraction": len(skipped) / len(rows),
        "recall_of_needed_review": (sum(1 for i in kept if truth[i] == 1) / sum(truth))
        if sum(truth) else None,
        "missed_needed_review": len(missed),
        "missed_examples": [f"{rows[i]['repo']}#{rows[i]['number']}" for i in missed[:10]],
        "false_skips_share": len(missed) / len(skipped) if skipped else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glm-sample", type=int, default=80)
    parser.add_argument("--no-glm", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--tier", default="fast", choices=["fast", "smart"])
    args = parser.parse_args()
    use_cache = not args.refresh

    rows = load_rows()
    positives = add_rework(rows)
    fast = jb.ClassifierDev(tier=args.tier, use_cache=use_cache)
    glm = jb.GLM(use_cache=use_cache)

    summaries = [summary_text(r) for r in rows]
    trivial = fast.classify(summaries, TRIVIAL_LABELS, instructions=TRIVIAL_INSTRUCTIONS)
    risk = fast.classify(summaries, RISK_LABELS, instructions=RISK_INSTRUCTIONS)

    trivial_yes, trivial_conf, risk_yes, risk_conf = [], [], [], []
    for t_res, r_res in zip(trivial, risk):
        trivial_yes.append(t_res["label"] == TRIVIAL_LABELS[0])
        trivial_conf.append(t_res["confidence"])
        risk_yes.append(r_res["label"] == RISK_LABELS[0])
        risk_conf.append(r_res["confidence"])

    curves = []
    for threshold in THRESHOLDS:
        flags = [trivial_yes[i] and (trivial_conf[i] or 0) >= threshold and not risk_yes[i]
                 for i in range(len(rows))]
        curves.append({"threshold": threshold, **gate_metrics(rows, flags)})

    routes = [rule_route(r) for r in rows]
    rule_flags = [route == "lightweight" for route in routes]

    glm_rows = []
    if not args.no_glm:
        pos = [i for i, r in enumerate(rows) if r["needed_review"]]
        neg = [i for i, r in enumerate(rows) if not r["needed_review"]]
        step = max(1, len(neg) // max(1, args.glm_sample - len(pos)))
        sample = sorted(pos + neg[::step][: max(0, args.glm_sample - len(pos))])
        for index in sample:
            response = glm.chat(GLM_PROMPT.format(summary=summaries[index]),
                                system=GLM_SYSTEM, max_tokens=600, json_object=True)
            needs, confidence = parse_glm(response["text"])
            glm_rows.append({
                "index": index, "needed_review": rows[index]["needed_review"],
                "glm_needs_review": None if needs is None else int(needs),
                "glm_confidence": confidence,
                "prompt_tokens": response["prompt_tokens"],
                "completion_tokens": response["completion_tokens"],
                "cost_usd": response["cost_usd"],
                "elapsed_ms": response["elapsed_ms"],
            })

    summary = {
        "test": "04_pr_gate",
        "data": {
            "prs": len(rows),
            "repos": dict(collections.Counter(r["repo"] for r in rows)),
            "needed_review": positives,
            "needed_review_fraction": positives / len(rows),
            "signal_sources": ["ci_failure", "changes_requested", "follow_up_fixes", "rework_prs"],
            "caveat": "Merged is not treated as safe. Positives come only from objective "
                      "GitHub metadata and file-overlap rework detection, so the positive "
                      "set is small and recall figures are coarse.",
        },
        "always_review_baseline": {"reviews_avoided": 0, "recall_of_needed_review": 1.0},
        "deterministic_rules": {"routes": dict(collections.Counter(routes)),
                                **gate_metrics(rows, rule_flags)},
        "jev_gate_curve": curves,
        "classifier_dev_fast": fast.stats(),
        "glm": glm.stats() if glm_rows else None,
        "provenance": jb.provenance(
            False,
            "inputs are PR metadata from private repositories (openreview, "
            "ad-platform-intelligence, codex-home, project-recall)",
            "python3 tests/04_pr_gate/collect.py --per-repo 30 (needs gh auth)"
            "decision: inputs stay local because four of the five repositories are private; "
            "collect.py is committed and re-runs against any repository the operator can read",),

    }
    if glm_rows:
        parsed = [r for r in glm_rows if r["glm_needs_review"] is not None]
        flags = [r["glm_needs_review"] == 0 for r in parsed]
        sub_rows = [{"repo": rows[r["index"]]["repo"], "number": rows[r["index"]]["number"],
                     "needed_review": r["needed_review"]} for r in parsed]
        summary["glm_gate_sample"] = {"parsed": len(parsed), **gate_metrics(sub_rows, flags)}
        per_pr_tokens = (sum(r["prompt_tokens"] for r in parsed) / len(parsed)) if parsed else 0
        summary["glm_cost"] = {
            "mean_prompt_tokens_per_pr": per_pr_tokens,
            "measured_sample_cost_usd": sum(r["cost_usd"] for r in parsed),
            "illustrative_full_review_cost_usd": round(
                jb.glm_equivalent_cost(per_pr_tokens * len(rows), 250 * len(rows)), 4),
            "note": "the GLM sample saw the diff summary, not the full diff; a real full "
                    "review reads the patch, so the illustrative figure is a lower bound",
        }

    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", [
        {
            "repo": row["repo"], "number": row["number"], "title": row["title"],
            "needed_review": row["needed_review"], "ci_failure": row["ci_failure"],
            "changes_requested": row["changes_requested"],
            "follow_up_fixes": row["follow_up_fixes"], "rework_prs": row["rework_prs"],
            "changed_files": row["changed_files"],
            "lines": row["additions"] + row["deletions"],
            "rule_route": routes[i],
            "jev_trivial": trivial_yes[i], "jev_trivial_confidence": trivial_conf[i],
            "jev_high_risk": risk_yes[i], "jev_risk_confidence": risk_conf[i],
        }
        for i, row in enumerate(rows)
    ])
    report(summary)
    return 0


def report(summary):
    print("=" * 72)
    print("TEST 4 - Jev gate before PR review")
    print("=" * 72)
    print(f"PRs={summary['data']['prs']} needed_review={summary['data']['needed_review']} "
          f"({100 * summary['data']['needed_review_fraction']:.1f}%)")
    print(f"deterministic rules: routes={summary['deterministic_rules']['routes']} "
          f"avoided={summary['deterministic_rules']['reviews_avoided']} "
          f"recall={fmt(summary['deterministic_rules']['recall_of_needed_review'])}")
    print("\nJev gate curve (trivial + confident + not high-risk):")
    for row in summary["jev_gate_curve"]:
        print(f"  >={row['threshold']:.2f}  avoided={row['reviews_avoided']:3d} "
              f"({100 * row['reviews_avoided_fraction']:5.1f}%)  "
              f"recall={fmt(row['recall_of_needed_review'])}  missed={row['missed_needed_review']}")
    if summary.get("glm_gate_sample"):
        g = summary["glm_gate_sample"]
        print(f"\nGLM sample gate: parsed={g['parsed']} avoided={g['reviews_avoided']} "
              f"recall={fmt(g['recall_of_needed_review'])} missed={g['missed_needed_review']}")
    if summary.get("glm_cost"):
        c = summary["glm_cost"]
        print(f"GLM: {c['mean_prompt_tokens_per_pr']:.0f} prompt tokens/PR, "
              f"${c['measured_sample_cost_usd']:.4f} measured, "
              f"${c['illustrative_full_review_cost_usd']:.3f} illustrative for "
              f"{summary['data']['prs']} PRs")
    print(f"\nmissed examples (lowest threshold): "
          f"{summary['jev_gate_curve'][0]['missed_examples']}")


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())