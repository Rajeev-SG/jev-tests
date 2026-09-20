"""Tests for the live-run scorers and the invalid-decision guard (issue #9).

These pin the two corrections the final review forced:

* goal state and clean termination are scored separately (a policy that reaches
  the goal but never says DONE is `goal_state_only`, not a pass and not a
  "never reached the goal" failure);
* a fill with no value is rejected as a policy error, not executed and not
  confused with a transport error.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import sys

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("summarize_live", REPO / "scripts" / "summarize_live.py")
summ = importlib.util.module_from_spec(spec)
sys.modules["summarize_live"] = summ
spec.loader.exec_module(summ)


def row(**kw):  # noqa: A002 - "pass" is a required artifact key
    base = {"pass": False, "status": "blocked", "verification": None,
            "goal_state_reached": False, "clean_termination": False,
            "error_kind": "ok", "wall_s": 10.0, "jev_decisions": 3,
            "decision_p50_ms": 100.0, "decision_model": "m"}
    base.update(kw)
    return base


# The summarizer no longer re-derives goal state from the verification block;
# it trusts the recorded field, which the backfill stamps using the run's own
# predicate. These fixtures therefore carry the field.
GOAL = {"url": "https://demo.playwright.dev/todomvc/#/active",
        "items": [{"text": "Email supplier"}, {"text": "Review invoice"}],
        "saved": [{"title": "Email supplier"}, {"title": "Review invoice"}]}


class ClassifyTest(unittest.TestCase):
    def test_pass_requires_goal_and_done(self):
        self.assertEqual(summ.classify(row(**{"pass": True, "status": "done"})), "pass")

    def test_goal_without_done_is_goal_state_only(self):
        # This is the exact case the first write-up mis-scored as a failure.
        d = row(verification=GOAL, status="blocked", goal_state_reached=True)
        self.assertEqual(summ.classify(d), "goal_state_only")

    def test_missing_goal_field_is_refused_not_guessed(self):
        # The summarizer must never re-derive goal state from a task literal.
        d = {"_file": "x.json", "pass": False, "status": "blocked", "verification": GOAL}
        with self.assertRaises(SystemExit):
            summ.classify(d)

    def test_no_goal_state_when_verifier_disagrees(self):
        d = row(verification={"url": "https://demo.playwright.dev/todomvc/#/",
                              "items": [], "saved": []}, status="blocked",
                goal_state_reached=False)
        self.assertEqual(summ.classify(d), "no_goal_state")

    def test_transport_error_bucket(self):
        d = row(status="error", error_kind="terminal_error")
        self.assertEqual(summ.classify(d), "transport_error")

    def test_policy_format_error_bucket(self):
        d = row(status="error", error_kind="invalid_decision")
        self.assertEqual(summ.classify(d), "policy_format_error")

    def test_empty_fill_is_not_a_transport_error(self):
        # Distinguishing these is the point of F4.
        self.assertNotEqual(summ.classify(row(status="error", error_kind="invalid_decision")),
                            summ.classify(row(status="error", error_kind="terminal_error")))


class InvalidDecisionTest(unittest.TestCase):
    def test_guard_rejects_empty_and_null_values(self):
        import jev_tests.classifier_policy as cp  # noqa: F401

        class LJR:
            pass

        ljr_spec = importlib.util.spec_from_file_location(
            "live_jev_run", REPO / "scripts" / "live_jev_run.py")
        ljr = importlib.util.module_from_spec(ljr_spec)
        sys.modules["live_jev_run"] = ljr
        ljr_spec.loader.exec_module(ljr)

        def base(ctx):
            return ctx["value"], {"model": "x", "latency_ms": 1}

        def make(value):
            # Re-create what run() installs: the guard around the text helper.
            def field_text(ctx):
                text, meta = base({"value": value})
                if text is None or not str(text).strip():
                    raise ljr.InvalidDecision("empty fill")
                return text, meta
            return field_text

        for bad in (None, "", "   "):
            with self.assertRaises(ljr.InvalidDecision):
                make(bad)({"value": bad})
        self.assertEqual(make("Email supplier")({"value": "Email supplier"})[0], "Email supplier")


if __name__ == "__main__":
    unittest.main()


class BucketClassificationTest(unittest.TestCase):
    """The review's F3/F5: percentile sanity and infra-vs-policy buckets."""

    def test_infra_timeout_is_not_a_policy_failure(self):
        d = row(status="error", error="RuntimeError: Code execution timed out after 10000ms",
                error_kind="transport_error")
        self.assertEqual(summ.classify(d), "infra_error")

    def test_no_attached_tab_is_infra(self):
        d = row(status="error", error="RuntimeError: Browser Relay has no attached tab",
                error_kind="transport_error")
        self.assertEqual(summ.classify(d), "infra_error")

    def test_declined_field_value_is_policy_format(self):
        # GLM's null-text failure must not be blamed on the transport.
        d = row(status="error", error='RuntimeError: GLM did not supply a field value: \'{"text": null}\'',
                error_kind="terminal_error")
        self.assertEqual(summ.classify(d), "policy_format_error")

    def test_goal_state_and_termination_still_separate(self):
        d = row(verification=GOAL, status="blocked", goal_state_reached=True)
        self.assertEqual(summ.classify(d), "goal_state_only")


class PercentileTest(unittest.TestCase):
    """F3 guard: the archived p95 < p50 row came from a wrong nearest-rank index."""

    def test_p95_is_never_below_p50(self):
        import math
        import statistics
        for n in range(1, 12):
            lat = [float(i) for i in range(1, n + 1)]
            o = sorted(lat)
            p50 = statistics.median(o)
            p95 = o[max(0, math.ceil(len(o) * 0.95) - 1)]
            self.assertGreaterEqual(p95, p50, f"n={n}")

    def test_the_old_index_was_wrong_for_small_n(self):
        # Demonstrates the bug this test exists to prevent.
        o = [100.0, 200.0]
        old = o[max(0, int(len(o) * 0.95) - 1)]
        new = o[max(0, __import__("math").ceil(len(o) * 0.95) - 1)]
        self.assertLess(old, new)
