---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/mcp-oauth/plan.md"
artifact_sha256: "5de4697ebcc2b757a32c1268898d39d5ba5834d97afd95bae1be19a72f71fa06"
repo_root: "."
git_head_sha: "2b12fd108688e2c824fcf0821b378755e4a891cc"
git_base_ref: "origin/main"
git_base_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "HIGH (Mode A not live until first tool call blocks first web-join): BINDING tasks requirement — call mark_seen at Mode A connection CREATION (which happens on the client's first call incl. tools/list), so the connection is live the moment the client connects; web-join then works while the client is connected. This mirrors the connector's existing 'must be live/running to join' constraint (web_player.py coverage+capacity gate), not a new flow. Captured in Slice 3 (create→mark_seen) + Slice 4 (connectorless join+play verification). MEDIUM (extraction drops throttles + side effects): BINDING tasks requirement for Slice 2 — the agent_play extraction MUST carry the route-level rate-limit guards (_last_poll/_last_pull, keyed off connection) and post-submit side effects (turns_played increment, mark_first_move) INTO the shared service so the MCP path gets them too; parity test asserts an MCP submit increments turns_played and marks first move. MEDIUM (get_game_state breaking): intentional + documented — AD-5 migration story; tests/test_mcp.py updated to expect auth-required; public reads remain on the HTTP spectator endpoint."
raw_output_path: "docs/workflow/feature-runs/mcp-oauth/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

1. **High [CODE-CONFIRMED]** The plan leaves the first OAuth-created Mode A connection non-live until after an MCP tool call, which blocks the core connectorless join flow. The web join path refuses to seat an agent unless `provider_is_covered()` is true and capacity is available, and both checks depend on a fresh `last_seen_at` via `connection_health._connection_is_live()`. The plan only calls `mark_seen()` on MCP tool calls, not on OAuth sign-in or Mode A connection creation, so the user cannot get through the first web join before they have already needed a tool call. Evidence: [`app/routes/web_player.py:176`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/web_player.py#L176)), [`app/routes/web_player.py:280`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/web_player.py#L280)), [`app/engine/connection_health.py:269`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/engine/connection_health.py#L269)), [`app/engine/connection_health.py:323`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/engine/connection_health.py#L323))

2. **Medium [CODE-CONFIRMED]** The in-process MCP refactor drops existing route-level throttles and post-submit side effects unless they are explicitly reintroduced in the shared service layer. Today `agent_api` enforces `_last_poll` and `_last_pull` rate limits, increments `turns_played`, and calls `mark_first_move()` after a real submission. The plan moves MCP off the HTTP routes but never says where those guards and side effects live afterward, so they are likely to disappear for MCP calls. Evidence: [`app/routes/agent_api.py:343`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_api.py#L343)), [`app/routes/agent_api.py:508`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_api.py#L508)), [`app/routes/agent_api.py:583`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_api.py#L583)), [`app/routes/agent_api.py:670`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_api.py#L670)), [`app/routes/agent_api.py:752`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_api.py#L752))

3. **Medium [CODE-CONFIRMED]** The plan removes a currently public MCP capability without keeping an MCP-side replacement. `get_game_state` is explicitly documented as public in the server module and is part of the shipped tool surface in tests; the plan turns it into an auth-required tool and pushes users to the separate HTTP spectator endpoint instead. That is a breaking API change for any MCP-only consumer that relied on anonymous state reads. Evidence: [`mcp_server/server.py:3`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/mcp_server/server.py#L3)), [`mcp_server/server.py:274`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/mcp_server/server.py#L274)), [`tests/test_mcp.py:7`]((/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/tests/test_mcp.py#L7))

## Residual Risks

- The plan still depends on getting the Mode A connection marked live before the first join check. If that heartbeat is not established during OAuth callback or connection creation, the connectorless flow stays blocked.
- The shared play-service extraction needs explicit coverage for the current poll/pull throttles and submission side effects, or MCP behavior will drift from the HTTP agent API.
- The `get_game_state` migration needs a compatibility story for existing MCP callers. The HTTP spectator endpoint is not a drop-in replacement for an MCP tool.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: HIGH (Mode A not live until first tool call blocks first web-join): BINDING tasks requirement — call mark_seen at Mode A connection CREATION (which happens on the client's first call incl. tools/list), so the connection is live the moment the client connects; web-join then works while the client is connected. This mirrors the connector's existing 'must be live/running to join' constraint (web_player.py coverage+capacity gate), not a new flow. Captured in Slice 3 (create→mark_seen) + Slice 4 (connectorless join+play verification). MEDIUM (extraction drops throttles + side effects): BINDING tasks requirement for Slice 2 — the agent_play extraction MUST carry the route-level rate-limit guards (_last_poll/_last_pull, keyed off connection) and post-submit side effects (turns_played increment, mark_first_move) INTO the shared service so the MCP path gets them too; parity test asserts an MCP submit increments turns_played and marks first move. MEDIUM (get_game_state breaking): intentional + documented — AD-5 migration story; tests/test_mcp.py updated to expect auth-required; public reads remain on the HTTP spectator endpoint.
