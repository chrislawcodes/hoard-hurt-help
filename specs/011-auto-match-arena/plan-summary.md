# Plan Summary: Auto-Match Arena & Operator Join Page

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/match.py` | modify | Add `MatchKind` enum and `match_kind` column |
| `app/engine/arena.py` | create | Practice Arena + auto-match lifecycle: `ensure_practice_arena`, `ensure_auto_match`, `fill_and_start_auto_matches` |
| `app/engine/scheduler.py` | modify | Poller calls three arena functions before `start_due_games` |
| `app/routes/web.py` | modify | Add `GET /play` route; join POST detects `practice_arena` and calls `start_game` immediately |
| `app/templates/play.html` | create | Operator join page (3 states: not signed in, no bot, connected bot) |
| `app/templates/agent_ludum.html` | modify | Change "Play now →" href from `/games/hoard-hurt-help` to `/play` |
| `migrations/versions/0019_add_match_kind.py` | create | Add `match_kind` column with index |
| `tests/test_arena.py` | create | Unit tests for `ensure_practice_arena`, `ensure_auto_match`, `fill_and_start_auto_matches` |

## Migration Steps

1. Create `migrations/versions/0019_add_match_kind.py` with `batch_alter_table` adding `match_kind VARCHAR(32) NOT NULL DEFAULT 'manual'` and index `ix_matches_match_kind`.
2. Run `alembic upgrade head` (applied automatically on server startup).
3. All existing rows default to `match_kind = "manual"` — no data rewrite needed.

## Data Model

**Match**: `matches` — add `match_kind: MatchKind` (enum: `manual` | `practice_arena` | `auto_scheduled`, default `manual`, indexed). No other table changes.

## Key Constraints

- **Reuse `add_sims_to_game()`**: Always call `app.engine.sims.seating.add_sims_to_game` for Sim seating — never write a second path. *Why: prevents drift; the existing function handles all seat-cap and name-collision edge cases.*
- **Reuse `start_game()`**: Practice Arena immediate start must call `app.engine.scheduler.start_game`. *Why: enforces the same SCHEDULED→REGISTERING→ACTIVE state machine path; bypassing it would skip the broadcast and turn-loop kickoff.*
- **Idempotent arena functions**: Each arena function must query before creating — safe to call on every 2-second poller tick. *Why: the poller has no between-tick memory; the DB is the sole source of truth.*
- **No new background processes**: Extend the existing `SchedulerRegistry._poll_due_loop`. *Why: one poller is easier to monitor and avoids asyncio task proliferation.*
- **SQLite batch_alter_table**: Migration 0019 must wrap the column add in `op.batch_alter_table`. *Why: SQLite cannot ALTER TABLE in place; this is required for dev DB compatibility.*
- **Poller order**: `fill_and_start_auto_matches` → `ensure_practice_arena` → `ensure_auto_match` → `start_due_games`. *Why: auto-matches must be Sim-filled before `start_due_games` evaluates player counts, or they'd be cancelled for being under `MIN_PLAYERS_TO_START`.*
