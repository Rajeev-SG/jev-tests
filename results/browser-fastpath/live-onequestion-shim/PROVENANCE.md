# Superseded run set (one-question shim)

These files are the run recorded by the **broken** harness, copied **byte-identical** from commit `9892447`, which is the run the broken
harness wrote before any later annotation touched it.
They are kept so the correction can be checked, not taken on trust.

They are NOT annotated or regenerated: no field has been added or changed. Each
file is exactly as the original run wrote it, which is why none of them contains
the `error_kind` / `goal_state_reached` fields added later.

What was wrong with the harness: `classifier_policy.py` collapsed Jev's two
questions (operation, then element) into one menu, so the real policy could not
choose an element and looped. See `results/browser-fastpath/README.md`.

Do not feed these into `scripts/summarize_live.py`; it reads `live/` only.
