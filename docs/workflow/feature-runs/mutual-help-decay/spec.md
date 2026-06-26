# Spec — Decaying Mutual-Help + Decay-Aware Bots

**Slug:** `mutual-help-decay`
**Game:** Hoard Hurt Help
**Status:** spec (authored, pre-checkpoint)

## 1. Problem

The round winner is the single highest in-round score. But **mutual help is perfectly symmetric** — when A and B HELP each other they each get +8 — so two pact-partners end a round tied at the top. Across 5-seed real-engine simulations this session, **~53% of rounds end in a tie for first** (split fractionally), which makes round outcomes mushy and lets a "lock onto one partner and farm +8" strategy dominate.

We tested many levers and recorded the results this session:

| Lever tried | Effect on tie-rate |
|---|---|
| Betrayal rule (−8) + betrayal memory | no change (~0.53) |
| Mutual-bonus magnitude 4 → 2 → 1 | no change (~0.47–0.63) — ties come from *symmetry*, not size |
| Roster mix (0→10 hurters) | no change (~0.59–0.68) |
| Dropping diplomat | ties got *worse* (0.50 → 0.63) |
| **Decaying mutual help** | **0.53 → 0.27** |
| **Decaying mutual help + decay-aware bots** | **0.53 → 0.19** |

The tie-rate is **structural** (symmetric pacts + the score floor compressing hit players together), not a magnitude problem. Only making the mutual-help payoff **depend on history** breaks it.

## 2. Goal

Cut the round-tie rate (~0.53 → ~0.19 in sim, −64%) and break pact-farming dominance, **without reducing how much cooperation happens** — by:

1. **Decaying mutual-help payoff** — a pair's mutual help is worth less each time *that same pair* repeats it, flooring at the Hoard value, so a long-running pact stops paying and a *fresh* partner is worth more.
2. **Decay-aware bots** — bots rotate off exhausted pacts to fresh partners, so the simulated (and eventually live) play actually responds to the incentive.

Validated on the real engine across 5 seeds: tie-rate falls in every seed (`aware < decay < baseline`), cooperation *count* barely changes under decay alone (the fix is score asymmetry, not less helping), mutual-pairs drop ~30% under aware (bots rotate), and the win distribution flattens to **0.055–0.160** (chance = 0.111) with no dominant strategy.

## 3. The mechanic (exact)

### 3.1 Decaying mutual-help payoff

For a given **unordered pair** {A, B}, let `k` = the number of times that pair has already mutually helped **earlier in this match** (k = 0 the first time).

- A mutual help pays each side a **total** of `max(2, 8 − k)`: `8, 7, 6, 5, 4, 3, 2, 2, 2, …`
- Floor is **2** = the Hoard value (a fully-decayed pact is no better than hoarding).
- A **fresh** pair resets to 8 (k starts at 0 for a pair that has never mutually helped this match).
- `k` is **per match**, **persisting across rounds** — it does **not** reset each round. (This is what the 5-seed validation used.)

Implementation note (single source of truth = `resolve_turn`): base HELP already credits +4 to each helper's target, so the **mutual bonus added** for a pair on its k-th mutual help becomes `max(-2, 4 − k)` (total = base 4 + bonus = `max(2, 8 − k)`). Non-mutual (one-directional) HELP stays **+4**. HOARD (+2) and HURT/betrayal (−4 / −8 vs a same-turn helper) are **unchanged**.

### 3.2 Decay-aware bots

Bots should abandon an exhausted pact and seek a fresh partner. Validated proxy: each prior mutual-help with a partner **erodes that partner's trust toward neutral** (toward 0, **not below** — a farmed partner is "meh," not an enemy), so the existing partner-selection logic naturally looks elsewhere. The real implementation lives in the bot trust map / partner selection and should be refined and tuned (the sim used a flat per-use fatigue).

## 4. Scope

### In scope
- `app/games/hoard_hurt_help/scoring.py` — `resolve_turn`: per-pair decay state + decayed mutual bonus.
- `app/games/hoard_hurt_help/scoring.py` — `apply_inround_turn` (viewer running-score mirror): same decay so the mirror matches the authoritative score.
- `app/games/hoard_hurt_help/viewer.py` — per-move display shows the **decayed** mutual value, not a flat +8; signal a "wearing-out" pact if cheap.
- `app/games/hoard_hurt_help/rules.py` — `GAME_RULES_TEXT`: tell agents mutual help decays to a floor of 2 with repeated use of the same pair; bump version.
- `app/engine/bots/trust.py` + `app/engine/bots/strategies.py` — decay-aware partner rotation.
- Tests for the decay math and the bot rotation.
- Docs: `docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md` (payoff/decay + rationale) and `HOARD_HURT_HELP_ARCHITECTURE.md` (where per-pair decay state and bot rotation live).

