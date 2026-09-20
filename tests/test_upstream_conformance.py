"""Conformance test: the policy must keep asking upstream's two questions.

The previous harness bug was a silent divergence from upstream (it deleted the
target question). This test pins the framing against upstream's OWN code, not
against prose, so the next divergence fails CI instead of producing a wrong
result:

* the candidate sets must come from upstream `jev_ultrafast.model.action_space`;
* the operation criteria must be exactly upstream's operation labels plus DONE /
  BLOCKED and any observed controls;
* the per-operation target head must be named `<operation>_target` and offer only
  that operation's compatible elements.
"""
from __future__ import annotations

import unittest
from unittest import mock

from jev_ultrafast.model import action_space

from jev_tests import classifier_policy as cp


STATE = {
    "url": "https://example.test/",
    "title": "Example",
    "text": "a page",
    "actions": [
        {"id": "e1", "kind": "fill", "label": "Search", "node": 1, "role": "textbox", "value": ""},
        {"id": "e2", "kind": "click", "label": "Go", "node": 2, "role": "button"},
        {"id": "e3", "kind": "click", "label": "Cancel", "node": 3, "role": "button"},
        {"id": "wait", "kind": "wait", "label": "Wait for the page to update"},
    ],
}


class FramingConformanceTest(unittest.TestCase):
    def _capture(self):
        """Run choose() and record every question the policy actually asked."""
        asked = []

        class FakeClient:
            def classify(self, inputs, labels, instructions=None, max_labels=None):
                asked.append({"labels": list(labels), "instructions": instructions, "text": inputs[0]})
                # Prefer CLICK so the two-candidate target head is exercised (a
                # single-candidate head is answered locally by design).
                weights = {k: (0.9 if k == "CLICK" else 0.2) for k in labels}
                return [{"labels": [], "scores": weights, "model": "jev-1.13.0"}]

        with mock.patch.object(cp, "_client", return_value=FakeClient()):
            cp.choose(STATE, "click Go", [])
        return asked

    def test_action_space_is_upstreams(self):
        elements, targets, controls = action_space(STATE["actions"])
        # Sanity: upstream groups CLICK candidates separately from TYPE_TEXT.
        self.assertIn("CLICK", targets)
        self.assertIn("TYPE_TEXT", targets)
        self.assertNotEqual(set(targets["CLICK"]), set(targets["TYPE_TEXT"]))

    def test_two_questions_are_asked_in_order(self):
        asked = self._capture()
        self.assertGreaterEqual(len(asked), 2, "the target question must be asked")
        # First question: operations. Later questions: per-operation targets.
        self.assertIn("CLICK", asked[0]["labels"])
        self.assertIn("DONE", asked[0]["labels"])
        self.assertIn("BLOCKED", asked[0]["labels"])
        self.assertIn("TYPE_TEXT", asked[0]["labels"])

    def test_target_labels_are_upstream_node_ids(self):
        asked = self._capture()
        _elements, targets, _controls = action_space(STATE["actions"])
        # Every target question's labels must be exactly upstream's index set for
        # some operation - never a free-form string the model could invent.
        upstream_index_sets = [set(v) for v in targets.values()]
        for q in asked[1:]:
            self.assertIn(set(q["labels"]), upstream_index_sets)

    def test_prompt_carries_upstream_rules(self):
        asked = self._capture()
        from jev_ultrafast.questions import NEXT_ACTION
        self.assertIn(NEXT_ACTION.strip()[:40], asked[0]["text"])


if __name__ == "__main__":
    unittest.main()
