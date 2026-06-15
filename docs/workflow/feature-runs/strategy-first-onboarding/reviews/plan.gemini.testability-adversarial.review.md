---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/plan.md"
artifact_sha256: "6b482ae84da5bae1bdfc446b2618908c31d69a9aad4df43a3c85462d0715e96d"
repo_root: "."
git_head_sha: "92ae8342b430e0a106e53f3086f58a7cceb5df4f"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 4: no actionable findings — plan converged."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

- **Logic Leak in Provider Coverage/Health:** The plan relies on `connection_health.provider_is_covered` to determine "readiness" but simultaneously states that `enabled_provider_values` should ignore `ConnectionStatus.PAUSED`. If the coverage helper uses `enabled_provider_values` internally without status filtering, an agent on a PAUSED connection will be incorrectly flagged as "ready".
  [CODE-CONFIRMED] `app/engine/connection_health.py` contains `enabled_provider_values` which gathers enabled providers without filtering for connection status (e.g., `PAUSED`).

- **Incomplete Batched Query Design:** The plan proposes a batched coverage query for the agent list to avoid N+1 issues but does not address how this batching interacts with the "status-aware" coverage requirement. If `list_agents` runs one query for all agents, it must be robust enough to categorize each agent correctly based on the status of the connections providing their specific provider.
  [UNVERIFIED] The plan mentions batched queries but doesn't define the complexity of the query to filter by `!PAUSED` status across multiple agents with different providers in a single round-trip.

- **Potential State Pollution in `?next`:** The plan relies on `?next` surviving create validation failures. If the validation logic in `agents_create.py` does not explicitly re-inject the `next` query parameter during the re-render of the form, the redirection chain is broken.
  [UNVERIFIED] Requires checking `app/routes/agents_create.py` to confirm the form re-render logic handles arbitrary query parameters.

## Residual Risks

- **Stale Liveness UX:** By deliberately using the platform's standard `LIVE_WINDOW_SECONDS` (via existing connection health badges) rather than a custom "live now" state, there is a risk that the UI will show "Ready to play" (because the provider is *configured/active*) while the connection is actually stalled/offline, causing confusion for users expecting immediate turn execution.
- **Join Redirect Loop:** If the "no-agent" user redirection to `/me/agents/new` is triggered without proper context, a user might get trapped in an onboarding loop if they decline to create an agent.
- **Batched Query Over-fetching:** An improperly implemented batched coverage query that fetches all connection-provider states for all users could lead to performance degradation as the `connections` table grows, or worse, expose configuration data across different users if the scope is not strictly limited to the current user's connections.

## Token Stats

- total_input=26461
- total_output=554
- total_tokens=27015
- `gemini-3.1-flash-lite`: input=26461, output=554, total=27015

## Resolution
- status: accepted
- note: Round 4: no actionable findings — plan converged.
