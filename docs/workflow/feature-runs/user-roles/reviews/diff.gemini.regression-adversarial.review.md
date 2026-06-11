---
reviewer: "gemini"
lens: "regression-adversarial"
stage: "diff"
artifact_path: "docs/workflow/feature-runs/user-roles/reviews/implementation.diff.patch"
artifact_sha256: "1df73f3c0687cf57ea36b09e7033f5b158d0b5e0ee6f81d005ef59881f23f3bd"
repo_root: "."
git_head_sha: "5a44eea2e004e72e2cfa0b2a8a37f6f16e45a9bf"
git_base_ref: "d310e23a837203803da0690e4f88cf0cfd720768"
git_base_sha: "d310e23a837203803da0690e4f88cf0cfd720768"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Slice 3b (shared delete cascade + cancel). All findings non-actionable. F1(delete_match atomicity) rejected/misread: the sequential db.execute(delete...) calls run in ONE implicit AsyncSession transaction committed atomically by the single db.commit() at the end — a mid-way exception never commits, so it rolls back, no partial state. Code moved VERBATIM from the proven admin_web route. F2(scheduler-teardown race) accept-as-documented: the two-pass submission sweep is the settled design for the cooperative-cancellation window (the in-code comment explains it). F3(no row-count verification) accept: original best-effort cascade behavior, the Match row delete is authoritative; out of scope. Also added cancel_match + converged 3 cancel sites (behavior-preserving, AC-7). Preflight green (650 tests). Expansion workaround applied."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/diff.gemini.regression-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: diff regression-adversarial

## Findings

1.  **[UNVERIFIED] Database Atomicity and Integrity Risk (High):** The `delete_match` function in `app/engine/match_deletion.py` executes multiple `await db.execute(...)` calls sequentially across many tables. Because these are individual statements without an explicit transaction block (e.g., `async with db.begin():`), any exception occurring mid-execution will leave the database in a partially deleted, inconsistent state. This breaks referential integrity and could lead to "dangling" records (e.g., orphans in `TurnSubmission` if `Player` deletion fails).

2.  **[UNVERIFIED] Race Condition in Scheduler Teardown (Medium):** In `delete_match`, `registry.stop(match_id)` is called before executing any deletions. If a running match scheduler task is still processing in an event loop iteration, it could potentially initiate further database writes *after* `registry.stop()` has returned but *before* the deletion of `Match` is finalized. While the code attempts a "second pass" to catch late writes, this relies on a non-deterministic timing window rather than a robust guard against concurrent modification during destruction.

3.  **[UNVERIFIED] Silently Dropped Records (Low):** `delete_match` performs wide `DELETE` operations based on `match_id` or `player_id`. If `registry.stop()` fails to stop a task or if an external process creates new related records during the deletion sequence, these records are silently orphaned or ignored. The function returns `None` (success) regardless of whether the expected rows were actually deleted, offering no audit trail or verification that the teardown was successful.

## Residual Risks

*   **Partial Cleanup Failures:** Since there is no atomicity, manual intervention via a database console will likely be required to fix corrupted state if the deletion process is interrupted.
*   **Inconsistent Views:** Admin web/API interfaces may temporarily display broken links or invalid game states if they perform lookups while the multi-step `delete_match` process is mid-execution, as the state is not isolated from concurrent readers.

## Token Stats

- total_input=1088
- total_output=453
- total_tokens=15151
- `gemini-3.1-flash-lite`: input=1088, output=453, total=15151

## Resolution
- status: accepted
- note: Slice 3b (shared delete cascade + cancel). All findings non-actionable. F1(delete_match atomicity) rejected/misread: the sequential db.execute(delete...) calls run in ONE implicit AsyncSession transaction committed atomically by the single db.commit() at the end — a mid-way exception never commits, so it rolls back, no partial state. Code moved VERBATIM from the proven admin_web route. F2(scheduler-teardown race) accept-as-documented: the two-pass submission sweep is the settled design for the cooperative-cancellation window (the in-code comment explains it). F3(no row-count verification) accept: original best-effort cascade behavior, the Match row delete is authoritative; out of scope. Also added cancel_match + converged 3 cancel sites (behavior-preserving, AC-7). Preflight green (650 tests). Expansion workaround applied.
