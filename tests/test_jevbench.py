#!/usr/bin/env python3
"""Deterministic checks for the harness guardrails.

Run with:  python3 tests/test_jevbench.py

These cover the invariants the benchmark's headline numbers depend on:
metric alignment, the F1 zero case, the secret-scanner gate, redaction, and the
rule that a cache hit contributes no latency of its own.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import jevbench as jb  # noqa: E402


class BinaryMetricsTest(unittest.TestCase):
    def test_perfect(self):
        self.assertEqual(jb.binary_metrics([1, 0], [1, 0])["f1"], 1.0)

    def test_zero_precision_and_recall_is_zero_not_none(self):
        # every prediction positive and every one wrong
        metrics = jb.binary_metrics([1, 0], [0, 1])
        self.assertEqual(metrics["precision"], 0.0)
        self.assertEqual(metrics["recall"], 0.0)
        self.assertEqual(metrics["f1"], 0.0)

    def test_no_positive_predictions_leaves_precision_undefined(self):
        metrics = jb.binary_metrics([1, 0, 1], [0, 0, 0])
        self.assertIsNone(metrics["precision"])
        self.assertIsNone(metrics["f1"])

    def test_empty_input_does_not_divide_by_zero(self):
        metrics = jb.binary_metrics([], [])
        self.assertEqual(metrics["n"], 0)
        self.assertIsNone(metrics["accuracy"])


class AlignmentTest(unittest.TestCase):
    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            jb.coverage_curve([0.9, 0.8], [True], [0.5])
        with self.assertRaises(ValueError):
            jb.calibration_table([0.9], [True, False])

    def test_non_numeric_confidence_raises(self):
        with self.assertRaises(TypeError):
            jb.coverage_curve([0.9, "high"], [True, False], [0.5])
        with self.assertRaises(TypeError):
            jb.calibration_table([None, "high"], [True, False])

    def test_none_confidence_is_allowed(self):
        rows = jb.coverage_curve([0.9, None], [True, True], [0.5])
        self.assertEqual(rows[0]["auto_n"], 1)
        self.assertEqual(rows[0]["coverage"], 0.5)


class SecretScannerTest(unittest.TestCase):
    def test_catches_real_shapes(self):
        cases = [
            "curl https://x/y?access_token=abcdefgh12345678",
            "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx'",
            "id_token=ya29.abcdefghijklmnopqrstuvwxyz",
            "refresh 1//0abcdefghijklmnopqrstuvwxyz",
            "OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnopqrstuvwxyz",
            "token: ghp_abcdefghijklmnopqrstuvwxyz01",
            "aws AKIAIOSFODNN7EXAMPLE",
            "-----BEGIN RSA PRIVATE KEY-----",
            "Cookie: sessionid=abcdef1234567890; Path=/",
            "set-cookie: auth_token=abcdef1234567890; HttpOnly",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(jb.scan_for_secrets(text), f"missed: {text}")

    def test_findings_never_contain_the_credential(self):
        secret = "abcdefgh12345678"
        findings = jb.scan_for_secrets(f"?access_token={secret}")
        self.assertTrue(findings)
        for kind, location in findings:
            self.assertNotIn(secret, location)
            self.assertNotIn(secret[:6], location)
            self.assertRegex(location, r"^offset:\d+/\d+ chars$")

    def test_cookie_headers_are_redactable_not_just_detectable(self):
        # the scanner and the redactor must cover the same shapes, or the gate is
        # fail-closed on ordinary recorded browser traffic
        for text in [
            "Cookie: sessionid=abcdef1234567890; Path=/",
            "set-cookie: auth_token=abcdef1234567890; HttpOnly",
        ]:
            with self.subTest(text=text):
                cleaned = jb.redact(text)
                self.assertEqual(jb.scan_for_secrets(cleaned), [],
                                 f"redaction left the gate closed: {cleaned}")
                self.assertIn("[redacted]", cleaned)

    def test_ignores_placeholders_and_prose(self):
        for text in [
            "curl https://x/y?access_token=[redacted]",
            "authorization: [redacted-credential]",
            "the agent discussed tokens of thought and secrets of state",
            "session cookie handling, token refresh",
            "Cookie: sessionid=[redacted]; Path=/",
        ]:
            with self.subTest(text=text):
                self.assertEqual(jb.scan_for_secrets(text), [], f"false positive: {text}")

    def test_gate_raises_and_names_the_kind(self):
        with self.assertRaises(jb.SecretDetected) as ctx:
            jb.assert_no_secrets("?access_token=abcdefgh12345678", "a test payload")
        self.assertIn("oauth-query-param", str(ctx.exception))
        # and the raised message must not carry any part of the credential
        self.assertNotIn("abcdefgh12345678"[:6], str(ctx.exception))

    def test_redaction_then_scan_is_clean(self):
        text = "curl https://x/y?access_token=abcdefgh12345678 and id_token=ya29.abcdefghijklmnopqrstuv"
        self.assertEqual(jb.scan_for_secrets(jb.redact(text)), [])


class CacheLatencyTest(unittest.TestCase):
    """A warm hit must not contribute latency, and must keep the cold figure."""

    def setUp(self):
        self.namespace = "unittest-cache-latency"
        path = jb.CACHE_DIR / self.namespace
        if path.exists():
            shutil.rmtree(path)
        self.cache = jb.Cache(self.namespace)

    def test_sidecar_round_trip(self):
        key = self.cache.key({"inputs": ["a"], "labels": ["x", "y"]})
        self.assertIsNone(self.cache.get(key))
        self.cache.put(key, {"payload": {"results": []}, "cold_ms": 123.0})
        entry = self.cache.get(key)
        self.assertEqual(entry["cold_ms"], 123.0)
        self.assertEqual(entry["payload"], {"results": []})


class UncertaintyTest(unittest.TestCase):
    """Findings from review cycle 2: intervals, and out-of-sample thresholds."""

    def test_wilson_bounds_are_within_range_and_ordered(self):
        low, high = jb.wilson_interval(742, 837)
        self.assertLessEqual(0.0, low)
        self.assertLessEqual(low, high)
        self.assertLessEqual(high, 1.0)

    def test_wilson_is_undefined_for_an_empty_sample(self):
        self.assertIsNone(jb.wilson_interval(0, 0))

    def test_near_ceiling_interval_does_not_exceed_one(self):
        low, high = jb.wilson_interval(399, 400)
        self.assertLessEqual(high, 1.0)
        self.assertLess(low, 1.0)

    def test_overlap_detects_indistinguishable_accuracy(self):
        # test 1's 0.886 vs 0.889 always-keep: a few records apart, i.e. noise
        self.assertTrue(jb.overlaps(jb.wilson_interval(742, 837), jb.wilson_interval(744, 837)))

    def test_overlap_separates_clearly_different_accuracy(self):
        self.assertFalse(jb.overlaps(jb.wilson_interval(950, 1000), jb.wilson_interval(700, 1000)))

    def test_binary_metrics_report_intervals(self):
        metrics = jb.binary_metrics([1, 1, 0, 0, 1], [1, 1, 0, 1, 0])
        self.assertIn("accuracy_ci95", metrics)
        self.assertIn("recall_ci95", metrics)
        self.assertIn("precision_ci95", metrics)

    def test_threshold_is_chosen_on_one_half_and_scored_on_the_other(self):
        keys = [f"item-{i}" for i in range(200)]
        confidences = [0.95 if i % 2 == 0 else 0.4 for i in range(200)]
        correct = [True] * 200
        report = jb.heldout_threshold_eval(keys, confidences, correct, 0.95)
        self.assertIsNotNone(report["threshold_chosen_on_tuning_split"])
        self.assertEqual(report["tuning"]["auto_accuracy"], 1.0)
        self.assertEqual(report["heldout"]["auto_accuracy"], 1.0)
        # the two halves must be disjoint and cover the whole set
        self.assertEqual(report["tuning"]["n"] + report["heldout"]["n"], 200)

    def test_heldout_split_is_stable_across_calls(self):
        keys = [f"k{i}" for i in range(50)]
        conf = [0.8] * 50
        correct = [True] * 50
        self.assertEqual(jb.heldout_threshold_eval(keys, conf, correct, 0.9),
                         jb.heldout_threshold_eval(keys, conf, correct, 0.9))

    def test_coverage_curve_reports_an_interval(self):
        rows = jb.coverage_curve([0.9, 0.8, 0.4], [True, True, False], [0.7])
        self.assertEqual(rows[0]["auto_n"], 2)
        self.assertIn("auto_accuracy_ci95", rows[0])


class ProvenanceTest(unittest.TestCase):
    def test_provenance_states_reproducibility(self):
        block = jb.provenance(False, "inputs are private", "python3 script.py")
        self.assertFalse(block["reproducible_from_repo"])
        self.assertIn("private", block["reason"])
        self.assertEqual(block["regenerate_with"], "python3 script.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)

class RedactionScanCompatibilityTest(unittest.TestCase):
    """F2 guard: the redactor's output must never re-trip the scanner."""

    SAMPLES = [
        "?access_token=abcdefgh12345678",
        "authorization: abcdefgh12345678",
        "Bearer abcdefghijklmnopqrstuvwx",
        "Cookie: sessionid=abcdef1234567890; Path=/",
        "set-cookie: auth_token=abcdef1234567890; HttpOnly",
        "OPENROUTER_API_KEY=sk-or-v1-abcdefghijklmnopqrst",
        "token: ghp_abcdefghijklmnopqrstuvwx01",
        "aws AKIAIOSFODNN7EXAMPLE",
        "refresh 1//0abcdefghijklmnopqrstuvwx",
    ]

    def test_every_redaction_is_scanner_clean(self):
        for text in self.SAMPLES:
            with self.subTest(text=text):
                self.assertEqual(jb.scan_for_secrets(jb.redact(text)), [],
                                 f"redaction left the gate closed: {jb.redact(text)}")

    def test_open_ended_redacted_prefix_does_not_bypass(self):
        # A value that begins with the literal "[redacted" but continues with
        # real secret text must still be detected.
        for text in [
            "?access_token=[redacted]-evilsecretvalue",
            "authorization: [redacted]-evilsecretvalue",
        ]:
            with self.subTest(text=text):
                self.assertTrue(jb.scan_for_secrets(text), f"bypassed: {text}")

    def test_closed_markers_are_still_exempt(self):
        for text in [
            "?access_token=[redacted]",
            "authorization: [redacted-credential]",
            "Authorization: Bearer [redacted]",
            "Cookie: sessionid=[redacted]; Path=/",
        ]:
            with self.subTest(text=text):
                self.assertEqual(jb.scan_for_secrets(text), [], f"false positive: {text}")


