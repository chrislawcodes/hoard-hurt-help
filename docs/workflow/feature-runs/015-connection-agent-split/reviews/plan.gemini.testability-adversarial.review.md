---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/015-connection-agent-split/plan.md"
artifact_sha256: "99d734d76db917cdec402bbe49783a1228502d9ece704d99dfbc4835d6ed4003"
repo_root: "."
git_head_sha: "f8927b533eb49cc075f740cd77020016ed3d23d7"
git_base_ref: "origin/main"
git_base_sha: "d4cf564e31f694dbd64e46ea785959beb1f55bcc"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "All 3 HIGH findings already addressed by committed slices 0-1: (1) agent_turn_token replay bounded by existing turn idempotency (DB unique(turn,player) + existing-check + turn resolution; slice-1 fanout test proves wrong-agent submit rejected); (2) migration 0023 downgrade implemented, round-trip test passes; (3) editing blocked mid-match (FR-011) + player.agent_version_id pins each match's version. Become explicit tests in the tasks stage; import-restructure risk covered by slice-5 gate (import app.main + pytest green)."
raw_output_path: "docs/workflow/feature-runs/015-connection-agent-split/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **Race Condition in Turn Resolution via `agent_turn_token` [HIGH]:** The plan relies on `agent_turn_token` to bind a submission to a specific `(agent, match)` pair [PLAN-DECISION-3]. However, the plan does not explicitly address how the server validates this token atomically against concurrent submissions or ensures tokens are single-use per turn. Without atomicity (e.g., in-memory or DB-level lock per token), two agents of one connection could potentially swap submissions if they receive them, or a malicious agent could potentially replay a token for a previous turn if the server doesn't invalidate it upon use. [UNVERIFIED]
2.  **Destructive Migration Risk in Test Suite [HIGH]:** The plan requires `downgrade()` for migration `0023` to rebuild the old `bots` schema to support `test_migrations.py` [PLAN-DECISION-4]. The current `test_migrations.py` in the workspace [CODE-CONFIRMED] tests migration logic by running `upgrade head` followed by `downgrade base`. If `downgrade()` does not perfectly recreate the original table constraints (e.g., exact index names, check constraints, FK relations), this test will fail, blocking the preflight gate. This risk is compounded by the fact that the original schema (e.g., `bots` table, `strategy_prompts` table) might have implicit dependencies that are easily missed in a manual `downgrade` definition. [CODE-CONFIRMED]
3.  **Ambiguous Agent-Version Forking Logic [HIGH]:** Decision 2 states that editing an unfrozen agent version edits it in place, while editing a frozen one forks a new version (N+1). The implementation strategy is undefined for cases where multiple concurrent matches are using different versions of the same agent. If an agent is mid-match when a version is edited, it is unclear if the agent identity immediately jumps to the "latest rated version" or if it maintains the specific `agent_version_id` for its current match until that match completes. The plan lacks logic for state transitions of "active" agents vs "draft" agents. [UNVERIFIED]

## Residual Risks

1.  **Import Dependency Hell:** The route restructure (e.g., `bots_*` → `connections_*`, `agents_*`) is extensive [PLAN-STRUCTURE]. Given the dense web of inter-dependencies in `app/routes/` and `app/main.py`, a small error in the registry or `nav_context.py` could trigger cascade failures during app startup. The verification step (`python3 -c "import app.main"`) is necessary but insufficient to catch route-specific import cycles or missing fragments in Jinja2 templates.
2.  **Health Computation Complexity:** `connection_health.py` will compute state across multiple agents [PLAN-DECISION-7]. If the platform grows to support a high `max_concurrent_games`, the overhead of walking the agent-player-match tree on every poll could become a bottleneck. The plan lacks caching or optimization strategies for this recomputation.
3.  **Bot/Agent Semantic Overlap:** Despite strict renaming rules (Decision 5), code comments and docstrings in `app/engine/sims/` that historically referred to "bots" as "sims" or "agents" might lead to developer confusion, specifically if the `kind=bot` constraint is accidentally bypassed in future PRs. The plan relies on a manual grep/rename sweep to solve this, which is prone to human error.

## Token Stats

- total_input=30134
- total_output=769
- total_tokens=30903
- `gemini-3.1-flash-lite`: input=30134, output=769, total=30903

## Resolution
- status: accepted
- note: All 3 HIGH findings already addressed by committed slices 0-1: (1) agent_turn_token replay bounded by existing turn idempotency (DB unique(turn,player) + existing-check + turn resolution; slice-1 fanout test proves wrong-agent submit rejected); (2) migration 0023 downgrade implemented, round-trip test passes; (3) editing blocked mid-match (FR-011) + player.agent_version_id pins each match's version. Become explicit tests in the tasks stage; import-restructure risk covered by slice-5 gate (import app.main + pytest green).
