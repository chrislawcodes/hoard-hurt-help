---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/spec.md"
artifact_sha256: "4874131768d7e14ebc98d1886dae853c6e2c518c9ab7bd34780b64c525c2fddc"
repo_root: "."
git_head_sha: "c2a95bd672a7ad771f3d2835e8ab5c984d205530"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 5: no new material gaps; viewer/spectator hidden-info + MatchState/PlayerState key layout verified by tests once files exist (plan slices 7/10, R1/R3/R5)."
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **HIGH: Missing "Last-Elimination" End-Game Edge Case in `SequentialDriver`**
    The `SequentialDriver` breaks from the loop when `is_match_over` is True. In Liar's Dice, a match ends when exactly one player has dice. However, the driver logic calls `on_round_start` at the beginning of *every* loop iteration after a round ends. If the "match over" state is reached exactly as the last player is eliminated, the platform must finalize the match without attempting to "start" a final round that should never exist. The current `run_match` logic (`if await module.is_match_over(db, game): break`) happens *after* `award_round`, which is correct, but there is a risk that `on_round_start` is triggered for a round that shouldn't happen if `is_match_over` isn't checked precisely at the transition.
    [CODE-CONFIRMED] — `SequentialDriver.run_match` (app/engine/turn_drivers.py) does check `is_match_over` after `award_round` and before the next `on_round_start`. The implementation looks robust, but this is a critical dependency on the `is_match_over` implementation in the module.

2.  **MEDIUM: Potential `MutableDict` Persistence Failures**
    The spec relies on `MutableDict` in `MatchState` and `PlayerState` to handle JSON persistence. While `MutableDict` tracks in-place mutations, it requires careful usage of session commit/flush cycles. If the game module performs complex state manipulations without intermediate flushes, there is a risk that the state is not dirtied correctly or the JSON blob is overwritten incorrectly.
    [CODE-CONFIRMED] — `app/models/game_state.py` defines `state_json` using `MutableDict.as_mutable(JSON)`. The spec correctly identifies this as a potential failure point ("AC8") and requires an round-trip test, which mitigates the risk.

3.  **MEDIUM: Bot Seam Integration Complexity**
    The spec requires adding a `bot_move` hook to `BaseGameModule` that `SequentialDriver` must call for bots, replacing `default_move`. The spec review notes that `sims/service.py` is simultaneous-only. There is a risk that the `SequentialDriver` implementation will have to duplicate or diverge from bot-routing logic, leading to two disparate paths for "bot decision making."
    [CODE-CONFIRMED] — `SequentialDriver._drive_actor_turn` (app/engine/turn_drivers.py) currently checks `_is_bot` and calls `module.default_move`. Adding a new `bot_move` hook is necessary but introduces complexity in how bot decisions are orchestrated across the two drivers (simultaneous vs. sequential).

4.  **LOW: Ambiguity in Admin API Player Bounds**
    The spec calls for game-aware player bounds in admin create paths. While it correctly suggests validating at the request schema layer, the implementation of "game-aware" validation in `admin_api.py` could become brittle if new games are added with widely different constraints.
    [CODE-CONFIRMED] — `app/admin.py` defines `CreateGameRequest` with hardcoded bounds `ge=6, le=10`. This will indeed require refactoring as identified in the spec.

## Residual Risks

1.  **Race Conditions in Turn Finalization:** There is a narrow risk that a turn resolve/award could race with a late submission if `resolve_turn` or `award_round` takes significant time, especially if not wrapped in proper transactional boundaries. The current sequential driver's approach to polling (`_SUBMIT_POLL_SECONDS`) and resolving is well-intentioned, but high-latency environments might trigger edge cases in turn advancement.
2.  **Incomplete Visibility State:** The `admin_only` flag is a powerful tool, but if the UI layer (which is not fully covered by the back-end-centric spec) does not respect this flag in all contexts (leaderboards, match lists), private games could inadvertently leak to non-admins.
3.  **State-Blob "Smearing":** Since `MatchState` and `PlayerState` are opaque JSON blobs, there is no structural validation of the state schema. If the module code evolves and changes the shape of the JSON without an explicit migration, old matches in the database will cause runtime errors in the module, potentially bricking active games. The platform provides no tooling for "schema-aware" JSON state management.

## Token Stats

- total_input=47355
- total_output=1166
- total_tokens=94822
- `gemini-3.1-flash-lite`: input=47355, output=1166, total=94822

## Resolution
- status: accepted
- note: Round 5: no new material gaps; viewer/spectator hidden-info + MatchState/PlayerState key layout verified by tests once files exist (plan slices 7/10, R1/R3/R5).
