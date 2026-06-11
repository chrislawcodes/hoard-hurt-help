---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/user-roles/spec.md"
artifact_sha256: "b8163e40f086083b548f50b860678e21c29d3263876d813f55e85e371479bd20"
repo_root: "."
git_head_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
git_base_ref: "origin/claude/admin-regular-user-roles-cp0y4g"
git_base_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 4 (converged). HIGH1(delete-cascade winner_player_id order) accepted-as-documented: spec mandates verbatim extraction (no rewrite) + a shared-delete regression test; existing test_admin_delete_completed_match_with_winner already pins the order. HIGH2(_next_match_id full-scan perf) accepted-correctness/deferred-perf: IntegrityError retry covers PK-collision correctness; full-scan perf is a pre-existing small-scale concern (hundreds of matches) — a sequence/autoincrement redesign is out of scope. MED1(migration interruption/env) accepted: one Alembic revision = one transaction (column add + UPDATE atomic); env-presence is a Risks verification item. MED2(hybrid platform-role/game-email admin) accepted-by-design: game-admin email mechanism is an explicit non-goal; chrome shows platform vs game controls independently as today. LOW(email change vs GAME_ADMIN_EMAILS) accepted-edge: game-admin allowlist is operator-managed env (non-goal); a rare email change needs an env update, same as today."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

### HIGH Severity

*   **[CODE-CONFIRMED] Potential Data Loss in `match_deletion.py` extraction:** The spec proposes extracting the deletion cascade from `admin_web.py` to `match_deletion.py`. In `test_admin.py`, the `test_admin_delete_completed_match_with_winner` test reveals that the delete operation is highly sensitive to the order of clearing foreign keys (`winner_player_id` on the `Match` table). If the extraction inadvertently changes the order of operations—such as deleting players before nulling the `winner_player_id` reference—it will violate database integrity constraints.
*   **[CODE-CONFIRMED] Race Condition in `_next_match_id`:** The spec mentions a `max + 1` ID allocation strategy. `arena.py` (`_next_match_id`) currently performs a full table scan (`select(Match.id)`) to find the maximum ID. With the proposed transition to a user-facing match creation flow (FR-4), this will become a massive performance bottleneck and a source of extreme contention/race conditions as the `Match` table grows. The proposal to "harden" this with `IntegrityError` retries does not solve the underlying performance issue of full table scans on every match creation.

### MEDIUM Severity

*   **[UNVERIFIED] Migration Backfill Risk:** FR-1 proposes a post-migration SQL `UPDATE` in the same revision to backfill `role='admin'`. If the migration is interrupted between adding the column (with `server_default='user'`) and the `UPDATE`, current platform admins will be demoted until their next login. The spec assumes the environment variable is available at migration time; if the environment configuration is inconsistent during deployment, this could lead to widespread admin lockouts.
*   **[CODE-CONFIRMED] Ambiguity in `_is_any_admin` for Role Transitions:** The spec proposes updating `_is_any_admin` (`app/routes/web_support.py`) to check `user.role` for platform admins, but notes that game-admin checks (email-based) remain untouched. This creates a hybrid authorization state where a user might be considered an "admin" for one UI segment (via role) and not another (via email). The spec does not address the UX consequences of this partial-admin state in the UI chrome.

### LOW Severity

*   **[CODE-CONFIRMED] Email Refresh Logic at Login:** FR-1 proposes refreshing `User.email` from Google identity on every login. If a user changes their Google email, this could potentially break existing `GAME_ADMIN_EMAILS__*` configurations that explicitly rely on the old email, as there is no mechanism to propagate email changes to the environment-based game admin allowlists.

## Residual Risks

*   **Consistency Gap:** The hybrid approach to authorization (Role-based for Platform Admins, Email-based for Game Admins) increases the surface area for logic errors. It is likely that future developers will inadvertently use the wrong check for the wrong context.
*   **Contention/Performance:** As match creation scales to regular users, the `max + 1` ID allocation pattern (even with retries) will likely cause significant latency spikes, as it relies on an increasingly large `SELECT` scan and high write contention on the `matches` table index.
*   **Operator UX:** Because there is no UI to manage user roles or email allowlists (as per "Non-goals"), administrators are effectively tethered to the static environment variables. If an environment is updated without a redeploy or if there's a drift between production and the source of truth, there is no in-application way to troubleshoot role-based access failures.

## Token Stats

- total_input=45192
- total_output=917
- total_tokens=88332
- `gemini-3.1-flash-lite`: input=45192, output=917, total=88332

## Resolution
- status: accepted
- note: Round 4 (converged). HIGH1(delete-cascade winner_player_id order) accepted-as-documented: spec mandates verbatim extraction (no rewrite) + a shared-delete regression test; existing test_admin_delete_completed_match_with_winner already pins the order. HIGH2(_next_match_id full-scan perf) accepted-correctness/deferred-perf: IntegrityError retry covers PK-collision correctness; full-scan perf is a pre-existing small-scale concern (hundreds of matches) — a sequence/autoincrement redesign is out of scope. MED1(migration interruption/env) accepted: one Alembic revision = one transaction (column add + UPDATE atomic); env-presence is a Risks verification item. MED2(hybrid platform-role/game-email admin) accepted-by-design: game-admin email mechanism is an explicit non-goal; chrome shows platform vs game controls independently as today. LOW(email change vs GAME_ADMIN_EMAILS) accepted-edge: game-admin allowlist is operator-managed env (non-goal); a rare email change needs an env update, same as today.
