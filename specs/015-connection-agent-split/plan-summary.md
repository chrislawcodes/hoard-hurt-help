# Plan Summary: Connection / Agent Split (015)

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/connection.py` | create | Connection + ConnectionProvider/ConnectionStatus enums |
| `app/models/agent.py` | create | Agent + AgentKind(ai/bot)/AgentStatus; former Bot + bot_* config fields |
| `app/models/player.py` | modify | bot_id→agent_id FK; agent_id(str)→seat_name; +strategy_snapshot; constraints |
| `app/models/strategy_prompt.py` | modify | player_id → agent_id FK |
| `app/models/bot.py` | delete | replaced by connection.py + agent.py |
| `app/models/__init__.py` | modify | export Connection/Agent; drop Bot |
| `app/deps.py` | modify | require_bot→require_connection (X-Connection-Key); require_agent_player |
| `app/engine/bot_activity.py` → `connection_activity.py` | rename+modify | mark_seen on Connection |
| `app/engine/sims/*` | modify | act on kind=bot agents; sim→bot naming |
| `app/routes/agent_next_turn.py` | modify | **HIGH-CARE** turn resolution across a connection's agents |
| `app/routes/agent_api.py` | modify | resolve player via connection→agent; auth header |
| `app/routes/connections_setup.py` / `connections_credentials.py` / `connections_lifecycle.py` | create | /me/connections (from bots_* split) |
| `app/routes/agents_setup.py` / `agents_lifecycle.py` / `agents_status.py` | create | /me/agents + combined create flow |
| `app/routes/bots_setup.py` / `bots_lifecycle.py` / `bots_status.py` / `bots_credentials.py` / `bots_web_support.py` | delete | superseded |
| `app/routes/web_player.py` / `web_lobby.py` / `admin_web.py` / `web_viewer.py` / `nav_context.py` / `auth.py` | modify | agent/connection references; two nav entries |
| `app/read_models/leaderboard.py` | modify | row = Agent; label model; ai/bot views |
| `mcp_server/server.py` | modify | header/key naming (tools proxy same agent API) |
| `scripts/agentludum_agent.py` | modify | key by connection; carry each agent's model per session |
| `app/templates/connections/*` + `agents/*` | create | split from `bots/`; combined create flow; drop MCP-direct path |
| `app/templates/bots/*` | delete | old templates |
| `migrations/versions/0023_connection_agent_split.py` | create | destructive reshape (pre-launch) |
| `tests/**` (~34 files) | modify | fixtures bot→connection+agent; new turn-resolution + bot-seating tests |

## Migration Steps

1. Dev/test: rebuild schema from models (`Base.metadata.create_all`) — no migration needed to run tests.
2. Prod (pre-launch, no data): `0023_connection_agent_split` — drop `strategy_prompts`, `players`, `bots`; create `connections`, `agents`, `players` (new shape), `strategy_prompts` (agent_id FK); recreate indexes/constraints. Then reset prod DB + `alembic upgrade head`.
3. Keep `tests/test_migrations.py` green: `alembic upgrade head` must pass on SQLite (batch-mode discipline for any in-place alter).

## Data Model

- **Connection**: `connections` — user_id, provider, key_lookup/prev_key_lookup/key_hint, status, runner/health fields, max_concurrent_games/stall_threshold. No model, no name.
- **Agent**: `agents` — user_id, nullable connection_id (NULL⇔bot), kind(ai/bot), name, game, model, status, archived_at, bot_* config. UNIQUE(user_id,name). Invariant: ai⇒connection+model, bot⇒no connection.
- **Player**: `players` — agent_id FK (was bot_id), seat_name (was agent_id string), +strategy_snapshot. UNIQUE(agent_id,match_id), UNIQUE(match_id,seat_name).
- **Strategy**: `strategy_prompts` — agent_id FK (was player_id); agent's current = latest row; bots have none.

## Key Constraints

- **Turn resolution fans out over a connection's agents** — Why: one login serves many competitors (US2/US3); riskiest unit (past mid-deploy freeze), isolate it and test heaviest.
- **next-turn payload names the agent (id/name/model)** — Why: one runner must keep a distinct session per (agent, match) and drive each with the right model.
- **kind=ai ⇒ connection+model; kind=bot ⇒ no connection** — Why: enforces "a bot has no login, an AI agent is always powered."
- **Provider on connection, model on agent** — Why: one login fields many models for benchmarking; provider is fixed (it IS the login).
- **Strategy on agent + per-match snapshot** — Why: an agent is a (model+strategy) competitor; snapshot keeps completed-match history accurate (FR-012).
- **Pre-launch destructive reshape, no backfill** — Why: nothing is live; simplest correct schema change.
- **Delete-connection blocked while it powers agents** — Why: avoid orphaning competitors/standings.
- **Implementation on hold** — Why: ~6 concurrent bot/sim/leaderboard branches overlap these files.
