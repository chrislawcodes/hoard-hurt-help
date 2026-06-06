---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/015-connection-agent-split/spec.md"
artifact_sha256: "f21ad7d4176aed3f4e70525985f0dde16dd7604c8f212fc7942db82ed5e4c597"
repo_root: "."
git_head_sha: "7086e307a8848a871e03364ed5793c048ca12b7e"
git_base_ref: "origin/main"
git_base_sha: "d4cf564e31f694dbd64e46ea785959beb1f55bcc"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "All findings fixed in commit f8927b5: HIGH FR-015/FR-029 contradiction (delete=detach not block) resolved; version-freeze timing pinned to first rated-match start (FR-011); pending-connection-orphan clarified (agent created only after connect, so GC can't orphan, FR-024)."
raw_output_path: "docs/workflow/feature-runs/015-connection-agent-split/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

1.  **Ambiguous "Agent Reattachment" Workflow (Severity: HIGH)**
    The requirement FR-029 states that deleting a connection detaches its agents, putting them in a "needs a connection" state where they can be "reattached to another connection of the *same provider*." The spec is silent on how the system validates or enforces "same provider" across connections *retrospectively* if provider capabilities change, or if a user attempts to reattach to a connection that is functionally compatible but differs in some minor configuration. Relying on an "implicit" provider match without an explicit handshake or validation UI poses a risk of stuck agents.

2.  **Versioning Inconsistency with "Bots" (Severity: MEDIUM)**
    FR-011 and the definition of a Bot create a conceptual rift. AI Agents get versioned histories (`AgentVersion`), but "Bots" are stated to have "one implicit version (their config)." If a user (or system maintainer) wants to iterate on a Bot's script (e.g., tweaking a "Sim's" strategy), the spec does not define how to preserve historical Bot performance for that specific, older scripted logic. This makes comparing performance across Bot revisions impossible, unlike the AI Agent path. [UNVERIFIED]

3.  **Potential Race Condition in Turn Resolution (Severity: MEDIUM)**
    FR-021 specifies that `next-turn` returns an "agent-scoped token" to prevent moves being applied to the wrong player when a connection fields two agents in one match. However, the spec lacks detail on how the `agent-scoped token` prevents a stale token from being used if the agent's turn is delayed or if the connection experiences high latency. If the runner attempts to submit a turn for an agent that has since been detached or paused, the spec does not explicitly state the expected error behavior, only that it is "resolved." [UNVERIFIED]

4.  **"Pending" Connection Garbage Collection Risk (Severity: LOW)**
    FR-024 defines a 24-hour TTL for "pending" (unconnected) connections. It assumes the user will be notified of this TTL in the UI or via some other feedback mechanism. If a user starts the connection flow but is interrupted for >24 hours, their state is silently deleted. The spec should require an explicit "resumption" notification or UI indicator to prevent frustration.

## Residual Risks

1.  **Semantic Overload of "Agent" (Architectural):** While "Agent" is intended to be the leaderboard competitor, it now holds identity, game association, strategy, *and* versioning. If a future requirement asks for an agent to play *multiple* games (e.g., an agent that plays both HHH and Liar's Dice), the current schema forces a hard split, as `Agent` is bound to a single `game` slug.
2.  **Sync Complexity:** The requirement to maintain FR-005 (turn resolution) across all agents on a connection while ensuring isolation (FR-021) increases the complexity of the hot path for move submission. Any regression here is likely to result in a total, system-wide halt of agent activity for that connection, which is a higher blast radius than the previous architecture.
3.  **UI/UX Discoverability:** The split requires users to manage two separate entities ("Connections" and "Agents"). Even with the combined "New agent" flow (US1), the mental model shift for a user to understand that "I need to connect first" versus "I can just create an agent" may lead to support/onboarding friction if the distinction isn't crystal clear in the UI.

## Token Stats

- total_input=20888
- total_output=926
- total_tokens=41509
- `gemini-3.1-flash-lite`: input=20888, output=926, total=41509

## Resolution
- status: accepted
- note: All findings fixed in commit f8927b5: HIGH FR-015/FR-029 contradiction (delete=detach not block) resolved; version-freeze timing pinned to first rated-match start (FR-011); pending-connection-orphan clarified (agent created only after connect, so GC can't orphan, FR-024).
