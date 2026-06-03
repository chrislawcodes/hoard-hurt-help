# Quickstart: Game/Match Rename — Manual Testing

## Prerequisites

- [ ] Repo on branch `feature/game-match-rename`, deps installed
- [ ] A dev DB with at least one finished match (seed via `scripts/_seed_replay_demo.py` or similar)
- [ ] App runnable locally; MCP server runnable

## US-3: Migration (do this FIRST — it changes the DB)

**Goal**: Existing `G_` matches migrate to `M_` with all data intact.

1. Back up the dev DB: `cp hoardhurthelp.db hoardhurthelp.db.pre0018-bak`
2. Dry run: `python3 scripts/preview_match_id_migration.py --dry-run --db hoardhurthelp.db`
3. Review printed `G_xxxx → M_xxxx` mapping + per-table row counts.
4. Apply: `alembic upgrade head`
5. Verify:
   ```sql
   SELECT id FROM matches LIMIT 5;                 -- all M_
   SELECT count(*) FROM matches WHERE id LIKE 'G\_%';  -- 0
   SELECT count(*) FROM players p LEFT JOIN matches m ON p.match_id=m.id WHERE m.id IS NULL;  -- 0
   ```

**Expected**: counts match the dry-run; no orphans; no `G_` left.

## US-1: URLs & vocabulary

1. Start the app. Visit `/games` → catalog lists Hoard Hurt Help.
2. Visit `/games/hoard-hurt-help` → game home + lobby + list of matches.
3. Open a match → `/games/hoard-hurt-help/matches/M_xxxx`; page says "match," shows `M_xxxx`.
4. Visit old URLs → expect 301:
   - `/play/hoard-hurt-help` → `/games/hoard-hurt-help`
   - `/games/G_0001` → `/games/hoard-hurt-help/matches/M_0001`
   - `/games/G_0001/analysis` → `.../matches/M_0001/analysis`

**Expected**: every page uses "game" for the title, "match" for the play; no 404 on old links.

## US-2: Live bot compatibility

1. OLD shape: `curl -H "X-Agent-Key: <key>" http://localhost:8000/api/games/M_0001/turn` → valid turn.
2. NEW shape: `curl -H "X-Agent-Key: <key>" http://localhost:8000/api/matches/M_0001/turn` → identical.
3. POST submit with `{"game_id": "..."}` body and with `{"match_id": "..."}` body → both recorded.
4. Inspect a turn response → contains both `match_id` and `game_id` (same value).
5. MCP: call `get_game_state` with `game_id` arg and with `match_id` arg → both accepted.

**Expected**: a bot that never changed its code keeps working.

## US-4 / US-5: Code & docs

1. `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` → all green.
2. `grep -rn "class Game\b" app/models` → only the state enum context, model is `Match`.
3. Skim `DESIGN.md` + `docs/writing-a-game-module.md` → vocabulary consistent.

## Troubleshooting

- **`alembic upgrade head` fails with "cannot ALTER"**: a DDL op isn't wrapped in `op.batch_alter_table`. Fix the migration.
- **FK violation during migration**: FKs weren't dropped before the value rewrite. See plan Decision 3.
- **Old URL 404s**: redirect didn't map `G_`→`M_`. Check the redirect handler.
- **Bot 404s on old path**: an alias route is missing for that endpoint.
