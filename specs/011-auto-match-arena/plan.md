# Implementation Plan: Auto-Match Arena & Operator Join Page

**Branch**: `011-auto-match-arena` | **Date**: 2026-06-03 | **Spec**: [spec.md](spec.md)

---

## Summary

Add a `match_kind` column to `Match`, a new `app/engine/arena.py` module that manages Practice Arena and auto-match lifecycle, extend the existing scheduler poller to call arena management, and add a `/play` operator join page. The join route detects Practice Arena matches and triggers an immediate start.

---

## Technical Context

**Language/Version**: Python 3.14+, async (FastAPI + SQLAlchemy async)  
**Primary Dependencies**: FastAPI, SQLAlchemy (async), Alembic, Jinja2, HTMX  
**Storage**: SQLite (dev) / PostgreSQL (prod) via SQLAlchemy; new Alembic migration  
**Testing**: pytest with in-memory SQLite test DB  
**Target Platform**: Railway (production), local uvicorn (dev)  
**Performance Goals**: Practice Arena join → game started < 1s; poller creates missing arena/auto-match within 2s of detection  
**Constraints**: No new background processes — extend existing `SchedulerRegistry` poller. `add_sims_to_game()` already exists in `app/engine/sims/seating.py` and must be reused as-is.  
**Scale/Scope**: Single-file new module (`arena.py`), one new migration, one new template, four modified files.

---

## Constitution Check

**Status**: PASS

Checked against `CLAUDE.md`:

- **Async consistency** ✓ — all new DB calls are `async def`, no sync mixing.
- **No bare `except`** ✓ — poller wraps calls in `except Exception`.
- **Type annotations** ✓ — all new signatures annotated.
- **No suppressions** ✓ — no `# type: ignore` or `# noqa`.
- **Test coverage** ✓ — new game logic in `app/engine/` gets tests.
- **SQLite batch mode** ✓ — migration uses `op.batch_alter_table` for column add on SQLite.
- **File focus** ✓ — arena logic goes to `app/engine/arena.py`, not into `web.py` or `scheduler.py` directly.

---

## Architecture Decisions

### Decision 1: `match_kind` column on `Match`

**Chosen**: Add a `match_kind` string column (enum values: `manual`, `practice_arena`, `auto_scheduled`) with a default of `manual`.

**Rationale**:
- All existing matches remain `manual` with no data change.
- Query patterns are simple (`where Match.match_kind == "practice_arena"`).
- Avoids a separate config table that would complicate joins on every lobby query.

**Alternatives considered**:
- Separate `ArenaConfig` table: cleaner schema but 2× the queries for every lobby render.
- Boolean `is_auto` + `is_practice`: two booleans for three states is a code smell.

**Tradeoffs**:
- Pros: one column, simple queries, zero impact on existing rows.
- Cons: adding a fourth kind later requires a migration; acceptable for now.

---

### Decision 2: Arena management in `app/engine/arena.py`

**Chosen**: A dedicated `arena.py` module with three async functions:
- `ensure_practice_arena(db)` — idempotently creates the Practice Arena match if none exists.
- `ensure_auto_match(db)` — idempotently creates the next 30-min auto-match if none exists.
- `fill_and_start_auto_matches(db)` — finds overdue auto-matches, calls `add_sims_to_game()`, then `start_game()`.

The `SchedulerRegistry` poller calls all three on each tick.

**Rationale**:
- Keeps match creation logic out of `scheduler.py` (which owns turn loops) and `web.py` (which owns HTTP routing).
- All three functions are idempotent — safe to call on every 2-second tick with no double-creation.
- Directly testable without a running scheduler.

**Alternatives considered**:
- Inline in `scheduler.py`: would make an already-complex file larger with unrelated logic.
- FastAPI startup event only: misses the "recreate after completion" requirement.

---

### Decision 3: Practice Arena starts immediately from the join route

**Chosen**: The `POST /games/{game}/matches/{match_id}/join` route detects `match.match_kind == "practice_arena"`, calls `add_sims_to_game()` to fill remaining slots, then calls `start_game()` directly — all within the same request.

**Rationale**:
- "Starts immediately" means within the HTTP response, not on the next poller tick (≤2s delay).
- `start_game()` already exists in `scheduler.py` and does the SCHEDULED→REGISTERING→ACTIVE transition.
- No new async machinery needed.

**Alternatives considered**:
- Set `scheduled_start = now()` and let the poller pick it up: 0–2 second delay, violates "immediately".
- Publish an event the poller listens for: over-engineered for a 2-second difference.

**Tradeoffs**:
- Pros: zero delay, simple code path.
- Cons: join request takes slightly longer (Sim registration + game start). Acceptable — Sim registration is a handful of DB rows.

---

### Decision 4: Practice Arena pre-registers Sims at creation time

**Chosen**: When `ensure_practice_arena()` creates the match, it immediately calls `add_sims_to_game()` with `PRACTICE_ARENA_SIM_COUNT` (default: 4) Sims. This fills slots 1–4; slot 5 is left for the first human.

**Rationale**:
- At join time, the match already has Sims seated — no need to compute "how many Sims are needed."
- The join route only needs to detect `match_kind == practice_arena` and call `start_game()`.
- If a human joins and there are still open slots (e.g., max_players=5, 4 Sims + 1 human already present), the match starts with those 5 players.

