---
reviewer: "codex"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/byo-terminal-mode-a/plan.md"
artifact_sha256: "81641ce86d8582a9feaad1866480df16a0b7a0ee9434b8628d98e7de6a641ad6"
repo_root: "."
git_head_sha: "2f67a923b1ce93b73459ff683ae2f4f3e3e5c504"
git_base_ref: "origin/main"
git_base_sha: "c9d01a3e4d1e90198936d568835b6ed2609bcc6f"
generation_method: "codex-runner"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/byo-terminal-mode-a/reviews/plan.codex.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

- **HIGH** `[CODE-CONFIRMED]` The long-poll fix is incomplete: dropping `db: DbSession` from `next_turn` does not stop the request from holding a pooled connection, because `require_connection` still depends on `DbSession`, and `get_session()` yields a request-scoped `AsyncSession` for the whole request. A 25s wait still pins that session and can exhaust the pool under concurrency. See [app/db.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/db.py), [app/deps.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/deps.py), [app/routes/agent_next_turn.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_next_turn.py).
- **MEDIUM** `[CODE-CONFIRMED]` The plan omits the documented `session_usage` response surface. The platform design says `get_next_turn` should carry a `session_usage` estimate, but `NextTurnWaiting` and `NextTurnYourTurn` in [app/schemas/agent.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/schemas/agent.py) do not have that field, and the plan only adds a raw `api_call_count` column/dashboard metric. That leaves the AI with no way to relay the estimate in-terminal. See [docs/platform/AGENT_LUDUM_DESIGN.md](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/docs/platform/AGENT_LUDUM_DESIGN.md), [app/schemas/agent.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/schemas/agent.py).
- **MEDIUM** `[CODE-CONFIRMED]` Crediting `turns_played` to the submitting connection is not exact. `agent_submit` is authorized through `require_agent_player`, which only checks same-user plus enabled-provider eligibility; the `agent_turn_token` binds the move to `turn_token:agent_id:match_id`, not to the connection that polled. Any eligible live connection can submit the action, so the count can be assigned to a different machine than the one that actually received the turn. See [app/deps.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/deps.py), [app/routes/agent_next_turn.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_next_turn.py), [app/routes/agent_api.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_api.py).
- **MEDIUM** `[CODE-CONFIRMED]` The plan’s mid-wait revalidation only mentions paused/deleted connections, but `require_connection` also blocks disabled users. If an account is disabled while a long-poll is in flight, this plan still lets the request sit until timeout and could hand back a turn after disable, which breaks the account-disable guarantee. See [app/deps.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/deps.py), [app/routes/agent_next_turn.py](/Users/chrislaw/hoard-hurt-help--feat-byo-terminal-mode-a/app/routes/agent_next_turn.py).

## Residual Risks

- The one-candidate-per-poll selection can still add avoidable latency under contention because a failed `_claim_pin` immediately falls back to waiting instead of trying the next eligible candidate in the same tick.
- The `api_call_count` metric will remain approximate and can drift from true usage whenever requests are bursty inside the heartbeat throttle window.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 