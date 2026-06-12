---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/user-roles/plan.md"
artifact_sha256: "2e35d63a44309f9aac8dc20c7cce4abebd0aa7cf0b7d91ec3797fb8042165327"
repo_root: "."
git_head_sha: "18e3ed1dd1af144bd37763816797d7062da1d0dd"
git_base_ref: "origin/claude/admin-regular-user-roles-cp0y4g"
git_base_sha: "046869a04043cb90943f6efe9c551d8d7bbc8aa8"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "F1 HIGH(revocation delay) accepted-by-design: settled decision — role seeded at login, demotion at next login; documented in spec Risks; PLATFORM_ADMIN_EMAILS is operator-controlled and only takes effect on redeploy/restart either way; immediate per-request revocation was not a requirement. F2 MED(legacy ADMIN_EMAILS = 2nd source) accepted-by-design: feature consumes settings.platform_admin_emails_set (incl. its existing ADMIN_EMAILS fallback) for BOTH the migration backfill and login seeding, so users.role exactly mirrors the prior allowlist semantics — no new divergence; deprecating ADMIN_EMAILS is a non-goal. F3 MED(email lowercase) accepted -> tasks.md: seed via user.email.lower() in settings.platform_admin_emails_set (the set is already lowercased; matches the deps.py/web_support.py convention). Residual(cap counts scheduled?) accepted -> tasks.md states the cap counts state IN (SCHEDULED, REGISTERING, ACTIVE) per spec FR-5. Residual(route/template wiring unverified) noted: those files do not exist yet; the diff checkpoint verifies wiring."
raw_output_path: "docs/workflow/feature-runs/user-roles/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

1. HIGH [CODE-CONFIRMED] The plan changes platform-admin auth from a per-request email check to a DB role that is only reseeded at login. That weakens revocation: today `require_platform_admin` checks the current allowlist on every request ([`app/deps.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/deps.py#L71)), but the plan moves the decision to `user.role` set during `sync_google_user` ([`app/routes/auth.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/routes/auth.py#L19)). A user removed from `PLATFORM_ADMIN_EMAILS` will keep platform-admin access until they sign out and authenticate again.

2. MEDIUM [CODE-CONFIRMED] The plan never fences off the legacy `ADMIN_EMAILS` fallback, so the new role system still has a second live admin source. `Settings.platform_admin_emails_set` currently falls back to `admin_emails` whenever `platform_admin_emails` is blank ([`app/config.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/config.py#L46)), and the plan proposes consuming that property both for migration backfill and login seeding. That means the rollout can still silently grant platform admin from a stale legacy env var, which contradicts the stated single-source-of-truth move.

3. MEDIUM [CODE-CONFIRMED] The login-seeding step omits the email normalization the rest of the code relies on. Current admin checks lowercase the stored email before comparing (`user.email.lower()` in [`app/deps.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/deps.py#L71) and [`app/routes/web_support.py`](/Users/chrislaw/hoard-hurt-help--user-roles/app/routes/web_support.py#L47)), but the plan describes `user.role = ADMIN if email in settings.platform_admin_emails_set else USER` without stating a lowercase step. If Google returns a differently cased address, the user can be misclassified and lose platform-admin access.

## Residual Risks

- The cap semantics are still underspecified in the plan. It says `USER_ACTIVE_MATCH_LIMIT` should gate creation, but it does not define whether scheduled-but-not-started matches count toward that limit.
- I did not verify the new route/template wiring because those files are not present in the provided code, so registration or form-action drift is still possible.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: F1 HIGH(revocation delay) accepted-by-design: settled decision — role seeded at login, demotion at next login; documented in spec Risks; PLATFORM_ADMIN_EMAILS is operator-controlled and only takes effect on redeploy/restart either way; immediate per-request revocation was not a requirement. F2 MED(legacy ADMIN_EMAILS = 2nd source) accepted-by-design: feature consumes settings.platform_admin_emails_set (incl. its existing ADMIN_EMAILS fallback) for BOTH the migration backfill and login seeding, so users.role exactly mirrors the prior allowlist semantics — no new divergence; deprecating ADMIN_EMAILS is a non-goal. F3 MED(email lowercase) accepted -> tasks.md: seed via user.email.lower() in settings.platform_admin_emails_set (the set is already lowercased; matches the deps.py/web_support.py convention). Residual(cap counts scheduled?) accepted -> tasks.md states the cap counts state IN (SCHEDULED, REGISTERING, ACTIVE) per spec FR-5. Residual(route/template wiring unverified) noted: those files do not exist yet; the diff checkpoint verifies wiring.
