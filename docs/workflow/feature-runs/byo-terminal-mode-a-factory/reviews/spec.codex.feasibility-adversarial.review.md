---
reviewer: "codex"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/byo-terminal-mode-a/spec.md"
artifact_sha256: "9d82e83ad25958775f6d85ddc703587cf5536429dbff17c9f337f11b471feb64"
repo_root: "."
git_head_sha: "2f67a923b1ce93b73459ff683ae2f4f3e3e5c504"
git_base_ref: "origin/main"
git_base_sha: "c9d01a3e4d1e90198936d568835b6ed2609bcc6f"
generation_method: "codex-runner"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/byo-terminal-mode-a/reviews/spec.codex.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

1. **HIGH [CODE-CONFIRMED]** FR-001/FR-002 understate the backend change. `next_turn` is still a one-shot handler that returns `waiting` immediately, and the code path is built around a request-scoped `DbSession` plus process-local throttles. A real bounded long-poll needs a re-check loop, session lifetime changes, and cross-process-safe coordination; this is not a thin plumbing add-on. See [app/routes/agent_next_turn.py:341](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_next_turn.py#L341), [app/routes/agent_next_turn.py:357](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_next_turn.py#L357), and [app/routes/agent_api.py:58](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L58).

2. **MEDIUM [CODE-CONFIRMED]** The usage story is too weak if you only ship an exact `turns-played` counter. The code already splits a turn into `talk` and `act` submissions, and the MCP wrapper polls `get_next_turn` as the primary loop, so a player can burn a lot of model calls without the exact turn count changing. That makes FR-008 a poor proxy for the stated “usage visible” goal unless an approximate call counter is first-class, not optional. See [mcp_server/server.py:120](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/mcp_server/server.py#L120), [app/routes/agent_api.py:450](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L450), and [app/routes/agent_api.py:506](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L506).

3. **MEDIUM [CODE-CONFIRMED]** The spec does not explain how the exact per-connection counter will be attributed at submission time. `require_agent_player` resolves only a `Player`; `agent_message` and `agent_submit` only receive that `Player`; the only connection-scoped hook in the provided code is `mark_seen`, which is explicitly a heartbeat on every authenticated call, not the act-submission increment point. This needs extra auth/context plumbing, or the counter will be wrong. See [app/deps.py:267](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/deps.py#L267), [app/routes/agent_api.py:450](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L450), [app/routes/agent_api.py:506](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L506), and [app/engine/connection_activity.py:87](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/engine/connection_activity.py#L87).

## Residual Risks

- The current poll throttles are in-memory (`_last_poll` / `_last_pull`), so any multi-worker or restart scenario will make rate limiting uneven. See [app/routes/agent_api.py:58](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py#L58).
- The spec still leaves the client timeout matrix unmeasured, so a 25-30 second hold window may need retuning per client.
- The design stays purely polling-based even though an in-process broadcaster already exists, so waiting clients will still scale linearly with load.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 