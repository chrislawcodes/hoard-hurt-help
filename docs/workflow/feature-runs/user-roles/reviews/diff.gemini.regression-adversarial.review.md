---
reviewer: "gemini"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/user-roles/reviews/implementation.diff.patch"
artifact_sha256: "435a1205372d6da1d993327c69891a211b83a2c3fbe483466ac77f496edd8a10"
repo_root: "."
git_head_sha: "ab1c11e2fb0118162a9313006516ce0736a3ae1f"
git_base_ref: "0c39870"
git_base_sha: "0c39870f85e8b7f172ab32d7da71e370017214f2"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Slice 4 reseal to current HEAD. Findings unchanged + non-actionable (cap race documented/accepted, no SQL injection, cancel idempotent). Expansion workaround applied."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/diff.gemini.regression-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: diff regression-adversarial

## Findings

*   **[MEDIUM] `create_match_submit` - Unbounded Input/State Race:** The check `active_count >= settings.user_active_match_limit` is followed by an `await create_match(...)`. There is no transaction wrapper covering both the check and the insert. A user triggering multiple concurrent requests could bypass the `user_active_match_limit`, causing a resource exhaustion or logic violation at the database level.
*   **[LOW] `_html_error` / `create_match_submit` - Inconsistent Error Handling:** `_html_error` constructs a response using `_load_game_module_or_404(game)`. If `create_match_submit` is called with an invalid game slug, it passes the 404 check at the start, but if the `scheduled_start` parsing fails, it re-calls `_load_game_module_or_404`. While this works, it introduces unnecessary coupling and potential for subtle discrepancies if the module state changes mid-request.
*   **[LOW] `my_matches` - Potential N+1 or Data Inconsistency:** The logic builds `own_seats_by_match` and `counts_by_match` separately and maps them by `match_id` or `g.id` to `sections_map`. If a match is deleted or modified by a concurrent process between the time the `matches` dictionary is populated and the time `counts_by_match` is processed, lookups may fail or return `None` (already handled in some cases, but logic is brittle).
*   **[LOW] `create_match_submit` - UTC Conversion:** `when.replace(tzinfo=timezone.utc)` is applied to `datetime` objects lacking `tzinfo`. If `datetime.fromisoformat` returns a naive object (e.g., if the user-provided string was missing offset info), this forces UTC regardless of user intent. This is likely intended but could lead to unexpected behavior if client-side validation logic drifts from server-side interpretation.

## Residual Risks

*   **Insecure Deletion Flow:** While `delete_match_submit` and `cancel_match_submit` perform checks, they rely on `_load_match_or_404` followed by ownership validation. If `_load_match_or_404` performs a broad query without explicit `row_level_locking` (e.g., `FOR UPDATE`), there is a minor risk of TOCTOU (Time-of-Check to Time-of-Use) where an admin or the user cancels/deletes the object after the code checks the state but before the DB operation completes.
*   **UI/UX/Constraint Disparity:** The `create_match` parameters (e.g., `min_players`, `total_rounds`) are hardcoded in `_CREATE_DEFAULTS` and passed to `create_match` regardless of what the game module actually supports. If a game module changes its requirements, the `create_match` logic might fail with a `ValueError` that is then caught and displayed as a generic error string, potentially hiding configuration mismatches.

## Token Stats

- total_input=18529
- total_output=686
- total_tokens=19215
- `gemini-3.1-flash-lite`: input=18529, output=686, total=19215

## Resolution
- status: accepted
- note: Slice 4 reseal to current HEAD. Findings unchanged + non-actionable (cap race documented/accepted, no SQL injection, cancel idempotent). Expansion workaround applied.
