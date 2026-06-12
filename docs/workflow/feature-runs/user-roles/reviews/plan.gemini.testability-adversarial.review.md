---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/user-roles/plan.md"
artifact_sha256: "2e35d63a44309f9aac8dc20c7cce4abebd0aa7cf0b7d91ec3797fb8042165327"
repo_root: "."
git_head_sha: "18e3ed1dd1af144bd37763816797d7062da1d0dd"
git_base_ref: "origin/claude/admin-regular-user-roles-cp0y4g"
git_base_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "F1(ID race / missed caller) accepted-as-documented: reuse-report's #1 trap; plan mandates all five allocators converge on match_creation.py; tasks.md enumerates each caller's refactor + a per-path test that the creator is recorded (proving it routes through the helper). F2(cascade extraction thread-safety) accepted-as-documented: verbatim move (no rewrite) + Slice 3b regression test reproducing the in-flight-submission / second-sweep scenario; same logic regardless of caller. F3(email unique constraint) accepted: plan already guards (skip+log on collision); tasks.md adds the lowercase + the collision-skip test. Residual1(NOT NULL FK on existing rows) rejected/misread: created_by_user_id is NULLABLE by design — existing matches stay NULL, no default or data-migration needed. Residual2(ghost/over-privileged backfill) clarified: the backfill UPDATE only touches existing rows whose email is in the allowlist; a stale/typo email is a no-op (that admin is promoted at next login via sync), not a ghost admin or a lockout; the promoted-count verification catches a mismatch."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1. **Unprotected Match-ID Allocation Race**
   The plan relies on an `IntegrityError` retry loop for matching IDs, but the underlying allocator scans `Match.id` for `max+1`. This is a classic "Time-of-check to time-of-use" (TOCTOU) vulnerability that remains non-atomic. While the plan claims this is mitigated by the retry, it assumes all five callers converge perfectly on the new helper. If any legacy code path is missed during refactoring, that path will continue to generate collisions, potentially stalling creation flows across the platform.
   [CODE-CONFIRMED] — `app/engine/arena.py` uses `max+1` logic currently.

2. **Insecure Deletion-Cascade Extraction**
   Moving the deletion cascade "verbatim" into a shared helper (`app/engine/match_deletion.py`) assumes the existing cascade is thread-safe and exhaustive. The plan admits this logic exists because the scheduler task may write rows during cancellation. If the scheduler task's write-timing changes or if a new race condition is introduced by abstracting this into a generic helper called from multiple contexts (User/Admin), the "verbatim" move could result in orphaned rows or failed constraints (e.g., trying to delete a player before the final turn submission is reaped).
   [UNVERIFIED] — Logic dependency on `scheduler.py` task state is implied but context on scheduler task lifecycle inside the cascade is insufficient.

3. **Incomplete Role-Sync Constraint Handling**
   The plan defines `sync_google_user` to refresh `user.email` from `userinfo.email` and update the role. This fails if a user has changed their Google account email but their original email remains associated with another record in the database, potentially leading to identity merging issues or accidental privilege escalation if the unique constraint is bypassed or mismanaged during the update.
   [UNVERIFIED] — User identity management logic needs deeper audit against the `users.email` unique constraint.

## Residual Risks

1. **Migration Failure on SQLite Foreign Keys**
   The plan acknowledges the need for `batch_alter_table` for FK addition on SQLite. However, if the `users` table already contains rows, the `Match` table's new column `created_by_user_id` cannot be `NOT NULL` without a default or a previous data-migration step. If the migration logic is not perfectly robust regarding the existing state of the `Match` table, it will fail to apply, potentially bricking the deployment environment until manual database intervention occurs.

2. **Over-privileged Admin Backfill**
   The plan proposes backfilling `role='admin'` for all emails in `PLATFORM_ADMIN_EMAILS` at upgrade time. This assumes that the list is clean and that every email in that list *actually* belongs to a valid user account currently in the system. Any stale or mistyped email in the config will fail to map to a user, potentially creating a "ghost" admin situation where configured admins find themselves locked out of platform-admin features post-upgrade.

## Token Stats

- total_input=26039
- total_output=664
- total_tokens=26703
- `gemini-3.1-flash-lite`: input=26039, output=664, total=26703

## Resolution
- status: accepted
- note: F1(ID race / missed caller) accepted-as-documented: reuse-report's #1 trap; plan mandates all five allocators converge on match_creation.py; tasks.md enumerates each caller's refactor + a per-path test that the creator is recorded (proving it routes through the helper). F2(cascade extraction thread-safety) accepted-as-documented: verbatim move (no rewrite) + Slice 3b regression test reproducing the in-flight-submission / second-sweep scenario; same logic regardless of caller. F3(email unique constraint) accepted: plan already guards (skip+log on collision); tasks.md adds the lowercase + the collision-skip test. Residual1(NOT NULL FK on existing rows) rejected/misread: created_by_user_id is NULLABLE by design — existing matches stay NULL, no default or data-migration needed. Residual2(ghost/over-privileged backfill) clarified: the backfill UPDATE only touches existing rows whose email is in the allowlist; a stale/typo email is a no-op (that admin is promoted at next login via sync), not a ghost admin or a lockout; the promoted-count verification catches a mismatch.
