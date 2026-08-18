---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/spec.md"
artifact_sha256: "4874131768d7e14ebc98d1886dae853c6e2c518c9ab7bd34780b64c525c2fddc"
repo_root: "."
git_head_sha: "c2a95bd672a7ad771f3d2835e8ab5c984d205530"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 5 accepted; addressed in plan.md (not spec, to keep spec checkpoint healthy). Plan decision 7: bot seed derived from persisted state (match_id,hand,seat,bid_index) so resume re-decides identically. Plan decision 8: public_state carries wild_ones + full §7.2 shape. Plan reconciles §2-vs-§9: game-aware bounds in route validators only; plan is authoritative for the implementer."
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH [CODE-CONFIRMED]: The bot requirement is under-specified in a way that makes AC3 fragile. The spec requires sequential bots to be “deterministic given a seed,” but it never defines where that seed comes from or how it is persisted across a restart. The current driver path in `app/engine/turn_drivers.py` has no bot-specific persistence hook, and the only thing it can do today is record a move after it is chosen. That means a resumed bot turn can be re-decided differently unless the spec adds an explicit seed/storage seam.
- MEDIUM [CODE-CONFIRMED]: The public-state contract is missing a field the spec itself says bots must read. Section 7 says bots “Must read `public_state.wild_ones` and play both modes correctly,” but Section 6’s public-state description only names standing bid, per-player dice counts, and recent showdowns. With the current code paths (`app/routes/web_viewer.py`, `app/routes/spectator_api.py`, `app/engine/agent_play.py`) there is no existing structured LD public-state payload to carry `wild_ones`, so the bot seam cannot distinguish wild vs. no-wild tables from the same state surface unless the spec adds that field explicitly.
- MEDIUM [CODE-CONFIRMED]: The player-bound rules are internally contradictory. Section 2 says to “relax the bound to allow 3..6,” but Section 9 later says not to blanket relax shared creation paths and instead enforce game-aware bounds in route validators only, because the same schema and helpers are reused by PD and arena flows (`app/schemas/admin.py`, `app/engine/match_creation.py`, `app/routes/game_admin_web.py`, `app/routes/game_admin_api.py`). A developer following the earlier instruction literally will break existing create flows; a developer following the later one still needs a precise schema/route split that the spec does not state cleanly.

## Residual Risks

- The viewer and spectator changes depend on files not included in the supplied code context, so the hidden-info sweep still needs end-to-end verification once `templates/fragments/*`, `app/routes/web_viewer.py`, and `app/routes/spectator_api.py` are updated.
- The spec assumes a stable LD state JSON layout for `MatchState`/`PlayerState`, but the exact key names and persistence semantics still need dedicated tests once the module exists, especially for restart/resume behavior.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 5 accepted; addressed in plan.md (not spec, to keep spec checkpoint healthy). Plan decision 7: bot seed derived from persisted state (match_id,hand,seat,bid_index) so resume re-decides identically. Plan decision 8: public_state carries wild_ones + full §7.2 shape. Plan reconciles §2-vs-§9: game-aware bounds in route validators only; plan is authoritative for the implementer.
