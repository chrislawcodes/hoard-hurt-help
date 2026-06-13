# Reuse audit — byo-terminal-mode-a

Adversarial check: prefer reuse/extend; justify any new module. Mode A rides the
existing MCP/agent stack — there is **no genuinely new module**; the work is
extensions + one migration.

| Capability | Existing module (path) | Verdict | Note |
|---|---|---|---|
| MCP tool surface for play | `mcp_server/server.py` | **reuse** | Already the Mode A interface (get_next_turn, submit_talk/action). No change. |
| Poll-for-turn endpoint | `app/routes/agent_next_turn.py` (`next_turn`) | **extend** | Add the bounded long-poll (internal async re-check loop) to the no-turn branch; raise `_POLL_WHEN_WAITING`. Shared with connector — must not regress. |
| Talk/act submission | `app/routes/agent_api.py` (`agent_submit`) | **extend** | Increment exact turns-played here at act-submission (FR-006). Needs connection attribution — see below. |
| Turn routing + sticky pin | `app/engine/turn_routing.py`, `app/engine/next_turn.py` | **reuse** | Eligibility + atomic `_claim_pin`; long-poll claims only when returning a real turn. No change. |
| Connection auth (`sk_conn_`) | `app/deps.py` (`require_connection`) | **reuse** | Already the choke point; calls `mark_seen` on every request. |
| Connection→player attribution at submit | `players.served_by_connection_id` (sticky pin), `app/deps.py` (`require_agent_player`) | **reuse** | `require_agent_player` resolves a `Player`; resolve the owning `Connection` via the pin to attribute turns-played. (Round-2 finding: needs this plumbing.) |
| Per-connection activity/heartbeat throttle | `app/engine/connection_activity.py` (`mark_seen`) | **extend** | Fold the *approximate* call counter into the existing throttled `last_seen_at` write — no new per-call write. |
| Connection storage | `app/models/` (Connection) + `migrations/versions/` | **extend** | Add `turns_played` + `api_call_count` columns; new Alembic migration (SQLite batch pattern). |
| Player dashboard | `app/routes/web_player.py` + `app/templates/` | **extend** | Render turns-played (exact) + approx call count (labeled). |
| Live viewer ("watch the table") | `app/routes/web_viewer.py` + `app/routes/sse.py` | **reuse** | No change — this is how the human watches the match. |
| Connect docs | `docs/setup-mcp.md` | **extend** | Fix `X-Agent-Key`/`sk_bot_` → `X-Connection-Key`/`sk_conn_`; add Mode A section + universal play-prompt + per-client connect snippets. |
| Real-token measurement | `scripts/agentludum_connector.py` (`_claude_usage`) | **justified-new = NONE** | Explicitly NOT reused — interactive Mode A exposes no token count to the server. Dashboard shows raw counts; no new token-capture module built. |

**Duplication risk:** none found. The one tempting wrong-reuse is counting turns
at `mark_seen` (the choke point) — rejected in the spec (round-1 HIGH): polls ≠
turns, and it recreates the write hot-spot. Turns are counted at act-submission;
`mark_seen` only carries the throttled approximate call count.
