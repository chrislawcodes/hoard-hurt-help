# Plan — Decaying Mutual-Help + Decay-Aware Bots

**Slug:** `mutual-help-decay` · Spec: `spec.md` · Reuse audit: `reuse-report.md`

## 1. Architecture decisions

### D1 — Per-pair decay state: derive `k` from history (no new storage)
`resolve_turn` computes `k` (a pair's prior mutual-help turns this match) by scanning this match's already-resolved `TurnSubmission`s grouped by turn. Resume-safe (state is the DB record itself), no migration, bounded O(≤49 turns × ≤10 players) per turn. Rejected: in-memory dict (dies on resume); `MatchState` write (couples resolution to a write + needs rebuild-on-resume) — kept only as a fallback if profiling ever shows the scan is hot.

### D2 — One canonical counter (kills the duplication)
Add a single pure helper:
```
mutual_help_counts(prior_turns: Iterable[turn-of-submissions]) -> dict[frozenset[int|str], int]
```
Returns, per unordered pair, how many prior turns they mutually helped. Used by `resolve_turn` (decay `k`), `apply_inround_turn` (mirror), and the bot fatigue (Slice 4). Home: `app/games/hoard_hurt_help/scoring.py` for the scoring callers; the bot fatigue reuses the same logic over `ActionRecord` history (the existing `trust._mutual_help_partners` already groups history by turn — extend it to return counts rather than a 5th scan).

### D3 — Decayed bonus formula (single source of truth)
For a current-turn mutual pair with prior count `k`: `bonus = max(-2, 4 - k)`, so each side's mutual total = base 4 + bonus = `max(2, 8 - k)`. Add a constant `MUTUAL_HELP_FLOOR = 2` to `rules.py`; keep `MUTUAL_HELP_BONUS = 4` as the fresh-pact bonus and the decay step = 1 (document both).

### D4 — Bots: fatigue in the trust map, not new selection logic
A farmed partner's trust erodes toward neutral (toward 0, not below) by a per-use fatigue, so `_best_partner` / `_recent_helper` naturally rotate. No change to the selection call sites. Tunable constant; the validation sim is the tuning oracle.

### D5 — Viewer shows the decayed value; `move_effect` stays nominal
`viewer.py` `display_delta` and `apply_inround_turn` use the real decayed value (turn context is available there). `game.py:move_effect(action)` cannot know `k` — left nominal, same accepted limitation as the betrayal sting.

## 2. Slice breakdown (each `[CHECKPOINT]`-bounded, ≤ ~300 lines)

**Slice 1 — Authoritative decay in `resolve_turn`** `[CHECKPOINT]` (~120 lines)
- Add `MUTUAL_HELP_FLOOR` to `rules.py`; add `mutual_help_counts()` helper to `scoring.py`.
- `resolve_turn`: load prior resolved submissions for the match, compute `k` per current mutual pair, apply `bonus = max(-2, 4-k)`. Everything else (hoard/help/hurt/betrayal/floor) unchanged.
- Tests: fresh pair = +8 each; 8→2 decay sequence for a repeated pair; floor holds at 2; **persists across rounds** (k from round 1 still counts in round 3); a *fresh* second partner resets to 8; two independent pairs decay independently.
- *Independent of #550.*

**Slice 2 — Viewer mirror + per-move display** `[CHECKPOINT]` (~80 lines)
- `apply_inround_turn`: apply the same decay via the shared counter (it already receives the turn's actions; extend its caller to pass prior counts, or compute from the viewer history it walks).
- `viewer.py` `display_delta`: show the decayed value for a mutual HELP, not flat 8.
- Tests: feed an identical decayed-pact sequence through `resolve_turn` and `apply_inround_turn`, assert **equal running scores** (acceptance #2).
- *Independent of #550.*

**Slice 3 — Agent rules text** `[CHECKPOINT]` (~30 lines)
- `rules.py` `GAME_RULES_TEXT`: add the decay rule (mutual help worth less each repeat with the same pair, floor 2); bump version.
- Extend `tests/test_rules_text.py` to assert the wording references the floor constant.
- *Independent of #550. Small-change-lane eligible on its own, but kept in the FF run for the record.*

**Slice 4 — Decay-aware bots** `[CHECKPOINT]` (~120 lines) — **depends on #550**
- Extend `trust._mutual_help_partners` (or add a counts variant) and `compute_trust_map` to apply per-pair fatigue eroding a farmed partner toward neutral.
- Tune the fatigue constant; re-run the validation sim.
- Tests: a partner farmed N times drops below the partnership threshold while a fresh partner stays attractive; fatigue never pushes trust below 0 from a positive start; deterministic.
- **Rebase onto #550 first** (it reworks `trust.py`); do not start this slice until #550 is merged or rebased in.

**Slice 5 — Validation + docs reconcile** `[CHECKPOINT]` (~20 lines)
- Run `decay_help_sim.py` (baseline/decay/aware, 5 seeds) and record the numbers against the spec targets.
- Reconcile `HOARD_HURT_HELP_DESIGN.md` / `ARCHITECTURE.md` if implementation drifted from the up-front edits.

## 3. Sequencing & parallelism
- Slices 1→2→3 are a clean chain on this branch, independent of #550.
- Slice 4 gates on #550. If #550 is still open at implementation time, ship Slices 1–3 as a first PR (scoring + viewer + rules), then Slice 4 in a follow-up PR rebased on #550. `[P]`: none within a slice; Slices 1–3 are sequential (2 depends on 1's helper, 3 is text-only and could run parallel to 2 but the gain is tiny).

## 4. Testing strategy
- Unit: decay math (Slice 1), mirror equality (Slice 2), rules text (Slice 3), bot fatigue/rotation (Slice 4) — all deterministic, SQLite in-memory per `CLAUDE.md`.
- Integration acceptance: the 5-seed `decay_help_sim.py` reproduces tie-rate ~0.19 under decay+aware (from ~0.53), `aware < decay < baseline` every seed, flat win distribution, mutual-pairs > 0.
- Preflight (`ruff` + `mypy` + `pytest`) green per slice before its diff checkpoint.

## 5. Residual risks (each with a pre-merge verification)
- **Resume correctness — `k` wrong after a DB resume.** verification: a unit test that resolves a turn whose match history was reconstructed from DB rows and asserts the same per-pair `k` and payoff as a straight-through run.
- **Mirror divergence — viewer running score disagrees with the authoritative score.** verification: the Slice-2 test feeding one decayed-pact sequence through both `resolve_turn` and `apply_inround_turn` and asserting equal scores; fail the slice if they differ.
- **Cooperation collapse — bots stop helping entirely under fatigue.** verification: the validation sim's mutual-pairs count stays well above zero (sim baseline ~6,300 → aware ~4,300, a ~30% drop, not a collapse); flag if it falls below ~2,000.
- **`#550` merge conflict in `trust.py`.** verification: rebase Slice 4 onto merged #550 and run the full bot test suite (`tests/test_bots_engine.py`, `tests/test_bot_personalities.py`) green before the Slice-4 diff checkpoint.
- **History-scan cost in `resolve_turn`.** verification: confirm the per-turn scan is bounded (≤49×10) and add the prior-submissions query once per turn (not per pair); a timing assertion is unnecessary at this scale but note the bound in the test docstring.
