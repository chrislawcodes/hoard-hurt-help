# Tasks — Decaying Mutual-Help + Decay-Aware Bots

Spec: `spec.md` · Plan: `plan.md` · Reuse: `reuse-report.md`. Each `[CHECKPOINT]` is a diff boundary (≤ ~300 lines). Run the Preflight Gate (`ruff` + `mypy` + `pytest`) green before each checkpoint.

---

## Slice 1 — Authoritative decay in `resolve_turn` (independent of #550) · ~120 lines

- [ ] **T001** `app/games/hoard_hurt_help/rules.py`: add `MUTUAL_HELP_FLOOR = 2` (and a one-line comment that mutual help decays −1/repeat to this floor). Do **not** change `MUTUAL_HELP_BONUS = 4` (still the fresh-pact bonus).
  - *verify:* `mypy` clean; constant importable.
- [ ] **T002** `app/games/hoard_hurt_help/scoring.py`: add pure helper `mutual_help_counts(prior_turns) -> dict[frozenset[int], int]` — given prior turns' submissions, return per unordered pair the count of prior turns they **both HELPed each other**. Count only `action == "HELP"` reciprocal pairs; ignore HOARD/HURT/defaulted.
  - *verify:* unit test the helper directly (no DB): 0 for a fresh pair, N for a pair that mutually helped N prior turns, independent pairs counted separately.
- [ ] **T003** `app/games/hoard_hurt_help/scoring.py` `resolve_turn`: load this match's **prior resolved** submissions (`Turn.resolved_at is not None`, `(round,turn) < current`, current turn excluded), build the prior-count map via T002, and for each current-turn mutual pair set `bonus = max(-2, MUTUAL_HELP_BONUS - k)` (k = that pair's prior count). Keep the existing `seen_pairs` same-turn guard, the betrayal `-8`, and the summed-then-floored delta untouched.
  - *verify:* see T004.
- [ ] **T004** `tests/test_resolver.py`: add decay tests — fresh pair +8 each; repeated pair 8→7→…→2 then floors at 2; **k persists across rounds** (round-1 pact still decayed in round 3); a **fresh second partner** resets to 8; two independent pairs decay independently; a prior **defaulted/HOARD-only** turn contributes `k=0` (M6); **resume-safety**: a turn whose prior history is reconstructed from DB rows yields the same `k`/payoff as a straight-through run.
  - *verify:* `pytest -q tests/test_resolver.py` green.
- [ ] **`[CHECKPOINT]` Slice 1** — preflight green; diff ≤ ~120 lines.

---

## Slice 2 — Viewer mirror + all flat-`+8` surfaces (independent of #550) · ~110 lines

- [ ] **T005** `app/games/hoard_hurt_help/scoring.py` `apply_inround_turn`: change signature so a mutual action carries an already-decayed `mutual_value` (caller computes `k`, match-scoped). Do **not** try to derive `k` inside this pure helper (review C2).
  - *verify:* mypy; existing callers updated in T006/T007.
- [ ] **T006** `app/games/hoard_hurt_help/viewer.py`: maintain a **match-scoped** per-pair counter across the turn walk; pass the decayed `mutual_value` into `apply_inround_turn`; decay the three flat-`+8` surfaces — `display_delta` (the `8 if mutual` literal), the `_turn_groups` `"+8"` pact badge, and the `_build_rc_data` narration captions (M1).
- [ ] **T007** `app/games/hoard_hurt_help/viewer_win_probs.py`: it resets `inround` per round — keep the per-pair counter **match-scoped** (not round-scoped) so its `k` matches `viewer.py` and `resolve_turn` (review C2).
  - *verify:* test that both viewer callers compute the same `k` for the same history.
- [ ] **T008** `tests/test_viewer.py` (or a new `tests/test_inround_mirror.py`): feed one **no-floor** decayed-pact sequence through `resolve_turn` and `apply_inround_turn`; assert the **same decayed mutual value applied** (not general score equality — M3). Assert a stale `+8` no longer appears in the rendered pact badge/caption for a decayed pact.
  - *verify:* `pytest -q` green.
