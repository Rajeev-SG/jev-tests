#!/usr/bin/env python3
"""Collect historical PRs and their outcome signals (Test 4).

Repos: openreview, ad-platform-intelligence, codex-home, project-recall,
jev-tests. Everything comes from the GitHub API through the `gh` CLI.

Outcome signals, none of which is "merged":
  - changes_requested : a review asking for changes
  - ci_failure        : any failing check on the PR
  - follow_up_fix     : a later PR in the same repo that references this one
                        and whose title starts with fix/revert
  - review_comments   : count of inline review comments

`needed_substantive_review` is the OR of changes_requested, ci_failure and
follow_up_fix. That is the recall target: the gate must not skip these.

Private-repo data stays local (data/prs/ is gitignored); only aggregates are
committed.

    python3 tests/04_pr_gate/collect.py --per-repo 40
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

DATA_DIR = jb.ROOT / "data" / "prs"
REPOS = ["Rajeev-SG/openreview", "Rajeev-SG/ad-platform-intelligence",
         "Rajeev-SG/codex-home", "Rajeev-SG/project-recall", "Rajeev-SG/jev-tests"]
FIELDS = ("number,title,body,state,mergedAt,createdAt,additions,deletions,"
          "changedFiles,files,reviews,comments,commits,statusCheckRollup,"
          "reviewDecision,labels,headRefName")


def gh(args):
    out = subprocess.run(["gh", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[:300])
    return out.stdout


def list_prs(repo: str, limit: int):
    raw = gh(["pr", "list", "--repo", repo, "--state", "all", "--limit", str(limit),
              "--json", "number,title,state,mergedAt,createdAt,closedAt"])
    return json.loads(raw)


def pr_detail(repo: str, number: int):
    return json.loads(gh(["pr", "view", str(number), "--repo", repo, "--json", FIELDS]))


def first_commit_day(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-repo", type=int, default=40)
    args = parser.parse_args()

    jb.ensure_dir(DATA_DIR)
    rows, skipped = [], []
    for repo in REPOS:
        try:
            listed = list_prs(repo, args.per_repo)
        except RuntimeError as exc:
            print(f"{repo}: {exc}", file=sys.stderr)
            continue
        print(f"{repo}: {len(listed)} PRs")
        plain = {pr["number"]: pr for pr in listed}
        for entry in listed:
            number = entry["number"]
            try:
                detail = pr_detail(repo, number)
            except RuntimeError as exc:
                skipped.append({"repo": repo, "number": number, "why": str(exc)})
                continue
            created = first_commit_day(detail.get("createdAt"))
            changes_requested = sum(
                1 for r in detail.get("reviews") or [] if r.get("state") == "CHANGES_REQUESTED")
            ci_failure = sum(
                1 for c in detail.get("statusCheckRollup") or []
                if (c.get("conclusion") or c.get("state") or "").upper() in ("FAILURE", "ERROR"))
            follow_up = []
            for other_number, other in plain.items():
                if other_number <= number:
                    continue
                title = (other.get("title") or "").strip().lower()
                if not (title.startswith("fix") or title.startswith("revert")):
                    continue
                if f"#{number}" not in title:
                    continue
                other_created = first_commit_day(other.get("createdAt"))
                if created and other_created and other_created - created > timedelta(days=45):
                    continue
                follow_up.append(other_number)
            files = detail.get("files") or []
            rows.append({
                "repo": repo.split("/")[-1],
                "number": number,
                "title": detail.get("title"),
                "body_len": len(detail.get("body") or ""),
                "merged": bool(detail.get("mergedAt")),
                "additions": detail.get("additions") or 0,
                "deletions": detail.get("deletions") or 0,
                "changed_files": detail.get("changedFiles") or 0,
                "file_paths": [f.get("path") for f in files],
                "file_sizes": {f.get("path"): (f.get("additions") or 0) + (f.get("deletions") or 0)
                               for f in files},
                "commits": len(detail.get("commits") or []),
                "review_comments": len(detail.get("comments") or []),
                "changes_requested": changes_requested,
                "ci_failure": ci_failure,
                "follow_up_fixes": follow_up,
                "needed_substantive_review": int(
                    changes_requested > 0 or ci_failure > 0 or bool(follow_up)),
            })
    jb.write_jsonl(DATA_DIR / "prs.jsonl", rows)
    positives = sum(r["needed_substantive_review"] for r in rows)
    print(f"\nPRs={len(rows)} needed_review={positives} ({positives / max(1, len(rows)):.3f}) "
          f"skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())