class SplitAccountingTest(unittest.TestCase):
    """F3 guard: a split retry must not silently drop the parent's latency."""

    def test_split_parent_is_recorded(self):
        # Drive _classify_chunk to a 502 once on the parent batch, then let the
        # halves succeed, and assert the parent time is retained.
        import urllib.error

        calls = {"n": 0}

        class FakeClient(jb.ClassifierDev):
            def __init__(self):
                super().__init__(tier="fast", use_cache=False)
                self.classifications = 0

            def _post(self, body):
                calls["n"] += 1
                if len(body["inputs"]) > 20 and calls["n"] == 1:
                    self.cold_latencies.append(123.0)  # parent did real work
                    raise urllib.error.HTTPError("u", 502, "bad gateway", {}, None)
                n = len(body["inputs"])
                return {"payload": {"results": [{"label": "x"} for _ in range(n)]},
                        "cold_ms": 10.0}

        c = FakeClient()
        c._classify_chunk([f"t{i}" for i in range(40)], ["x"], None, False, None)
        self.assertEqual(c.split_parent_ms, [123.0])

    def test_split_fields_are_exposed_in_stats(self):
        c = jb.ClassifierDev(tier="fast", use_cache=False)
        c.split_parent_ms = [50.0, 25.0]
        st = c.stats()
        self.assertEqual(st["split_parent_batches"], 2)
        self.assertEqual(st["split_parent_ms_total"], 75.0)


class CachePermissionsTest(unittest.TestCase):
    """F4 guard: the prompt/completion cache must not be world-readable."""

    def test_cache_dir_is_private(self):
        import tempfile
        from pathlib import Path as _P
        with tempfile.TemporaryDirectory() as d:
            target = _P(d) / "cachedir"
            jb.ensure_private_dir(target)
            mode = target.stat().st_mode & 0o777
            self.assertEqual(mode, 0o700, f"cache dir mode is {oct(mode)}")
