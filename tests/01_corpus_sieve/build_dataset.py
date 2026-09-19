#!/usr/bin/env python3
"""Build the Test 1 dataset from the real published ad-platform-intelligence export.

Source: Rajeev-SG/adpi-data (public, machine-written by the adpi acquisition
pipeline on Oracle). `dataset.json` is the current-state v2 export: 837
normalised capability records across Google Ads, Meta, TikTok and Pinterest.

Why this shape
--------------
The pipeline's expensive step is model-driven extraction and synthesis over
harvested vendor content. What is public is the *output* of that step, not the
raw vendor prose (data/README.md in ad-platform-intelligence: raw HTML and full
snapshots stay in private storage). So the measured sieve is the real one that
is reproducible from public data:

    given a published capability record, is it worth carrying into the
    expensive synthesis layer, or is it a record the pipeline itself could not
    verify as a live, actionable capability?

Ground truth is the pipeline's own outcome fields (`maturity`, `control_mode`),
which are deliberately REMOVED from the classifier input so the label cannot be
read off the text.

Usage:
    python3 tests/01_corpus_sieve/build_dataset.py [--refresh]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

DATASET_URL = "https://raw.githubusercontent.com/Rajeev-SG/adpi-data/main/dataset.json"
DATA_DIR = jb.ROOT / "data" / "adpi"

# Words that mark navigation/boilerplate rather than a capability. Used only by
# the trivial deterministic baseline, never by Jev or GLM.
NAV_WORDS = (
    "menu", "overview", "tab", "page", "link", "dashboard", "home",
    "navigation", "breadcrumb", "drop-down", "dropdown", "tooltip",
)


def fetch(refresh: bool = False) -> dict:
    jb.ensure_dir(DATA_DIR)
    path = DATA_DIR / "dataset.json"
    if refresh or not path.exists():
        print(f"fetching {DATASET_URL}")
        with urllib.request.urlopen(DATASET_URL, timeout=120) as resp:
            path.write_bytes(resp.read())
    with path.open() as fh:
        return json.load(fh)


def record_text(record: dict) -> str:
    """The text a classifier is allowed to see. No label fields."""
    scope = record.get("scope") or {}
    locator = ""
    for item in record.get("evidence") or []:
        locator = item.get("locator") or ""
        break
    parts = [
        f"{record.get('platform')} | {record.get('capability_type')}",
        f"name: {record.get('name')}",
    ]
    if record.get("vendor_term"):
        parts.append(f"vendor term: {record['vendor_term']}")
    if locator:
        parts.append(f"source locator: {locator}")
    if scope:
        keys = []
        for key, value in sorted(scope.items()):
            if value:
                keys.append(key)
        if keys:
            parts.append(f"scope fields: {', '.join(keys)}")
    if record.get("description"):
        parts.append(f"description: {record['description']}")
    return "\n".join(parts)


def label_for(record: dict) -> tuple[int, str]:
    """Pipeline outcome -> binary ground truth.

    0 = the pipeline could not confirm this as a live, actionable capability
        (maturity unknown, or control_mode not_applicable): not worth carrying
        into expensive synthesis.
    1 = substantive capability.
    """
    if record.get("maturity") == "unknown" and record.get("control_mode") == "not_applicable":
        return 0, "unknown_and_not_applicable"
    if record.get("maturity") == "unknown":
        return 0, "maturity_unknown"
    if record.get("control_mode") == "not_applicable":
        return 0, "control_mode_not_applicable"
    return 1, "substantive"


def heuristic_predict(text: str) -> int:
    lowered = text.lower()
    return 0 if any(word in lowered for word in NAV_WORDS) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="re-download dataset.json")
    args = parser.parse_args()

    payload = fetch(args.refresh)
    records = payload["capabilities"]
    rows = []
    for record in records:
        label, reason = label_for(record)
        text = record_text(record)
        rows.append({
            "id": record["id"],
            "vendor": record.get("vendor"),
            "platform": record.get("platform"),
            "capability_type": record.get("capability_type"),
            "held_out_fields": {
                "maturity": record.get("maturity"),
                "control_mode": record.get("control_mode"),
            },
            "label": label,
            "label_reason": reason,
            "heuristic": heuristic_predict(text),
            "text": text,
            "text_tokens_approx": jb.approx_tokens(text),
        })

    jb.write_jsonl(DATA_DIR / "records.jsonl", rows)
    positives = sum(r["label"] for r in rows)
    print(f"generated_at={payload.get('generated_at')} schema={payload.get('schema_version')}")
    print(f"records={len(rows)} worth_processing={positives} "
          f"low_value={len(rows) - positives} positive_rate={positives / len(rows):.3f}")
    print(f"wrote {DATA_DIR / 'records.jsonl'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())