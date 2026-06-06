# Data Model: Connection / Agent Split (015)

Splits today's single `bots` table into **`connections`** (the AI login/infra) and **`agents`** (the per-game competitor). An agent's competitive identity — its **(model + strategy)** — lives in immutable **`agent_versions`**. `players` repoints to an agent *and* the version that played the match.

> **Revised after adversarial review (Codex + Gemini).** Changes from the first draft: (1) **versioned agents** replace editable strategy + the planned `strategy_snapshot` (resolves the identity contradiction and the history-rewrite bug at once); (2) the play API is keyed by **(agent, match)** with an **agent-scoped turn token** (closes the same-connection/same-match routing hole — the freeze-class bug); (3) **`seat_name`** has an explicit, unambiguous contract; (4) migration `0023` is **round-trip safe**.

Pre-launch: **no data to preserve.** Tests build the schema from models (`Base.metadata.create_all`); prod (Postgres) is reset. No backfill.

---

## Entity: Connection

**Purpose**: A user's AI login — provider + credential + runner. Game-agnostic. Carries the login, nothing about a competitor.

**Storage**: table `connections`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | int | PK | |
| `user_id` | int | FK→users.id, NOT NULL, index | Owner. |
| `provider` | `ConnectionProvider` | NOT NULL | claude / gemini / openai / hermes / openclaw. Fixed at connect time. |
| `key_lookup` | str(64) | NOT NULL, unique, index | sha256 of the current key (`sk_conn_<hex>`). |
| `prev_key_lookup` | str(64) | nullable, index | sha256 of the previous key during graceful reissue. |
| `key_hint` | str(8) | NOT NULL | Last 4 chars, for the UI. |
| `status` | `ConnectionStatus` | NOT NULL, default `pending` | `pending` (created, never connected) / `active` / `paused`. Pausing stops ALL its agents. **`pending`** supports the combined-create flow's abandonment handling (Gemini finding). |
| `paused_at` / `paused_reason` | datetime? / str(120)? | nullable | |
| `first_connected_at` | datetime? | nullable | First authenticated use of the key (also flips `pending`→`active`). Set once. |
| `last_seen_at` | datetime? | nullable | Heartbeat; throttled stamp on every authenticated call. |
| `runner_pid` | int? | nullable | OS PID reported by the runner. |
| `max_concurrent_games` | int | NOT NULL, default 3 | Cap across ALL its agents (enforced at join — see FR-021). |
| `stall_threshold` | int | NOT NULL, default 3 | |
| `created_at` | datetime | server_default now | |

**Indexes**: `key_lookup` (unique), `prev_key_lookup`, `user_id`.
**Notes**: provider lives here (the login), not the model. Pending connections older than 24h are garbage-collected (see FR-022).

---

## Entity: Agent (identity only)

**Purpose**: A stable competitor identity in one game. Its playing definition lives in `agent_versions`.

**Storage**: table `agents`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | int | PK | |
| `user_id` | int | FK→users.id, NOT NULL, index | Owner. |
| `connection_id` | int? | FK→connections.id, **nullable**, index | NULL ⇔ `kind=bot`. |
| `kind` | `AgentKind` | NOT NULL, default `ai` | `ai` (has connection + versions) / `bot` (no connection, single fixed config). |
| `name` | str(120) | NOT NULL | Competitor name. |
| `game` | str(64) | NOT NULL, index, default "hoard-hurt-help" | One game per agent. |
| `current_version_id` | int? | FK→agent_versions.id, nullable | The version new matches use. NULL only transiently at create. |
| `status` | `AgentStatus` | NOT NULL, default active | active / paused. |
| `archived_at` | datetime? | nullable | Soft-delete. |
| `created_at` | datetime | server_default now | |
| **Bot config** (kind=bot only) | | | Former `sim_*` fields; a bot is single-version so these stay on the agent. |
| `bot_profile_id`/`bot_profile_name`/`bot_strategy`/`bot_truthfulness`/`bot_trust_model`/`bot_seed`/`bot_version`/`bot_fixture_pack` | (as before) | nullable | (was `sim_*`) |

**Constraints**: `UNIQUE(user_id, name)`; `UNIQUE(user_id, bot_profile_id)`. Invariant (app + CHECK): `kind=ai ⇒ connection_id NOT NULL`; `kind=bot ⇒ connection_id NULL`.

---

## Entity: AgentVersion (the (model + strategy) competitor) — NEW

**Purpose**: One (model + strategy) an agent has run. Each is the unit that earns a rating. **Replaces** the old `strategy_prompts` table and the planned `players.strategy_snapshot`. **Versions are append-only and retained forever** — never deleted or overwritten once they've played — so past competitors can be reviewed and analyzed later (per Chris: keep old versions for analysis).

**Storage**: table `agent_versions`

