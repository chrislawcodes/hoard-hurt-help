# Data Model: Connection / Agent Split (015)

Splits today's single `bots` table into **`connections`** (the AI login/infra) and **`agents`** (the per-game competitor). `players` repoints from a bot to an agent. Strategy ownership moves from per-player to per-agent, with a per-match snapshot kept for history.

Pre-launch: **no data to preserve.** Tests build the schema from the models (`Base.metadata.create_all`); prod (Postgres) is reset and re-created from a fresh destructive migration. No backfill.

---

## Entity: Connection

**Purpose**: A user's AI login — provider + credential + runner. Game-agnostic. One user may own several. Carries everything that is "the login," and nothing about a competitor.

**Storage**: table `connections`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | int | PK | |
| `user_id` | int | FK→users.id, NOT NULL, index | Owner. |
| `provider` | `ConnectionProvider` | NOT NULL | claude / gemini / openai / hermes / openclaw. Fixed at connect time. |
| `key_lookup` | str(64) | NOT NULL, unique, index | sha256 of the current key (`sk_conn_<hex>`). Plaintext never stored. |
| `prev_key_lookup` | str(64) | nullable, index | sha256 of the previous key during a graceful reissue. |
| `key_hint` | str(8) | NOT NULL | Last 4 chars of the key, for the UI. Not secret. |
| `status` | `ConnectionStatus` | NOT NULL, default active | active / paused. Pausing stops ALL its agents. |
| `paused_at` | datetime? | nullable | |
| `paused_reason` | str(120)? | nullable | |
| `first_connected_at` | datetime? | nullable | First authenticated use of the key. Set once; reissue does not clear it. |
| `last_seen_at` | datetime? | nullable | Heartbeat; throttled stamp on every authenticated call. |
| `runner_pid` | int? | nullable | OS PID reported by the runner at startup. |
| `max_concurrent_games` | int | NOT NULL, default 3 | Token-budget guardrail (was on Bot). Connection-level: it caps the runner's load across all its agents. |
| `stall_threshold` | int | NOT NULL, default 3 | Consecutive missed turns before flag/auto-pause. |
| `created_at` | datetime | server_default now | |

**Indexes**: `key_lookup` (unique), `prev_key_lookup`, `user_id`.

**Notes**:
- Provider lives here (the login), **not** the model. Model is per-agent.
- No `name` on a connection in this cut — it's identified by provider + key_hint in the UI (e.g. "Claude · …a1b2"). (Optional friendly name is a later nicety.)

---

## Entity: Agent

**Purpose**: A single competitor in a single game, defined by **name + game + model + strategy**. The leaderboard entity. Either an AI agent (has a connection, runs on a model) or a Bot (no connection, deterministic).

**Storage**: table `agents`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | int | PK | |
| `user_id` | int | FK→users.id, NOT NULL, index | Owner. |
| `connection_id` | int? | FK→connections.id, **nullable**, index | The login powering it. **NULL ⇔ kind=bot.** |
| `kind` | `AgentKind` | NOT NULL, default ai | `ai` (has connection) / `bot` (no connection, deterministic). |
| `name` | str(120) | NOT NULL | Competitor name; the in-match display derives from this. |
| `game` | str(64) | NOT NULL, index, default "hoard-hurt-help" | Game slug. One game per agent. |
| `model` | str(64)? | nullable | Model ID (e.g. "claude-sonnet-4-6"). Required for `ai`; NULL for `bot`. Must be valid for the connection's provider. |
| `status` | `AgentStatus` | NOT NULL, default active | active / paused (an agent can be paused independently of its connection). |
| `archived_at` | datetime? | nullable | Soft-delete (kept if it has match history). |
| `created_at` | datetime | server_default now | |
| **Strategy (current)** | | | The agent's current strategy text — see "Strategy ownership" below. |
| **Bot config** (kind=bot only) | | | The former `sim_*` fields, used only when `kind=bot`. |
| `bot_profile_id` | str(64)? | nullable, index | Preset catalog identity (was `sim_profile_id`). |
| `bot_profile_name` | str(120)? | nullable | (was `sim_profile_name`) |
| `bot_strategy` | str(64)? | nullable | Deterministic strategy key (was `sim_strategy`). |
| `bot_truthfulness` | int? | nullable | (was `sim_truthfulness`) |
| `bot_trust_model` | str(64)? | nullable | (was `sim_trust_model`) |
| `bot_seed` | int? | nullable | (was `sim_seed`) |
| `bot_version` | str(32)? | nullable | (was `sim_version`) |
| `bot_fixture_pack` | str(64)? | nullable | (was `sim_fixture_pack`) |

