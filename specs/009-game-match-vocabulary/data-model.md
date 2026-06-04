# Data Model: Game/Match Rename

This feature renames an existing model and its FK columns; it adds no new entities. Below is the before → after for every affected table, plus the migration shape.

## Entities (renamed/modified)

### Match (was Game)

**Storage**: table `games` → **`matches`**
**File**: `app/models/game.py` → `app/models/match.py`, class `Game` → `Match`

| Field (after) | Was | Type | Constraints | Notes |
|---|---|---|---|---|
| `id` | `id` | String(32) | PK | value `G_NNNN` → **`M_NNNN`** (rewritten) |
| `game` | `game_type` | String | NOT NULL, default `"hoard-hurt-help"` | title slug; **column renamed**, registry key string unchanged |
| `name` | `name` | String(255) | NOT NULL | unchanged |
| `state` | `state` | enum GameState | NOT NULL | enum **name unchanged** (`GameState`) — see note |
| `scheduled_start`, `started_at`, `completed_at`, `cancelled_at` | same | DateTime | — | unchanged |
| `min_players`, `max_players`, `per_turn_deadline_seconds`, `total_rounds`, `turns_per_round`, `current_round`, `current_turn`, `rounds_awarded`, `rules_version` | same | Integer/String | — | unchanged |
| `winner_player_id` | same | Integer | FK players.id, `use_alter` | FK constraint **name** `fk_games_winner_player_id_players` → `fk_matches_winner_player_id_players` |
| `created_at` | same | DateTime | — | unchanged |

> **Note on `GameState`**: the lifecycle enum is internal and named for the *state machine*, not a single play. Keep the name `GameState` (renaming it ripples through the state machine for no user value, and it's not part of the overloaded UX vocabulary). This is a deliberate scope boundary.

### Player

**Storage**: table `players` (name unchanged)
**File**: `app/models/player.py`

| Field (after) | Was | Notes |
|---|---|---|
| `match_id` | `game_id` | String(32), FK `matches.id`, NOT NULL, indexed — **value rewritten** `G_`→`M_` |
| UniqueConstraint `uq_players_match_id_agent_id` | `uq_players_game_id_agent_id` | renamed |
| UniqueConstraint `uq_players_bot_id_match_id` | `uq_players_bot_id_game_id` | renamed |

### Turn

**Storage**: table `turns` (name unchanged)
**File**: `app/models/turn.py`

| Field (after) | Was | Notes |
|---|---|---|
| `match_id` | `game_id` | String(32), FK `matches.id`, NOT NULL, indexed — **value rewritten** |
| UniqueConstraint `uq_turns_match_id_round_turn` | `uq_turns_game_id_round_turn` | renamed |

`TurnSubmission`, `TurnMessage`: **unchanged** (link via `turn_id` / `player_id`; no game/match id).

### RequestIncident

**Storage**: table `request_incidents` (name unchanged)
**File**: `app/models/request_incident.py`

| Field (after) | Was | Notes |
|---|---|---|
| `match_id` | `game_id` | String(32), nullable, indexed — **plain string, not a real FK**; value rewritten |

### Untouched (confirmed by reading models)

`strategy_prompts` (links via `player_id`), `bots`, `users` — no game/match id column. The spec's mention of `StrategyPrompt.match_id` was incorrect; it links through `player_id`.

## Migration: `0018_rename_game_to_match`

Single atomic migration. All structural ops use `op.batch_alter_table` (SQLite requirement).

**Upgrade order**:
1. `batch_alter_table("games")`: rename column `game_type`→`game`; rename FK `fk_games_winner_player_id_players`→`fk_matches_winner_player_id_players`. Then `op.rename_table("games", "matches")`.
2. `batch_alter_table("players")`: rename `game_id`→`match_id`; drop FK to games; rename unique constraints + index. (Re-add FK in step 5.)
3. `batch_alter_table("turns")`: rename `game_id`→`match_id`; drop FK to games; rename unique constraint + index.
4. `batch_alter_table("request_incidents")`: rename `game_id`→`match_id`; rename index. (No FK.)
5. **Data rewrite** (shared helper `swap_prefix`): `UPDATE matches SET id = 'M_'||substr(id,3) WHERE id LIKE 'G\_%'`; same for `players.match_id`, `turns.match_id`, `request_incidents.match_id`.
6. Re-add FKs `players.match_id→matches.id`, `turns.match_id→matches.id` (batch).

**Downgrade**: reverse the column/table/constraint renames and swap `M_`→`G_`. If a clean downgrade proves infeasible under SQLite batch mode for the value rewrite, document irreversibility in the migration docstring and the test asserts forward-only.

**Shared helper**: the `G_`↔`M_` prefix-swap and the per-table row-count logic live in one module imported by both the migration and `scripts/preview_match_id_migration.py`, so the dry-run plan and the applied change can't drift.

## Verification queries (used by tests + post-deploy)

```sql
-- counts must be identical before/after
SELECT count(*) FROM matches;
SELECT count(*) FROM players;
SELECT count(*) FROM turns;
SELECT count(*) FROM turn_submissions;
SELECT count(*) FROM request_incidents;
-- zero orphans
SELECT count(*) FROM players  p LEFT JOIN matches m ON p.match_id=m.id WHERE m.id IS NULL;
SELECT count(*) FROM turns    t LEFT JOIN matches m ON t.match_id=m.id WHERE m.id IS NULL;
-- no stragglers
SELECT count(*) FROM matches WHERE id LIKE 'G\_%';
```
