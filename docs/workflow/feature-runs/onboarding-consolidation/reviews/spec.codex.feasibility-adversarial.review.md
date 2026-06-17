---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/onboarding-consolidation/spec.md"
artifact_sha256: "3e328a03975eabb1442ec5b86115fcc8bc898b617f4d74922096ae4f1484540b"
repo_root: "."
git_head_sha: "ca89008ead36657078d59cc78df679fbf38fe0dc"
git_base_ref: "origin/main"
git_base_sha: "26344e20132ea647198d2fac86cfa4cb4b6ea2f9"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Manual sub-agent feasibility pass (codex CLI unavailable); all findings folded into spec v2."
raw_output_path: ""
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: "Sub-agent read connection_health.py, nav_context.py, auth.py, agents_create.py, web_player.py, web_games_catalog.py, deps.py, connections_pages.py, seat_hold.py, agents_list.py, agents_detail.py and verified the disagreement-table line refs."
---

# Review: spec feasibility-adversarial (manual sub-agent fallback)

Verdict: **PASS-WITH-CHANGES** — design is sound; the predicate collapse and the
multi-agent resolver were the likely hiding spots and both needed tightening.
Every code claim in the spec's disagreement table was verified ACCURATE against
the live source (only cosmetic note: `has_ai_agent` is an inline check in
`web_player.py:186`, not a named predicate).

## Findings

1. **[MAJOR] A `require_live` boolean cannot express the readiness bars actually in use.** The live code has **three** bars, not two: `provider_has_current_setup` (MCP-recent), `provider_has_live_current_setup`/`provider_is_covered` ("seen now"), and `provider_loop_running` ("polling now"). Collapsing to one boolean drops the "seen now" rung that `connections_pages.py:132,219` depends on for its auto-forward. `[CODE-CONFIRMED]` → **Resolved in v2:** replaced with a 4-state `ProviderReadiness` signal (`NO_MCP_CONNECTION`/`CONNECTED_NOT_LIVE`/`SEEN_NOT_POLLING`/`LIVE`) and a `require: OnboardingStage` threshold.

2. **[MAJOR] Global-intent multi-agent behavior undefined.** With no `target_agent` (nav/`/play`/post-login) the resolver returns one `(stage, next_url)` for a user with several agents at different rungs; the spec never said how to reduce. `[CODE-CONFIRMED]` → **Resolved in v2:** explicit most-ready reduction rule + acceptance criterion + test (also a settled decision).

3. **[MAJOR] Four affected call sites were out of scope.** `connections_pages.py`, `seat_hold.py:56`, `agents_list.py:54`, `agents_detail.py:139` consume these predicates and would re-create the drift if left alone. `[CODE-CONFIRMED]` → **Resolved in v2:** all four added to scope + the disagreement table (sites 11–14).

4. **[MINOR] `NEEDS_HANDLE` double-enforced with `deps.py`.** `require_user_with_handle` (`deps.py:56`) already bounces handle-less users. `[CODE-CONFIRMED]` → **Resolved in v2:** §3 "Handle-gate ownership" — `deps.py` keeps the gate; the resolver's `NEEDS_HANDLE` only covers the `get_current_user` entry points.

5. **[MINOR] PAUSED boundary differs across the folded predicates.** `provider_has_current_setup` ignores PAUSED; `provider_loop_running` excludes it — so a paused-only user lands in `CONNECTED_NOT_LIVE` and is told "start your AI." `[CODE-CONFIRMED]` → **Resolved in v2:** decided to keep that fold (settled decision 3) with a documented residual limitation + verification test.

6. **[MINOR] Redirect-loop surface via `connections_pages.py`.** Every `NEEDS_MCP_CONNECTION` site sends to `/me/connections?next=...`, which itself auto-forwards on a *different* bar (`provider_has_live_current_setup`) than the seat pages use (`provider_has_current_setup`) — a two-page oscillation under a "seen-but-not-polling" fixture. `[CODE-CONFIRMED]` → **Resolved in v2:** one shared signal for both pages + a required `/play ⇄ /me/connections` loop-guard test (acceptance criterion 6, Risks §).

## Residual Risks

- Cross-cutting redirect changes across ~10 files are the main regression surface; v2 carries a per-entry-point `Location` test + "READY user never redirected" invariant. *verification:* named in spec Risks §.
- `seat_hold.confirm_seat_if_live` must stay bit-identical to the `LIVE` boundary; v2 requires a shared-constant test. *verification:* named in spec Risks §.

## Resolution
- status: accepted
- note: Manual sub-agent feasibility pass (codex CLI unavailable); all findings folded into spec v2.
