---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/spec.md"
artifact_sha256: "4ff4457e43d0fef62590f034c2662c2c11330c8bd8731d9cf6ab6998d0d9cc8e"
repo_root: "."
git_head_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 4 (final spec round) accepted. Folded in: override match_placement_key for LD leaderboard order (§6); apply game-aware bounds at request-validation layer only, not match_creation/arena (§9); AC5 clarified — LD is admin_only so absent from public lobby, started via admin create flow. Snapshot-key stripping done in LD record_submission. Remaining items are plan-stage implementation detail (plan has its own implementation+testability review)."
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH [CODE-CONFIRMED]: The spec misses the actual lobby entrypoint, [`app/routes/matches_user.py`](/private/tmp/wt-liars-dice-engine-factory/app/routes/matches_user.py). That route is the public “new match” flow, it hides `admin_only` games from non-admins, and it still uses hardcoded generic defaults. As written, `liars-dice` cannot be started from the lobby, so AC5 is not achievable without adding this path to scope.

- MEDIUM [CODE-CONFIRMED]: The proposed game-aware player-bound change is not isolated to the three admin routes. [`app/engine/match_creation.py`](/private/tmp/wt-liars-dice-engine-factory/app/engine/match_creation.py) is shared by the lobby flow and by automated match creation, and [`app/engine/arena.py`](/private/tmp/wt-liars-dice-engine-factory/app/engine/arena.py) already creates HHH matches with `min_players=1`. If validation is tightened to a module’s `min_players..max_players` without revisiting those callers, existing non-LD creation paths will fail.

- MEDIUM [CODE-CONFIRMED]: The spec omits `match_placement_key`, but the leaderboard engine uses it directly in [`app/read_models/leaderboard.py`](/private/tmp/wt-liars-dice-engine-factory/app/read_models/leaderboard.py). Overriding only `final_placement` will not change rating/placement math; LD will still inherit PD’s `(round_wins, total_score)` proxy, so leaderboard/rating ordering will disagree with the game’s elimination order.

- LOW [UNVERIFIED]: The validation-snapshot plan has no central sanitizing boundary. [`app/engine/agent_play.py`](/private/tmp/wt-liars-dice-engine-factory/app/engine/agent_play.py) copies the validated move dict straight into `internal_move` and passes that to `record_submission()`. If the LD module persists or reuses the dict, the new snapshot keys will ride along unless every persistence path strips them manually. I could not confirm the LD storage code, so this is lower confidence.

## Residual Risks

- The provided code does not include the new LD engine/module, so the rules core, showdown resolution, resume idempotency, and bot determinism claims remain unverified.

- Hidden-info safety still depends on updating every public surface consistently. The current read paths are split across agent polling, viewer rendering, spectator JSON, and export/read-model code, so one missed projection can still leak or omit state even after the main feature work lands.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 4 (final spec round) accepted. Folded in: override match_placement_key for LD leaderboard order (§6); apply game-aware bounds at request-validation layer only, not match_creation/arena (§9); AC5 clarified — LD is admin_only so absent from public lobby, started via admin create flow. Snapshot-key stripping done in LD record_submission. Remaining items are plan-stage implementation detail (plan has its own implementation+testability review).
