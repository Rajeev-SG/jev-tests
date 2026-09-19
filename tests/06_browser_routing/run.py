#!/usr/bin/env python3
"""Test 6 - browser next-action routing on the real web-automation-microbench traces.

946 recorded next-action decisions from 192 real-work trace files (CHANEL,
Puma, Porsche, Gymshark, Allbirds, rajeevg.com, tldraw tasks). The TodoMVC
instrument round is tagged separately and excluded from the scored set.

Compares Jev (classifier.dev), a majority-class baseline and GLM-5.3-Flash on a
stratified sample. Offline replay only: no live browser runs were made.

    python3 tests/06_browser_routing/run.py --tier smart --limit 400
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

DECISIONS = jb.ROOT / "data" / "browser" / "decisions.jsonl"
OUT = jb.ROOT / "results" / "06_browser_routing"

LABELS = ["inspect or read the page", "click an element", "type text",
          "press a key", "scroll", "wait", "navigate to a url", "finish the task"]
FAMILY_OF = {
    "inspect or read the page": "inspect", "click an element": "click",
    "type text": "type", "press a key": "press", "scroll": "scroll", "wait": "wait",
    "navigate to a url": "navigate", "finish the task": "done",
}
LABEL_OF = {v: k for k, v in FAMILY_OF.items()}
THRESHOLDS = [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]

INSTRUCTIONS = (
    "You see the goal of a browser-automation task, the actions already taken "
    "and the current page state. Choose the single next browser action to take."
)
GLM_SYSTEM = "You drive a browser agent. Answer with JSON only."
GLM_PROMPT = """{state}

Choose exactly one next action from:
{labels}

