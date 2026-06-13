---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/mcp-oauth/spec.md"
artifact_sha256: "4f2b7a50cab09b51633ed703355abe532fe2feb615b812aef277577000f33adc"
repo_root: "."
git_head_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
git_base_ref: "origin/main"
git_base_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
generation_method: "codex-runner"
resolution_status: "accepted"
resolution_note: "All three findings are correct and already captured as explicit PLAN-stage decisions in the spec — they are HOW choices a spec intentionally defers, not spec defects: (1) get_game_state public carve-out = open design point 7 (plan must keep it reachable or make it auth-required + add a test); (2) one-canonical-connection uniqueness = FR-006 (plan must add a DB uniqueness constraint on a per-user Mode A marker + transactional upsert/lock; user_id is indexed-not-unique); (3) FR-012 credential mechanism = open design point 1 (plan must CHOOSE encrypted-at-rest vs short-lived internal token vs in-process call, with rotation/expiry semantics). Residual risks (fastmcp mount/lifespan hazard, four-client external risk, provider enablement) map to open design points 3/4, R1, and FR-007/design point 2. No further spec edit — these resolve at the plan, verified at the plan checkpoint."
raw_output_path: "docs/workflow/feature-runs/mcp-oauth/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

- HIGH [CODE-CONFIRMED] The spec leaves `get_game_state` unresolved under the proposed OAuth gate. The current MCP app is mounted as one ASGI app at `/mcp` in [app/main.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/main.py#L141-L223), and `get_game_state` is the only tool explicitly designed to be public and unauthenticated in [mcp_server/server.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/mcp_server/server.py#L273-L292). If `/mcp` is wrapped in a blanket OAuth provider without a carve-out or split mount, that required public tool disappears.

- HIGH [CODE-CONFIRMED] The “one canonical per-user Mode A Connection” invariant is not enforced anywhere in the current data model or routing. `Connection.user_id` is only indexed, not unique, in [app/models/connection.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/models/connection.py#L33-L39), `require_connection` can materialize a new `Connection` from a pending setup in [app/deps.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/deps.py#L187-L228), and the turn router already treats all of a user’s connections as live inputs in [app/routes/agent_next_turn.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/agent_next_turn.py#L104-L142). Without a transactional uniqueness rule and a stable marker for the OAuth-owned row, concurrent sign-ins will create duplicates and the runtime will keep treating them as separate connections.

- MEDIUM [CODE-CONFIRMED] FR-012 is still underspecified at the load-bearing point: the OAuth layer must call the internal HTTP API, but that API only authenticates with a raw `X-Connection-Key` header in [app/deps.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/deps.py#L144-L228), while the setup flow explicitly notes that the raw key is unrecoverable from the stored hash in [app/routes/connections_setup.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/routes/connections_setup.py#L425-L440). The spec lists candidate mechanisms, but it does not choose one or define rotation/expiry semantics, so the bridge cannot be implemented safely yet.

## Residual Risks

- The `fastmcp` / `OAuthProxy` migration still has a mount-and-lifespan hazard because [app/main.py](/Users/chrislaw/hoard-hurt-help--feat-mcp-oauth/app/main.py#L141-L173) manually drives the MCP sub-app lifespan.
- Real-client OAuth support on Claude Code, Claude Desktop, Codex, and Gemini CLI is still external risk; the spec depends on all four succeeding before shipping.
- Provider enablement for newly added agents remains a likely follow-up because `require_agent_player` only matches enabled `connection_providers` rows.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: All three findings are correct and already captured as explicit PLAN-stage decisions in the spec — they are HOW choices a spec intentionally defers, not spec defects: (1) get_game_state public carve-out = open design point 7 (plan must keep it reachable or make it auth-required + add a test); (2) one-canonical-connection uniqueness = FR-006 (plan must add a DB uniqueness constraint on a per-user Mode A marker + transactional upsert/lock; user_id is indexed-not-unique); (3) FR-012 credential mechanism = open design point 1 (plan must CHOOSE encrypted-at-rest vs short-lived internal token vs in-process call, with rotation/expiry semantics). Residual risks (fastmcp mount/lifespan hazard, four-client external risk, provider enablement) map to open design points 3/4, R1, and FR-007/design point 2. No further spec edit — these resolve at the plan, verified at the plan checkpoint.
