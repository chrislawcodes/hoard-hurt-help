---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/onboarding-consolidation/plan.md"
artifact_sha256: "30a79288f38ea0e686092a7937d88b641d5d84e38667822720320926e282bdab"
repo_root: "."
git_head_sha: "1a01e42a1a8463da36ca499c5ca9f5429f07d41a"
git_base_ref: "origin/main"
git_base_sha: "26344e20132ea647198d2fac86cfa4cb4b6ea2f9"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Manual sub-agent testability pass; findings folded into plan test lists + verifications."
raw_output_path: "docs/workflow/feature-runs/onboarding-consolidation/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: "Verifiability of residual-risk checks and per-⚠-site test coverage."
---

# Review: plan testability-adversarial (manual sub-agent fallback)

Verdict: **PASS-WITH-CHANGES** — the verification lines exist but several were
not meaningfully testable, and several ⚠ sites had no named test.

## Findings

1. **[MINOR] The "grep body for `select(`" no-7th-predicate check is circumventable** (queries are in callees). → **Resolved:** replaced with a `before_cursor_execute` counter asserting ≤3 queries (slice 1 + residual risk).
2. **[MAJOR] No non-MCP (hermes/openclaw) boundary tests** — load-bearing after the cascade-order fix. → **Resolved:** slice 1 adds them incl. stale-seen-but-polling.
3. **[MINOR] `/play` ⚠ change has no named redirect test.** → **Resolved:** slice 3 names a `/play` Location test.
4. **[MINOR] agent-list (13) and agent-detail (14) ⚠ swaps have no named tests.** → **Resolved:** slice 4 names before/after badge tests for both.
5. **[MINOR] `confirm_seat_if_live` parity test underspecified** (one happy path). → **Resolved:** slice 4 + residual risk require agreement across all four states incl. non-MCP.
6. **[MINOR] Exclusions (`provider IS NULL` / `kind=bot` / `archived_at`) and mixed-provider reduction untested.** → **Resolved:** slice 2 names both.

## Residual Risks
- The `origin/main` loop-guard reproduction is concrete and runnable (worktree + `scripts/agent-worktree.sh` present). *verification:* plan Residual Risks.

## Resolution
- status: accepted
- note: Manual sub-agent testability pass; findings folded into plan test lists + verifications.
