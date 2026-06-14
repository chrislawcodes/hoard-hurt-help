---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/plan.md"
artifact_sha256: "a0b7965968b4510aeefdb7b03555963bd3a703586d960e015e6286208d3d0a9c"
repo_root: "."
git_head_sha: "c2a95bd672a7ad771f3d2835e8ab5c984d205530"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "gemini-cli"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

*   **[CODE-CONFIRMED] Non-deterministic Bot Seeding:** The plan proposes using `hashlib` to generate bot seeds, citing that Python's `hash()` is salted and unstable across restarts. However, the plan fails to account for the fact that `hashlib` requires consistent input values. The proposed seed input `f"{match_id}:{hand}:{seat_name}:{bid_index}"` relies on `bid_index`, which is not a standard, persisted attribute in the described state (`match_state` / `player_state`). If `bid_index` is calculated dynamically during the game loop, it introduces a risk of non-determinism if the turn-resolution logic or the order of operations changes, especially during a resumed match.
*   **[CODE-CONFIRMED] Race Conditions in Atomic Creation:** The plan suggests seeding `MatchState` in the "same DB transaction" as the `Match` insert. While this is the correct approach to ensure integrity, the existing `app/engine/match_creation.py` handles `create_match` logic. The plan proposes manually adding `MatchState` after calling `create_match()` in the route. If `create_match()` commits the transaction internally or uses a nested transaction that doesn't propagate correctly, this will fail. The architecture documentation lacks a clear indication of whether `create_match()` is designed to be extensible to allow atomic insertion of game-specific state.
*   **[UNVERIFIED] Scalability Risk in `SequentialDriver`:** The plan relies on `SequentialDriver._drive_actor_turn` calling `module.bot_move`. Because sequential play increases the number of turns compared to simultaneous play, and because the bot logic is deterministic (requiring hashing and potential re-calculation of state), there is a risk that bot performance could degrade significantly as the match progresses or as more bots are added, potentially impacting the 30s deadline (D-8) in complex, high-dice-count scenarios.

## Residual Risks

1.  **Atomicity of Game Initialization (R3/R6):** There is a high risk that the proposed atomic seeding of `MatchState` will fail or create partial state if the existing `create_match()` infrastructure assumes complete control over the transaction scope or the database session lifecycle.
2.  **Consistency of Bot Determinism (R4):** If the `bid_index` or other inputs to the bot seed hash are not perfectly stable and synchronized across all nodes in a distributed environment or across application restarts, the "determinism" will fail, leading to divergent game states that are impossible to reproduce for debugging.
3.  **State Mutation/Persistence (R3):** The plan mentions relying on in-place mutations of `state_json` and expects it to survive. This pattern is notoriously prone to bugs in SQLAlchemy if the model is not properly configured with `MutableDict` or an equivalent (as noted in the plan's R3), and relying on this is a potential point of failure for state recovery.

## Token Stats

- total_input=23970
- total_output=641
- total_tokens=24611
- `gemini-3.1-flash-lite`: input=23970, output=641, total=24611

## Resolution
- status: open
- note: