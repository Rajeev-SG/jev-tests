"""Tests for the faithful two-question Jev policy over classifier.dev (issue #9).

These pin the contract that matters: the policy asks the SAME two questions
upstream asks (operation, then target), maps the target back to a code-owned
observed node id, and never lets the model invent a selector.

An earlier test file pinned a one-question design that collapsed both decisions
into a single menu. That design was the bug - it left Jev unable to choose an
element - so these tests replace it rather than preserve it.
"""
from __future__ import annotations

import unittest
from unittest import mock

from jev_tests import classifier_policy as cp


STATE = {
    "url": "https://example.test/",
    "title": "Example",
    "text": "a page",
    "actions": [
        {"id": "e1", "kind": "fill", "label": "Search", "node": 1, "role": "textbox", "value": ""},
        {"id": "e2", "kind": "click", "label": "Go", "node": 2, "role": "button"},
        {"id": "e3", "kind": "click", "label": "Cancel", "node": 3, "role": "button"},
    ],
}


class TwoQuestionTest(unittest.TestCase):
    def _choose(self, op_scores: dict, target_scores: dict | None):
        """Drive `choose` with scripted per-question scores."""
        calls = {"n": 0}

        class FakeClient:
            def classify(self, inputs, labels, instructions=None, max_labels=None):
                calls["n"] += 1
                scores = op_scores if calls["n"] == 1 else (target_scores or {})
                return [{"labels": [], "scores": {k: scores.get(k, 0.01) for k in labels},
                         "ms": 1, "model": "jev-1.13.0"}]

        with mock.patch.object(cp, "_client", return_value=FakeClient()):
            return cp.choose(STATE, "click Go", []), calls["n"]

    def test_operation_then_target_are_two_questions(self):
        # Two CLICK candidates, so the target head is a real choice.
        d, n = self._choose({"CLICK": 0.9, "TYPE_TEXT": 0.2, "DONE": 0.1}, {"2": 0.9, "3": 0.1})
        self.assertEqual(n, 2, "the target question must actually be asked")
        self.assertEqual(d["operation"], "CLICK")
        self.assertEqual(d["target"], "2")
        # The executed choice is upstream's observed node id, not a model string.
        self.assertEqual(d["choice"], "e2")

    def test_no_target_question_when_operation_has_no_targets(self):
        d, n = self._choose({"DONE": 0.9, "CLICK": 0.2}, None)
        self.assertEqual(n, 1)
        self.assertEqual(d["choice"], "DONE")

    def test_model_never_returns_a_selector(self):
        d, _ = self._choose({"CLICK": 0.9, "TYPE_TEXT": 0.2}, {"2": 0.9, "3": 0.1})
        self.assertIn(d["choice"], {"e1", "e2", "e3"})
        self.assertNotIn("data-jev", d["choice"])
        self.assertNotIn(">", d["choice"])

    def test_single_candidate_target_skips_the_network(self):
        # classifier.dev requires >=2 labels, so a lone candidate is answered locally.
        one = {"url": "u", "title": "t", "text": "", "actions": [
            {"id": "e1", "kind": "click", "label": "Only", "node": 1, "role": "button"}]}
        calls = {"n": 0}

        class FakeClient:
            def classify(self, inputs, labels, instructions=None, max_labels=None):
                calls["n"] += 1
                return [{"labels": [], "scores": {k: 1.0 for k in labels}, "model": "jev-1.13.0"}]

        with mock.patch.object(cp, "_client", return_value=FakeClient()):
            d = cp.choose(one, "click", [])
        self.assertEqual(d["choice"], "e1")


class NormaliseTest(unittest.TestCase):
    def test_scores_become_a_distribution(self):
        p = cp._normalise({"a": 0.7, "b": 0.3, "unused": 5.0}, ["a", "b"])
        self.assertAlmostEqual(sum(p.values()), 1.0)
        self.assertGreater(p["a"], p["b"])

    def test_missing_label_gets_a_floor_not_zero(self):
        p = cp._normalise({"a": 0.7}, ["a", "b"])
        self.assertGreater(p["b"], 0.0)


if __name__ == "__main__":
    unittest.main()
