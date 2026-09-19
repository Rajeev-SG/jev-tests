#!/usr/bin/env python3
"""Test 1 - corpus relevance sieve over the real adpi record set.

Compares, on the same 837 real records:
  1. deterministic heuristic baseline (nav-word rule)
  2. classifier.dev fast  (== Jev alone)
  3. classifier.dev fast + smart escalation on the items fast was unsure about
  4. GLM-5.3-Flash on a stratified sample (LLM baseline and ground-truth sanity check)

Ground truth is the pipeline's own outcome fields, held out of every classifier
input (see build_dataset.py).

    python3 tests/01_corpus_sieve/run.py --glm-sample 150
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import jevbench as jb  # noqa: E402

RECORDS = jb.ROOT / "data" / "adpi" / "records.jsonl"
OUT = jb.ROOT / "results" / "01_corpus_sieve"

LABELS = ["worth synthesis", "not worth synthesis"]
INSTRUCTIONS = (
    "You are sieving ad-platform capability records before an expensive "
    "synthesis step. Decide whether the record describes a substantive, "
    "actionable capability (a real product, policy, API, measurement, "
    "targeting, bidding or billing feature) or something a pipeline cannot "
    "act on (navigation, boilerplate, a purely informational menu item)."
)
THRESHOLDS = [round(0.05 * i, 2) for i in range(1, 20)]
GLM_SYSTEM = (
    "You triage ad-platform capability records for a research pipeline. "
    "Answer with JSON only."
)
GLM_PROMPT = """Decide whether this record is worth carrying into an expensive \
synthesis step, or whether it is something the pipeline cannot act on \
(navigation, boilerplate, a purely informational menu item, or a capability \
whose existence the source does not actually establish).

{text}

