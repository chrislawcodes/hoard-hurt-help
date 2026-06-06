# Implementation Plan: Connection / Agent Split (015)

**Branch**: `015-connection-agent-split` | **Date**: 2026-06-06 | **Spec**: [spec.md](./spec.md)

## Summary

Split the single `Bot` concept into **`Connection`** (a user's AI login: provider + key + runner) and **`Agent`** (a single-game competitor = name + game + model + strategy). Repoint `Player` to agents, move strategy ownership to the agent, rename the deterministic "Sim" opponents to **Bots** (an agent kind with no connection), and drop the MCP-direct connect path. Pre-launch, so the schema is reshaped and recreated rather than migrated with data preserved.

## Technical Context

**Language/Version**: Python ≥3.11 (async throughout)
**Framework**: FastAPI; SQLAlchemy 2.0 (asyncio); Jinja2 + HTMX templates; FastMCP (`mcp_server/`)
**Storage**: SQLite (dev/test, aiosqlite) / PostgreSQL (prod, Alembic; chain at 0022)
**Testing**: pytest + pytest-asyncio; test DB is SQLite in-memory built from models (`Base.metadata.create_all`)
**Target Platform**: Railway single-instance; server-rendered pages, SSE/HTMX fragments
**Performance/Scale**: Small; per-turn deadlines already exist. No new perf targets — correctness and the green preflight are the bar.
**Constraints (from spec)**: pre-launch (no data); auth/turn-resolution is the one high-risk unit; one feature per branch; preflight must pass at phase boundaries.

## Constitution Check (CLAUDE.md)

**Status: PASS**

- **Async & types** — all new handlers/DB calls `async`; full type annotations (mypy gate). ✔ planned
- **No suppressions / no bare except** — none introduced; fix root causes. ✔
- **Testing** — new engine/turn logic (turn resolution across a connection's agents; bots as connectionless agents) gets focused tests; test DB stays SQLite in-memory. ✔ (see Slice 1 & 5)
- **File structure** — split by domain: `connections_*` vs `agents_*` routes; templates `connections/` + `agents/`; no vague `utils.py`. ✔
- **Delivery** — single branch `015-connection-agent-split`; no push/merge during plan/tasks; preflight before any push. ✔
- **High-care area** — turn resolution previously caused a mid-deploy freeze; it gets the heaviest test coverage and lands as an isolated slice. ⚠ flagged

## Architecture Decisions

### Decision 1: Two tables (`connections` + `agents`), not one table with a discriminator

**Chosen**: Separate `connections` and `agents` tables; `agents.connection_id` is a nullable FK (NULL ⇔ bot).

**Rationale**: The two concepts have genuinely different lifecycles (a login vs a competitor) and a one-to-many relationship (one connection → many agents). A single table with a "kind" discriminator would re-merge what we are deliberately separating and would not express one-login-many-competitors.

**Alternatives**: (a) keep `bots`, add `agents` as a view — rejected, can't carry per-agent model/strategy; (b) single polymorphic table — rejected, re-merges the concepts.

**Tradeoffs**: +clear model, +natural benchmarking; −a join to render an agent's provider (cheap, indexed).

### Decision 2: Strategy stays in `strategy_prompts`, repointed player→agent

**Chosen**: Keep the versioned `strategy_prompts` table; change its FK from `player_id` to `agent_id`. The agent's current strategy = its latest row. Each `Player` snapshots the strategy text at match start (`players.strategy_snapshot`).

**Rationale**: Smallest change that satisfies "strategy lives on the agent" while preserving version history and the snapshot-for-history requirement (FR-012). Bots have no strategy_prompts (deterministic from `bot_*`).

**Alternatives**: inline `strategy_text` on `agents` — rejected (loses versioning, more route churn).

### Decision 3: Auth resolves a Connection; turn resolution fans out to its agents

**Chosen**: `X-Connection-Key` (prefix `sk_conn_`) → `require_connection` → a `Connection`. `/api/agent/next-turn` returns the most urgent turn across **all agents on that connection**, and the payload names the agent (→ its model, strategy, game) so the runner drives the right session.

**Rationale**: One login serves many competitors (US2/US3). This is the load-bearing change and the riskiest; it lands as its own slice with the most tests.

**Tradeoffs**: turn selection now ranges over a set of players (one per agent) rather than one bot's players — same query shape, wider `IN` set. Keep the existing urgency ordering.

### Decision 4: One destructive reshape migration (pre-launch), tests build from models

**Chosen**: append `0023_connection_agent_split` that drops `strategy_prompts/players/bots` and creates the new tables; tests keep using `create_all` from models. Prod is reset and `alembic upgrade head`.

**Rationale**: No data to preserve, so a destructive reshape is the simplest correct step. Squashing the 0001–0022 chain is unnecessary and risky.

**Constraint**: `tests/test_migrations.py` must still pass `alembic upgrade head` on SQLite — keep batch-mode discipline for any in-place alters.

### Decision 5: Combined create-agent flow; provider fixed per connection

**Chosen**: "New agent" detects no connection and walks provider→connect→name+model inline (US1); with a connection present it's pick-connection→name+model+strategy (US2). Provider is set at connect time and not switchable in place (make another connection to use another provider).

### Decision 6: Drop the MCP-direct "Advanced" connect path

**Chosen**: remove the `<details>` MCP path and its copy; the runner is the only connect method. Removes the only HHH-rule-hardcoded connect surface.

## Project Structure

Monolithic `app/` + `mcp_server/`. Files this feature creates or changes:

```
app/models/
  connection.py            CREATE  — Connection model + ConnectionProvider/ConnectionStatus enums
  agent.py                 CREATE  — Agent model + AgentKind/AgentStatus enums (former Bot fields + bot_* config)
  player.py                MODIFY  — bot_id→agent_id FK, agent_id(str)→seat_name, +strategy_snapshot, constraints
  strategy_prompt.py       MODIFY  — player_id → agent_id FK
  bot.py                   DELETE  — replaced by connection.py + agent.py
  __init__.py              MODIFY  — export new models, drop Bot

app/deps.py                MODIFY  — require_bot→require_connection (X-Connection-Key); require_bot_player→require_agent_player
app/engine/bot_activity.py → connection_activity.py  RENAME/MODIFY — mark_seen on a Connection
app/engine/sims/*          MODIFY  — operate on bot-kind agents (seating/runtime/service); naming sweep sim→bot
app/routes/
  agent_next_turn.py       MODIFY  — turn resolution across a connection's agents (HIGH-CARE)
  agent_api.py             MODIFY  — resolve player via (connection→agent) ; submit/turn/state/leave
  connections_setup.py     CREATE  — /me/connections list + create + detail (from bots_setup split)
  connections_credentials.py CREATE — reissue/revoke/runner status (from bots_credentials)
  connections_lifecycle.py CREATE  — pause/resume/delete a connection (block delete if it powers agents)
  agents_setup.py          CREATE  — /me/agents list + combined create flow + detail
  agents_lifecycle.py      CREATE  — rename/pause/delete an agent, set model/strategy
  agents_status.py         CREATE  — agent onboarding/health fragments (from bots_status)
  bots_setup.py / bots_lifecycle.py / bots_status.py / bots_credentials.py / bots_web_support.py  DELETE/REWRITE
  web_player.py            MODIFY  — join uses agent; strategy edit on agent; seat_name
  web_lobby.py             MODIFY  — agent references
  admin_web.py             MODIFY  — agent/connection references
  web_viewer.py            MODIFY  — display name from agent
  nav_context.py           MODIFY  — two nav entries: Connections, Agents
  auth.py                  MODIFY  — bot references

app/read_models/leaderboard.py  MODIFY — a row = an Agent; label model; ai vs bot views
mcp_server/server.py            MODIFY — header/key naming; tools proxy the same agent API (no MCP-direct path to remove here)
scripts/agentludum_agent.py     MODIFY — key by connection; carry each agent's model per session

app/templates/
  connections/  CREATE  — list/detail/_health_badge/_reconnect
  agents/       CREATE  — list/detail/_status (combined create flow)
  bots/         DELETE   — old templates
  base.html / nav        MODIFY — nav entries

migrations/versions/0023_connection_agent_split.py  CREATE — destructive reshape

tests/  MODIFY (~34 files) — fixtures bot→connection+agent; new tests for turn-resolution fan-out and bot-as-agent seating
```

**Structure Decision**: routes and templates split cleanly along the connection/agent seam; the only shared, high-risk logic is the agent API + next-turn resolution.

## Implementation Sequencing (vertical slices; preflight green at each boundary)

> Implementation is **on hold** (concurrent branches). This is the order to use when it resumes.

- **Slice 0 — Models + schema (no behavior change yet).** Create `connection.py`/`agent.py`, modify `player.py`/`strategy_prompt.py`, delete `bot.py`, update `__init__`. Add migration 0023. Get `create_all` + `alembic upgrade head` (SQLite) green. Tests still red elsewhere — that's expected within the slice; the boundary target is models import + migration tests.
- **Slice 1 — Auth + turn resolution (HIGH-CARE).** `require_connection`, `connection_activity.mark_seen`, and `/api/agent/next-turn` fan-out across a connection's agents with agent identification in the payload. **Heaviest tests here** (single agent, multiple agents, paused connection, urgency ordering, agent identification). Update `agent_api.py` player resolution.
- **Slice 2 — Bots as connectionless agents.** `engine/sims/*` → operate on `kind=bot` agents; seating fills matches with bots; leaderboard distinguishes ai/bot. Tests: a bot has no connection, plays deterministically, appears labeled.
- **Slice 3 — Agent management (US5/US6/US7).** `/me/agents` + `/me/connections` routes & templates; combined create flow; strategy on the agent with active-match block + per-match snapshot; leaderboard row = agent + model.
- **Slice 4 — Runner + MCP + drop MCP-direct.** Runner keys by connection, carries each agent's model; remove the Advanced MCP connect path/copy.
- **Slice 5 — Sweep + rename hygiene (US8) + full preflight.** Grep for residual `bot`/`sim` in code/copy, fix nav, ensure no `/me/bots` route or `Bot` class remains; whole-suite `ruff` + `mypy` + `pytest -q` green (SC-007).

Each slice ends with the relevant tests passing; the final boundary is the full preflight.