**Tradeoffs**:
- Pros: join path is simpler.
- Cons: Sim bots are created even if no human ever joins this Practice Arena. Low cost (a handful of DB rows per cycle), acceptable.

---

### Decision 5: Auto-match Sim fill happens in the scheduler poller

**Chosen**: `fill_and_start_auto_matches(db)` is called from the existing `_poll_due_loop`. It queries auto-matches past their start time, calls `add_sims_to_game()` to fill empty slots, then calls `start_game()`.

**Rationale**:
- Reuses `add_sims_to_game()` identically to how the admin "Add Sims" screen uses it.
- The 2-second poller frequency means auto-matches start within 2 seconds of their boundary time — acceptable per SC-004.

**Tradeoffs**:
- Pros: zero new async primitives; poller already handles transient errors.
- Cons: ≤2s latency before auto-match starts. Invisible to users (Sims fill and start in the background).

---

### Decision 6: `/play` as a new top-level route in `app/routes/web.py`

**Chosen**: Add a `GET /play` route to the existing `web.py` router. New template `app/templates/play.html`.

**Rationale**:
- `/play` is game-agnostic from the operator's perspective; it doesn't belong under `/games/hoard-hurt-help/`.
- Adding to the existing `web.py` router requires zero new wiring in `app/main.py`.

**Tradeoffs**:
- Pros: minimal new files, consistent with existing routing pattern.
- Cons: `web.py` grows slightly — acceptable, it's already the main web router.

---

## Project Structure

```
app/
├── engine/
│   ├── arena.py             ← NEW: Practice Arena + auto-match lifecycle
│   └── scheduler.py         ← MODIFY: poller calls arena functions
├── models/
│   └── match.py             ← MODIFY: add match_kind column
├── routes/
│   └── web.py               ← MODIFY: add /play route; join detects practice_arena
├── templates/
│   ├── play.html            ← NEW: operator join page
│   └── agent_ludum.html     ← MODIFY: update "Play now →" href to /play

migrations/versions/
└── 0019_add_match_kind.py   ← NEW: add match_kind to matches table

tests/
└── test_arena.py            ← NEW: unit tests for arena.py functions
```

---

## Scheduler Poller Extension

The existing `_poll_due_loop` currently calls only `start_due_games()`. After this feature it calls three arena functions first (order matters):

```
each tick (every 2s):
  1. arena.fill_and_start_auto_matches(db)   # fill overdue auto-matches with Sims, start them
  2. arena.ensure_practice_arena(db)          # recreate if missing/completed
  3. arena.ensure_auto_match(db)             # create next 30-min window if missing
  4. start_due_games(db)                     # existing: start any scheduled/registering games
```

Step 1 runs before step 4 so that auto-matches are Sim-filled before `start_due_games` evaluates their player count.

---

## Join Route Extension

The existing `POST /games/{game}/matches/{match_id}/join` route, after registering the human player, adds one branch:

```python
if match.match_kind == MatchKind.PRACTICE_ARENA:
    await start_game(db, match)              # immediate start
    # poller will recreate the Practice Arena on next tick
```

The existing redirect-to-game-viewer logic is unchanged.

---

## `/play` Route Data

```python
GET /play → play.html

Context:
  user: current user (or None)
  bots: list[Bot] for this user (empty list if not signed in)
  practice_arena: dict | None   # upcoming Practice Arena match
  next_auto_match: dict | None  # next upcoming auto-match
  my_games: list[dict]          # user's active/upcoming player entries
```

The template shows three states based on user/bot status:
1. Not signed in → sign-in prompt
2. Signed in, no bots → "Set up your bot" CTA
3. Signed in, has bots → Practice Arena + auto-match cards + active games

---

## Practice Arena Configuration

Constants in `app/engine/arena.py` (not in DB — tunable in code, no migration needed to change):

| Constant | Default | Meaning |
|---|---|---|
| `PRACTICE_ARENA_MAX_PLAYERS` | 5 | Total player cap |
| `PRACTICE_ARENA_SIM_COUNT` | 4 | Sims pre-registered at creation |
| `PRACTICE_ARENA_NAME` | `"Practice Arena"` | Displayed in lobby |
| `AUTO_MATCH_INTERVAL_MINUTES` | 30 | Minutes between auto-match windows |
| `AUTO_MATCH_MAX_PLAYERS` | 8 | Total player cap for auto-match |
| `AUTO_MATCH_SIM_COUNT_MAX` | 7 | Max Sims to fill at start time |

---

## Edge Case Handling

| Edge case | Handling |
|---|---|
| Server restart with open Practice Arena | `ensure_practice_arena` checks for existing SCHEDULED/REGISTERING match first — no duplicate created |
| Two join requests race on same Practice Arena | First commit wins; second gets a player-count or state error from `start_game` — handled as HTTP 409 with redirect to the new Practice Arena |
| Auto-match poller fires late | `ensure_auto_match` sets `scheduled_start` to the boundary time (`:00`/`:30`), not the late-fire time; poller's next tick immediately fills and starts it |
| Admin deletes a Practice Arena or auto-match | Poller recreates on next tick |
| No Sim preset profiles exist | `ensure_practice_arena` returns early with a warning log; no match created; the `/play` page shows "No practice match available right now" |
| Practice Arena fills before human joins | Shouldn't happen (4 Sims + 1 human slot = 5 max), but if it does: join returns "match full", redirect to new Practice Arena |