Reply with JSON only: {{"action": "<one of the list>", "confidence": 0.0}}"""


def load_rows(kind="real_work"):
    with DECISIONS.open() as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [r for r in rows if r["task_kind"] == kind]


def stratified(rows, size):
    by_label = collections.defaultdict(list)
    for index, row in enumerate(rows):
        by_label[row["label"]].append(index)
    picked = []
    for label, indices in sorted(by_label.items()):
        take = max(1, round(size * len(indices) / len(rows)))
        picked.extend(indices[:take])
    return sorted(picked)[:size]


def parse_action(text):
    match = re.search(r"\{.*\}", text or "", re.S)
    payload = None
    if match:
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None, None
    if not isinstance(payload, dict):
        return None, None
    raw = str(payload.get("action", "")).strip().lower()
    for label, family in FAMILY_OF.items():
        if raw == label or raw == family or family in raw:
            try:
                confidence = float(payload.get("confidence"))
            except (TypeError, ValueError):
                confidence = None
            return family, confidence
    return None, None


def accuracy(pred, truth):
    return sum(1 for p, t in zip(pred, truth) if p == t) / len(truth) if truth else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="fast", choices=["fast", "smart"])
    parser.add_argument("--limit", type=int, default=0, help="stratified sample size; 0 = all")
    parser.add_argument("--glm-sample", type=int, default=80)
    parser.add_argument("--no-glm", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    use_cache = not args.refresh

    all_rows = load_rows("real_work")
    instrument = load_rows("instrument")
    rows = all_rows
    if args.limit and args.limit < len(all_rows):
        rows = [all_rows[i] for i in stratified(all_rows, args.limit)]
    truth = [r["label"] for r in rows]
    states = [r["state"] for r in rows]
    majority = collections.Counter(truth).most_common(1)[0][0]

    client = jb.ClassifierDev(tier=args.tier, use_cache=use_cache)
    results = client.classify(states, LABELS, instructions=INSTRUCTIONS)
    pred, conf, escalated = [], [], 0
    for result in results:
        pred.append(FAMILY_OF.get(result["label"], "other"))
        conf.append(result["confidence"])
        if result["confidence"] is None:
            escalated += 1

    confident = [(c, t == p) for c, t, p in zip(conf, truth, pred) if c is not None]
    # The threshold sweep above is in-sample. This is the honest counterpart: the
    # threshold is chosen on a tuning half and scored on a disjoint held-out half.
    heldout = jb.heldout_threshold_eval(
        [f"{r['trace']}:{r['step']}" for r in rows], conf, [t == p for t, p in zip(truth, pred)], 0.95)
    heldout_90 = jb.heldout_threshold_eval(
        [f"{r['trace']}:{r['step']}" for r in rows], conf, [t == p for t, p in zip(truth, pred)], 0.90)
    summary = {
        "test": "06_browser_routing",
        "data": {
            "decisions_real_work": len(all_rows),
            "decisions_instrument_excluded": len(instrument),
            "decisions_scored": len(rows),
            "traces": len({r["trace"] for r in all_rows}),
            "tasks": dict(collections.Counter(r["task"] for r in all_rows).most_common(12)),
            "sampled": bool(args.limit and args.limit < len(all_rows)),
            "mode": "offline replay of recorded traces; no live browser runs",
        },
        "label_distribution": dict(collections.Counter(truth)),
        "methods": {
            "majority_baseline": {"majority": majority, "top1_accuracy": accuracy([majority] * len(rows), truth)},
            f"classifier_dev_{args.tier}": {
                "top1_accuracy": accuracy(pred, truth),
                "escalated_to_reasoning": escalated,
                "escalated_fraction": escalated / len(rows),
            },
        },
        "threshold_curve": jb.coverage_curve([c for c, _ in confident], [ok for _, ok in confident], THRESHOLDS),
        "threshold_curve_caveat": (
            "in-sample: this sweep and the calibration table are computed on the same items used "
            "to report accuracy, so any operating point read off it is optimistic. Use the "
            "heldout_gate blocks for an out-of-sample number."),
        "heldout_gate_target_95": heldout,
        "heldout_gate_target_90": heldout_90,
        "provenance": jb.provenance(
            False,
            "inputs are next-action decisions parsed from Rajeev-SG/web-automation-microbench "
            "trace artifacts, which is a private repository; the decision states contain page "
            "text from client sites, so they are not committed",
            "python3 tests/06_browser_routing/extract.py (needs a local clone of the microbench repo)"
            "decision: inputs stay local because the traces hold client-site page text; the "
            "extractor is committed so the method can be repeated on any local trace archive",),
        "calibration": jb.calibration_table([c for c, _ in confident], [ok for _, ok in confident]),
        "per_label_recall": {
            label: {
                "n": sum(1 for t in truth if t == label),
                "recall": (sum(1 for t, p in zip(truth, pred) if t == label and p == label)
                           / max(1, sum(1 for t in truth if t == label))),
            }
            for label in sorted(set(truth))
        },
        "classifier": client.stats(),
    }

    glm_rows = []
    if not args.no_glm:
        sample = stratified(rows, args.glm_sample)
        glm = jb.GLM(use_cache=use_cache)
        for index in sample:
            response = glm.chat(
                GLM_PROMPT.format(state=states[index], labels="\n".join(f"- {l}" for l in LABELS)),
                system=GLM_SYSTEM, max_tokens=600, json_object=True)
            family, confidence = parse_action(response["text"])
            glm_rows.append({"index": index, "truth": truth[index], "glm": family,
                             "confidence": confidence, "prompt_tokens": response["prompt_tokens"],
                             "completion_tokens": response["completion_tokens"],
                             "cost_usd": response["cost_usd"], "elapsed_ms": response["elapsed_ms"]})
        parsed = [r for r in glm_rows if r["glm"]]
        summary["glm"] = glm.stats()
        summary["methods"]["glm_5_3_flash_sample"] = {
            "sample_size": len(glm_rows), "parsed": len(parsed),
            "top1_accuracy": accuracy([r["glm"] for r in parsed], [r["truth"] for r in parsed]),
            "mean_prompt_tokens": (sum(r["prompt_tokens"] for r in parsed) / len(parsed)) if parsed else None,
        }

    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", [
        {"trace": r["trace"], "task": r["task"], "step": r["step"], "truth": truth[i],
         "pred": pred[i], "confidence": conf[i], "correct": pred[i] == truth[i],
         "actions_so_far": r["actions_so_far"]}
        for i, r in enumerate(rows)
    ])
    report(summary)
    return 0


def report(summary):
    print("=" * 72)
    print("TEST 6 - browser next-action routing (offline replay)")
    print("=" * 72)
    print(f"real-work decisions={summary['data']['decisions_real_work']} "
          f"scored={summary['data']['decisions_scored']} "
          f"instrument excluded={summary['data']['decisions_instrument_excluded']}")
    print(f"labels: {summary['label_distribution']}")
    for name, metrics in summary["methods"].items():
        if "top1_accuracy" in metrics:
            print(f"  {name:34s} top1={fmt(metrics['top1_accuracy'])}")
    print("\nper-label recall:")
    for label, metrics in summary["per_label_recall"].items():
        print(f"  {label:10s} n={metrics['n']:4d} recall={fmt(metrics['recall'])}")
    print("\ncoverage/accuracy curve (IN-SAMPLE; see held-out below):")
    for row in summary["threshold_curve"]:
        print(f"  >={row['threshold']:.2f}  coverage={100 * row['coverage']:5.1f}%  "
              f"auto-accuracy={fmt(row['auto_accuracy'])}")
    for key in ("heldout_gate_target_95", "heldout_gate_target_90"):
        block = summary[key]
        tune, hold = block["tuning"], block["heldout"]
        print(f"\nheld-out check (target {block['target_accuracy']}, threshold "
              f"{block['threshold_chosen_on_tuning_split']} chosen on the tuning half):")
        print(f"  tuning   n={tune['n']:4d} coverage={100 * tune['coverage']:5.1f}% "
              f"accuracy={fmt(tune['auto_accuracy'])} ci95={fmt_ci(tune['auto_accuracy_ci95'])}")
        print(f"  held-out n={hold['n']:4d} coverage={100 * hold['coverage']:5.1f}% "
              f"accuracy={fmt(hold['auto_accuracy'])} ci95={fmt_ci(hold['auto_accuracy_ci95'])}")


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


def fmt_ci(interval):
    return "n/a" if not interval else f"[{interval[0]:.3f}, {interval[1]:.3f}]"


if __name__ == "__main__":
    raise SystemExit(main())