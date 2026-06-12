---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-user-management/spec.md"
artifact_sha256: "0446541506708c9815c2b2eda19de6bc4766c9dc6d6fbe3337121ccf9c34d765"
repo_root: "."
git_head_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
git_base_ref: "origin/main"
git_base_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Implementation-level, resolved in plan.md. MEDIUM admin JSON APIs + 303: same content-negotiation fix (plan Decision 2) covers admin_api/game_admin_api. MEDIUM login ordering: plan specifies disabled check happens before establishing the session (sync is additive/harmless but no session is set for disabled users). LOW ADMIN_EMAILS fallback: plan clarifies the floor = resolved platform_admin_emails_set (includes legacy fallback) and FR-017 warns on the RESOLVED set being empty."
raw_output_path: "docs/workflow/feature-runs/admin-user-management/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- [CODE-CONFIRMED] Medium: The spec’s disabled-auth matrix is incomplete for JSON admin APIs. `require_platform_admin` in [`app/deps.py`](/Users/chrislaw/hoard-hurt-help/app/deps.py) is used by both [`app/routes/admin_api.py`](/Users/chrislaw/hoard-hurt-help/app/routes/admin_api.py) and [`app/routes/game_admin_api.py`](/Users/chrislaw/hoard-hurt-help/app/routes/game_admin_api.py). If `require_user` starts issuing a 303 redirect to `/disabled`, these APIs will return HTML redirects instead of structured API errors, which conflicts with the spec’s “content-appropriate” auth behavior.
- [CODE-CONFIRMED] Medium: The login rejection path is underspecified and can still write persistent user state. [`app/routes/auth.py`](/Users/chrislaw/hoard-hurt-help/app/routes/auth.py) calls `sync_google_user()` and commits before it sets the session, so if the disabled check is added after that point, a rejected login can still update email/name and re-assert admin role from config. The spec needs to say the disabled check happens before any sync/commit, or explicitly accept those side effects.
- [CODE-CONFIRMED] Low: The “immutable bootstrap floor” story ignores the legacy fallback in [`app/config.py`](/Users/chrislaw/hoard-hurt-help/app/config.py). `platform_admin_emails_set` still uses `platform_admin_emails or admin_emails`, so an environment that still carries `ADMIN_EMAILS` can silently keep an admin floor even when `PLATFORM_ADMIN_EMAILS` is empty. That makes FR-017’s startup warning and FR-010’s revocation semantics ambiguous unless the spec explicitly retires or incorporates the fallback.

## Residual Risks

- The spec still depends on a clean env-var transition away from the legacy admin list. If that rollout is not coordinated, operators can misread who is actually protected by the floor.
- Disabled-user behavior needs explicit tests on both HTML and JSON surfaces, because the same auth dependency is shared across them and the response shape differs by caller.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Implementation-level, resolved in plan.md. MEDIUM admin JSON APIs + 303: same content-negotiation fix (plan Decision 2) covers admin_api/game_admin_api. MEDIUM login ordering: plan specifies disabled check happens before establishing the session (sync is additive/harmless but no session is set for disabled users). LOW ADMIN_EMAILS fallback: plan clarifies the floor = resolved platform_admin_emails_set (includes legacy fallback) and FR-017 warns on the RESOLVED set being empty.
