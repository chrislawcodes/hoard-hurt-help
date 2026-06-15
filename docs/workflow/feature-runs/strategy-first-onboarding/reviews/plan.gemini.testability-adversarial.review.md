---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "1723f19192d6c172ed534e0958a7e3b66c2d6276e7fdb28cab06da523d78a651"
repo_root: "."
git_head_sha: "75818faa2989d59dcb960839b6eae15a7e56f646"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Finding #1 (stale readiness) addressed: needs-connecting keys on provider_enabled_on_any_connection (not live window); live-now via existing health badge; verification added. Findings #2 (?next robustness) and #3 (capacity math) already carried as residual risks with pre-merge verifications."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **Risk of stale/misleading "readiness" UI (High):** The plan relies on `connection_health.py` for "ready vs. needs-connecting" status, but `connection_health.py` calculates liveness based on the `LIVE_WINDOW_SECONDS` threshold [CODE-CONFIRMED]. If a connection has a high `LIVE_WINDOW_SECONDS` or the runner is dead but the last heartbeat was recent, the agent might display as "ready" even if it cannot actually receive turns, misleading the user who just went through the onboarding flow.
2.  **Potential for "Split Brain" in `_join_setup_redirect` (Medium):** Reversing the Join hub to always route no-agent users to `agents/new` assumes the `?next` chain is perfectly robust across all validation failures, including potential edge cases where `AgentCreateSchema` or `GameModule` validation might drop query params during re-renders [UNVERIFIED].
3.  **Capacity Math Consistency (Medium):** The plan mandates that "a needs-connecting agent must stay excluded from live-connection capacity math" and plans to verify this with a test [UNVERIFIED]. If `active_matches_for_provider` or `live_provider_capacity` logic ever implicitly relies on `Agent` existence to project potential load (rather than strictly `ConnectionProviders` status), a simple check might miss subtle over-allocation bugs when an agent is created but not yet connected.

## Residual Risks

1.  **Race conditions in "readiness" state (Low):** Because readiness is derived on-the-fly and not persistent, a user could see an agent listed as "needs-connecting", click to connect, and by the time the page renders, the background scheduler might have already marked the connection as "stale" or "down" (if it was flapping), creating a flickering UI experience.
2.  **N+1 Query Regression (Low):** While the plan explicitly calls out "compute coverage in ONE batched query" for the agent list, ensuring this remains performant as the number of agents and providers grows—and ensuring it stays batched—requires strict adherence in the implementation, as it is easy to accidentally revert to lazy-loading coverage inside the template loop.
3.  **Migration/Downgrade Consistency:** While the plan correctly notes no DB migration is needed, it doesn't explicitly account for how existing "no-connection" agents (if any exist due to historical edge cases) might behave when suddenly exposed to the new "needs-connecting" UI, potentially creating support tickets for users whose agents were previously "forgotten".

## Token Stats

- total_input=25117
- total_output=562
- total_tokens=25679
- `gemini-3.1-flash-lite`: input=25117, output=562, total=25679

## Resolution
- status: accepted
- note: Finding #1 (stale readiness) addressed: needs-connecting keys on provider_enabled_on_any_connection (not live window); live-now via existing health badge; verification added. Findings #2 (?next robustness) and #3 (capacity math) already carried as residual risks with pre-merge verifications.
