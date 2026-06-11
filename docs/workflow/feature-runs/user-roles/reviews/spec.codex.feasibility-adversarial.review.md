---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/user-roles/spec.md"
artifact_sha256: "b8163e40f086083b548f50b860678e21c29d3263876d813f55e85e371479bd20"
repo_root: "."
git_head_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
git_base_ref: "origin/claude/admin-regular-user-roles-cp0y4g"
git_base_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Round 4 (converged). F1(email unique conflict) deferred-to-plan: email refresh must guard the users.email unique constraint (skip+log on collision; google_sub is the real key) — implementation detail for the plan. F2(backfill env parity) accepted: already a Risks verification item (confirm PLATFORM_ADMIN_EMAILS present at Alembic runtime; note legacy ADMIN_EMAILS fallback when asserting promoted row count). F3(admins cancel ACTIVE only via new route) accepted-by-design: the new role-based /matches/{id}/cancel IS the admin cancel path and the dashboard retargets to it; legacy /api/admin + /api/game-admin cancel stay 409-on-ACTIVE unchanged per AC-7. Residuals(cascade order unverifiable here, env parity) carried to plan checkpoint where admin_web.py + the shared helper are in-context."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

| Severity | Finding | Evidence |
|---|---|---|
| Medium | Refreshing `User.email` on login needs an explicit conflict policy. `users.email` is unique, and the current login sync only looks up by `google_sub` and fills in names; it never updates email. If the Google account now maps to an email already used by another row, the login-time update the spec requires can fail with an integrity error instead of cleanly reseeding role. [CODE-CONFIRMED] | [`app/models/user.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/models/user.py), [`app/routes/auth.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/routes/auth.py) |
| Medium | The admin-role backfill is environment-sensitive in a way the spec does not fully pin down. `Settings.platform_admin_emails_set` is derived from the live process environment and still falls back to legacy `ADMIN_EMAILS`; a migration run with the wrong env snapshot will seed the wrong roles, and the fallback can promote a broader set than the spec’s new allowlist contract intends. [CODE-CONFIRMED] | [`app/config.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/config.py) |
| Medium | The spec’s “admins can cancel any match in any state” promise is only realized through the new route/template retarget. The existing admin cancel endpoints still reject `ACTIVE`, so any direct API caller or unretargeted admin surface will keep getting the old 409 behavior. [CODE-CONFIRMED] | [`app/routes/admin_api.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/routes/admin_api.py), [`app/routes/game_admin_web.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/routes/game_admin_web.py) |

## Residual Risks

- The delete-cascade extraction cannot be audited here because `app/routes/admin_web.py` and the proposed shared deletion helper were not provided. Preserving the scheduler-stop-first ordering is therefore unverified.
- The migration/backfill behavior still depends on deploy-time env parity. If the allowlist is missing or stale when Alembic runs, role seeding can be wrong even if the implementation matches the spec.
- I could not verify the template wiring for the new create/delete controls because the relevant template files were not included in the provided code.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 4 (converged). F1(email unique conflict) deferred-to-plan: email refresh must guard the users.email unique constraint (skip+log on collision; google_sub is the real key) — implementation detail for the plan. F2(backfill env parity) accepted: already a Risks verification item (confirm PLATFORM_ADMIN_EMAILS present at Alembic runtime; note legacy ADMIN_EMAILS fallback when asserting promoted row count). F3(admins cancel ACTIVE only via new route) accepted-by-design: the new role-based /matches/{id}/cancel IS the admin cancel path and the dashboard retargets to it; legacy /api/admin + /api/game-admin cancel stay 409-on-ACTIVE unchanged per AC-7. Residuals(cascade order unverifiable here, env parity) carried to plan checkpoint where admin_web.py + the shared helper are in-context.
