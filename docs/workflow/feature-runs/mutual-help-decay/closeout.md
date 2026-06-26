# Closeout: mutual-help-decay

## What shipped

A pair's mutual-help bonus **decays** the more that same pair farms it, and bots
**rotate** off a farmed partner — together breaking the "lock onto one ally and
farm +8" pattern that left two partners tied at the top of most rounds.

Delivered as two PRs on top of the betrayal-memory work (#550):

- **PR #553 — scoring, viewer, rules (Slices 1–3 + Slice 5 docs/sim).**
  - `scoring.resolve_turn`: a pair's k-th mutual help pays each side `max(2, 8−k)`,
    k = that pair's prior mutual-help turns this match, derived from match history
    (resume-safe, no migration). New `mutual_help_counts()` helper.
  - `apply_inround_turn` / `build_pd_replay_view`: the viewer mirror carries the
    decayed `mutual_value`; pact badge and narration caption show it, never a
    stale +8.
  - `GAME_RULES_TEXT`: documents the decay; version bumped v3 → v4.
  - `scripts/decay_validation_sim.py`, design + architecture docs, win-prob
    known-limitation note.
- **PR #2 — decay-aware bots (Slice 4).**
  - `trust.py`: `PARTNER_FATIGUE = 8`. Each prior mutual-help turn with a partner
    erodes that partner's trust toward 0 (never below) via a `_mutual_help_counts()`
    count (the existing `_mutual_help_partners` is now its set view — single scan).
    A farmed pact cools under the partnership threshold and selection rotates;
    a fresh partner stays attractive; a hostile player is untouched.

## Validation (acceptance #5)

Reproduce with `scripts/decay_validation_sim.py` (deterministic, no LLM). The
canonical run is **5 seeds × 40 matches** on the real shipped engine; baseline
pins the pre-decay flat +8 rule and `PARTNER_FATIGUE=0`, decay adds the scoring
decay, aware adds the bot rotation.

Round tie-rate (mean across seeds 42, 99, 7, 13, 23):

| condition | tie-rate | vs baseline |
|---|---|---|
| baseline (flat +8) | 0.549 | — |
| decay | 0.289 | −47% |
| **decay + aware** | **0.216** | **−61%** |

- **`aware < decay < baseline` on every seed** (per-seed aware: 0.207 / 0.236 /
  0.179 / 0.214 / 0.246).
- **Cooperation stays alive:** mutual-pairs (mean) baseline 6329 → decay 6284 →
  aware 4428 (rotation thins farming, does not kill it).
- **Flatter win distribution:** the baseline coalition_seeker edge (0.222) drops
  to 0.090; decay's diplomat spike (0.264) settles to 0.119; no single strategy
  runs away.

**Tuning (T013).** An aware-only sweep (3 seeds × 30) confirmed `PARTNER_FATIGUE=8`
is the sweet spot — higher values do not lower ties and erode cooperation:

| PARTNER_FATIGUE | aware tie-rate | mutual-pairs |
|---|---|---|
| **8** | **0.189** | **3223** |
| 12 | 0.198 | 3069 |
| 16 | 0.200 | 3018 |

(The sweep's 3-seed subset reads 0.189; the canonical 5-seed run reads 0.216 —
seeds 13/23 run hotter. We report the conservative 5-seed number.)

## Known limitations / follow-ups

- **Win-probability bands not retrained.** The replay overlay
  (`viewer_win_probs.py` → `app/engine/win_probability.py`) is trained on
  pre-decay score dynamics, so it is mildly optimistic for a pact-leader late in a
  match. Display-only — never affects scoring. Documented in
  `HOARD_HURT_HELP_DESIGN.md`; retrain (regenerate `baseline_features.csv` +
  retrain the two `.pkl`s under decay) is a tracked follow-up.

## Where the artifacts live

`docs/workflow/feature-runs/mutual-help-decay/` — spec.md, plan.md, tasks.md,
reuse-report.md, reviews/, state.json, this closeout. Validation sim:
`scripts/decay_validation_sim.py`.
