# Plan Summary: Game/Match Rename

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/game.py` → `app/models/match.py` | rename+modify | class `Game`→`Match`; `game_type`→`game`; keep enum name `GameState` |
| `app/models/player.py` | modify | `game_id`→`match_id`; rename 2 unique constraints + index |
| `app/models/turn.py` | modify | `Turn.game_id`→`match_id`; rename unique constraint |
| `app/models/request_incident.py` | modify | `game_id`→`match_id` (plain string col + index) |
| `app/models/__init__.py` | modify | export `Match` |
| `app/engine/tokens.py` | modify | `generate_game_id`→`generate_match_id`, `"G_"`→`"M_"` |
| `app/engine/scheduler.py`,`resolver.py`,`next_turn.py`,`bot_activity.py`,`game_insights.py` | modify | `Game`→`Match`, `game_id`→`match_id` |
| `app/engine/sims/{service,seating,types}.py` | modify | `game_id`→`match_id` |
| `app/games/base.py`,`app/games/__init__.py` | modify (minimal) | registry key string stays; align reads of renamed `game` column |
| `app/games/hoard_hurt_help/game.py` | modify | `Game`→`Match`, `game_id`→`match_id` |
| `app/schemas/agent.py`,`app/schemas/spectator.py` | modify | `match_id` canonical + `game_id` alias (req); both keys in responses |
| `app/routes/web.py` | modify | nested `/games/<game>/matches/<id>`; `/play`→`/games` + `G_`→`M_` 301 redirects |
| `app/routes/agent_api.py`,`agent_next_turn.py`,`spectator_api.py`,`admin_api.py`,`admin_web.py`,`sse.py`,`bots_web.py` | modify | `/api/matches` canonical + `/api/games` alias |
| `app/deps.py`,`app/broadcast.py`,`app/request_logging.py` | modify | `game_id`→`match_id` |
| `app/templates/**` | modify | copy: "game"=title, "match"=single play |
| `mcp_server/server.py`,`mcp_server/README.md` | modify | tool **names stable**; accept `game_id`+`match_id` args |
| `migrations/versions/0018_rename_game_to_match.py` | create | atomic schema rename + `G_`→`M_` rewrite, batch mode |
| `scripts/preview_match_id_migration.py` | create | `--dry-run` mapping + row counts, read-only |
| `app/engine/match_id_rewrite.py` (or similar) | create | shared prefix-swap + count helper (imported by migration + preview) |
| `tests/test_migrations.py` | modify | assert 0018 upgrades on SQLite, counts preserved, zero orphans |
| `tests/test_match_rename_*.py` | create | model/alias/redirect/MCP coverage |
| `DESIGN.md`,`docs/writing-a-game-module.md` | modify | vocabulary update |

## Migration Steps

1. `batch_alter_table("games")`: `game_type`→`game`, rename winner FK constraint; then `rename_table games→matches`.
2. `batch_alter_table("players")`: `game_id`→`match_id`, drop FK to games, rename uniques + index.
3. `batch_alter_table("turns")`: `game_id`→`match_id`, drop FK to games, rename unique.
4. `batch_alter_table("request_incidents")`: `game_id`→`match_id`, rename index.
5. Data rewrite: `UPDATE ... SET <col> = 'M_'||substr(<col>,3) WHERE <col> LIKE 'G\_%'` for matches.id, players.match_id, turns.match_id, request_incidents.match_id.
6. Re-add FKs players.match_id→matches.id, turns.match_id→matches.id.

Pre-apply: review `scripts/preview_match_id_migration.py --dry-run` output (mapping + counts) against a prod DB copy.

## Data Model

- **Match**: `matches` — PK `id` (`M_NNNN`), `game` (title slug), `state`, scheduling, `winner_player_id`→players.id.
- **Player**: `players` — `match_id`→matches.id; uniques on (match_id, agent_id) and (bot_id, match_id).
- **Turn**: `turns` — `match_id`→matches.id; unique (match_id, round, turn).
- **RequestIncident**: `request_incidents` — `match_id` (plain string, nullable).
- Unchanged: `turn_submissions`, `turn_messages`, `strategy_prompts` (link via turn_id/player_id), `bots`, `users`.

## Key Constraints

- **Atomic migration**: schema rename + ID rewrite in one transaction — *Why: a crash must leave the DB fully-old or fully-new, never half-renamed (FR-008).*
- **Batch mode for all DDL**: `op.batch_alter_table` — *Why: SQLite can't ALTER constraints in place; `alembic upgrade head` dies on dev/test DBs otherwise (MEMORY: sqlite-migration-batch-mode).*
- **Drop→update→re-add FKs**: — *Why: the `G_`→`M_` prefix swap momentarily breaks referential integrity; Postgres checks FKs at statement end, so FKs must be absent during the UPDATE.*
- **`--dry-run` before live apply**: — *Why: data-critical-waves rule; the printed counts must equal the post-apply counts (SC-005).*
- **API + MCP aliases, dual-key responses**: — *Why: live bots mid-match must not break (US2, FR-011–014).*
- **MCP tool names stay (`get_game_state` etc.)**: only arg names move — *Why: renaming tool names is a breaking change to the public contract.*
- **`app/games/` semantics untouched**: registry key string unchanged — *Why: that dir correctly means "title"; only the single-play model is wrong (FR-016).*
- **Deterministic `G_`→`M_` redirect**: prefix swap, no lookup table — *Why: old bookmarks must not 404 (FR-010).*