**Constraints**:
- `UNIQUE(user_id, name)` — owner can tell agents apart (was on Bot).
- `UNIQUE(user_id, bot_profile_id)` — one agent per preset per user (was the sim_profile_id constraint).
- **Invariant** (enforced in app + ideally a CHECK): `kind=ai ⇒ connection_id NOT NULL AND model NOT NULL`; `kind=bot ⇒ connection_id NULL`.

**Indexes**: `user_id`, `connection_id`, `game`, `bot_profile_id`.

**Strategy ownership** (decision): the agent owns its strategy. Two viable shapes — the plan picks **A**:
- **A (chosen): keep the `strategy_prompts` table, repoint `player_id` → `agent_id`.** Versioned per edit, `is_default` flag retained. The agent's "current strategy" = latest row for that agent. Smallest change to existing strategy code; preserves versioning. A Bot has no strategy_prompts rows (its play is deterministic from `bot_*`).
- B (rejected): inline a single `strategy_text` column on `agents`. Loses version history; more migration churn in the strategy routes.

---

## Entity: Player (repointed)

**Purpose**: An agent's participation in one match. Adds a real FK to agents and a per-match strategy snapshot.

**Storage**: table `players` (modified)

| Field | Change | Description |
|---|---|---|
| `bot_id` | **→ `agent_id`** (int, FK→agents.id, NOT NULL, index) | Now references an agent. |
| `agent_id` (old string) | **→ `seat_name`** (str(32), NOT NULL) | The in-match display name; derived from the agent's name. Renamed to free up `agent_id`. |
| `strategy_snapshot` | **NEW** (Text, nullable) | The strategy text this agent actually ran in this match, captured at match start, so later edits don't rewrite history. |
| `model_self_report` | unchanged | What the agent reported itself as at join (provider+model label). |
| others | unchanged | scores, joined/left timestamps. |

**Constraints**:
- `UNIQUE(match_id, seat_name)` (was `UNIQUE(match_id, agent_id)`).
- `UNIQUE(agent_id, match_id)` (was `UNIQUE(bot_id, match_id)`) — one agent plays a match once. Two different agents on one connection MAY both be in a match.

---

## Enums

| Enum | Values | Was |
|---|---|---|
| `ConnectionProvider` | claude, gemini, openai, hermes, openclaw | `BotProvider` |
| `ConnectionStatus` | active, paused | `BotStatus` (connection half) |
| `AgentKind` | ai, bot | `BotKind` (external→ai, sim→bot) |
| `AgentStatus` | active, paused | `BotStatus` (agent half) |

All use the existing `FlexibleEnumType` wrapper.

---

## Migrations

**Dev/test**: schema is built from the models via `Base.metadata.create_all` (tests already do this). No migration needed to run tests.

**Prod (Postgres, pre-launch, no data)**: one **destructive reshape migration** (next in chain, e.g. `0023_connection_agent_split`):
1. `drop table strategy_prompts, players, bots` (no data to keep — confirmed pre-launch).
2. `create table connections`, `create table agents`, `create table players` (new shape), `create table strategy_prompts` (player_id→agent_id).
3. Recreate indexes/constraints.

**Notes**:
- Wrap constraint ops in `op.batch_alter_table` where SQLite dev DBs run the chain (per the known SQLite batch-mode gotcha; guarded by `tests/test_migrations.py`). Because this migration drops-and-creates rather than altering in place, batch mode is mostly moot, but `test_migrations.py` must still pass `alembic upgrade head` on SQLite.
- Decision to confirm in plan: a single destructive reshape migration appended to the chain (chosen) vs squashing the whole 0001–0022 chain into a fresh baseline (rejected — large, risky, and unnecessary when one destructive step suffices pre-launch).
- A pre-launch prod reset (drop schema, `alembic upgrade head`) is acceptable per the spec assumptions.
