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
import sys
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
# secret scanning: a gate, not a best-effort blocklist
# --------------------------------------------------------------------------

# High-precision credential shapes. A match here means the payload is NOT sent
# to a third-party endpoint; the benchmark fails instead. These are the forms
# seen in real session logs: OAuth tokens in query strings, bearer headers,
# key=value secrets, and the well-known key prefixes.
_SCANNER_PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("google-oauth-token", re.compile(r"\bya29\.[A-Za-z0-9_\-\.]{20,}")),
    ("google-refresh-token", re.compile(r"\b1//0[A-Za-z0-9_\-]{20,}")),
    ("google-client-secret", re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{10,}")),
    ("openrouter-key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{16,}")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9._\-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("classifier-key", re.compile(r"\bclassifier_pro_[A-Za-z0-9]{8,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    # secret-bearing query parameters and headers, which is how these usually
    # appear in a captured browser or curl log
    ("oauth-query-param", re.compile(
        r"(?i)[?&](access_token|refresh_token|id_token|client_secret|api_key|apikey|auth)="
        r"(?!\[redacted)[^\s&\"']{8,}")),
    ("bearer-header", re.compile(r"(?i)\bbearer\s+(?!\[redacted)[A-Za-z0-9._\-]{16,}")),
    ("cookie-header", re.compile(r"(?i)\b(set-)?cookie:\s*[^\n]{0,80}?(session|token|auth)[^\s=;]{0,20}=(?!\[redacted)[^;\s]+")),
    ("key-value-secret", re.compile(
        r"(?i)\b(authorization|api[_-]?key|password|passwd|secret|client_secret|access_token|refresh_token)\b"
        r"\s*[:=]\s*[\"']?(?!\[redacted)[^\s\"',{]{8,}")),
]


class SecretDetected(RuntimeError):
    """Raised when a payload about to leave the machine still contains a credential."""


def scan_for_secrets(text: str) -> list[tuple[str, str]]:
    """Return [(kind, location)] for credential-shaped strings in `text`.

    The location is `offset:<n>/<len>` and never contains any part of the
    matched text: a scanner hit must not become a new way for a credential
    fragment to reach a log or a committed artefact.
    """
    findings = []
    for kind, pattern in _SCANNER_PATTERNS:
        for match in pattern.finditer(text):
            findings.append((kind, f"offset:{match.start()}/{len(match.group(0))} chars"))
    return findings


def assert_no_secrets(text: str, where: str) -> None:
    findings = scan_for_secrets(text)
    if findings:
        kinds = sorted({kind for kind, _ in findings})
        raise SecretDetected(
            f"refusing to send {where}: credential-shaped strings detected ({', '.join(kinds)}). "
            f"Redact the source sample first; this gate exists because a real OAuth token "
            f"reached a staged file once.")


# --------------------------------------------------------------------------
# redaction: applied to everything sent, before the scanner gate
# --------------------------------------------------------------------------

# High-precision credential shapes. A match here means the payload is NOT sent
# to a third-party endpoint; the benchmark fails instead.
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
    (re.compile(r"\bya29\.[A-Za-z0-9_\-\.]{20,}"), "[redacted-google-oauth-token]"),
    (re.compile(r"\b1//0[A-Za-z0-9_\-]{20,}"), "[redacted-google-refresh-token]"),
    (re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{10,}"), "[redacted-google-client-secret]"),
    (re.compile(r"(?i)([?&](?:access_token|refresh_token|id_token|client_secret|api_key|apikey|auth)=)[^\s&\"']{8,}"), r"\1[redacted]"),
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1[redacted]"),
    # Cookie headers are the common shape in recorded browser sessions, so they
    # must be redactable, not just detectable: the scanner and the redactor have
    # to cover the same ground or the gate is fail-closed on real data.
    (re.compile(r"(?i)((?:set-)?cookie:[^\n]{0,80}?(?:session|token|auth)[^\s=;]{0,20}=)[^;\s]+"), r"\1[redacted]"),
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
    MAX_INPUTS = {"fast": 100, "smart": 60}

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
        # Latency accounting keeps cold calls and cache replays apart. Only a
        # call that actually went over the wire contributes to `cold_latencies`;
        # a cache hit contributes the cold measurement recorded when that entry
        # was first written, and nothing else.
        self.cold_latencies: list[float] = []
        self.recorded_cold_ms: list[float] = []
        self.observed: list[tuple[float, int]] = []
        self.historical_observed: list[tuple[float, int]] = []
        self.warm_hits = 0
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
                self.cold_latencies.append(elapsed_ms)
                self.requests += 1
                return {"payload": payload, "cold_ms": elapsed_ms}
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
        the smart tier escalated the item to a reasoning model. A 502 on a
        large batch is handled by splitting the batch in half and retrying.
        """
        texts = [redact(t) if self.redact_inputs else t for t in texts]
        for index, text in enumerate(texts):
            assert_no_secrets(text, f"input {index} to classifier.dev")
        out: list[dict] = []
        size = self.MAX_INPUTS[self.tier]
        for start in range(0, len(texts), size):
            chunk = texts[start : start + size]
            if start:
                # classifier.dev counts classifications per minute (3000/min on
                # the free fast tier), so a full batch needs a real pause.
                time.sleep(4.0)
            out.extend(self._classify_chunk(chunk, labels, instructions, multi, max_labels))
        return out

    def _classify_chunk(self, chunk, labels, instructions, multi, max_labels) -> list[dict]:
        body = {"inputs": list(chunk), "labels": list(labels), "tier": self.tier}
        if instructions:
            body["instructions"] = instructions
        if multi:
            body["multi"] = True
        if max_labels:
            body["max_labels"] = max_labels
        key = self.cache.key(body)
        cached = self.cache.get(key) if self.use_cache else None
        if cached is not None:
            # Entries written before the sidecar split hold a bare payload, and may
            # carry the cold measurement that was stamped on it at the time.
            if isinstance(cached, dict) and "payload" in cached:
                entry = cached
            else:
                entry = {"payload": cached, "cold_ms": cached.get("_measured_batch_ms") if isinstance(cached, dict) else None}
            payload = entry["payload"]
            self.cache_hits += 1
            self.warm_hits += 1
            was_cached = True
            batch_ms = entry.get("cold_ms")
            if batch_ms:
                # a genuine cold measurement, but taken by an earlier run
                self.historical_observed.append((batch_ms, len(chunk)))
                self.recorded_cold_ms.append(batch_ms)
        else:
            try:
                fresh = self._post(body)
            except urllib.error.HTTPError as exc:
                if exc.code == 502 and len(chunk) > 20:
                    mid = len(chunk) // 2
                    print(f"classifier.dev 502 on {len(chunk)} inputs; splitting", file=sys.stderr)
                    return (self._classify_chunk(chunk[:mid], labels, instructions, multi, max_labels)
                            + self._classify_chunk(chunk[mid:], labels, instructions, multi, max_labels))
                raise
            payload = fresh["payload"]
            self.cache.put(key, fresh)
            was_cached = False
            batch_ms = fresh["cold_ms"]
            self.observed.append((batch_ms, len(chunk)))
        if len(payload["results"]) != len(chunk):
            raise RuntimeError(
                f"classifier.dev returned {len(payload['results'])} results for "
                f"{len(chunk)} inputs; results are documented as input-ordered, so "
                f"a mismatch means the alignment assumption no longer holds")
        results = []
        for result in payload["results"]:
            result = dict(result)
            result["_cached"] = was_cached
            result["_tier"] = self.tier
            result["_batch_ms"] = batch_ms
            results.append(result)
        self.classifications += len(results)
        return results

    def stats(self):
        # `observed` holds only measurements taken in this process while a
        # request was in flight. Cold measurements recorded by an earlier run are
        # reported separately: a rerun cannot present another run's timings as
        # its own, and a warm hit contributes no latency at all.
        batches = {ms: n for ms, n in self.observed}
        total_ms = sum(ms for ms, _ in self.observed)
        total_items = sum(n for _, n in self.observed)
        hist_batches = {ms: n for ms, n in self.historical_observed}
        hist_ms = sum(ms for ms, _ in self.historical_observed)
        hist_items = sum(n for _, n in self.historical_observed)
        return {
            "tier": self.tier,
            "requests": self.requests,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
            "classifications": self.classifications,
            "latency_meaning": "cold network calls made in this process only; cache hits contribute none",
            "cold_batches_measured": len(self.observed),
            "warm_cache_hits_excluded_from_latency": self.warm_hits,
            "batch_latency": latency_summary([ms for ms, _ in self.observed]),
            "ms_per_item_amortised": (total_ms / total_items) if total_items else None,
            "batch_latency_historical_from_cache": latency_summary([ms for ms, _ in self.historical_observed]),
            "ms_per_item_amortised_historical": (hist_ms / hist_items) if hist_items else None,
            "historical_meaning": "cold measurements recorded by the run that first made those calls; not this run",
            "distinct_batches": len(batches),
            "distinct_batches_historical": len(hist_batches),
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
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
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
        self.cached_cost = 0.0
        self.cached_prompt_tokens = 0
        self.cached_completion_tokens = 0
        self.cached_cold_ms: list[float] = []
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
        assert_no_secrets(messages[-1]["content"], "prompt to OpenRouter")
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
            # The elapsed_ms stored in the entry is the cold measurement from the
            # call that produced it. It is a real cold observation, not a warm
            # replay, so it is reported in a clearly separate bucket.
            self.cached_cold_ms.append(cached.get("elapsed_ms", 0.0))
            self.cached_cost += float(cached.get("cost_usd") or 0.0)
            self.cached_prompt_tokens += cached.get("prompt_tokens", 0)
            self.cached_completion_tokens += cached.get("completion_tokens", 0)
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
                # Never propagate the provider's response body: it can echo the
                # Authorization header back on an auth error.
                last_error = f"HTTP {exc.code}"
                if exc.code in (429, 502, 503) and attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                self.failures += 1
                raise RuntimeError(last_error) from None
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = f"{type(exc).__name__}"
                if attempt < retries - 1:
                    time.sleep(delay); delay *= 2; continue
                self.failures += 1
                raise RuntimeError(last_error) from None
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
            "tokens_including_cache": {
                "prompt": self.prompt_tokens + self.cached_prompt_tokens,
                "completion": self.completion_tokens + self.cached_completion_tokens,
            },
            "cost_usd_including_cache": round(self.reported_cost + self.cached_cost, 6),
            "latency": latency_summary(self.latencies),
            "latency_meaning": (
                "cold calls made in this process only. Cache hits contribute no latency, "
                "and measurements recorded by an earlier run are reported separately under "
                "latency_historical_from_cache rather than mixed into this figure."),
            "latency_historical_from_cache": latency_summary(self.cached_cold_ms),
            "latency_historical_meaning": (
                "cold measurements recorded by the run that first made those calls; not this run."),
            "cold_calls_recorded_from_cache": len(self.cached_cold_ms),
            "warm_cache_hits_excluded_from_latency": self.cache_hits,
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
    """Precision/recall/F1 for the `positive` class, plus raw counts.

    A zero precision or recall is a real result (a gate that never fires, or one
    that always fires wrongly), so F1 is computed whenever both are known, not
    whenever both are truthy.
    """
    if not y_true:
        return {"n": 0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "precision": None,
                "recall": None, "f1": None, "accuracy": None, "false_negative_rate": None}
    tp = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p == positive)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p == positive)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == positive and p != positive)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t != positive and p != positive)
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    elif precision is not None and recall is not None:
        f1 = 0.0  # both are known and both are zero
    else:
        f1 = None
    return {
        "n": len(y_true),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": (tp + tn) / len(y_true),
        "false_negative_rate": (fn / (fn + tp)) if (fn + tp) else None,
    }


def _check_alignment(confidences, correct):
    """Fail loudly on misaligned inputs instead of emitting a garbage curve.

    A silent off-by-one between a classifier result list and the truth list is
    exactly the kind of bug that would fake a headline accuracy number, so this
    is an assertion rather than a warning.
    """
    if len(confidences) != len(correct):
        raise ValueError(
            f"confidences and correct must be aligned: {len(confidences)} vs {len(correct)}")
    for index, value in enumerate(confidences):
        if value is not None and not isinstance(value, (int, float)):
            raise TypeError(f"confidence at {index} is {type(value).__name__}, expected float or None")


def coverage_curve(confidences, correct, thresholds):
    """Auto-decision coverage and accuracy when only confident answers are auto-taken.

    `correct` is a list of bools aligned with `confidences`. Items below the
    threshold are assumed escalated to the LLM, which we report separately;
    this curve answers "at what threshold does Jev stay >= X% accurate, and
    what fraction of decisions can it cover by itself?".
    """
    _check_alignment(confidences, correct)
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
    _check_alignment(confidences, correct)
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