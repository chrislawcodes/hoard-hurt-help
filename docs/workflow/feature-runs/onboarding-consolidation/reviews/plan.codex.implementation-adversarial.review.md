---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/onboarding-consolidation/plan.md"
artifact_sha256: "30a79288f38ea0e686092a7937d88b641d5d84e38667822720320926e282bdab"
repo_root: "."
git_head_sha: "1a01e42a1a8463da36ca499c5ca9f5429f07d41a"
git_base_ref: "origin/main"
git_base_sha: "26344e20132ea647198d2fac86cfa4cb4b6ea2f9"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Manual sub-agent implementation pass; findings folded into plan (cascade order, query bound, HTMX endpoints)."
raw_output_path: ""
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: "Verified the 3-predicate wrap, non-MCP fallbacks, nav query cost, slice write-sets, and all cited line numbers."
---

# Review: plan implementation-adversarial (manual sub-agent fallback)

Verdict: **PASS-WITH-CHANGES** — architecture is sound (thin wrappers, no new
module, correct reuse); two real gaps fixed below. Code claims verified ACCURATE
(only off-by-one: `agents_detail` is `:139` not `:138`; `populate_nav_cta` is
skipped for HX requests at `nav_context.py:152` — now stated in the plan).

## Findings

1. **[MAJOR] Nav multi-agent query cost asserted-away.** `provider_readiness` runs up to 3 SQL predicates; a naive most-ready loop is up to 3·K queries on `compute_nav_cta`, a hot path. `[CODE-CONFIRMED]` → **Resolved:** AD-4 now mandates provider-dedup + early-exit on the caller's `require` bar (common ready user ≈1 query); slice 2 adds a per-page query-bound test; added as a residual risk with verification.

2. **[MAJOR] AD-3 ladder ill-defined for non-MCP providers (hermes/openclaw).** Their predicates fall back to liveness-free / `last_seen_at` checks, so `LIVE` (fresh `last_polled_at`) can coexist with `provider_has_live_current_setup`=False — breaking a "definitional AND" form. `[CODE-CONFIRMED]` → **Resolved:** AD-3 rewritten as a **top-down cascade, first match wins** (LIVE→SEEN_NOT_POLLING→CONNECTED_NOT_LIVE→NO_MCP_CONNECTION); slice 1 adds non-MCP boundary tests incl. stale-seen-but-polling.

3. **[MINOR] Two HTMX poll sub-endpoints not in slice 4's write set.** `connections_pages.live_status_fragment` (`:219`) and `web_player.seat_connect_status` (`:649`) call the reassigned predicates; changing only the page-load forward re-creates the split. `[CODE-CONFIRMED]` → **Resolved:** slice 4 now names both load-path (`:156-159`) and poll-path (`:216-227`) plus `:649`.

4. **[MINOR] Slice 2 mislabeled "foundation".** It ships the nav ⚠ ready-bar swap, a live behavior change. → **Resolved:** slice 2 relabeled "ships the nav ⚠ change."

## Residual Risks
- Cross-cutting redirect changes; covered by per-entry-point Location tests + invariant. *verification:* plan Residual Risks.
- All MAJOR/MINOR items above carry verification lines in the plan.

## Resolution
- status: accepted
- note: Manual sub-agent implementation pass; findings folded into plan (cascade order, query bound, HTMX endpoints).
