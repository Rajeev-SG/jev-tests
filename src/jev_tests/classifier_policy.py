"""Upstream Jev policy, with TypeSafe's transport replaced by classifier.dev.

This is a deliberate drop-in for `jev_ultrafast.model.choose`:

* the two questions upstream asks (``operation``, then ``<operation>_target``)
  are asked here, unchanged, in **one** request — matching upstream's single
  round trip;
* the candidate sets come from upstream's own ``action_space()``, so targets are
  still code-owned observed node indexes and the model never emits a selector;
* the page/state payload is built the same way upstream builds it.

The only substitution is the endpoint: this Mac has no TypeSafe key, and
classifier.dev's fast tier *is* Jev (``jev-1.13.0``), so its scores stand in for
TypeSafe's probabilities. classifier.dev returns independent scores that do not
sum to 1, so they are normalised per question before the same validation
upstream applies.

An earlier version of this file collapsed the two questions into a single
menu of concrete actions. That was wrong: it removed the target head entirely
and was the likely cause of the repeat-action loop, because the policy could
answer "what operation" but not "which element".
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))

import jevbench  # noqa: E402
from jev_ultrafast.model import action_space  # noqa: E402
from jev_ultrafast.questions import NEXT_ACTION, TARGET  # noqa: E402

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = jevbench.ClassifierDev(
            tier=os.environ.get("JEV_TIER", "fast"),
            use_cache=os.environ.get("JEV_USE_CACHE", "1") == "1",
        )
    return _CLIENT


def _normalise(scores: dict[str, float], ids: list[str]) -> dict[str, float]:
    """Turn classifier.dev's independent scores into a distribution over `ids`."""
    picked = {i: max(float(scores.get(i, 0.0)), 1e-6) for i in ids}
    total = sum(picked.values())
    return {i: v / total for i, v in picked.items()}


def _ask(client, question_text: str, criteria: dict[str, str], instructions: str):
    """One head: returns (choice, probabilities, confidence, raw).

    classifier.dev rejects a request with fewer than two labels, so a
    single-candidate head is answered here without a network call.
    """
    ids = list(criteria)
    if len(ids) == 1:
        only = ids[0]
        return only, {only: 1.0}, 1.0, {"model": "classifier.dev/single-candidate"}
    lines = [f"- {i}: {criteria[i].get('element', criteria[i]) if isinstance(criteria[i], dict) else criteria[i]}"
             for i in ids]
    text = instructions + "\n\n" + question_text + "\n\nChoices:\n" + "\n".join(lines)
    result = client.classify([text], ids, instructions=instructions, max_labels=8)[0]
    scores = dict(result.get("scores") or {})
    if not scores:
        # single-label shape
        best = result["label"]
        return best, {best: 1.0}, float(result.get("confidence") or 0.0), result
    probs = _normalise(scores, ids)
    choice = max(probs, key=lambda k: probs[k])
    return choice, probs, probs[choice], result


def choose(state, goal, history):
    client = _client()
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.",
                      BLOCKED="No supported operation can progress.")

    page_text = (
        f"Page: {state['url']}  Title: {state['title']}\n"
        f"Visible text:\n{state['text'][:4000]}\n"
        f"Elements: {json.dumps(elements, ensure_ascii=False)[:3000]}\n"
        f"Recent actions: {json.dumps([{k: h.get(k) for k in ('action','kind','text','page_changed')} for h in history[-10:]], ensure_ascii=False)}"
    )

    started = time.perf_counter()
    op_choice, op_probs, op_conf, op_raw = _ask(
        client, f"Goal: {goal}\n\n{page_text}\n\nWhich operation advances the goal?", operations, NEXT_ACTION)

    target = None
    target_probs = {}
    target_conf = None
    if op_choice in targets:
        tgt_choice, target_probs, target_conf, _ = _ask(
            client, f"Goal: {goal}\n\n{page_text}\n\nChoose the best target for {op_choice}.", targets[op_choice],
            TARGET)
        target = tgt_choice
        choice = targets[op_choice][tgt_choice]["id"]
        probabilities = {a["id"]: target_probs[index] for index, a in targets[op_choice].items()}
    else:
        choice = controls[op_choice]["id"] if op_choice in controls else op_choice
        probabilities = {choice: op_probs[op_choice]}

    return {
        "choice": choice,
        "operation": op_choice,
        "target": target,
        "confidence": op_conf,
        "probabilities": probabilities,
        "operation_probabilities": op_probs,
        "target_probabilities": target_probs,
        "target_confidence": target_conf,
        "raw_answers": {"operation": op_raw},
        "model": f"classifier.dev/{op_raw.get('model', 'jev')}",
        "usage": {"classifications": 1 if target is None else 2},
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {},
    }


def field_text(context):
    return _openrouter_text(context)


def _openrouter_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "openai/gpt-4.1-mini")
    started = time.perf_counter()
    body = json.dumps({
        "model": model, "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": __import__("jev_ultrafast.questions", fromlist=["TEXT_VALUE"]).TEXT_VALUE},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }).encode()
    req = urllib.request.Request(base + "/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {key}", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    content = payload["choices"][0]["message"].get("content")
    if not content:
        raise RuntimeError("text helper returned empty content")
    text = json.loads(content).get("text")
    if text is None or not str(text).strip():
        raise ValueError("text helper returned no value")
    return text, {"model": payload.get("model", model),
                  "latency_ms": round((time.perf_counter() - started) * 1000)}
