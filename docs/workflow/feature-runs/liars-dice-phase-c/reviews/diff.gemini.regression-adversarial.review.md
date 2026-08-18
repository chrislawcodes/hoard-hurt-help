---
reviewer: "gemini"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/implementation.diff.patch"
artifact_sha256: "21a6e1867fa60fbd9d93426a8e9b79381937718d29a344694ab38bacf336313c"
repo_root: "."
git_head_sha: "46399ff1a771d6dfe22d74004ce9f7422a6fba0d"
git_base_ref: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
git_base_sha: "ab0afa5788a8b6a8eef3c83ad594fc5de508848a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Automated diff review hit PARTIAL coverage — Gemini times out on the one giant diff (the no-CHECKPOINT-markers slicing failure). Compensated with orchestrator manual review: engine verified correct + non-circular ace test added; award_round showdown verified correct WITH resume-idempotency guard (showdown_resolved_hand, R5); hidden-info verified — public channels never read PlayerState.dice (grep-confirmed), only counts exposed. Partial Gemini findings assessed: (1) snapshot-None fail-open — UNVERIFIED hypothetical; (2) blacklist key-strip is brittle — valid design note, keys match today, logged as follow-up; (3) bot_move regression — disproven by the passing 232-line driver test that plays a seeded match to completion. No confirmed correctness bugs."
raw_output_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/diff.gemini.regression-adversarial.review.md.json"
narrowed_artifact_path: "docs/workflow/feature-runs/liars-dice-phase-c/reviews/diff.gemini.regression-adversarial.review.md.narrowed.txt"
narrowed_artifact_sha256: "f9dacf1f9d4e25d31a7e625eb24c805ab7f03a0723037f3ec9c6b5299cff4850"
coverage_status: "partial"
coverage_note: "artifact exceeded max_artifact_chars and was narrowed"
---

# Review: diff regression-adversarial

## Findings

### 1. [UNVERIFIED] High: Potential Silent Failure in `submit_action` Snapshot Merging
In `app/engine/agent_play.py`, the `submit_action` function calls `await module.validation_snapshot(db, game, player)`. If `snapshot` is `None` (or falsy), it silently skips merging into `built_move`. If the game module expects mandatory validation keys to be present for the move to be valid, `validate_move` will likely fail or produce inconsistent results when the snapshot is missing. The logic assumes that if `snapshot` is missing, the code can proceed with partial data, which may be a source of "silent fail-open" behavior if an implementation of `validation_snapshot` errors out and returns `None`.

### 2. Medium: Brittle Key Filtering Strategy in `submit_action`
The `internal_move` construction uses a blacklist (`_LD_VALIDATION_SNAPSHOT_KEYS`) to strip validation metadata before `record_submission` is called. This is a fragile design:
*   If a developer adds a new key to the snapshot but forgets to update `_LD_VALIDATION_SNAPSHOT_KEYS`, the validation metadata will be leaked into the permanent database record.
*   The blacklist approach is "fail-safe" only if you want to keep everything by default; here, it is intended to *strip* state, making the default failure mode (leaking data) potentially harmful to state integrity.

### 3. [UNVERIFIED] Medium: `turn_drivers.py` Logic Change without Regression Analysis
The change in `SequentialDriver` from `module.default_move` to `module.bot_move` represents a potential change in core engine behavior. If `module.bot_move` is not strictly guaranteed to provide a valid move in all scenarios where `default_move` previously did (or if it raises exceptions differently), this could cause bot matches to stall or crash. The artifact lacks evidence that `bot_move` preserves the failure-handling properties (like flagging `was_defaulted`) of the previous implementation.

## Residual Risks

*   **Validation Bypass:** Since `validate_move` receives a dictionary `built_move` that is heavily manipulated by merging/stripping logic, there is a risk of a "Man-in-the-Middle" style logic bug where the object inspected by `validate_move` is different from the object persisted to the database.
*   **State Bloat:** If the `MatchState` configuration JSON continues to grow (as seen in the `test_platform_admin_api` tests), the lack of a formal schema or migration path for these configs could lead to runtime errors when existing matches are loaded by updated game modules expecting a different config structure.

## Token Stats

- total_input=15706
- total_output=591
- total_tokens=16297
- `gemini-3.1-flash-lite`: input=15706, output=591, total=16297

## Resolution
- status: accepted
- note: Automated diff review hit PARTIAL coverage — Gemini times out on the one giant diff (the no-CHECKPOINT-markers slicing failure). Compensated with orchestrator manual review: engine verified correct + non-circular ace test added; award_round showdown verified correct WITH resume-idempotency guard (showdown_resolved_hand, R5); hidden-info verified — public channels never read PlayerState.dice (grep-confirmed), only counts exposed. Partial Gemini findings assessed: (1) snapshot-None fail-open — UNVERIFIED hypothetical; (2) blacklist key-strip is brittle — valid design note, keys match today, logged as follow-up; (3) bot_move regression — disproven by the passing 232-line driver test that plays a seeded match to completion. No confirmed correctness bugs.
