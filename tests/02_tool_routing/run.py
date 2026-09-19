#!/usr/bin/env python3
"""Test 2 - coding-agent tool routing from successful historical traces.

Data: 2,983 next-action decision points extracted from real local sessions
(claude, droid, pi, codex) by extract.py. Only the state visible before the
action is given to any classifier; the observed next action is the label.

Compares Jev (classifier.dev fast), a majority-class baseline, a repeat-last-
action baseline, Jev with smart escalation, and GLM-5.3-Flash on a stratified
sample.

    python3 tests/02_tool_routing/run.py --glm-sample 120
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

DECISIONS = jb.ROOT / "data" / "sessions" / "decisions.jsonl"
OUT = jb.ROOT / "results" / "02_tool_routing"

LABEL_TO_FAMILY = {
    "shell command": "shell",
    "search or list files": "search",
    "read a file": "read",
    "edit or write a file": "edit",
    "run tests or build": "test_build",
    "git or GitHub": "git",
    "browser or web": "browser",
    "memory or recall": "memory",
}
FAMILY_TO_LABEL = {v: k for k, v in LABEL_TO_FAMILY.items()}
LABELS = list(LABEL_TO_FAMILY)

# extract.py names families file_read/file_edit; the labels above are shorter.
FAMILY_ALIAS = {"file_read": "read", "file_edit": "edit"}
THRESHOLDS = [0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]

INSTRUCTIONS = (
    "You see the current state of a coding agent working on a task: the task "
    "title, the working directory, the actions already taken, the last "
    "assistant note and the last few tool results. Choose the single next "
    "action family the agent should take."
)
GLM_SYSTEM = "You route a coding agent's next action. Answer with JSON only."
GLM_PROMPT = """Current coding-agent state:

{state}

Choose exactly one next action family from this list:
{labels}

