---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/spec.md"
artifact_sha256: "4ff4457e43d0fef62590f034c2662c2c11330c8bd8731d9cf6ab6998d0d9cc8e"
repo_root: "."
git_head_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
git_base_ref: "origin/main"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 4 (final spec round) accepted; no new material gaps beyond Codex's, which are folded in. Orchestrator convergence call: 4 rounds is past diminishing returns; carry remaining detail to plan."
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1. **Hardcoded Player Range Validation** [CODE-CONFIRMED]
   - **Severity:** HIGH
   - **Evidence:** `app/schemas/admin.py` defines `CreateGameRequest` with `min_players` and `max_players` capped by `Field(ge=6, le=10)`. The `spec.md` states Liar's Dice (LD) supports 3–6 players. These hardcoded constants in the schema currently prevent creating valid LD matches. This confirms a mismatch between the current platform design and the requirements defined in the spec.

2. **Insecure Exposure of Private State** [UNVERIFIED]
   - **Severity:** HIGH
   - **Evidence:** The current implementation of `app/routes/spectator_api.py` and `app/routes/web_viewer.py` uses `load_players` and `load_match_timeline` which rely on generic reads. The spec explicitly requires sourcing LD public state from the module's `public_state_for()` and warns about potential leaks of dice faces. As the provided code for `spectator_api.py` shows no call to `public_state_for()`, it is highly probable that LD, if implemented per the spec, would leak hidden dice faces to spectators if not explicitly restricted.

3. **Inconsistent Per-Match Configuration Persistence** [CODE-CONFIRMED]
   - **Severity:** HIGH
   - **Evidence:** The admin API (`app/routes/admin_api.py`) and `app/engine/match_creation.py` only initialize the `Match` table. No code exists to initialize `MatchState` or `PlayerState` records for a new match. The spec requires persisting LD-specific "wild on/off" and "dice per player" settings. Failure to persist this configuration at creation time means these variables will be missing or default-initialized inconsistently when the match begins.

## Residual Risks

*   **Concurrency/Persistence Race:** The architecture relies on `MatchState` and `PlayerState` using `MutableDict` for dirty-tracking. While this is standard for SQLAlchemy, if multiple asynchronous tasks attempt to modify the `state_json` simultaneously, it may lead to lost updates. The current implementation relies on single-writer sequential turns per match, but this assumption must be strictly enforced across all game modules.
*   **Validator Parity:** The "validation snapshot" requirement involves merging read-only state into a `move` dictionary before `validate_move` is called. If the snapshot logic is not perfectly synchronized with the actual state persisted during `record_submission`, the validator will reject legitimate moves or accept illegal ones, creating a mismatch between logic and persistence.
*   **Sequential Bot Seam Integration:** The `SequentialDriver` currently records `default_move` for bots. Replacing this with a module-level `bot_move` hook introduces a new dependency in the sequential loop. If this hook fails to account for the deterministic requirement (using provided seeds), bot behavior will become inconsistent during re-runs or test replays.

## Token Stats

- total_input=46428
- total_output=654
- total_tokens=47082
- `gemini-3.1-flash-lite`: input=46428, output=654, total=47082

## Resolution
- status: accepted
- note: Round 4 (final spec round) accepted; no new material gaps beyond Codex's, which are folded in. Orchestrator convergence call: 4 rounds is past diminishing returns; carry remaining detail to plan.
