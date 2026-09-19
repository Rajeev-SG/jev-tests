#!/usr/bin/env python3
"""Render the RESULTS.md numbers table straight from the committed artefacts.

The final frontier review found that RESULTS.md's numbers had drifted from the
regenerated summaries. This script removes that failure mode: the table between
the `BEGIN/END GENERATED TABLE` markers in RESULTS.md is produced by this file,
so anyone can re-derive it in one command.

    python3 tests/render_results.py            # print the table
    python3 tests/render_results.py --check    # fail if RESULTS.md is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import jevbench as jb  # noqa: E402

RESULTS = jb.ROOT / "RESULTS.md"
BEGIN = "<!-- BEGIN GENERATED TABLE -->"
END = "<!-- END GENERATED TABLE -->"


def load(name):
    with (jb.ROOT / "results" / name / "summary.json").open() as fh:
        return json.load(fh)


def f(value, places=2):
    return "n/a" if value is None else f"{value:.{places}f}"


def jev_latency(stats):
    """This run's cold figure if there was one, else the recorded one, labelled."""
    here = stats["batch_latency"]
    if here["n"]:
        return f"{f(stats['ms_per_item_amortised'], 1)} ms/decision (this run)"
    hist = stats.get("batch_latency_historical_from_cache", {"n": 0})
    if hist["n"]:
        return f"{f(stats.get('ms_per_item_amortised_historical'), 1)} ms/decision (recorded run)"
    return "n/a"


def llm_latency(stats):
    if not stats:
        return "n/a"
    here, hist = stats["latency"], stats.get("latency_historical_from_cache", {"n": 0})
    if here["n"]:
        return f"p50 {f(here['p50_ms'], 0)} / p95 {f(here['p95_ms'], 0)} ms (this run)"
    if hist.get("n"):
        return f"p50 {f(hist['p50_ms'], 0)} / p95 {f(hist['p95_ms'], 0)} ms (recorded run)"
    return "n/a"


def llm_spend(stats):
    return f"${f((stats or {}).get('cost_usd_including_cache'), 4)}"


def rows():
    t1 = load("01_corpus_sieve")
    t2 = load("02_tool_routing")
    t3 = load("03_context_pruning")
    t4 = load("04_pr_gate")
    t5 = load("05_retrieval_gate")
    t6 = load("06_browser_routing")
    cost1 = json.load((jb.ROOT / "results" / "01_corpus_sieve" / "cost.json").open())
    cost2 = json.load((jb.ROOT / "results" / "02_tool_routing" / "cost.json").open())

    m1 = t1["methods"]
    d1 = t1["sieve_view_drop_decision"]["classifier_dev_fast"]
    ill = cost1["illustrative_glm_cost_usd"]
    m2 = t2["methods"]
    c2 = {r["threshold"]: r for r in t2["threshold_curve_fast"]}.get(0.8, {})
    h2 = t2["heldout_gate_target_90"]
    d2 = t2["subset_representativeness"]["largest_share_deltas"][0]
    t3t = t3["context_tokens"]
    g4 = {r["threshold"]: r for r in t4["jev_gate_curve"]}[0.7]
    rule4 = t4["deterministic_rules"]
    c6 = {r["threshold"]: r for r in t6["threshold_curve"]}.get(0.7, {})
    h6 = t6["heldout_gate_target_95"]
    h6_tune, h6_hold = h6["tuning"], h6["heldout"]
    m6 = t6["methods"]

    return "\n".join([
        "| # | Use case | Decision | Verdict | Measured quality | Jev latency | LLM latency | Cost | Expensive calls avoided |",
        "|---|---|---|---|---|---|---|---|---|",
        f"| 1 | adpi corpus sieve | worth synthesis vs not | **NOT PROVEN** | "
        f"{f(m1['classifier_dev_fast']['accuracy'], 3)} accuracy vs "
        f"{f(m1['always_keep_baseline']['accuracy'], 3)} always-keep; drop precision "
        f"{f(d1['drop_precision'])}; {f(100 * m1['classifier_dev_fast']['false_negative_rate'], 1)}% false-negative; "
        f"GLM agrees with the label only {f(m1['glm_5_3_flash_sample']['accuracy'])}; "
        f"the accuracy gap vs the baseline is "
        f"{f(abs(m1['classifier_dev_fast']['accuracy'] - m1['always_keep_baseline']['accuracy']) * t1['data']['records'], 1)} records "
        f"out of {t1['data']['records']}, inside the noise | "
        f"{jev_latency(t1['classifier_dev_fast'])} | {llm_latency(t1.get('glm'))} | "
        f"Jev ${f(cost1['illustrative_direct_jev_cost_usd'], 4)} direct-API equivalent; "
        f"GLM {llm_spend(t1.get('glm'))} measured; illustrative corpus pass "
        f"${f(ill['no_sieve_full_corpus'], 3)} -> ${f(ill['with_sieve_kept_records'], 3)} | "
        f"{f(100 * d1['records_dropped_fraction'], 1)}% of records ({d1['records_dropped']} of {t1['data']['records']}) |",
        f"| 2 | coding-agent tool routing | which tool family next | **PRIOR ONLY** | "
        f"top-1 {f(m2['classifier_dev_fast']['top1_accuracy'])} / top-2 "
        f"{f(m2['classifier_dev_fast']['top2_accuracy'])} vs majority "
        f"{f(m2['majority_class_baseline']['top1_accuracy'])} / repeat-last "
        f"{f(m2['repeat_last_action_baseline']['top1_accuracy'])} / GLM "
        f"{f(m2['glm_5_3_flash_sample']['top1_accuracy'])} over {t2['data']['decisions']} decisions "
        f"(time-ordered subset, not a random sample: largest composition drift "
        f"{d2['axis']} {100 * d2['delta']:+.1f} points) | "
        f"{jev_latency(t2['classifier_dev_fast'])} | {llm_latency(t2.get('glm'))} | "
        f"Jev ${f(cost2['illustrative_direct_jev_cost_usd'], 4)} direct-API equivalent; GLM {llm_spend(t2.get('glm'))}; "
        f"${f(cost2['illustrative_glm_cost_usd']['per_1k_decisions'], 3)} per 1k decisions illustrative | "
        f"none at 98% accuracy; gate picked on a tuning half gives held-out "
        f"{f(100 * h2['heldout']['coverage'], 1)}% coverage at {f(h2['heldout']['auto_accuracy'])} "
        f"(in-sample {f(100 * c2.get('coverage', 0), 1)}% at {f(c2.get('auto_accuracy'))}) |",
        f"| 3 | context pruning | KEEP / TRUNCATE / DROP per history unit | **NOT PROVEN** | "
        f"{f(100 * t3['removed_fraction_B'], 1)}% of context removed at a 0.7 gate; "
        f"{t3t['kept']} kept / {t3t['truncated']} truncated / {t3t['dropped']} dropped over {t3t['chunks']} chunks "
        f"in {t3['data']['sessions']} sessions | {jev_latency(t3['classifier_dev_fast'])} | "
        f"{llm_latency(t3.get('glm'))} | Jev $0.00 measured; GLM {llm_spend(t3.get('glm'))} recorded | 0% |",
        f"| 4 | PR review gate | skip the reviewer? | **NOT PROVEN for skipping** | "
        f"{f(100 * g4['reviews_avoided_fraction'], 1)}% of reviews avoided, recall "
        f"{f(g4['recall_of_needed_review'])}, {f(100 * g4['false_skips_share'], 0)}% of skips wrong; "
        f"rules avoid {f(100 * rule4['reviews_avoided'] / t4['data']['prs'], 1)}% at "
        f"{f(rule4['recall_of_needed_review'])} recall over {t4['data']['prs']} PRs | "
        f"{jev_latency(t4['classifier_dev_fast'])} | {llm_latency(t4.get('glm'))} | "
        f"Jev $0.00 measured; GLM {llm_spend(t4.get('glm'))}; illustrative full review of all "
        f"{t4['data']['prs']} PRs ${f(t4['glm_cost']['illustrative_full_review_cost_usd'], 4)} | "
        f"{f(100 * g4['reviews_avoided_fraction'], 1)}%, at a {f(g4['false_skips_share'], 2)} false-skip rate |",
        f"| 5 | retrieval -> relevance gate | keep chunk or not | **NOT PROVEN — BLOCKED** | "
        f"{t5['coverage_diagnostics']['pools_with_ground_truth']} of "
        f"{t5['coverage_diagnostics']['tasks_matched']} retrieval pools contained any file the session used; "
        f"pool sizes {t5['coverage_diagnostics']['pool_sizes']} | n/a | n/a | $0.00, no calls needed "
        f"to establish the blocker | unmeasurable |",
        f"| 6 | browser next-action | inspect / click / type / ... | **USE AS GATE** | "
        f"raw {f(m6['classifier_dev_smart']['top1_accuracy'])} vs majority "
        f"{f(m6['majority_baseline']['top1_accuracy'])} and GLM {f(m6['glm_5_3_flash_sample']['top1_accuracy'])}; "
        f"threshold {h6['threshold_chosen_on_tuning_split']} picked on a tuning half: "
        f"held-out {f(100 * h6_hold['coverage'], 1)}% coverage at {f(h6_hold['auto_accuracy'], 3)} accuracy "
        f"(ci95 {h6_hold['auto_accuracy_ci95'][0]:.2f}-{h6_hold['auto_accuracy_ci95'][1]:.2f}); "
        f"in-sample was {f(100 * c6.get('coverage', 0), 1)}% at {f(c6.get('auto_accuracy'), 3)}, "
        f"over {t6['data']['decisions_scored']} scored decisions | {jev_latency(t6['classifier'])} | "
        f"{llm_latency(t6.get('glm'))} | Jev $0.00; GLM {llm_spend(t6.get('glm'))} | "
        f"{f(100 * c6.get('coverage', 0), 1)}% of planner turns on the confident subset |",
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    table = rows()
    if not args.check:
        print(table)
        return 0
    text = RESULTS.read_text()
    start, end = text.find(BEGIN), text.find(END)
    if start == -1 or end == -1:
        print("RESULTS.md has no generated-table markers", file=sys.stderr)
        return 2
    current = text[start + len(BEGIN):end].strip()
    if current != table.strip():
        print("RESULTS.md table is stale; regenerate with: python3 tests/render_results.py",
              file=sys.stderr)
        return 3
    print("RESULTS.md table matches the committed artefacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())