Reply with exactly this JSON shape and nothing else:
{{"worth": true, "confidence": 0.0, "reason": "short"}}"""


def load_records() -> list[dict]:
    with RECORDS.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def stratified_glm_sample(records: list[dict], size: int) -> list[int]:
    """All low-value records plus a random sample of the worth-processing ones."""
    positives = [i for i, r in enumerate(records) if r["label"] == 1]
    negatives = [i for i, r in enumerate(records) if r["label"] == 0]
    keep_negatives = negatives[:size]
    remaining = max(0, size - len(keep_negatives))
    step = max(1, len(positives) // remaining) if remaining else len(positives)
    sampled_positives = positives[::step][:remaining]
    return sorted(keep_negatives + sampled_positives)


def parse_glm(text: str):
    """Return (worth: bool, confidence: float) or (None, None) on parse failure."""
    payload = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text or "", re.S)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return None, None
    worth = payload.get("worth")
    if isinstance(worth, str):
        worth = worth.strip().lower() in ("true", "yes", "1")
    if not isinstance(worth, bool):
        return None, None
    confidence = payload.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = None
    return worth, confidence


def drop_view(truth, pred):
    """How the sieve behaves operationally: what it removes, and at what cost."""
    dropped = [i for i, p in enumerate(pred) if p == 0]
    true_lows = [i for i, t in enumerate(truth) if t == 0]
    true_worth = [i for i, t in enumerate(truth) if t == 1]
    good = [i for i in dropped if truth[i] == 0]
    bad = [i for i in dropped if truth[i] == 1]
    return {
        "records_dropped": len(dropped),
        "records_dropped_fraction": len(dropped) / len(truth),
        "drop_precision": (len(good) / len(dropped)) if dropped else None,
        "drop_recall_of_low_value": (len(good) / len(true_lows)) if true_lows else None,
        "worth_records_lost": len(bad),
        "worth_recall": 1 - (len(bad) / len(true_worth)) if true_worth else None,
    }


def decide_from_result(result: dict) -> int:
    return 1 if result["label"] == LABELS[0] else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glm-sample", type=int, default=150)
    parser.add_argument("--smarten", type=float, default=0.7,
                        help="fast answers below this confidence are re-asked on the smart tier")
    parser.add_argument("--no-glm", action="store_true")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached responses and re-measure cold latency (costs real calls)")
    args = parser.parse_args()
    use_cache = not args.refresh

    records = load_records()
    texts = [r["text"] for r in records]
    truth = [r["label"] for r in records]

    # 1. deterministic heuristic
    heuristic = [r["heuristic"] for r in records]

    # 2. classifier.dev fast (Jev alone)
    fast_client = jb.ClassifierDev(tier="fast", use_cache=use_cache)
    fast = fast_client.classify(texts, LABELS, instructions=INSTRUCTIONS)
    fast_pred = [decide_from_result(r) for r in fast]
    fast_conf = [r["confidence"] for r in fast]

    # 3. smart escalation on the items fast was unsure about only
    unsure = [i for i, c in enumerate(fast_conf) if c is None or c < args.smarten]
    smart_client = jb.ClassifierDev(tier="smart", use_cache=use_cache)
    escalated = smart_client.classify([texts[i] for i in unsure], LABELS,
                                      instructions=INSTRUCTIONS)
    gated_pred = list(fast_pred)
    for index, result in zip(unsure, escalated):
        gated_pred[index] = decide_from_result(result)

    # 4. GLM baseline on a stratified sample
    glm_stats = None
    glm_rows = []
    if not args.no_glm:
        sample = stratified_glm_sample(records, args.glm_sample)
        glm = jb.GLM(use_cache=use_cache)
        for index in sample:
            try:
                response = glm.chat(
                    GLM_PROMPT.format(text=texts[index]),
                    system=GLM_SYSTEM, max_tokens=500, json_object=True,
                )
            except RuntimeError as exc:
                print(f"GLM call failed at record {index}: {exc}", file=sys.stderr)
                break
            worth, confidence = parse_glm(response["text"])
            glm_rows.append({
                "index": index,
                "id": records[index]["id"],
                "truth": truth[index],
                "glm_worth": None if worth is None else int(worth),
                "glm_confidence": confidence,
                "prompt_tokens": response["prompt_tokens"],
                "completion_tokens": response["completion_tokens"],
                "cost_usd": response["cost_usd"],
                "elapsed_ms": response["elapsed_ms"],
                "cached": response is not None,
            })
        glm_stats = glm.stats()

    # ---------------------------------------------------------------- metrics
    summary = {
        "test": "01_corpus_sieve",
        "data": {
            "source": "Rajeev-SG/adpi-data dataset.json (public export of ad-platform-intelligence)",
            "records": len(records),
            "worth_processing": sum(truth),
            "low_value": len(truth) - sum(truth),
            "fields_held_out_from_classifiers": ["maturity", "control_mode"],
        },
        "methods": {
            "always_keep_baseline": jb.binary_metrics(truth, [1] * len(truth)),
            "deterministic_heuristic": jb.binary_metrics(truth, heuristic),
            "classifier_dev_fast": jb.binary_metrics(truth, fast_pred),
            "classifier_dev_fast_smart_escalation": jb.binary_metrics(truth, gated_pred),
        },
        "sieve_view_drop_decision": {
            "always_keep_baseline": drop_view(truth, [1] * len(truth)),
            "deterministic_heuristic": drop_view(truth, heuristic),
            "classifier_dev_fast": drop_view(truth, fast_pred),
            "classifier_dev_fast_smart_escalation": drop_view(truth, gated_pred),
        },
        "classifier_dev_fast": fast_client.stats(),
        "classifier_dev_smart_escalation": {
            "unsure_count": len(unsure),
            "unsure_fraction": len(unsure) / len(records),
            "threshold": args.smarten,
            **smart_client.stats(),
        },
    }
    if glm_stats:
        sample_truth = [r["truth"] for r in glm_rows if r["glm_worth"] is not None]
        sample_pred = [r["glm_worth"] for r in glm_rows if r["glm_worth"] is not None]
        summary["methods"]["glm_5_3_flash_sample"] = jb.binary_metrics(sample_truth, sample_pred)
        summary["glm"] = glm_stats
        summary["glm_adjudication"] = {
            "sample_size": len(glm_rows),
            "parsed": sum(1 for r in glm_rows if r["glm_worth"] is not None),
            "agree_with_pipeline_label": (
                sum(1 for r in glm_rows if r["glm_worth"] is not None and r["glm_worth"] == r["truth"])
                / max(1, sum(1 for r in glm_rows if r["glm_worth"] is not None))
            ),
            "jev_agrees_with_glm": (
                sum(1 for r in glm_rows if r["glm_worth"] is not None
                    and fast_pred[r["index"]] == r["glm_worth"])
                / max(1, sum(1 for r in glm_rows if r["glm_worth"] is not None))
            ),
            "glm_says_worth_count": sum(1 for r in glm_rows if r["glm_worth"] == 1),
            "parse_failures": sum(1 for r in glm_rows if r["glm_worth"] is None),
        }

    confident = [(c, t == p) for c, t, p in zip(fast_conf, truth, fast_pred) if c is not None]
    summary["threshold_curve_fast"] = jb.coverage_curve(
        [c for c, _ in confident], [ok for _, ok in confident], THRESHOLDS)
    summary["calibration_fast"] = jb.calibration_table(
        [c for c, _ in confident], [ok for _, ok in confident])

    volume = cost_summary(records, texts, fast_pred, glm_stats)

    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", raw_rows(records, fast, fast_pred, gated_pred, glm_rows))
    jb.write_csv(OUT / "decisions.csv", summary["threshold_curve_fast"])
    jb.write_csv(OUT / "calibration.csv", summary["calibration_fast"])
    jb.write_json(OUT / "cost.json", volume)

    report(summary, volume)
    return 0


def raw_rows(records, fast, fast_pred, gated_pred, glm_rows):
    glm_by_index = {r["index"]: r for r in glm_rows}
    rows = []
    for index, record in enumerate(records):
        glm_row = glm_by_index.get(index)
        rows.append({
            "index": index,
            "id": record["id"],
            "vendor": record["vendor"],
            "platform": record["platform"],
            "capability_type": record["capability_type"],
            "label": record["label"],
            "label_reason": record["label_reason"],
            "heuristic_pred": record["heuristic"],
            "fast_label": fast[index]["label"],
            "fast_confidence": fast[index]["confidence"],
            "fast_pred": fast_pred[index],
            "gated_pred": gated_pred[index],
            "batch_ms": fast[index]["_batch_ms"],
            "glm_worth": glm_row["glm_worth"] if glm_row else None,
            "glm_confidence": glm_row["glm_confidence"] if glm_row else None,
            "text_tokens_approx": record["text_tokens_approx"],
        })
    return rows


def cost_summary(records, texts, fast_pred, glm_stats):
    """Measured spend plus illustrative full-corpus LLM cost."""
    total = len(records)
    kept = sum(fast_pred)
    dropped = total - kept
    input_tokens = sum(jb.approx_tokens(t) for t in texts)

    # Empirical per-record LLM cost from the GLM sample, if we have one. Token
    # totals include calls served from cache on a rerun, so the mean stays the
    # measured one instead of falling back to a guess.
    calls = 0
    if glm_stats:
        calls = glm_stats.get("requests", 0) + glm_stats.get("cache_hits", 0)
    if glm_stats and calls:
        prompt_total = glm_stats.get("tokens_including_cache", {}).get("prompt", glm_stats["prompt_tokens"])
        completion_total = glm_stats.get("tokens_including_cache", {}).get("completion", glm_stats["completion_tokens"])
        prompt_per_record = prompt_total / calls
        completion_per_record = completion_total / calls
    else:
        prompt_per_record, completion_per_record = 220.0, 25.0
    llm_full = jb.glm_equivalent_cost(prompt_per_record * total, completion_per_record * total)
    llm_sieved = jb.glm_equivalent_cost(prompt_per_record * kept, completion_per_record * kept)
    return {
        "records": total,
        "records_kept": kept,
        "records_dropped": dropped,
        "records_dropped_fraction": dropped / total,
        "classifier_input_tokens_approx": input_tokens,
        "measured_cost_usd": {
            "classifier_dev": 0.0,
            "glm_sample": glm_stats["cost_usd_from_usage"] if glm_stats else None,
        },
        "illustrative_direct_jev_cost_usd": round(jb.jev_equivalent_cost(input_tokens), 6),
        "illustrative_glm_cost_usd": {
            "no_sieve_full_corpus": round(llm_full, 6),
            "with_sieve_kept_records": round(llm_sieved, 6),
            "saved": round(llm_full - llm_sieved, 6),
            "saved_fraction_of_llm_cost": (llm_full - llm_sieved) / llm_full if llm_full else None,
        },
        "per_record_llm_assumptions": {
            "prompt_tokens": prompt_per_record,
            "completion_tokens": completion_per_record,
            "basis": "measured mean of the GLM-5.3-Flash sample in this run",
        },
        "expensive_llm_calls_avoided": dropped,
        "llm_calls_avoided_fraction": dropped / total,
    }


def report(summary, volume):
    print("=" * 72)
    print("TEST 1 - adpi corpus relevance sieve")
    print("=" * 72)
    for name, metrics in summary["methods"].items():
        print(f"{name:38s} acc={fmt(metrics['accuracy'])} "
              f"P={fmt(metrics['precision'])} R={fmt(metrics['recall'])} "
              f"F1={fmt(metrics['f1'])} FNR={fmt(metrics['false_negative_rate'])}")
    print(f"\nfast tier: {summary['classifier_dev_fast']['requests']} requests, "
          f"{summary['classifier_dev_fast']['classifications']} classifications, "
          f"p50={fmt(summary['classifier_dev_fast']['batch_latency']['p50_ms'])}ms")
    esc = summary["classifier_dev_smart_escalation"]
    print(f"smart escalation: {esc['unsure_count']} unsure "
          f"({100 * esc['unsure_fraction']:.1f}%), {esc['requests']} requests")
    if "glm" in summary:
        print(f"GLM sample: {summary['glm']['requests']} calls, "
              f"{summary['glm']['prompt_tokens']}+{summary['glm']['completion_tokens']} tokens, "
              f"${summary['glm']['cost_usd_from_usage']:.4f} billed, "
              f"p50={fmt(summary['glm']['latency']['p50_ms'])}ms; "
              f"agrees with pipeline label {fmt(summary['glm_adjudication']['agree_with_pipeline_label'])}")
    print(f"\ndropped {volume['records_dropped']}/{volume['records']} records "
          f"({100 * volume['records_dropped_fraction']:.1f}%)")
    print(f"illustrative LLM cost: full ${volume['illustrative_glm_cost_usd']['no_sieve_full_corpus']:.4f} "
          f"vs sieved ${volume['illustrative_glm_cost_usd']['with_sieve_kept_records']:.4f}")
    print("\nthreshold curve (fast confidence):")
    for row in summary["threshold_curve_fast"]:
        print(f"  >={row['threshold']:.2f}  coverage={100 * row['coverage']:5.1f}%  "
              f"auto-accuracy={fmt(row['auto_accuracy'])}")
    print("\nsieve view (drop decision):")
    for name, metrics in summary["sieve_view_drop_decision"].items():
        print(f"  {name:38s} dropped={metrics['records_dropped']:4d} "
              f"({100 * metrics['records_dropped_fraction']:5.1f}%) "
              f"drop_precision={fmt(metrics['drop_precision'])} "
              f"worth_recall={fmt(metrics['worth_recall'])}")


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())