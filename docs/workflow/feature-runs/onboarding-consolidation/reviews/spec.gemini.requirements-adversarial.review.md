---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/onboarding-consolidation/spec.md"
artifact_sha256: "3e328a03975eabb1442ec5b86115fcc8bc898b617f4d74922096ae4f1484540b"
repo_root: "."
git_head_sha: "ca89008ead36657078d59cc78df679fbf38fe0dc"
git_base_ref: "origin/main"
git_base_sha: "26344e20132ea647198d2fac86cfa4cb4b6ea2f9"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Manual sub-agent requirements pass (gemini CLI unavailable); all findings folded into spec v2."
raw_output_path: "docs/workflow/feature-runs/onboarding-consolidation/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: "Completeness of entry-point coverage, testability of acceptance criteria, and enumeration of intentional vs silent behavior changes."
---

# Review: spec requirements-adversarial (manual sub-agent fallback)

Verdict: **PASS-WITH-CHANGES** — the spec's thesis ("one signal answers it
everywhere") was undercut by missing call sites and undefined edge behavior;
both closed in v2.

## Findings

1. **[MAJOR] Completeness: not all entry points that re-derive the ladder were covered.** The spec claimed "four predicates across these sites" but scoped only 6 of ~14 actual sites. Leaving `connections_pages.py`, `seat_hold.py`, `agents_list.py`, `agents_detail.py` on old predicates re-creates the drift. `[CODE-CONFIRMED]` → **Resolved in v2:** disagreement table expanded to 14 sites; all brought into scope or justified.

2. **[MAJOR] Testability: the acceptance criteria were missing the failure-mode tests.** No criterion forced a loop-guard or a multi-agent-reduction test. → **Resolved in v2:** criteria 4 (multi-agent reduction tested) and 6 (`/play ⇄ /me/connections` loop-guard with a seen-but-not-polling fixture) added; each readiness boundary (incl. PAUSED) gets a unit test.

3. **[MAJOR] #444 made the disagreement table stale (predicate count 3 → 6).** The spec described pre-#444 state; PR #444 doubled the readiness predicates and split the seat pages (`provider_has_current_setup`) from the connections page (`provider_has_live_current_setup`). `[CODE-CONFIRMED]` → **Resolved in v2:** table refreshed to post-#444 reality; the #444 split is named as the live redirect-loop risk; `mode_a_at → mcp_connected_at` (migration 0038) noted.

4. **[MINOR] Unflagged behavior change at post-login.** The destination shift `/me/agents` (list) → `/me/agents/new` (create) was in the ⚠ table but never posed as a yes/no for the human. → **Resolved in v2:** raised as a settled decision (lands on `/me/agents/new`).

5. **[MINOR] `/play` routing was the only surfaced open decision; others were hiding.** Multi-agent reduction, PAUSED placement, and the connect-page bar were product-visible decisions presented as plan-detail. → **Resolved in v2:** all five surfaced and answered in "Resolved decisions."

## Residual Risks

- Non-goal "do not touch `deps.py`" vs routing the handle gate through the resolver — reconciled in v2 §3 (deps.py keeps the handle gate). *verification:* §3 states the ownership split explicitly.
- A paused-only provider keeps a misleading CTA (accepted limitation). *verification:* spec Residual limitation carries a unit test asserting `CONNECTED_NOT_LIVE`.

## Resolution
- status: accepted
- note: Manual sub-agent requirements pass (gemini CLI unavailable); all findings folded into spec v2.
