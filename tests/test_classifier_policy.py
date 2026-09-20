"""Offline tests for the classifier.dev-backed Jev policy (issue #9).

These pin the response-shape handling that broke during the first live run:
classifier.dev returns `{label, confidence}` for a single-label request and
`{labels, scores}` when several labels are requested, and the upstream agent
reads the executed action's probability by action id.
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
        {"id": "e1", "kind": "fill", "label": "What needs to be done?", "node": 1},
        {"id": "e2", "kind": "click", "label": "Active", "node": 2},
    ],
}


def _client_returning(result):
    client = mock.Mock()
    client.classify.return_value = [result]
    return client


class ScoresShapeTest(unittest.TestCase):
    def test_multi_label_scores_shape(self):
        result = {"labels": ["CLICK e2 :: Active"], "scores": {"CLICK e2 :: Active": 0.82, "DONE": 0.1, "BLOCKED": 0.08}}
        with mock.patch.object(cp, "_client", return_value=_client_returning(result)):
            d = cp.choose(STATE, "click Active", [])
        self.assertEqual(d["choice"], "e2")
        self.assertEqual(d["operation"], "CLICK")
        self.assertAlmostEqual(d["confidence"], 0.82)
        # The agent indexes probabilities by the executed action id.
        self.assertIn("e2", d["probabilities"])

    def test_single_label_shape(self):
        result = {"label": "CLICK e2 :: Active", "confidence": 0.7}
        with mock.patch.object(cp, "_client", return_value=_client_returning(result)):
            d = cp.choose(STATE, "click Active", [])
        self.assertEqual(d["choice"], "e2")
        self.assertAlmostEqual(d["confidence"], 0.7)

    def test_done_is_honoured(self):
        result = {"labels": ["DONE"], "scores": {"CLICK e2 :: Active": 0.2, "DONE": 0.6, "BLOCKED": 0.2}}
        with mock.patch.object(cp, "_client", return_value=_client_returning(result)):
            d = cp.choose(STATE, "finish", [])
        self.assertEqual(d["choice"], "DONE")
        self.assertIn("DONE", d["probabilities"])

    def test_unknown_label_falls_back_to_blocked(self):
        result = {"labels": ["NOT AN ACTION"], "scores": {"NOT AN ACTION": 0.9}}
        with mock.patch.object(cp, "_client", return_value=_client_returning(result)):
            d = cp.choose(STATE, "x", [])
        self.assertNotIn(d["choice"], {"e1", "e2"})


if __name__ == "__main__":
    unittest.main()
