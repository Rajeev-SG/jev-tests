#!/usr/bin/env python3
"""Block committed aggregates that leak session-identifying absolute paths.

`data/sessions/*.jsonl` is gitignored because it holds real work paths and
client names. Aggregate summaries are committed, so the rule only holds if a
derived summary never carries a raw `/Users/...` or `/home/...` path through.

Run from CI and from a pre-commit hook:

    python3 scripts/privacy_guard.py <paths...>

Exit 0 when clean, 1 when a committed artefact carries a home-directory path.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Absolute home paths are the leak vector: they embed the user name and the
# client/project directory. Aggregates must use opaque keys instead.
HOME_PATH = re.compile(r"/(?:Users|home)/[^/\"\'\s]+/[^\"\'\s]+")

DEFAULT_GLOBS = ("results/**/*.json", "results/**/*.csv", "results/**/*.md")


def scan(path: Path) -> list[str]:
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    return sorted({m for m in HOME_PATH.findall(text)})


def main(argv: list[str]) -> int:
    targets: list[Path] = []
    paths = argv[1:]
    root = Path.cwd()
    if paths:
        for p in paths:
            path = Path(p)
            targets.extend(path.rglob("*") if path.is_dir() else [path])
    else:
        for pattern in DEFAULT_GLOBS:
            targets.extend(root.glob(pattern))
    bad = 0
    for path in sorted(set(targets)):
        if not path.is_file():
            continue
        hits = scan(path)
        if hits:
            bad += 1
            print(f"privacy-guard: {path} carries a home-directory path", file=sys.stderr)
            for hit in hits[:3]:
                print(f"  {hit}", file=sys.stderr)
    if bad:
        print(
            f"privacy-guard: {bad} committed artefact(s) leak a home-directory path. "
            "Replace absolute paths with opaque keys before committing.",
            file=sys.stderr,
        )
        return 1
    print("privacy-guard: no home-directory paths in committed artefacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
