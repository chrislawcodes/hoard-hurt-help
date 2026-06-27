# Tasks — Bots D3 + D5 dedup

From `plan.md`. Each `[CHECKPOINT]` = full Preflight + commit. Anchors resolved by
symbol/grep. Baseline (Slice 0): `.venv/bin/pytest -q --co` count = **1331**;
sorted test-ID list saved to `reviews/base-test-ids.txt` for the AC4 diff.

## D5 disposition ledger (fill during Slice 2 — all 6 sites)

| Site (file:line) | sign | access | seed | disposition | proof |
|---|---|---|---|---|---|
| `strategies._probe_target` | favor-high | `.get` | `(profile,context,aid,turn)` | TBD | |
| `strategies._best_partner` | favor-high | `[aid]` | `(profile,context,aid)` | TBD | |
| `strategies._most_hostile` | favor-low | `[aid]` | `(profile,context,aid)` | TBD | |
| `strategies._choose_from_candidates` (favor_high) | favor-high | `.get` | `(context,aid)` | TBD | |
| `strategies._choose_from_candidates` (favor_low) | favor-low | `.get` | `(context,aid)` | TBD | |
| `runtime._talk_target` | favor-high | `.get` | `(profile,context,aid)` | TBD | |

`unified` → per-site recorded-pick test (≥2 distinguishing inputs) + `rg` absence proof.
`not-a-true-duplicate` → byte-unchanged proof (`git diff origin/main -- <file>`) + reason.

## Slices

### [CHECKPOINT] Slice 0 — baseline (no code)
- `.venv/bin/pytest -q --co -q | sort > reviews/base-test-ids.txt`; record the count here.

### [CHECKPOINT] Slice 1 — D3 (tests-first) ✅ unified
- Add `tests/test_bot_presets_profile.py`: assert `resolve_profile_choice("P:0")` ==
  `expand_pack("P")[0]` for `P=fixture_zero_floor` (hidden → `fixture_pack="fixture_zero_floor"`)
  AND `P=mixed_20` (non-hidden → `fixture_pack=None`); also vary `seed_base` to pin
  `seed=seed_base+entry.seed_offset`. Run green on base.
- Extract `_entry_to_profile(entry, pack, seed_base) -> BotProfile` in `presets.py`;
  `resolve_profile_choice` + `expand_pack` delegate. Keep green.
- Presence: `rg "BotProfile\(" app/engine/bots/presets.py` → one construction site
  (`_entry_to_profile`). Full Preflight.

### [CHECKPOINT] Slice 2 — D5 (tests-first, adjudicate)
- Add `tests/test_bot_selectors.py`: per-site recorded-pick tests for each site intended
  to route — ≥2 inputs that PRODUCE DIFFERENT picks; for `_probe_target`, a turn-only pair
  that FLIPS the pick. Commit + green on base BEFORE any `pick_by_trust` edit.
- Add annotated `pick_by_trust(candidates, *, trust_key, favor_high, seed)` in
  `strategies.py`. Route only clean-win sites (each closure preserves exact access + seed +
  sign); for `_choose_from_candidates` keep the pre-filter + favor-low branch in the caller.
  Leave thin-win sites byte-unchanged.
- Fill the ledger; for `unified` sites add the `rg` absence proof; for `not-a-true-duplicate`
  add the `git diff origin/main` byte-unchanged proof.
- Verify: `rg "_seed_int\(" app/engine/bots/` arg tuples unchanged; import smoke + structural
  grep (strategies imports neither runtime nor trust). Full Preflight.

### [CHECKPOINT] Slice 3 — deliver
- AC4: `.venv/bin/pytest -q --co -q | sort` vs `reviews/base-test-ids.txt` → additions only.
- Final full Preflight; PR with `Validation` (ruff/mypy/pytest counts + D3 disposition +
  the 6-site D5 ledger with per-site reasons).

## Parallelization
Sequential — D3 (presets.py) and D5 (strategies.py/runtime.py) are independent files but
small; one agent, ordered slices.
