---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/strategy-first-onboarding/spec.md"
artifact_sha256: "c42697eb20f7b868ca287b802ca6f283ff648c5ca36eb5cbaa54db29bdcb3774"
repo_root: "."
git_head_sha: "fec4fcad2535856ded3533e67243ba454ba02f9b"
git_base_ref: "origin/main"
git_base_sha: "4723b62322a808d5a9c34d77e84e714d681d863e"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "HIGH create-blocks/join-hijack covered by FR-001/FR-005; picker MEDIUM by FR-002. Added edge cases: preserve ?next through create validation failure; disconnected agents excluded from capacity math; FR-006 batches the per-agent coverage query."
raw_output_path: "docs/workflow/feature-runs/strategy-first-onboarding/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

*   **Create Flow Blocks Pre-Connection (HIGH) [CODE-CONFIRMED]**: `app/routes/agents_create.py:create_agent_or_connection` explicitly rejects agent creation if the selected model's provider is not enabled on any of the user's connections. This forces a connection-first dependency, directly violating US1.
*   **Join-Flow Hijack (HIGH) [CODE-CONFIRMED]**: `app/routes/web_player.py:_join_setup_redirect` mandates that users with no AI agent must connect a client before they can create an agent. This creates a hard stop for new users, violating US3.
*   **Model Picker Grouping Limits (MEDIUM) [CODE-CONFIRMED]**: `app/routes/agents_create.py:_build_model_picker_groups` filters `enabled` status, but the downstream post-create logic in `create_agent_or_connection` enforces that the selected provider MUST be enabled. The picker allows selection, but the submission will fail (redirect to connect), creating a disjointed user experience.
*   **Hard-coded Dependency on Agent for Join (LOW) [CODE-CONFIRMED]**: `app/routes/web_player.py:_join_setup_redirect` conflates "AI agent" existence with connection status. It checks for *any* agent existence and then immediately jumps to connection requirements, ignoring the potential to simply route to an agent creation page.

## Residual Risks

*   **Derivation Complexity**: The spec assumes "needs connecting" state can be derived from `app/engine/connection_health.py` helpers. If the agent list rendering loop in `agents_list.py` becomes too query-heavy by re-calculating coverage per-agent, it may introduce latency for users with many agents.
*   **Redirect Loops**: Re-routing the Join flow to `/me/agents/new` while carrying `?next` assumes the create flow reliably consumes/forwards that `next` param upon completion. If `agents_create.py` loses this state during a validation failure, the user could be trapped in a circular flow between Join and Create.
*   **Capacity Gate Divergence**: The current join gate logic relies on active connections (`active_matches_for_provider`). Since the strategy-first flow allows agents to exist without connections, we must ensure these "disconnected" agents don't accidentally satisfy (or break) capacity calculations intended for live agents.

## Token Stats

- total_input=16
- total_output=532
- total_tokens=32532
- `gemini-3.1-flash-lite`: input=16, output=532, total=32532

## Resolution
- status: accepted
- note: HIGH create-blocks/join-hijack covered by FR-001/FR-005; picker MEDIUM by FR-002. Added edge cases: preserve ?next through create validation failure; disconnected agents excluded from capacity math; FR-006 batches the per-agent coverage query.
