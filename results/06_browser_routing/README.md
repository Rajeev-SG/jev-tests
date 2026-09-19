# Test 6 — browser next-action routing (offline replay)

**Verdict: USE AS GATE, not as the sole router.** Jev cannot beat the trivial
"always inspect the page" baseline on raw accuracy, but on the 63% of decisions
where it is confident its next-action choice is 97.2% correct.

## What was tested

Data: the recorded traces in `Rajeev-SG/web-automation-microbench` — 192 trace
files, 946 per-step next-action decisions. 647 are real work (CHANEL GB tag
checks, Puma/Porsche script inventories, Gymshark and Allbirds add-to-cart tag
checks, rajeev.com and tldraw audits); 299 come from the TodoMVC instrument
round and are excluded, per that repo's own rule that TodoMVC is not capability
evidence.

Decision: given the goal, the actions taken so far and the page state seen
before the step, which action comes next — inspect, click, type, press, scroll,
wait, navigate or finish. The label is the first action inside the recorded
step's code.

Mode: offline replay of recorded DOM text. **No live browser runs were made.**

## Results (400-decision stratified sample of the 647 real-work decisions)

| method | top-1 accuracy |
|---|---|
| always "inspect the page" (majority baseline) | **0.865** |
| classifier.dev (Jev) | 0.680 |
| GLM-5.3-Flash (60-call sample) | 0.689 |

Neither model beats the base rate. That is the honest headline: on this corpus
the next action is nearly always "read the page" (346 of 400 sampled decisions
are inspect), so a classifier adds nothing on the full set.

The picture changes with confidence gating:

| confidence threshold | coverage | accuracy on auto-routed |
|---|---|---|
| ≥0.50 | 86.5% | 0.754 |
| ≥0.60 | 75.8% | 0.845 |
| ≥0.70 | 63.2% | 0.972 |
| ≥0.80 | 46.5% | 0.995 |
| ≥0.90 | 17.5% | 1.000 |

At ≥0.70, Jev routes 63% of decisions itself at 97.2% accuracy and hands the
rest to the planner. Items it escalated carry no confidence and were excluded
from the curve.

## Tier note

The run requested the classifier.dev **smart** tier, because the free fast
tier's 20,000-classifications-per-IP-per-day cap was already spent. In the
event the service escalated **zero** items (all 400 answers came back with a
confidence and `jev-1.13.0` as the model), so these numbers are Jev's own
answers — the tier caveat does not apply. A `--tier fast` rerun should reproduce
them once quota resets.

## Per-label behaviour

`click` recall 0.65, `inspect` 0.70, and every rare action (type, wait,
navigate) is missed. The gate is therefore useful for the two common, high-value
decisions and should not be trusted for rare deterministic actions.

## Cost and latency

- Measured classifier.dev spend: $0.00 (free tier).
- Jev: 400 classifications in 7 requests, **246 ms/item amortised**.
- GLM-5.3-Flash: 52 calls, ≈359 prompt tokens per decision, **$0.0105 measured**.

Jev is roughly an order of magnitude faster per decision and free, which is the
whole basis for using it as a gate rather than the planner.

## Reproduce

```sh
python3 tests/06_browser_routing/extract.py     # reads the microbench artifacts
python3 tests/06_browser_routing/run.py --tier smart --limit 400 --glm-sample 60
```