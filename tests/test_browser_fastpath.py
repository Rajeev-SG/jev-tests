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

import os
import unittest
from pathlib import Path

from jev_tests.bridge import (
    BridgeBrowser,
    BrowserRelayTransport,
    LITE_MARKER,
    _decode_jsonish,
)
import jev_tests.microbench as mb
import jev_ultrafast.agent as jev_agent
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


class TestFreshnessProbe(unittest.TestCase):
    """Regression guard for the F2 efficiency finding: observe() must not need
    the full snapshot to answer a no-action freshness check."""

    def test_lite_marker_is_small_and_not_the_full_snapshot(self):
        # The lite probe must be a short identity string, not the whole READ_STATE.
        self.assertLess(len(LITE_MARKER), 400)
        self.assertNotIn("snapshot", LITE_MARKER.lower())

    def test_fresh_uses_lite_marker_when_present(self):
        b = make_browser({})
        seen = []

        def fake_evaluate(expr):
            seen.append(expr)
            return [1, "u", "t", 0, 0, 100, 100, 5] if LITE_MARKER in expr else None

        b.evaluate = fake_evaluate
        page = {"lite_marker": [1, "u", "t", 0, 0, 100, 100, 5]}
        self.assertTrue(b.fresh(page))
        self.assertTrue(any(LITE_MARKER in e for e in seen))

    def test_fresh_detects_change_via_lite_marker(self):
        b = make_browser({})
        b.evaluate = lambda expr: [2, "u", "t", 0, 0, 100, 100, 9]
        self.assertFalse(b.fresh({"lite_marker": [1, "u", "t", 0, 0, 100, 100, 5]}))

    def test_fill_is_node_guarded(self):
        # fill must go through the node guard path, not only the page marker.
        b = make_browser({})
        b.evaluate = lambda expr: None  # guard mismatch
        self.assertFalse(b.fresh({"page_key": "k", "guards": {"7": [0]}},
                                 {"id": "e", "kind": "fill", "node": 7}))


class TestBrowserPatchRestore(unittest.TestCase):
    """Regression guard for the F1 correctness finding: the module-level patch of
    jev_agent.Browser must be restored after a run/condition."""

    def test_run_one_restores_browser_class(self):
        sentinel = object()
        original_load = mb._load_benchlib
        original_browser = jev_agent.Browser

        class FakeTask:
            id = "fake"
            url = "https://example.test/"
            instruction = "noop"
            verify_js = "1"
            def check(self, parsed):
                return False

        class FakeBenchlib:
            def get_task(self, tid):
                return FakeTask()

        class BoomBrowser:
            def __init__(self, url):
                raise RuntimeError("boom")

        import tempfile
        original_bridge = mb.BridgeBrowser
        mb._load_benchlib = lambda root: FakeBenchlib()
        mb.BridgeBrowser = BoomBrowser
        jev_agent.Browser = sentinel
        cwd = os.getcwd()
        tmp = tempfile.mkdtemp()
        os.chdir(tmp)
        try:
            # Agent construction fails; run_one must NOT abort the matrix (it
            # records the error) and the finally must restore the class.
            result = mb.run_one(Path("/tmp"), "fake", "1", "playwriter", False)
            self.assertFalse(result["pass"])
            self.assertIsNotNone(result["error"])
            self.assertIs(jev_agent.Browser, sentinel)
        finally:
            os.chdir(cwd)
            mb._load_benchlib = original_load
            mb.BridgeBrowser = original_bridge
            jev_agent.Browser = original_browser


if __name__ == "__main__":
    unittest.main()


class TestRunnerArgParsing(unittest.TestCase):
    """F5 guard: the matrix runner must parse args and dispatch correctly."""

    def _run(self, argv):
        import subprocess
        return subprocess.run(
            [".venv/bin/python", "scripts/run_browser_matrix.py", *argv],
            capture_output=True, text=True, cwd=str(REPO),
        )

    def test_help_lists_task_and_backend_flags(self):
        r = self._run(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--task", r.stdout)
        self.assertIn("--backend", r.stdout)

    def test_runner_imports_and_exposes_run_one(self):
        # A smoke test: the runner must import and its per-row entrypoint must
        # exist, so CI notices an import/API break even without a live browser.
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "run_browser_matrix", REPO / "scripts" / "run_browser_matrix.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertTrue(callable(mod.main))
        self.assertTrue(callable(mod.run_one))


class TestBridgeDispatch(unittest.TestCase):
    """F5 guard: bridge dispatch must route each Jev action to the transport."""

    def test_each_action_kind_dispatches(self):
        b = make_browser({})
        b.fresh = lambda page, action=None: True
        b.evaluate = lambda expr: True  # stamp + select confirmations
        page = {"marker": "m"}
        b.act({"id": "w", "kind": "wait", "node": None}, page)
        b.act({"id": "s", "kind": "scroll", "node": None, "delta": 100}, page)
        b.act({"id": "e1", "kind": "click", "node": 1}, page)
        b.act({"id": "e2", "kind": "fill", "node": 2}, page, text="hi")
        kinds = [c[0] for c in b.transport.calls]
        self.assertIn("scroll", kinds)
        self.assertIn("click", kinds)
        self.assertIn("fill", kinds)

    def test_unknown_action_kind_raises(self):
        b = make_browser({})
        b.fresh = lambda page, action=None: True
        b.evaluate = lambda expr: True
        with self.assertRaises(ValueError):
            b.act({"id": "e9", "kind": "teleport", "node": 1}, {"marker": "m"})

    def test_fill_without_text_raises(self):
        b = make_browser({})
        b.fresh = lambda page, action=None: True
        b.evaluate = lambda expr: True
        with self.assertRaises(ValueError):
            b.act({"id": "e2", "kind": "fill", "node": 2}, {"marker": "m"}, text=None)
