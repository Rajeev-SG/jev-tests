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

## File digests (sha256, first 16 hex chars)

Lets anyone confirm the archived bytes without this repo's history.

- `todomvc-glm-browser-relay-enter-1.json` — `b5a8c6559b3581b0`
- `todomvc-glm-browser-relay-enter-2.json` — `d7f93dca1f211d5d`
- `todomvc-glm-browser-relay-enter-3.json` — `8bae56a701f7cd9b`
- `todomvc-glm-playwriter-1.json` — `99cbbb1151f0d8a3`
- `todomvc-glm-playwriter-enter-1.json` — `ed594b01c7643276`
- `todomvc-glm-playwriter-enter-2.json` — `2dd63800722eabe2`
- `todomvc-glm-playwriter-enter-3.json` — `227c24d55b1fb912`
- `todomvc-glm-playwriter-enter-4.json` — `9de32c3416060cf2`
- `todomvc-glm-playwriter-enter-5.json` — `ebf0b19a4e30d568`
- `todomvc-jev-browser-relay-enter-1.json` — `b6cfc0544b13de35`
- `todomvc-jev-browser-relay-enter-2.json` — `426d33c644298c8c`
- `todomvc-jev-browser-relay-enter-3.json` — `10075a58802d6c03`
- `todomvc-jev-playwriter-enter-1.json` — `194c588376d76b9c`
- `todomvc-jev-playwriter-enter-2.json` — `c412d691eb6f3e04`
- `todomvc-jev-playwriter-enter-3.json` — `37ead36e84535af7`
- `todomvc-jev-playwriter-enter-4.json` — `e06cac466f996153`
- `todomvc-jev-playwriter-enter-5.json` — `90b8756ad1d7f618`
- `todomvc-playwriter-1.json` — `c5d9fb5d15871dd3`