### Out of scope (non-goals)
- Changing base HELP (+4), HOARD (+2), or HURT/betrayal (−4/−8) values.
- Changing round/match structure or the single-highest-score win condition.
- Retuning the betrayal mechanic or adding a tit-for-tat bot (separate ideas).
- A DB migration — unless the chosen decay-state storage requires one (see Open Decisions).

## 5. Acceptance criteria

1. A pair's mutual-help total pays `max(2, 8 − k)`, k = that pair's prior mutual-helps this match; fresh pair = 8; floor 2; resets only at match end.
2. `resolve_turn` (authoritative) and `apply_inround_turn` (viewer mirror) agree on the decayed value; the per-move display shows the decayed value, not a flat +8.
3. `GAME_RULES_TEXT` documents the decay + floor; rules version bumped; a `rules.py` text test guards the wording matches the constants.
4. Decay-aware bots rotate off exhausted pacts (a farmed partner's partnership pull erodes toward neutral, not below 0).
5. Re-running the validation sim (baseline vs decay vs decay+aware, 5 seeds × 40 matches) reproduces: tie-rate ~0.19 under decay+aware (from ~0.53 baseline), `aware < decay < baseline` in every seed, flat win distribution.
6. Preflight green (`ruff` + `mypy` + `pytest`); new tests cover decay math (8→2 floor, per-pair, fresh-partner reset, persists across rounds) and bot rotation.

## 6. Open decisions (resolve in plan)

- **Where the per-pair decay counter lives.** Options: (a) **derive `k` from turn history** inside `resolve_turn` each turn — crash/resume-safe, no new column, O(history) per turn (cheap at ≤49 turns × 10 players); (b) in-memory dict — fails on DB resume; (c) the existing generic per-title state store `MatchState` (`app/models/game_state.py`, migration 0033 — no new migration needed) — survives resume but adds write coupling in `resolve_turn`. **Recommendation: (a)** for resume-safety and zero schema change; fall back to (c) only if the history scan proves too costly. The plan must verify the chosen path is deterministic and matches the sim's per-pair count.
- **Exact bot fatigue curve / tuning.** Sim used a flat per-use trust erosion toward 0. Plan should pick a principled value and re-run the validation sim as the check.
- **Viewer "pact wearing out" affordance.** Decide whether to show the decayed number only, or add a visual cue. Minimal: show the real decayed value.

## 7. Dependencies & sequencing

- Builds on merged work this session: **#546** (betrayal −8 scoring), **#547** (viewer), **#548** (agent rules + "betray" naming), **#549** (pragmatist betrays).
- **#550 (betrayal memory in the trust map) is OPEN.** The decay-aware bot rotation extends the **same trust map** #550 reworks (`app/engine/bots/trust.py`). To avoid a merge conflict and build on the latest trust model, **#550 should land first**, or this feature should rebase onto it before the bot-rotation slice. The decay *scoring* slice (Section 3.1) is independent of #550 and can proceed either way.

## 8. Validation plan

Acceptance check is the scratchpad simulation already written this session (`decay_help_sim.py`): run `baseline` vs `decay` vs `aware` across 5 seeds × 40 matches on the real engine and confirm the tie-rate and win-distribution targets in §5.5. This is the pre-merge verification for the "does it actually fix ties" residual risk — concrete, cheap, reproducible. The implementation's decayed-payoff math must match the sim's (`max(2, 8−k)` per pair, per match).

## 9. Risks

- **Resume correctness** (decay state after a crash) — mitigated by deriving `k` from history. *verification:* unit test that resolves a reconstructed mid-match turn sequence and asserts the same per-pair `k` and payoff as a straight-through run.
- **Mirror divergence** (viewer vs authoritative) — *verification:* a test feeding the same decayed-pact sequence through `resolve_turn` and `apply_inround_turn` and asserting equal running scores.
- **Over-correction of cooperation** (bots stop helping entirely) — *verification:* the validation sim's mutual-pairs count stays well above zero (sim showed ~4,300, a ~30% drop, not a collapse).
