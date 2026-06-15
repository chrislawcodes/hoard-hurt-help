---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "d2512b8d0aec2dafb04e74daf48195ac8b5fcf6d1358670baf0812874e9cc814"
repo_root: "."
git_head_sha: "e3e63999d922df4064a53e8b323fb05d6e279489"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Reaffirmed: readiness keys on enabled coverage; needs-connecting state now explicit (also Codex MEDIUM); ?next + capacity verifications retained."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

*   **Risk of Inconsistent Readiness State (HIGH):** The plan differentiates "needs-connecting" (derived from `provider_enabled_on_any_connection`) from the "live-now" status (derived from connection liveness). The plan admits that this UI could imply an agent is "ready to play now" when its provider is enabled but the connection is currently stale or dead (Residual Risk #7). Relying on disparate signals for "readiness" vs. "live status" is fragile.
    *   [CODE-CONFIRMED] `app/engine/connection_health.py` and `app/routes/agent_next_turn.py` confirm these signals are handled separately and could indeed result in a confusing UI state if an agent is displayed as "ready" while its backing connection is effectively stalled.
*   **Missing Atomic Constraint for Join Hub (HIGH):** Slice 3 proposes redirecting "no-agent" users to `/me/agents/new`. However, the system architecture (documented in `AGENT_LUDUM_ARCHITECTURE.md` §1) distinguishes between "Bots" and "Agents." It is unclear if this redirect forces a user to create an AI Agent when a Bot might suffice, or if it inadvertently prevents a new user from accessing the practice arena (which uses Bots) without first completing the Agent design flow.
    *   [CODE-CONFIRMED] `app/engine/arena.py` confirms that bots are system-managed, not user-managed. A new user might reasonably expect to participate in the arena without having to go through the Agent-design onboarding flow.
*   **Potential for N+1 Query in Agent List (MEDIUM):** The plan calls for a "batched coverage query" in the agent list to avoid N queries (Residual Risk #5). If the implementation does not properly utilize a join or a single `IN` clause to fetch provider status for all agents in the list at once, it will revert to N queries, which is a known performance anti-pattern in the current architecture.
    *   [UNVERIFIED] The implementation logic for the batched query is not provided.
*   **Ambiguity in `?next` Parameter Persistence (MEDIUM):** While the plan identifies the risk of losing `?next` during form re-rendering (Residual Risk #2), it does not specify the mechanism for persistence. If it relies on a hidden input, it is vulnerable to manipulation.
    *   [UNVERIFIED] The actual implementation strategy for keeping `?next` intact was not detailed.

## Residual Risks

*   **Logic Drift in Adapter Layers:** The `AGENT_LUDUM_ARCHITECTURE.md` warns that the shared play-service layer must be strictly followed, or HTTP routes and MCP tools will drift. The plan modifies the `agents_create.py` (HTTP-specific route) but does not provide a mechanism to ensure that the new "strategy-first" state remains consistent if a user creates an agent via an MCP tool.
*   **Capacity Gate Bypass:** The plan assumes `active_matches_for_provider` / `live_provider_capacity` will handle disconnected agents by excluding them. If these functions do not explicitly filter by `Connection.last_seen_at` or `Connection.status` and only check `enabled` status, then disconnected agents will indeed inflate capacity, violating the design goal.

## Token Stats

- total_input=25336
- total_output=717
- total_tokens=26053
- `gemini-3.1-flash-lite`: input=25336, output=717, total=26053

## Resolution
- status: accepted
- note: Reaffirmed: readiness keys on enabled coverage; needs-connecting state now explicit (also Codex MEDIUM); ?next + capacity verifications retained.
