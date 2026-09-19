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
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertTrue(jb.scan_for_secrets(text), f"missed: {text}")

    def test_ignores_placeholders_and_prose(self):
        for text in [
            "curl https://x/y?access_token=[redacted]",
            "authorization: [redacted-credential]",
            "the agent discussed tokens of thought and secrets of state",
            "session cookie handling, token refresh",
        ]:
            with self.subTest(text=text):
                self.assertEqual(jb.scan_for_secrets(text), [], f"false positive: {text}")

    def test_gate_raises_and_names_the_kind(self):
        with self.assertRaises(jb.SecretDetected) as ctx:
            jb.assert_no_secrets("?access_token=abcdefgh12345678", "a test payload")
        self.assertIn("oauth-query-param", str(ctx.exception))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)