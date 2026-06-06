# Reuse Audit — 015 (remaining slices 3–5)

Guided by `ARCHITECTURE.md`. The remaining work is overwhelmingly **rename/extend** of existing modules, not new construction — the connection/agent split reshapes the existing bot panel + auth + runner rather than building parallel systems.

| Capability needed | Existing module (path) | Verdict | Note |
|---|---|---|---|
| Connection setup/list page | `app/routes/bots_setup.py` | **extend** (→ `connections_setup.py`) | restructure the existing "My Bots" create/list into `/me/connections`; reuse its key-issue + onboarding wiring |
| Connection key reissue/revoke | `app/routes/bots_credentials.py` | **reuse** (→ `connections_credentials.py`) | graceful-overlap reissue + immediate revoke already implemented; repoint to Connection |
| Connection pause/resume/delete | `app/routes/bots_lifecycle.py` | **extend** (→ `connections_lifecycle.py`) | reuse pause/resume; change delete to **detach** (FR-029); add reattach |
| Agent list/create page | `app/routes/bots_setup.py` | **extend** (→ `agents_setup.py` + `/me/agents/new`) | combined create flow + version 1; lift the existing provider/model picker |
| Agent onboarding/status fragment | `app/routes/bots_status.py` | **extend** (→ `agents_status.py`) | reuse the SSE/HTMX status fragment pattern |
| Connection health (live/stalled/ready) | `app/engine/connection_activity.py` (`compute_bot_health`, `BotHealth`) | **extend** | already renamed in slice 1; compute across a connection's agents (optionally split to `connection_health.py` per plan Decision 7 — justify if split) |
| Model source-of-truth | `app/config.py` `PROVIDER_MODELS` | **reuse** | added in slice 0; use for set-model validation (FR-023) |
| Versioning storage | `agent_versions` table + `AgentVersion` model | **reuse** | added in slice 0; create v1 at agent create, fork on edit |
| Leaderboard ai/bot + per-agent | `app/read_models/leaderboard.py` | **reuse** | done in slice 2 (ai labeled name·model, bots by profile) |
| Join flow | `app/routes/web_player.py` | **extend** | record `agent_id` + `agent_version_id`; enforce `max_concurrent_games` (FR-022); `seat_name = handle/name` |
| In-match identity | `app/routes/web_viewer.py`, `app/read_models/matches.py` | **extend** | expose `seat_name` (matches.py already projects it) |
| Templates | `app/templates/bots/` | **extend/rename** | split into `templates/connections/` + `templates/agents/`; drop the MCP-direct "Advanced" section |
| Runner | `scripts/agentludum_agent.py` | **extend** (→ `agentludum_connector.py`) | reuse the per-provider session logic; key by connection; carry each agent's model |
| MCP tools | `mcp_server/server.py` | **extend** | header/key naming → `X-Connection-Key`; tools proxy the same agent API |
| Nav | `app/routes/nav_context.py` | **extend** | two entries: Connections + Agents |

**Justified-new (small):**
- `app/templates/agents/_versions.html` (or inline) — the version-history panel has no existing equivalent.
- `/me/agents/new` create-page route — a focused page; the logic reuses the existing provider/model picker + connect-message components.
- Pending-connection GC — a small scheduled sweep; reuse the existing poller pattern in `app/engine/scheduler.py`.

**No duplication risk found.** Everything either renames an existing `bots_*` module to `connections_*`/`agents_*`, extends an existing route/engine, or reuses a slice-0/1/2 primitive. The only genuinely new UI is the version-history panel, which has no prior art.