- [ ] **`[CHECKPOINT]` Slice 2** — preflight green; diff ≤ ~110 lines.

---

## Slice 3 — Agent rules text (independent of #550) · ~30 lines

- [ ] **T009** `app/games/hoard_hurt_help/rules.py` `GAME_RULES_TEXT`: add the decay rule (a pair's mutual help is worth `−1` less each repeat, floor `MUTUAL_HELP_FLOOR`; a fresh partner resets to +8); bump the version header (v3 → v4).
- [ ] **T010** `tests/test_rules_text.py`: **update `test_rules_text_is_versioned_v3`** to the new version (M2) and add an assertion that the text references the floor value.
  - *verify:* `pytest -q tests/test_rules_text.py` green.
- [ ] **`[CHECKPOINT]` Slice 3** — preflight green; diff ≤ ~30 lines.

> Slices 1–3 form a shippable first PR (scoring + viewer + rules), independent of #550.

---

## Slice 4 — Decay-aware bots · ~120 lines · **BLOCKED ON #550**

- [ ] **T011** Pre-work: fetch + read PR #550's actual diff to `app/engine/bots/trust.py` (M4); confirm `_mutual_help_partners` survives and record the real conflict surface. Rebase this branch onto merged #550.
- [ ] **T012** `app/engine/bots/trust.py`: add a per-pair **fatigue** to `compute_trust_map` — each prior mutual-help with a partner erodes that partner's trust **toward 0, not below**, reusing the T002 counting logic over `ActionRecord` history (extend `_mutual_help_partners` to a counts variant; no new scan). Add a tunable `PARTNER_FATIGUE` constant.
- [ ] **T013** Tune `PARTNER_FATIGUE`: re-run the validation sim (Slice 5 tooling); pick the value that reproduces the spec targets.
- [ ] **T014** `tests/test_bots_engine.py`: a partner farmed N times drops below the partnership threshold while a fresh partner stays attractive; fatigue never pushes a positive trust below 0; deterministic.
  - *verify:* full bot suite (`test_bots_engine.py`, `test_bot_personalities.py`) green.
- [ ] **`[CHECKPOINT]` Slice 4** — preflight + full bot suite green; diff ≤ ~120 lines.

---

## Slice 5 — Win-prob decision, sim commit, validation, docs reconcile · ~40 lines

- [ ] **T015** Win-prob model decision (review C1): **retrain** (regenerate `baseline_features.csv` + retrain both `.pkl`s under decay) **or accept+document**. Default: accept + add a known-limitation note to `HOARD_HURT_HELP_DESIGN.md` and file a retrain follow-up. *verify:* eyeball the win-prob overlay on one decayed-pact replay before merge.
- [ ] **T016** Commit the validation sim to `scripts/` (e.g. `scripts/decay_validation_sim.py`, from the session's `decay_help_sim.py`) so acceptance #5 is reproducible from the repo (M7).
- [ ] **T017** Run the committed sim (baseline/decay/aware, 5 seeds × 40) and record the numbers in `closeout.md` against the spec targets (tie-rate ~0.19 under decay+aware, `aware < decay < baseline`, flat distribution, mutual-pairs > 0).
- [ ] **T018** Reconcile `HOARD_HURT_HELP_DESIGN.md` / `HOARD_HURT_HELP_ARCHITECTURE.md` if implementation drifted from the up-front edits.
- [ ] **`[CHECKPOINT]` Slice 5** — preflight green.

---

## Parallelism
No safe `[P]` within a slice. Slices 1→2 are sequential (2 depends on the T002 helper). Slice 3 is text-only and could overlap Slice 2, but the gain is tiny. Slice 4 is gated on #550. **Recommended delivery:** Slices 1–3 as PR #1 (decay scoring + viewer + rules); Slice 4 as PR #2 rebased on #550; Slice 5 folded into whichever PR carries the bots, or its own.
