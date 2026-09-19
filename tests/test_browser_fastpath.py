"""Focused tests for the Jev browser fast-path bridge (issue #9).

These are offline unit tests: a fake transport stands in for Playwriter / Browser
Relay so the bridge's observed-node contract can be checked without a live browser.

Bugs found and fixed during live validation (#9) are guarded here:
  1. pyproject `requires-python` was `>=3.11` but jev-ultrafast needs `>=3.12`,
     so `uv sync` failed. Guarded by test_pyproject_supports_uv_sync.
  2. hatchling rejected the direct git dependency without
     `[tool.hatch.metadata] allow-direct-references = true`. Same test.
  3. Browser Relay prints a bare string for string eval results and JSON for
     objects; Playwriter prints a `__JEV_JSON__`-prefixed line. The decoder must
     handle all three. Guarded by TestDecodeJsonish.
  4. `browser-relay tabs` emits `id\ttitle\turl`; `_choose_tab` must parse it.
     Guarded by test_choose_tab_prefers_matching_host.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from jev_tests.bridge import BridgeBrowser, BrowserRelayTransport, _decode_jsonish
from jev_ultrafast.browser import StalePage

REPO = Path(__file__).resolve().parents[1]


class FakeTransport:
    """Minimal transport that records calls and answers `evaluate` from a script."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.closed = False

    def navigate(self, url):
        self.calls.append(("navigate", url))

    def evaluate(self, expression):
        self.calls.append(("evaluate", expression))
        for needle, value in self.responses.items():
            if needle in expression:
                return value
        return None

    def click(self, selector):
        self.calls.append(("click", selector))

    def fill(self, selector, text):
        self.calls.append(("fill", selector, text))

    def key(self, key):
        self.calls.append(("key", key))

    def scroll(self, delta):
        self.calls.append(("scroll", delta))

    def close(self):
        self.closed = True


def make_browser(responses):
    b = BridgeBrowser.__new__(BridgeBrowser)  # skip transport construction
    b.transport = FakeTransport(responses)
    b.after_input = None
    return b


class TestDecodeJsonish(unittest.TestCase):
    def test_playwriter_marker(self):
        self.assertEqual(_decode_jsonish('__JEV_JSON__{"a": 1}'), {"a": 1})

    def test_playwriter_nested_string(self):
        # Playwriter prints `__JEV_JSON__"..."` for a JSON-encoded string.
        self.assertEqual(_decode_jsonish('__JEV_JSON__"hello"'), "hello")

    def test_relay_bare_string(self):
        self.assertEqual(_decode_jsonish("document.title"), "document.title")

    def test_relay_json_object(self):
        self.assertEqual(_decode_jsonish('{"page": 1}'), {"page": 1})


class TestObservedNodeContract(unittest.TestCase):
    def test_forged_node_fails_closed(self):
        b = make_browser({"fresh": True})
        with self.assertRaises(StalePage):
            b._stamp_target({"id": "e1", "kind": "click", "node": 999999})

    def test_stale_page_fails_closed(self):
        # act() must call fresh() and refuse when it reports the page moved on.
        b = make_browser({})
        b.fresh = lambda page, action=None: False
        with self.assertRaises(StalePage):
            b.act({"id": "e1", "kind": "click", "node": 1}, {"marker": "m1"})

    def test_click_uses_generated_selector_not_model_value(self):
        # The model may never supply a selector: act() must build one from the node.
        b = make_browser({"fresh": True})
        guarded = []

        def fake_evaluate(expr):
            if "fresh" in expr:
                return True
            if "setAttribute" in expr:
                return True
            if "marker" in expr:
                return True
            return None

        b.fresh = lambda page, action=None: True
        b.evaluate = fake_evaluate
        b.mark = True
        b.act({"id": "e1", "kind": "click", "node": 7, "selector": "body > evil"},
              {"marker": "m"})
        clicks = [c for c in b.transport.calls if c[0] == "click"]
        self.assertEqual(len(clicks), 1)
        self.assertIn("data-jev-fast-target", clicks[0][1])
        self.assertNotIn("evil", clicks[0][1])


class TestPyproject(unittest.TestCase):
    def test_pyproject_supports_uv_sync(self):
        text = (REPO / "pyproject.toml").read_text()
        self.assertIn('requires-python = ">=3.12"', text)
        self.assertIn("allow-direct-references = true", text)


class TestRelayTabSelection(unittest.TestCase):
    def test_choose_tab_prefers_matching_host(self):
        t = BrowserRelayTransport.__new__(BrowserRelayTransport)
        t.bin = "browser-relay"
        t._run = lambda *a, **k: (
            "t_other\tGmail\thttps://mail.google.com/\n"
            "t_demo\tTodoMVC\thttps://demo.playwright.dev/todomvc/\n"
        )
        self.assertEqual(t._choose_tab("https://demo.playwright.dev/todomvc/"), "t_demo")

    def test_choose_tab_falls_back_to_first(self):
        t = BrowserRelayTransport.__new__(BrowserRelayTransport)
        t.bin = "browser-relay"
        t._run = lambda *a, **k: "t_only\tPage\thttps://example.com/\n"
        self.assertEqual(t._choose_tab("https://nothing-matches.test/"), "t_only")


if __name__ == "__main__":
    unittest.main()
