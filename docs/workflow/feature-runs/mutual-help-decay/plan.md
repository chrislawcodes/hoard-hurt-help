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
Returns, per unordered pair, how many prior turns they mutually helped. Used by `resolve_turn` (decay `k`), `apply_inround_turn` (mirror), and the bot fatigue (Slice 4). Home: `app/games/hoard_hurt_help/scoring.py` for the scoring callers; the bot fatigue reuses the same logic over `ActionRecord` history (the existing `trust._mutual_help_partners` already groups history by turn — extend it to return counts rather than a new scan).

**Caveat (review C2 — the canonical counter does NOT extend to `apply_inround_turn` for free).** `apply_inround_turn` is a *pure function over one turn's actions* with no match history, so it cannot compute `k` itself. Its two callers also disagree on scoping: `viewer.py` walks turns in order (could accumulate), but `viewer_win_probs.py` **resets `inround` per round** while `k` must persist **per match**. Resolution: change `apply_inround_turn` to accept an already-decayed `mutual_value` per mutual action (compute `k` in the caller, once, match-scoped), and update **both** callers to maintain the same match-scoped per-pair counter. Do not leave one caller round-scoped.

**Reuse-audit correction (review M5).** Reciprocal-help detection actually lives in ~9 spots, not 4. Most read **counts/thresholds** (`board_signals.detect_alliances`, `insights.grudges`, `match_summary` superlatives, `win_probability._table`'s binary `mutual`) — they are correct to leave unchanged. The canonical counter unifies only the **payoff** sites: `resolve_turn`, `apply_inround_turn`, the bot fatigue.

### D3 — Decayed bonus formula (single source of truth)
For a current-turn mutual pair with prior count `k`: `bonus = max(-2, 4 - k)`, so each side's mutual total = base 4 + bonus = `max(2, 8 - k)`. Add a constant `MUTUAL_HELP_FLOOR = 2` to `rules.py`; keep `MUTUAL_HELP_BONUS = 4` as the fresh-pact bonus and the decay step = 1 (document both).

### D4 — Bots: fatigue in the trust map, not new selection logic
A farmed partner's trust erodes toward neutral (toward 0, not below) by a per-use fatigue, so `_best_partner` / `_recent_helper` naturally rotate. No change to the selection call sites. Tunable constant; the validation sim is the tuning oracle.

### D5 — Viewer shows the decayed value; `move_effect` stays nominal
`viewer.py` `display_delta` and `apply_inround_turn` use the real decayed value (turn context is available there). `game.py:move_effect(action)` cannot know `k` — left nominal, same accepted limitation as the betrayal sting.

## 2. Slice breakdown (each `[CHECKPOINT]`-bounded, ≤ ~300 lines)

**Slice 1 — Authoritative decay in `resolve_turn`** `[CHECKPOINT]` (~120 lines)
- Add `MUTUAL_HELP_FLOOR` to `rules.py`; add `mutual_help_counts()` helper to `scoring.py`.
- `resolve_turn`: load this match's prior **resolved** submissions (turns with `resolved_at` set, `(round,turn) < current`, current excluded), compute `k` per current mutual pair (count only reciprocal HELP pairs), apply `bonus = max(-2, 4-k)`. Everything else (hoard/help/hurt/betrayal/floor) unchanged. The existing per-turn `seen_pairs` guard already prevents same-turn double-count.
- Tests: fresh pair = +8 each; 8→2 decay sequence for a repeated pair; floor holds at 2; **persists across rounds** (k from round 1 still counts in round 3); a *fresh* second partner resets to 8; two independent pairs decay independently; a defaulted/HOARD-only prior turn contributes `k=0` (review M6); a reconstructed-from-DB mid-match turn yields the same `k`/payoff as a straight-through run (resume-safety verification).
- *Independent of #550.*

**Slice 2 — Viewer mirror + all per-move/display surfaces** `[CHECKPOINT]` (~110 lines)
- Change `apply_inround_turn` to take an already-decayed `mutual_value` per mutual action (review C2); compute `k` once, **match-scoped**, in the caller.
- Update **both** callers to maintain the same match-scoped per-pair counter: `viewer.py` (turn walk) **and** `viewer_win_probs.py` (currently resets `inround` per round — must keep the pair counter match-scoped).
- `viewer.py`: decay the flat-`+8` surfaces (review M1) — `display_delta` (`8 if mutual`), the `_turn_groups` `"+8"` pact badge, and the `_build_rc_data` narration captions.
- Tests: feed one **no-floor** decayed-pact sequence through `resolve_turn` and `apply_inround_turn` and assert the **same decayed mutual value applied** (not general equality — review M3); both viewer callers compute the same `k`.
- *Independent of #550.*

**Slice 3 — Agent rules text** `[CHECKPOINT]` (~30 lines)
- `rules.py` `GAME_RULES_TEXT`: add the decay rule (mutual help worth less each repeat with the same pair, floor 2); bump version.
- `tests/test_rules_text.py`: **update `test_rules_text_is_versioned_v3`** to the new version (review M2 — it currently asserts `"(v3)"`) and add a floor-wording assertion.
- *Independent of #550.*

**Slice 4 — Decay-aware bots** `[CHECKPOINT]` (~120 lines) — **depends on #550**
- **First, read #550's actual diff** (review M4) — confirm `_mutual_help_partners` survives and the real conflict surface; only then design the fatigue on top of it.
- Extend the trust map to apply per-pair fatigue eroding a farmed partner toward neutral (not below 0). Tune the fatigue constant; re-run the validation sim.
- Tests: a partner farmed N times drops below the partnership threshold while a fresh partner stays attractive; fatigue never pushes trust below 0 from a positive start; deterministic.
- **Rebase onto #550 first**; do not start until #550 is merged or rebased in. Run the full bot suite green before the diff checkpoint.

**Slice 5 — Win-prob decision, validation, sim commit, docs reconcile** `[CHECKPOINT]` (~40 lines)
- **Win-prob model (review C1):** record the decision (retrain vs accept+document); if accept, add the known-limitation note to the design doc and a follow-up issue; if retrain, regenerate `baseline_features.csv` + retrain both `.pkl`s under decay.
- **Commit `decay_help_sim.py` to `scripts/`** (review M7) so acceptance #5 is reproducible from the repo, not scratchpad-bound.
- Run the committed sim (baseline/decay/aware, 5 seeds) and record the numbers against the spec targets.
- Reconcile `HOARD_HURT_HELP_DESIGN.md` / `ARCHITECTURE.md` if implementation drifted.

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
- **Win-prob model miscalibration (review C1).** The trained `.pkl`s were calibrated on flat +8; decay shifts their score-feature inputs. verification: before merge, eyeball the win-prob overlay on one decayed-pact replay; if it is visibly wrong, ship with the documented known-limitation note (Slice 5 decision) and file the retrain follow-up — do not silently ship a miscalibrated overlay.
- **Mirror "agreement" overclaimed (review M3).** `resolve_turn` floors the summed delta; `apply_inround_turn` floors per-HURT — they differ by design when a floor bites. verification: the Slice-2 test uses a no-floor sequence and asserts the decayed *mutual value* matches, not general score equality.
