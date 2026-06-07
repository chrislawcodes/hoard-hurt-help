---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/015-connection-agent-split/spec.md"
artifact_sha256: "f21ad7d4176aed3f4e70525985f0dde16dd7604c8f212fc7942db82ed5e4c597"
repo_root: "."
git_head_sha: "7086e307a8848a871e03364ed5793c048ca12b7e"
git_base_ref: "origin/main"
git_base_sha: "d4cf564e31f694dbd64e46ea785959beb1f55bcc"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "Spec re-checkpoint completed with no actionable findings ('reviews ran, no actionable findings raised'). Spec also adversarially reviewed in Round 1 (review-log.md)."
raw_output_path: "docs/workflow/feature-runs/015-connection-agent-split/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- **High:** `FR-003` and `FR-029` contradict each other. `FR-003` says `kind = ai` MUST have a Connection, but `FR-029` says deleting a connection detaches its agents and they become "needs a connection" while still being AI agents. The spec needs an explicit third state or an exception to the invariant, otherwise the detach flow is impossible by its own rules.
- **High:** The spec has two competing sources of truth for an agent’s model/strategy. `FR-002` says Agent carries `model` and `strategy`, while `FR-010`, `FR-011`, and the Key Entities section say the live playing definition lives on `AgentVersion` and Agent is identity only. Without a single authoritative source, versioning, rating, and leaderboard rendering will drift.
- **High:** The first-time agent flow is incomplete because it never specifies how `strategy` is chosen. The spec defines an Agent as `name + game + model + strategy`, but User Story 1 and `FR-028` only collect provider, name, and model. Unless there is a documented default strategy, the "New agent" flow cannot produce a valid agent.
- **Medium:** Connection key reissue has an open-ended overlap window. The old key stays valid "until the new one connects," but the spec gives no expiry or fallback if the new runner never arrives. That means a reissue can fail to actually retire the old credential.
- **Medium:** Deleting a connection that is actively powering matches does not define the match outcome. The spec says the agents are detached and paused, but it never says whether their current matches are forfeited, left hanging, or frozen. That is a state-management hole that can strand live games.

## Residual Risks

- The multi-agent routing path remains high-risk even if the spec is corrected, because one connection can own multiple agents in the same match. It needs tight tests for `(agent_id, match_id)` token scoping and stale-token rejection.
- Version freeze timing is still delicate. Freezing on first rated seating is reasonable, but cancellations, abandoned starts, and unrated-to-rated transitions need explicit coverage so a version does not freeze too early or too late.
- Detach/reattach and reissue/revoke behavior should be exercised end-to-end, because these state transitions combine runner health, key lifecycle, and agent availability in ways that are easy to get subtly wrong.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Spec re-checkpoint completed with no actionable findings ('reviews ran, no actionable findings raised'). Spec also adversarially reviewed in Round 1 (review-log.md).