Reply with JSON only:
{{"family": "<one of the list>", "confidence": 0.0}}"""


def load_decisions():
    with DECISIONS.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def true_family(row) -> str:
    return FAMILY_ALIAS.get(row["family"], row["family"])


def parse_glm(text: str):
    payload = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text or "", re.S)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None, None
    if not isinstance(payload, dict):
        return None, None
    raw = str(payload.get("family", "")).strip().lower()
    for label, family in LABEL_TO_FAMILY.items():
        if raw == label or raw == family or family in raw:
            confidence = payload.get("confidence")
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                confidence = None
            return family, confidence
    return None, None


def covered_indices(rows, states, client, batch_sizes=(250, 1000, 100, 50, 15, 7)):
    """Indices already classified by the fast tier (present in the cache).

    Used because classifier.dev's free tier caps at 20,000 fast classifications
    per IP per day, and the failed 502-retry rounds spent this day's budget.
    """
    covered = set()
    for size in batch_sizes:
        for start in range(0, len(states), size):
            chunk = [jb.redact(t) for t in states[start:start + size]]
            body = {"inputs": chunk, "labels": list(LABELS), "tier": "fast",
                    "instructions": INSTRUCTIONS}
            if client.cache.get(client.cache.key(body)) is not None:
                covered.update(range(start, min(start + size, len(states))))
    return sorted(covered)


def load_cached_results(states, batch_sizes=(250, 1000, 100, 50, 15, 7)):
    """Per-item results recovered from cached classifier.dev batch responses.

    The free fast tier caps at 20,000 classifications per IP per day and this
    run's day is spent, so the partial result is scored from the batches that
    already completed rather than re-asking the service.
    """
    client = jb.ClassifierDev(tier="fast")
    out = {}
    for size in batch_sizes:
        for start in range(0, len(states), size):
            chunk = [jb.redact(t) for t in states[start:start + size]]
            body = {"inputs": chunk, "labels": list(LABELS), "tier": "fast",
                    "instructions": INSTRUCTIONS}
            payload = client.cache.get(client.cache.key(body))
            if payload is None:
                continue
            for offset, result in enumerate(payload["results"]):
                index = start + offset
                if index >= len(states) or index in out:
                    continue
                item = dict(result)
                item["_cached"] = True
                item["_tier"] = "fast"
                item["_batch_ms"] = payload.get("_measured_batch_ms", 0.0)
                out[index] = item
    return out


def stratified_sample(rows, families, size):
    by_family = collections.defaultdict(list)
    for index, row in enumerate(rows):
        by_family[true_family(row)].append(index)
    picked = []
    for family, indices in sorted(by_family.items()):
        take = max(1, round(size * len(indices) / len(rows)))
        picked.extend(indices[:take])
    return sorted(picked)[:size]


def accuracy(pred, truth):
    if not truth:
        return None
    return sum(1 for p, t in zip(pred, truth) if p == t) / len(truth)


def top2_families(result) -> list[str]:
    scores = result.get("scores") or {}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    names = []
    for label, _ in ranked[:2]:
        family = LABEL_TO_FAMILY.get(label, label)
        names.append(FAMILY_ALIAS.get(family, family))
    return names


def escalation_estimate(conf, correct, glm_rows, thresholds):
    """Estimate Jev -> GLM escalation: auto-route the confident, LLM the rest.

    GLM accuracy on the unsure bucket is measured only on the sample, so the
    combined figure is an estimate and is labelled as one.
    """
    parsed = [r for r in glm_rows if r["glm_family"] is not None]
    if not parsed:
        return []
    rows = []
    for threshold in thresholds:
        auto = [i for i, c in enumerate(conf) if c is not None and c >= threshold]
        auto_correct = sum(1 for i in auto if correct[i])
        unsure_count = len(conf) - len(auto)
        unsure_sample = [r for r in parsed
                         if (conf[r["index"]] is None) or (conf[r["index"]] < threshold)]
        glm_unsure_acc = accuracy([r["glm_family"] for r in unsure_sample],
                                  [r["truth"] for r in unsure_sample])
        combined = None
        if glm_unsure_acc is not None:
            combined = (auto_correct + unsure_count * glm_unsure_acc) / len(conf)
        rows.append({
            "threshold": threshold,
            "auto_coverage": len(auto) / len(conf),
            "auto_accuracy": auto_correct / len(auto) if auto else None,
            "glm_accuracy_on_unsure_sample": glm_unsure_acc,
            "glm_unsure_sample_n": len(unsure_sample),
            "estimated_combined_accuracy": combined,
        })
    return rows

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--glm-sample", type=int, default=120)
    parser.add_argument("--smarten", type=float, default=0.7)
    parser.add_argument("--no-glm", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--smart", action="store_true", help="also use the classifier.dev smart tier")
    parser.add_argument("--only-covered", action="store_true",
                        help="restrict to decisions already classified (daily cap workaround)")
    args = parser.parse_args()
    use_cache = not args.refresh

    all_rows = load_decisions()
    partial = False
    cached_results = None
    if args.only_covered:
        cached_results = load_cached_results([r["state"] for r in all_rows])
        keep = sorted(cached_results)
        rows = [all_rows[i] for i in keep]
        cached_results = [cached_results[i] for i in keep]
        partial = True
    else:
        rows = all_rows
    truth = [true_family(r) for r in rows]
    states = [r["state"] for r in rows]
    majority = collections.Counter(truth).most_common(1)[0][0]

    # deterministic baselines
    majority_pred = [majority] * len(rows)
    repeat_pred = []
    for row in rows:
        previous = [FAMILY_ALIAS.get(f, f) for f in row.get("previous_families") or []]
        repeat_pred.append(previous[-1] if previous else majority)

    # Jev
    fast_client = jb.ClassifierDev(tier="fast", use_cache=use_cache)
    if cached_results is not None:
        fast = cached_results
    else:
        fast = fast_client.classify(states, LABELS, instructions=INSTRUCTIONS)
    if len(fast) != len(rows):
        raise RuntimeError(f"classifier results ({len(fast)}) are not aligned with "
                           f"decisions ({len(rows)})")
    fast_pred = [FAMILY_ALIAS.get(LABEL_TO_FAMILY.get(r["label"], r["label"]),
                                 LABEL_TO_FAMILY.get(r["label"], r["label"])) for r in fast]
    fast_conf = [r["confidence"] for r in fast]
    top2_hits = [true_family(row) in top2_families(result) for row, result in zip(rows, fast)]

    # smart escalation is optional: the classifier.dev smart tier has a 2000/day
    # per-IP cap and Test 1 already spent part of it.
    smart_client = None
    gated_pred = list(fast_pred)
    if args.smart:
        unsure = [i for i, c in enumerate(fast_conf) if c is None or c < args.smarten]
        smart_client = jb.ClassifierDev(tier="smart", use_cache=use_cache)
        escalated = smart_client.classify([states[i] for i in unsure], LABELS, instructions=INSTRUCTIONS)
        if len(escalated) != len(unsure):
            raise RuntimeError(f"smart escalation returned {len(escalated)} results for "
                               f"{len(unsure)} unsure decisions")
        for index, result in zip(unsure, escalated):
            gated_pred[index] = FAMILY_ALIAS.get(
                LABEL_TO_FAMILY.get(result["label"], result["label"]),
                LABEL_TO_FAMILY.get(result["label"], result["label"]))
    unsure = [i for i, c in enumerate(fast_conf) if c is None or c < args.smarten]

    # GLM baseline on a stratified sample
    glm_rows, glm_stats = [], None
    if not args.no_glm:
        sample = stratified_sample(rows, LABEL_TO_FAMILY.values(), args.glm_sample)
        glm = jb.GLM(use_cache=use_cache)
        for index in sample:
            prompt = GLM_PROMPT.format(state=states[index], labels="\n".join(f"- {l}" for l in LABELS))
            try:
                response = glm.chat(prompt, system=GLM_SYSTEM, max_tokens=600, json_object=True)
            except RuntimeError as exc:
                print(f"GLM failed at decision {index}: {exc}", file=sys.stderr)
                break
            family, confidence = parse_glm(response["text"])
            glm_rows.append({
                "index": index,
                "truth": truth[index],
                "glm_family": family,
                "glm_confidence": confidence,
                "prompt_tokens": response["prompt_tokens"],
                "completion_tokens": response["completion_tokens"],
                "reasoning_tokens": response.get("reasoning_tokens", 0),
                "cost_usd": response["cost_usd"],
                "elapsed_ms": response["elapsed_ms"],
            })
        glm_stats = glm.stats()

    # ------------------------------------------------------------- metrics
    families = sorted(set(truth))
    confident_pairs = [(c, t == p) for c, t, p in zip(fast_conf, truth, fast_pred) if c is not None]
    summary = {
        "test": "02_tool_routing",
        "data": {
            "decisions": len(rows),
            "sources": dict(collections.Counter(r["source"] for r in rows)),
            "families": dict(collections.Counter(truth)),
            "excluded_ambiguous": 1260,
            "excluded_note_kind": "ambiguous steps",
            "excluded_note": "exec_command/bash steps whose command matched no family rule",
            "label_basis": "observed next tool in successful historical sessions",
            "partial_run": partial,
            "partial_reason": ("classifier.dev free tier hit its 20,000 fast classifications "
                               "per IP per day cap; only the decisions already classified "
                               "in this run's cache are scored") if partial else None,
            "coverage_by_source": dict(collections.Counter(r["source"] for r in rows)),
        },
        "methods": {
            "majority_class_baseline": {"top1_accuracy": accuracy(majority_pred, truth), "majority": majority},
            "repeat_last_action_baseline": {"top1_accuracy": accuracy(repeat_pred, truth)},
            "classifier_dev_fast": {
                "top1_accuracy": accuracy(fast_pred, truth),
                "top2_accuracy": sum(top2_hits) / len(top2_hits),
            },
            "classifier_dev_fast_smart_escalation": {"top1_accuracy": accuracy(gated_pred, truth)},
        },
        "per_family_recall_fast": {},
        "per_source_accuracy_fast": {},
        "classifier_dev_fast": fast_client.stats(),
        "classifier_dev_fast_smart_escalation": {
            "unsure_count": len(unsure),
            "unsure_fraction": len(unsure) / len(rows),
            "threshold": args.smarten,
            "ran": smart_client is not None,
            **(smart_client.stats() if smart_client else {}),
        },
        "threshold_curve_fast": jb.coverage_curve(
            [c for c, _ in confident_pairs], [ok for _, ok in confident_pairs], THRESHOLDS),
        "calibration_fast": jb.calibration_table(
            [c for c, _ in confident_pairs], [ok for _, ok in confident_pairs]),
    }
    for family in families:
        picked = [i for i, t in enumerate(truth) if t == family]
        summary["per_family_recall_fast"][family] = {
            "n": len(picked),
            "recall": sum(1 for i in picked if fast_pred[i] == family) / len(picked),
        }
    for source in sorted(set(r["source"] for r in rows)):
        picked = [i for i, r in enumerate(rows) if r["source"] == source]
        summary["per_source_accuracy_fast"][source] = {
            "n": len(picked),
            "top1_accuracy": sum(1 for i in picked if fast_pred[i] == truth[i]) / len(picked),
        }
    if glm_rows:
        parsed = [r for r in glm_rows if r["glm_family"] is not None]
        summary["methods"]["glm_5_3_flash_sample"] = {
            "sample_size": len(glm_rows),
            "parsed": len(parsed),
            "top1_accuracy": accuracy([r["glm_family"] for r in parsed], [r["truth"] for r in parsed]),
        }
        summary["glm"] = glm_stats
        pairs = [(r["truth"], r["glm_family"]) for r in parsed]
        summary["escalation_estimate"] = escalation_estimate(
            fast_conf, [p == t for p, t in zip(fast_pred, truth)], glm_rows, THRESHOLDS)
        summary["glm_vs_jev"] = {
            "agreement": (sum(1 for r in parsed if fast_pred[r["index"]] == r["glm_family"]) / len(parsed))
            if parsed else None,
        }
        volume = cost_summary(rows, states, glm_stats, len(parsed) or 1)
    else:
        volume = cost_summary(rows, states, None, 1)

    jb.write_json(OUT / "summary.json", summary)
    jb.write_jsonl(OUT / "raw.jsonl", raw_rows(rows, truth, fast_pred, gated_pred, fast_conf, glm_rows))
    jb.write_csv(OUT / "threshold_curve.csv", summary["threshold_curve_fast"])
    jb.write_json(OUT / "cost.json", volume)
    report(summary, volume)
    return 0


def raw_rows(rows, truth, fast_pred, gated_pred, fast_conf, glm_rows):
    glm_by_index = {r["index"]: r for r in glm_rows}
    out = []
    for index, row in enumerate(rows):
        glm_row = glm_by_index.get(index)
        out.append({
            "decision_id": row["decision_id"],
            "source": row["source"],
            "session_id": row["session_id"],
            "step": row["step"],
            "observed_tool": row["observed_tool"],
            "label_how": row["label_how"],
            "truth": truth[index],
            "fast_pred": fast_pred[index],
            "fast_confidence": fast_conf[index],
            "gated_pred": gated_pred[index],
            "correct_fast": fast_pred[index] == truth[index],
            "glm_family": glm_row["glm_family"] if glm_row else None,
            "glm_confidence": glm_row["glm_confidence"] if glm_row else None,
        })
    return out


def cost_summary(rows, states, glm_stats, parsed_calls):
    total = len(rows)
    input_tokens = sum(jb.approx_tokens(s) for s in states)
    if glm_stats and glm_stats["requests"]:
        prompt_per = glm_stats["prompt_tokens"] / glm_stats["requests"]
        completion_per = glm_stats["completion_tokens"] / glm_stats["requests"]
        latency = glm_stats["latency"]
    else:
        prompt_per, completion_per, latency = 300.0, 250.0, {"p50_ms": None, "p95_ms": None}
    per_call = jb.glm_equivalent_cost(prompt_per, completion_per)
    return {
        "decisions": total,
        "classifier_input_tokens_approx": input_tokens,
        "measured_cost_usd": {
            "classifier_dev": 0.0,
            "glm_sample": glm_stats["cost_usd_from_usage"] if glm_stats else None,
        },
        "illustrative_direct_jev_cost_usd": round(jb.jev_equivalent_cost(input_tokens), 6),
        "illustrative_glm_cost_usd": {
            "per_decision": round(per_call, 6),
            "per_1k_decisions": round(per_call * 1000, 4),
            "for_this_workload": round(per_call * total, 4),
        },
        "per_decision_llm_tokens": {"prompt": prompt_per, "completion": completion_per,
                                    "basis": "measured mean of the GLM-5.3-Flash sample"},
        "llm_latency": latency,
        "expensive_llm_calls_avoided": total,
        "note": "Jev replaces one LLM reasoning turn per decision; the LLM figures are illustrative from the measured sample mean.",
    }


def report(summary, volume):
    print("=" * 72)
    print("TEST 2 - coding-agent tool routing")
    print("=" * 72)
    print(f"decisions={summary['data']['decisions']} families={summary['data']['families']}")
    for name, metrics in summary["methods"].items():
        if "top1_accuracy" in metrics:
            extra = ""
            if "top2_accuracy" in metrics:
                extra = f" top2={metrics['top2_accuracy']:.3f}"
            print(f"{name:42s} top1={fmt(metrics['top1_accuracy'])}{extra}")
    print("\nper-family recall (fast):")
    for family, metrics in sorted(summary["per_family_recall_fast"].items(), key=lambda kv: -kv[1]["n"]):
        print(f"  {family:12s} n={metrics['n']:5d} recall={fmt(metrics['recall'])}")
    print("\nper-source accuracy (fast):")
    for source, metrics in summary["per_source_accuracy_fast"].items():
        print(f"  {source:8s} n={metrics['n']:5d} top1={fmt(metrics['top1_accuracy'])}")
    fast = summary["classifier_dev_fast"]
    print(f"\nfast tier: {fast['requests']} requests, p50 batch={fmt(fast['batch_latency']['p50_ms'])}ms "
          f"({fmt(fast['ms_per_item_amortised'])} ms/item amortised)")
    esc = summary["classifier_dev_fast_smart_escalation"]
    print(f"smart escalation: {esc['unsure_count']} unsure ({100 * esc['unsure_fraction']:.1f}%)")
    if "glm" in summary:
        print(f"GLM: {summary['glm']['requests']} calls, ${summary['glm']['cost_usd_from_usage']:.4f} billed, "
              f"p50={fmt(summary['glm']['latency']['p50_ms'])}ms")
    print("\ncoverage/accuracy curve (auto-routed only):")
    for row in summary["threshold_curve_fast"]:
        print(f"  >={row['threshold']:.2f}  coverage={100 * row['coverage']:5.1f}%  "
              f"auto-accuracy={fmt(row['auto_accuracy'])}")
    print(f"\nillustrative GLM cost per 1k decisions: ${volume['illustrative_glm_cost_usd']['per_1k_decisions']}")


def fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())