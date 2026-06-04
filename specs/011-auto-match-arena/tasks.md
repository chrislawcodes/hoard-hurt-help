# Tasks: Auto-Match Arena & Operator Join Page

**Feature**: 011-auto-match-arena  
**Prerequisites**: spec-acceptance.md, plan-summary.md, data-model.md  

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — files listed must not overlap with another concurrent [P] task.
- **[USN]**: User story label (user story phases only).
- File paths are repo-relative.

---

## Phase 1: Setup

**Purpose**: Branch creation and sync with main before any code changes.

- [ ] T001 Create branch `feat/011-auto-match-arena` off `origin/main` (`git fetch origin main && git checkout -b feat/011-auto-match-arena origin/main`)
- [ ] T002 Confirm server starts cleanly on the new branch (migration 0018 must pass; see spawned fix task)

**Checkpoint**: Clean branch, server starts, ready to build.

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: Data model changes that ALL user stories depend on. No user story work begins until T003–T005 are complete and the server starts clean.

⚠️ **CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T003 [P: app/models/match.py] Add `MatchKind` enum (`manual` | `practice_arena` | `auto_scheduled`) and `match_kind` column (String 32, NOT NULL, default `"manual"`, indexed) to `Match` in `app/models/match.py`
- [ ] T004 [P: migrations/versions/0019_add_match_kind.py] Create Alembic migration `migrations/versions/0019_add_match_kind.py`: `batch_alter_table("matches")` adding `match_kind VARCHAR(32) NOT NULL DEFAULT 'manual'` and index `ix_matches_match_kind`; downgrade drops index then column
- [ ] T005 Run `alembic upgrade head` and confirm server starts without error; spot-check that existing matches have `match_kind = "manual"` in the DB

**Checkpoint**: `MatchKind` exists, migration applied, server starts — user story implementation may begin.

---

## Phase 3: User Story 1 — Practice Arena (Priority: P1) 🎯 MVP

**Goal**: An always-available match against Sims that starts immediately when a human joins.

**Independent test**: With no upcoming admin matches, a signed-in user with a connected bot can join the Practice Arena at `/play` and watch their bot play within 60 seconds.

### Implementation

