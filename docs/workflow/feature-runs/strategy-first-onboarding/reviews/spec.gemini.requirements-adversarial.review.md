---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/spec.md"
artifact_sha256: "71a45879b10d602ea73cab24094252f5d2fe9377bcba5dc7e335103e395face7"
repo_root: "."
git_head_sha: "d46884de0735b6e334b670dfffe151f50205462c"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "Round 3: confirmations only, no new findings — spec converged."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

*   **[CODE-CONFIRMED] Agent Creation Gate (FR-001):** The `create_agent_or_connection` POST handler in `app/routes/agents_create.py` explicitly checks `if agent_provider.value not in await enabled_provider_values(db, user.id)` and redirects to `/me/connections` if no enabled provider exists, creating a hard block for users without connections.
*   **[CODE-CONFIRMED] GET Form Gate (FR-001):** The `new_agent_form` in `app/routes/agents_create.py` sets `has_enabled_provider = bool(enabled_values)`. While the template logic isn't provided, this variable is explicitly passed for the purpose of controlling access to the creation form, confirming the "Connect a client first" constraint.
*   **[CODE-CONFIRMED] Join-Flow Routing (FR-005):** The `_join_setup_redirect` function in `app/routes/web_player.py` implements the existing connect-first requirement: if a user has no agent, it checks `if not await enabled_provider_values(db, user.id)` and redirects to `/me/connections`, directly contradicting the "design first" requirement.
*   **[CODE-CONFIRMED] Readiness Derivation (FR-003, FR-006):** The readiness of an agent (whether it's "live" or "needs connecting") is currently derived dynamically in `app/engine/connection_health.py` via `provider_is_covered` and `provider_enabled_on_any_connection`. This confirms that a new database column is not strictly necessary, supporting the preference for derivation.
*   **[CODE-CONFIRMED] Join Capacity Gate (FR-007, NFR-005):** The `_seat_user_agent` logic in `app/routes/web_player.py` uses `provider_is_covered` to gate `bypass_capacity` and seat holding (`reserved_until`). This logic must be carefully preserved to ensure disconnected agents cannot bypass capacity limits or incorrectly claim "live" status.

## Residual Risks

*   **Race Conditions in Join Setup:** `_join_setup_redirect` in `app/routes/web_player.py` performs multiple async DB checks (`enabled_provider_values`, `_load_user_agents`). If a user manages to initiate agent creation and connection in separate tabs, the redirect flow might intermittently trigger confusing transitions.
*   **Model/Provider Consistency:** The requirement to allow selecting any provider in `new_agent_form` means the UI will offer models for providers the user has not connected. If a user picks a model for an unconnected provider, and the backend logic for `provider_for_model` (`app/config.py`) or the validation logic changes/drifts, the user could end up with an agent they *cannot* connect using the current system, creating a dead-end that is only discovered post-submission.
*   **Capacity Math Fragility:** The "needs connecting" logic hinges on `provider_enabled_on_any_connection`. If an agent's provider is "enabled" but the underlying connection is not "live" (stale/disconnected), and there is an edge case where an agent is partially configured, the distinction between "needs connecting" and "ready" might become opaque to the user, potentially leading to support friction when they believe they are "ready" but are not.

## Token Stats

- total_input=32527
- total_output=764
- total_tokens=33291
- `gemini-3.1-flash-lite`: input=32527, output=764, total=33291

## Resolution
- status: accepted
- note: Round 3: confirmations only, no new findings — spec converged.
