# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md)  
**Constitution**: CLAUDE.md

## Code Quality (per CLAUDE.md)

- [ ] All new function signatures have type annotations (`async def ensure_practice_arena(db: AsyncSession) -> None`)
- [ ] No `# type: ignore` or `# noqa` suppressions — fix root causes instead
- [ ] No bare `except:` — use `except Exception` at poller boundaries only
- [ ] Files are focused: `arena.py` only contains arena lifecycle; no match-creation logic in `scheduler.py` or `web.py`
- [ ] No vague filenames — `arena.py` not `utils.py`

## Async Consistency (per CLAUDE.md)

- [ ] All new functions that touch the DB are `async def`
- [ ] No sync DB calls inside async route handlers or poller
- [ ] `await db.commit()` used after mutations; no fire-and-forget commits

## SQLite Migration (per CLAUDE.md memory: sqlite-migration-batch-mode)

- [ ] Migration 0019 uses `op.batch_alter_table("matches")` for the column add
- [ ] Migration includes both upgrade and downgrade
- [ ] `server_default="manual"` set in migration so existing rows get the value without a data rewrite
- [ ] `alembic upgrade head` passes on the dev SQLite DB after migration is written

## Reuse Over Reinvention (per plan-summary.md constraints)

- [ ] `add_sims_to_game()` from `app.engine.sims.seating` is the ONLY Sim seating path — no parallel implementation
- [ ] `start_game()` from `app.engine.scheduler` is called for Practice Arena immediate start — state machine not bypassed
- [ ] `ensure_*` functions are idempotent — safe to call every 2 seconds without creating duplicates

## Poller Order (per plan-summary.md constraints)

- [ ] `fill_and_start_auto_matches` runs BEFORE `start_due_games` in `_poll_due_loop` — auto-matches are Sim-filled before player count is evaluated
- [ ] Each arena function call is wrapped in `try/except Exception` with `logger.exception` — poller must not die on a transient error

## Practice Arena Scheduling

- [ ] Practice Arena `scheduled_start` is set far in the future (e.g., `now + timedelta(days=365)`) so the poller's `start_due_games` never fires it automatically
- [ ] Only the join route triggers Practice Arena start (via direct `start_game()` call)

## Template Hygiene

- [ ] `play.html` uses existing CSS variables and component classes from `style.css` — no inline styles or new CSS classes introduced without adding them to `style.css`
- [ ] `play.html` handles all three user states: not signed in, no bot, connected bot — no blank or broken state
- [ ] Time displays in `play.html` use `time.localtime` for local time conversion (same as other templates)