- [ ] T006 [P: app/engine/arena.py] Create `app/engine/arena.py` with:
  - `PRACTICE_ARENA_MAX_PLAYERS = 5`, `PRACTICE_ARENA_SIM_COUNT = 4`, `PRACTICE_ARENA_NAME = "Practice Arena"`
  - `async def ensure_practice_arena(db) -> None` — idempotent: queries for a match with `match_kind=practice_arena` AND `state IN (scheduled, registering)`; if none, creates a `Match` with `match_kind=practice_arena`, `state=REGISTERING`, `scheduled_start` set far in future (prevents poller's `start_due_games` from firing it), `max_players=PRACTICE_ARENA_MAX_PLAYERS`, then calls `add_sims_to_game()` with `PRACTICE_ARENA_SIM_COUNT` Sim personalities chosen from `sim_preset_by_id`; logs and returns early if no Sim presets are available
  - Helper `_choose_sim_seats(n: int) -> list[tuple[str, str]]` that picks n personality IDs from `sim_presets` and generates unique agent names (e.g., `Sim_1`, `Sim_2`)
  - Import: `from app.engine.sims.seating import add_sims_to_game`; `from app.engine.sim_presets import list_sim_presets` (or equivalent)

- [ ] T007 Extend `app/engine/scheduler.py` — modify `_poll_due_loop` to call `ensure_practice_arena(db)` on each tick (after `fill_and_start_auto_matches` but before `start_due_games`); add `from app.engine.arena import ensure_practice_arena` import; wrap call in try/except Exception to match existing poller error handling

- [ ] T008 Extend `app/routes/web.py` — in the `POST /games/{game}/matches/{match_id}/join` handler, after successfully registering the human player, check `if match.match_kind == MatchKind.PRACTICE_ARENA`: call `await start_game(db, match)`; add `from app.models.match import MatchKind` and `from app.engine.scheduler import start_game` imports; keep the existing redirect-to-game-viewer unchanged

- [ ] T009 [P: tests/test_arena.py] Write `tests/test_arena.py` with tests for `ensure_practice_arena`:
  - `test_ensure_creates_practice_arena_when_none_exists` — call with empty DB; assert one REGISTERING match with `match_kind=practice_arena` and 4 Sim players
  - `test_ensure_idempotent` — call twice; assert still only one practice arena
  - `test_ensure_recreates_after_completion` — insert a completed practice arena, call ensure; assert a new one is created
  - Use in-memory SQLite test DB (same pattern as other test files)

**Checkpoint**: Practice Arena appears in the lobby, starts when a human joins, a new one appears within 2 seconds. Tests pass.

---

## Phase 4: User Story 2 — Auto-Scheduled Matches (Priority: P1)

**Goal**: A match opens every 30 minutes on the clock; Sims fill empty slots at start time; no minimum human count.

**Independent test**: At any :00 or :30, an auto-match appears in the lobby. At its start time it begins with Sims filling empty slots, even with 0 humans.

### Implementation

- [ ] T010 Extend `app/engine/arena.py` — add:
  - `AUTO_MATCH_INTERVAL_MINUTES = 30`, `AUTO_MATCH_MAX_PLAYERS = 8`, `AUTO_MATCH_SIM_COUNT_MAX = 7`, `AUTO_MATCH_NAME_PREFIX = "Auto Match"`
  - `_next_boundary() -> datetime` — returns the next :00/:30 UTC boundary (or current if we just passed one and no match exists for it)
  - `async def ensure_auto_match(db) -> None` — idempotent: queries for a match with `match_kind=auto_scheduled` AND `state IN (scheduled, registering)` with `scheduled_start >= now`; if none, computes `_next_boundary()` and creates a `Match` with `match_kind=auto_scheduled`, `state=SCHEDULED`, `scheduled_start=boundary`, `max_players=AUTO_MATCH_MAX_PLAYERS`, `name=f"{AUTO_MATCH_NAME_PREFIX} {boundary:%H:%M}"`
  - `async def fill_and_start_auto_matches(db) -> None` — queries for auto-matches with `state IN (scheduled, registering)` AND `scheduled_start <= now`; for each: computes empty slots = `match.max_players - active_player_count`; if slots > 0, calls `add_sims_to_game(db, match, seats)` with up to `AUTO_MATCH_SIM_COUNT_MAX` Sims; then calls `start_game(db, match)` from `app.engine.scheduler`

- [ ] T011 Extend `app/engine/scheduler.py` — add `fill_and_start_auto_matches` and `ensure_auto_match` to the poller imports and call them in `_poll_due_loop` in the correct order:
  ```
  await fill_and_start_auto_matches(db)   # 1st: fill overdue before start_due_games evaluates count
  await ensure_practice_arena(db)          # 2nd: recreate if missing
  await ensure_auto_match(db)             # 3rd: create next window if missing
  # start_due_games already called after  # 4th: existing logic
  ```
  Each wrapped in try/except Exception with logger.exception

- [ ] T012 [P: tests/test_arena.py] Add tests for auto-match functions to `tests/test_arena.py`:
  - `test_ensure_auto_match_creates_when_none` — call with empty DB; assert one SCHEDULED match with `match_kind=auto_scheduled` at the next boundary
  - `test_ensure_auto_match_idempotent` — call twice; assert only one auto-match
  - `test_fill_and_start_auto_matches_fills_sims` — insert an auto-match with `scheduled_start` in the past and 0 players; call `fill_and_start_auto_matches`; assert match is ACTIVE and has `AUTO_MATCH_MAX_PLAYERS` players
  - `test_fill_and_start_auto_matches_zero_humans` — same as above; confirm Sims fill all slots

**Checkpoint**: Auto-match appears in lobby at each 30-min boundary. At start time, Sims fill and the match runs. Tests pass.

---

## Phase 5: User Story 3 — Operator Join Page (Priority: P1)

**Goal**: `/play` shows bot status, Practice Arena, next auto-match, and active games for the signed-in operator.

**Independent test**: Visit `/play` in all three user states (not signed in, no bot, connected bot) and verify each shows the correct content.

### Implementation

- [ ] T013 Extend `app/routes/web.py` — add `GET /play` route:
  - Imports: `from app.engine.arena import PRACTICE_ARENA_NAME` (for filtering), `MatchKind`
  - Queries: practice arena match (upcoming, `match_kind=practice_arena`), next auto-match (upcoming, `match_kind=auto_scheduled`, earliest `scheduled_start`), user's bots (if signed in), user's active/upcoming player entries with their matches (if signed in)
  - Bot health: for each bot, query `onboarding.state` equivalent or `health.is_connected` (same data the bot detail page uses)
  - Context keys: `practice_arena: dict | None`, `next_auto_match: dict | None`, `bots: list`, `my_entries: list[dict]`, `user`
  - Renders `play.html`

- [ ] T014 Create `app/templates/play.html` — extends `base.html`; title "Play — Agent Ludum"; three visible states:
  - **Not signed in**: hero text ("See how far your agent will go.") + "Sign in to play →" button linking to `/auth/google/login`; no match cards
  - **Signed in, no bots**: "Set up your bot first" card with "Create a bot →" link to `/me/bots`; match cards shown (grayed join buttons) so they can see what's coming
  - **Signed in, has bots**: bot status badge (pulled from health, same display as `bots/_health_badge.html`), Practice Arena card with "Join now →" (`btn-primary`), next auto-match card with start time + "Join →" (`btn-ghost`), "Your games" section listing active/upcoming entries with "Watch →" links
  - Practice Arena join button disabled with "Connect your bot first" text when bot is not connected
  - Match card countdown via `time.localtime` (same pattern as join.html)

**Checkpoint**: `/play` renders correctly in all three states; join buttons route to existing join form; tests pass.

---

## Phase 6: User Story 4 — "Play now →" Routing (Priority: P2)

**Goal**: The hero CTA on the Agent Ludum homepage links to `/play`, not the spectator lobby.

**Independent test**: Click "Play now →" on `/`; confirm you land on `/play`.

### Implementation

- [ ] T015 [P: app/templates/agent_ludum.html] Modify `app/templates/agent_ludum.html` — change both `href="/games/hoard-hurt-help"` occurrences on "Play now →" buttons (hero section and bottom CTA band) to `href="/play"`; leave all other links unchanged; `{{ featured_game_slug }}` references in `games.html` are NOT this file — do not touch them

**Checkpoint**: Clicking "Play now →" on the homepage lands on `/play`. Spectator lobby still accessible at its own URL.

---

## Phase 7: User Story 5 — Lobby Visibility (Priority: P2)

**Goal**: Practice Arena and auto-match appear in the lobby upcoming section automatically.

**Independent test**: Visit the HHH lobby; verify Practice Arena and next auto-match appear in "Upcoming" alongside admin games.

### Implementation

- [ ] T016 Verify (no code change expected): the existing `_upcoming_views(db)` query in `app/routes/web.py` filters by `state IN (scheduled, registering)` with no `match_kind` filter, so Practice Arena and auto-matches already appear. Confirm by checking the query; if a filter is found, remove it.
- [ ] T017 [P: app/templates/fragments/lobby_upcoming.html] If Practice Arena or auto-match should be visually distinguished (e.g., a "Practice" badge on the Practice Arena card), add a conditional `{% if g.match_kind == 'practice_arena' %}` badge in `app/templates/fragments/lobby_upcoming.html`. Otherwise no change needed — verify and skip if plain display is acceptable.

**Checkpoint**: Lobby upcoming section shows Practice Arena and auto-match. No visual regressions on admin-created games.

---

## Phase 8: Polish & Cross-Cutting

**Purpose**: Test coverage, preflight gate, quickstart verification.

- [ ] T018 Run full test suite: `pytest -q`; fix any failures before proceeding
- [ ] T019 Run preflight gate: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`; all must pass
- [ ] T020 Manual quickstart verification: follow `specs/011-auto-match-arena/quickstart.md` steps US-1 through US-5; note any failures

**Checkpoint**: All preflight checks pass. Feature is ready to ship.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Foundation)**: Depends on Phase 1. **BLOCKS Phase 3–7.**
- **Phase 3 (US-1, Practice Arena)**: Depends on Phase 2.
- **Phase 4 (US-2, Auto-match)**: Depends on Phase 3 (extends arena.py).
- **Phase 5 (US-3, /play page)**: Depends on Phase 2; can run after T006 (arena.py) is started but needs arena constants.
- **Phase 6 (US-4, Play now →)**: Depends only on Phase 5 existing (template must exist first).
- **Phase 7 (US-5, Lobby)**: Mostly verify-only; can run alongside Phase 5.
- **Phase 8 (Polish)**: Depends on all user story phases.

### Parallel Opportunities Within Phases

- **T003 + T004** (Phase 2): Different files — run in parallel.
- **T006 + T009** (Phase 3): arena.py creation and test scaffolding can start together.
- **T012 + T010** (Phase 4): Tests can be written alongside the arena extension.
- **T015** (Phase 6): One-line template change, fully independent of Phase 5 internals.