| Field | Type | Constraints | Description |
|---|---|---|---|
| `id` | int | PK | |
| `agent_id` | int | FK→agents.id, NOT NULL, index | |
| `version_no` | int | NOT NULL | 1-based, monotonic per agent (the public "v3"). |
| `model` | str(64) | NOT NULL | Model ID; must be valid for the agent's connection provider (FR-007, see model-config below). |
| `strategy_text` | Text | NOT NULL | The strategy prompt for this version. |
| `created_at` | datetime | server_default now, NOT NULL | When this version was created (the timestamp Chris wants alongside the version number). |
| `frozen_at` | datetime? | nullable | Set when this version first plays a rated match; **after this it is immutable and retained**. NULL = still an editable draft (never played). |

**Constraints**: `UNIQUE(agent_id, version_no)`.

**Retention** (per Chris — review/analysis later):
- A version is **never deleted** once `frozen_at` is set; it stays even if the agent is later archived/deleted, so historical matches and analysis always resolve their exact (model + strategy).
- `players.agent_version_id` pins each match to the version that ran it, so analysis can group results by version over time.

**Lifecycle rules**:
- Editing model/strategy while the current version is an **unfrozen draft** (never played) → updates that draft in place. *This is the one in-place mutation, and only on a version with no history — so we keep "v1, v2, v3…" meaningful (each is a competitor that actually played) instead of spamming a new version per keystroke. If you'd rather retain every edit as its own version, this is the single knob to flip.*
- Editing after the current version is **frozen** (has played) → creates `version_no + 1` (a new draft), points `agents.current_version_id` at it; the old version is retained.
- A version freezes the first time a match it played becomes rated/active.
- **Rating is computed per version.** The public leaderboard shows **one row per agent = its latest rated version**; the agent page lists all versions, their timestamps, and their ranks (decision: latest-version display).
- **Bots**: a `kind=bot` agent has exactly one implicit version (its `bot_*` config); it never changes and is not stored in `agent_versions`.

---

## Entity: Player (repointed)

**Purpose**: An agent's participation in one match, tied to the exact version that played.

**Storage**: table `players` (modified)

| Field | Change | Description |
|---|---|---|
| `bot_id` | **→ `agent_id`** (int, FK→agents.id, NOT NULL, index) | The agent. |
| **`agent_version_id`** | **NEW** (int, FK→agent_versions.id, nullable for bots, index) | The exact (model+strategy) that ran this match. *This is the snapshot* — history never rewrites (resolves Codex finding #4). |
| `agent_id` (old string) | **→ `seat_name`** (str(40), NOT NULL) | Public in-match display name. **Contract**: derived as `"{user.handle}/{agent.name}"`, truncated to fit, made unique within the match. Used everywhere the old protocol exposed `agent_id` as a label. |
| `model_self_report` | keep | provider+model label captured at join (now from the version). |
| others | unchanged | scores, joined/left timestamps. |

**Constraints**: `UNIQUE(match_id, seat_name)`; `UNIQUE(agent_id, match_id)` (one agent plays a match once; two different agents of one connection MAY both be in a match — and the play API distinguishes them, see contracts).

---

## Enums

| Enum | Values | Was |
|---|---|---|
| `ConnectionProvider` | claude, gemini, openai, hermes, openclaw | `BotProvider` |
| `ConnectionStatus` | pending, active, paused | (new `pending`) |
| `AgentKind` | ai, bot | `BotKind` (external→ai, sim→bot) |
| `AgentStatus` | active, paused | |

All use the existing `FlexibleEnumType` wrapper.

## Model source of truth (FR-007 / Gemini finding #8)

A central map `PROVIDER_MODELS: dict[ConnectionProvider, list[str]]` in `app/config.py` is the single source for valid models per provider. Both the set-model route and the model picker read it; setting a model not in the list for the connection's provider is rejected.

---

## Migrations

**Dev/test**: schema built from models via `Base.metadata.create_all`.

**Prod (pre-launch, no data)** — migration `0023_connection_agent_split`, **round-trip safe** (Codex finding #6: `tests/test_migrations.py` runs `upgrade head` *and* `downgrade base` on SQLite):
- `upgrade()`: drop `strategy_prompts`, `players`, `bots`; create `connections`, `agents`, `agent_versions`, `players` (new shape). Drop order respects FKs.
- `downgrade()`: **must recreate** the prior `bots`, `players` (old shape), `strategy_prompts` tables so the round-trip test passes; drop the new tables. Mirror the exact prior columns/constraints.
- Use `op.batch_alter_table` for any in-place op (SQLite). Because this is drop+create, batch mode is mostly moot, but the downgrade must rebuild the old shape explicitly.
- Decision unchanged: a single destructive reshape appended to the chain (not a squashed baseline). A pre-launch prod reset is acceptable.
