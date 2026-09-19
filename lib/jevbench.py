"""Shared harness for the jev-tests benchmark programme.

Standard library only, so every result is reproducible with the system Python.

Two clients:
  ClassifierDev  - the public classifier.dev endpoint (Jev fast tier, optional
                   smart escalation). No key needed.
  GLM            - GLM-5.3-Flash on OpenRouter, the LLM path being compared.

Both cache every response under `results/.cache/`, so re-running a benchmark is
free and returns byte-identical numbers. Cache keys are content hashes, never
text, so the cache holds no sensitive content beyond what the call already sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import statistics
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

try:  # macOS system Python often has no CA bundle wired up for urllib
    import certifi

    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    SSL_CONTEXT = ssl.create_default_context()

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "results" / ".cache"
PRICING_PATH = ROOT / "pricing" / "pricing-2026-09-19.json"


def pricing() -> dict:
    with PRICING_PATH.open() as fh:
        return json.load(fh)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def pct(values, q):
    """Simple percentile with linear interpolation; [] -> None."""
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    frac = pos - lo
    return float(values[lo] * (1 - frac) + values[hi] * frac)


def latency_summary(values):
    return {
        "n": len(values),
        "p50_ms": pct(values, 0.50),
        "p95_ms": pct(values, 0.95),
        "mean_ms": statistics.fmean(values) if values else None,
        "max_ms": max(values) if values else None,
    }


# --------------------------------------------------------------------------
# redaction: nothing leaves this machine that looks like a credential
# --------------------------------------------------------------------------

_SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[redacted-private-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9._\-]{16,}"), "[redacted-api-key]"),
    (re.compile(r"\bsk-or-v1-[A-Za-z0-9]{16,}"), "[redacted-openrouter-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[redacted-github-token]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "[redacted-slack-token]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "[redacted-jwt]"),
    (re.compile(r"(?i)\b(authorization|api[_-]?key|password|secret|token)\b\s*[:=]\s*[\"']?[^\s\"',]{8,}"), "[redacted-credential]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted-aws-key]"),
    (re.compile(r"\bclassifier_pro_[A-Za-z0-9]{8,}"), "[redacted-classifier-key]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def truncated(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


class Cache:
    """Content-addressed JSON cache. Calls are only made on a cache miss."""

    def __init__(self, namespace: str):
        self.dir = ensure_dir(CACHE_DIR / namespace)

    def key(self, payload) -> str:
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def get(self, key: str):
        path = self.dir / f"{key}.json"
        if path.exists():
            with path.open() as fh:
                return json.load(fh)
        return None

    def put(self, key: str, value) -> None:
        path = self.dir / f"{key}.json"
        tmp = path.with_suffix(".tmp")
        with tmp.open("w") as fh:
            json.dump(value, fh)
        tmp.replace(path)

    def hits(self) -> int:
        return len(list(self.dir.glob("*.json")))


# --------------------------------------------------------------------------
# classifier.dev (Jev)
# --------------------------------------------------------------------------


class ClassifierDev:
    """classifier.dev client. `tier` is 'fast' (Jev alone) or 'smart'."""

    ENDPOINT = "https://classifier.dev/v1/classify"
    MAX_INPUTS = {"fast": 1000, "smart": 200}

    def __init__(self, tier: str = "fast", redact_inputs: bool = True, max_retries: int = 5,
                 use_cache: bool = True):
        if tier not in self.MAX_INPUTS:
            raise ValueError(f"tier must be fast or smart, got {tier!r}")
        self.tier = tier
        self.redact_inputs = redact_inputs
        self.max_retries = max_retries
        self.use_cache = use_cache
        self.cache = Cache(f"classifier-{tier}")
        self.requests = 0
        self.cache_hits = 0
        self.failures = 0
        self.batch_latencies: list[float] = []
        self.observed: list[tuple[float, int]] = []
        self.classifications = 0

    def _post(self, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            self.ENDPOINT,
            data=data,
            headers={"content-type": "application/json", "user-agent": "jev-tests/1.0"},
        )
        delay = 1.0
        for attempt in range(self.max_retries):
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=120, context=SSL_CONTEXT) as resp:
                    payload = json.loads(resp.read().decode())
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.batch_latencies.append(elapsed_ms)
                self.requests += 1
                payload["_measured_batch_ms"] = elapsed_ms
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 502, 503) and attempt < self.max_retries - 1:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    sleep = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else delay
                    time.sleep(min(sleep, 30.0))
                    delay = min(delay * 2, 30.0)
                    continue
                self.failures += 1
                raise
            except (urllib.error.URLError, TimeoutError):
                if attempt < self.max_retries - 1:
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                self.failures += 1
                raise
        raise RuntimeError("unreachable")

    def classify(self, texts, labels, instructions=None, multi=False, max_labels=None):
        """Return one result dict per input, in input order.

        Each result gains `_cached` and `_batch_ms`. `confidence` is None when
        the smart tier escalated the item to a reasoning model.
        """
        texts = [redact(t) if self.redact_inputs else t for t in texts]
        out: list[dict | None] = [None] * len(texts)
        size = self.MAX_INPUTS[self.tier]
        for start in range(0, len(texts), size):
            chunk = texts[start : start + size]
            body = {"inputs": chunk, "labels": list(labels), "tier": self.tier}
            if instructions:
                body["instructions"] = instructions
            if multi:
                body["multi"] = True
            if max_labels:
                body["max_labels"] = max_labels
            key = self.cache.key(body)
            cached = self.cache.get(key) if self.use_cache else None
            if cached is not None:
                self.cache_hits += 1
                payload = cached
                was_cached = True
            else:
                payload = self._post(body)
                self.cache.put(key, payload)
                was_cached = False
            batch_ms = payload.get("_measured_batch_ms", 0.0)
            self.observed.append((batch_ms, len(chunk)))
            for offset, result in enumerate(payload["results"]):
                result = dict(result)
                result["_cached"] = was_cached
                result["_tier"] = self.tier
                result["_batch_ms"] = batch_ms
                out[start + offset] = result
            self.classifications += len(payload["results"])
        return out

    def stats(self):
        batches = {ms: n for ms, n in self.observed}  # dedupe identical cached batches
        total_ms = sum(ms for ms, _ in self.observed)
        total_items = sum(n for _, n in self.observed)
        return {
            "tier": self.tier,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "classifications": self.classifications,
            "batch_latency": latency_summary([ms for ms, _ in self.observed]),
            "ms_per_item_amortised": (total_ms / total_items) if total_items else None,
            "distinct_batches": len(batches),
            "cache_entries": self.cache.hits(),
        }

    def cost_usd(self, smart_escalated=0):
        price = pricing()["classifier_dev"]
        return smart_escalated * price["smart_escalation_per_item_usd"]


# --------------------------------------------------------------------------
# GLM-5.3-Flash via OpenRouter
# --------------------------------------------------------------------------


def openrouter_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "openrouter", "-w"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            "No OpenRouter key: set OPENROUTER_API_KEY or store one in the "
            "login keychain under service 'openrouter'."
        ) from exc


class GLM:
    """GLM-5.3-Flash on OpenRouter with token and cost accounting."""

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, model: str = "z-ai/glm-5.3-flash", redact_inputs: bool = True,
                 use_cache: bool = True):
        self.model = model
        self.redact_inputs = redact_inputs
        self.use_cache = use_cache
        self.cache = Cache(f"glm-{model.replace('/', '_')}")
        self.requests = 0
        self.cache_hits = 0
        self.failures = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reported_cost = 0.0
        self.latencies: list[float] = []
        self.seen_latencies: list[float] = []
        self._key = None

    def _api_key(self):
        if self._key is None:
            self._key = openrouter_key()
        return self._key

    def chat(self, prompt, system=None, max_tokens=600, temperature=0.0,
             json_object=False, retries=4):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": redact(prompt) if self.redact_inputs else prompt})
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if json_object:
            body["response_format"] = {"type": "json_object"}
        key = self.cache.key(body)
        cached = self.cache.get(key) if self.use_cache else None
        if cached is not None and not (not cached.get("text") and cached.get("finish_reason") == "length"):
            self.cache_hits += 1
            self.seen_latencies.append(cached.get("elapsed_ms", 0.0))
            return cached
        req = urllib.request.Request(
            self.ENDPOINT,
            data=json.dumps(body).encode(),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key()}",
                "x-title": "jev-tests",
            },
        )
        delay, last_error = 1.0, None
        for attempt in range(retries):
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=180, context=SSL_CONTEXT) as resp:
                    payload = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {exc.read()[:300]!r}"
                if exc.code in (429, 502, 503) and attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                self.failures += 1
                raise RuntimeError(last_error) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = repr(exc)
                if attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                self.failures += 1
                raise RuntimeError(last_error) from exc
        else:
            raise RuntimeError(last_error or "GLM call failed")
        elapsed_ms = (time.perf_counter() - started) * 1000
        usage = payload.get("usage") or {}
        choice = payload["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        if not text and choice.get("finish_reason") == "length" and max_tokens < 4000:
            # GLM-5.3-Flash is a reasoning model: a small max_tokens can be spent
            # entirely on hidden reasoning, leaving empty content. Retry bigger,
            # then store the usable answer under the original key too.
            self.failures += 1
            retried = self.chat(prompt, system=system, max_tokens=max_tokens * 4,
                                temperature=temperature, json_object=json_object,
                                retries=retries)
            if retried.get("text"):
                self.cache.put(key, retried)
            return retried
        self.latencies.append(elapsed_ms)
        self.seen_latencies.append(elapsed_ms)
        self.requests += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.reported_cost += float(usage.get("cost") or 0.0)
        result = {
            "text": text,
            "model": payload.get("model", self.model),
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0),
            "cost_usd": float(usage.get("cost") or 0.0),
            "elapsed_ms": elapsed_ms,
            "finish_reason": choice.get("finish_reason"),
        }
        self.cache.put(key, result)
        return result

    def stats(self):
        price = pricing()["llm"]["glm_5_3_flash"]
        computed = (
            self.prompt_tokens / 1e6 * price["input_per_mtok_usd"]
            + self.completion_tokens / 1e6 * price["output_per_mtok_usd"]
        )
        return {
            "model": self.model,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd_from_usage": round(self.reported_cost, 6),
            "cost_usd_from_snapshot": round(computed, 6),
            "latency": latency_summary(self.seen_latencies),
            "latency_cold_only": latency_summary(self.latencies),
        }


def glm_equivalent_cost(prompt_tokens: int, completion_tokens: int) -> float:
    price = pricing()["llm"]["glm_5_3_flash"]
    return (
        prompt_tokens / 1e6 * price["input_per_mtok_usd"]
        + completion_tokens / 1e6 * price["output_per_mtok_usd"]
    )


def jev_equivalent_cost(input_tokens: int) -> float:
    """Direct-Jev illustrative cost: $0.042/1M input tokens, output free."""
    return input_tokens / 1e6 * pricing()["jev"]["input_per_mtok_usd"]


def approx_tokens(text: str) -> int:
    """Cheap token estimate used for illustrative cost only (chars / 4)."""
    return max(1, round(len(text) / 4))


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def binary_metrics(y_true, y_pred, positive=1):
    """Precision/recall/F1 for the `positive` class, plus raw counts."""
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p == positive)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p == positive)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p != positive)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p != positive)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None
    accuracy = (tp + tn) / len(y_true) if y_true else None
    return {
        "n": len(y_true),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "false_negative_rate": (fn / (fn + tp)) if (fn + tp) else None,
    }


def coverage_curve(confidences, correct, thresholds):
    """Auto-decision coverage and accuracy when only confident answers are auto-taken.

    `correct` is a list of bools aligned with `confidences`. Items below the
    threshold are assumed escalated to the LLM, which we report separately;
    this curve answers "at what threshold does Jev stay >= X% accurate, and
    what fraction of decisions can it cover by itself?".
    """
    rows = []
    total = len(confidences)
    for threshold in thresholds:
        picked = [i for i, c in enumerate(confidences) if c is not None and c >= threshold]
        if not picked:
            rows.append({"threshold": threshold, "coverage": 0.0,
                         "auto_n": 0, "auto_accuracy": None})
            continue
        hits = sum(1 for i in picked if correct[i])
        rows.append({
            "threshold": threshold,
            "coverage": len(picked) / total if total else 0.0,
            "auto_n": len(picked),
            "auto_accuracy": hits / len(picked),
        })
    return rows


def calibration_table(confidences, correct, buckets=((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))):
    rows = []
    for low, high in buckets:
        picked = [i for i, c in enumerate(confidences) if c is not None and low <= c < high]
        rows.append({
            "confidence_bucket": f"[{low}, {high})",
            "n": len(picked),
            "accuracy": (sum(1 for i in picked if correct[i]) / len(picked)) if picked else None,
        })
    return rows


def accuracy_by_confidence(confidences, correct, threshold=None):
    """Plain accuracy, optionally restricted to the confident subset."""
    picked = [i for i, c in enumerate(confidences) if threshold is None or (c is not None and c >= threshold)]
    if not picked:
        return None
    return sum(1 for i in picked if correct[i]) / len(picked)


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------


def test_dir(name: str) -> Path:
    return ensure_dir(ROOT / "results" / name)


def write_jsonl(path: Path, rows) -> None:
    ensure_dir(path.parent)
    with path.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, payload) -> None:
    ensure_dir(path.parent)
    with path.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def write_csv(path: Path, rows, columns=None) -> None:
    ensure_dir(path.parent)
    rows = list(rows)
    if not rows:
        path.write_text("")
        return
    columns = columns or list(rows[0].keys())
    with path.open("w") as fh:
        fh.write(",".join(columns) + "\n")
        for row in rows:
            cells = []
            for col in columns:
                value = row.get(col)
                text = "" if value is None else str(value)
                if any(ch in text for ch in ',"\n'):
                    text = '"' + text.replace('"', '""') + '"'
                cells.append(text)
            fh.write(",".join(cells) + "\n")