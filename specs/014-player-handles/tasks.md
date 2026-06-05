# Tasks: Player Handles (Public Operator Identity)

**Feature**: 014-player-handles
**Prerequisites**: spec.md, plan.md
**Branch**: `claude/player-identification-strategy-fADg6`

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — files listed must not overlap with another concurrent [P] task.
- **[USN]**: User story label (user story phases only).
- File paths are repo-relative.

---

## Phase 1: Setup

**Purpose**: Confirm a clean starting point before any code changes.

- [ ] T001 Confirm on branch `claude/player-identification-strategy-fADg6`, synced with `origin/main`. Confirm the server starts clean and the migration chain head (`0020`) applies; if SQLite dev DB lags the models, rebuild it from models per `CLAUDE.md` / the ux-design Ground note.

**Checkpoint**: Server starts, tests pass on a clean tree, ready to build.

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: Data + identity primitives that every user story depends on.

⚠️ **CRITICAL**: No user story work begins until T002–T008 are complete and the server starts clean.

- [ ] T002 [P: app/models/user.py] Add to `User`: `handle: Mapped[str | None]` (`String(20)`, display case), `handle_key: Mapped[str | None]` (`String(20)`, lowercased, `unique=True`, `index=True`), `handle_changed_at: Mapped[datetime | None]` (`DateTime(timezone=True)`). All nullable.
- [ ] T003 [P: migrations/versions/0021_add_user_handle.py] Create migration `0021_add_user_handle.py` (down_revision = `0020`): `batch_alter_table("users")` adds the three columns; create unique index `ix_users_handle_key` on `handle_key`. Downgrade drops the index then the columns. Use batch mode so it applies on SQLite.
- [ ] T004 Run `alembic upgrade head`; confirm server starts and a fresh `Base.metadata.create_all` (test path) also has the columns + index. Spot-check existing users have `handle = NULL` and multiple NULL `handle_key` rows coexist.
- [ ] T005 [P: app/identity/word_filter.py] Create `app/identity/__init__.py` + `app/identity/word_filter.py`: in-code `RESERVED` set (`admin`, `system`, `sim`, `agentludum`, `staff`, `mod`, `null`, `none`) and a `BLOCKED` slur list; `normalize(text) -> str` (lowercase, strip simple look-alikes/spacing); `contains_blocked(text) -> bool`; `mask(text) -> str` (replace each blocked word with exactly `****`). No DB, fully typed.
- [ ] T006 [P: app/identity/handle.py] Create `app/identity/handle.py` (depends on T005): `normalize(raw) -> str`; `validate(raw) -> str` raising a typed `HandleError` on regex `^[A-Za-z][A-Za-z0-9_]{2,19}$` failure, reserved word, or blocked word (never echo the input); `suggest(*, given_name, email, taken: Callable[[str], bool]) -> str` with fallback chain given-name → email local-part → `player<random>`, de-duplicated. Uniqueness is checked by the caller against `handle_key`.
- [ ] T007 [P: tests/test_word_filter.py] Test `contains_blocked` (catches a blocked word incl. spacing/case dodge), `mask` (blocked word → `****`, fixed length regardless of word length, clean text untouched).
- [ ] T008 [P: tests/test_handle.py] Test `validate` (good/bad chars, length bounds, must-start-letter, reserved, blocked → raises, no echo), casing (display preserved, `handle_key` lowercased), and `suggest` fallback chain + de-dup.

**Checkpoint**: Columns exist + migrated, identity package + tests green. User story work may begin.

---

## Phase 3: User Story 1 — Pick a handle + gate (Priority: P1) 🎯 MVP

**Goal**: One pre-filled `/me/handle` page, reached via a gate, so an operator gets a handle before owning an agent. Existing agent-owners are gated at next login.

**Independent test**: A signed-in user with an agent and no handle is redirected to a pre-filled `/me/handle`; saving a valid handle returns them to `next`. A spectator with no agent is never gated.

### Implementation

- [ ] T009 [P: app/deps.py] Add `async def require_user_with_handle(...) -> User`: builds on `require_user`; if `user.handle is None`, raise `HTTPException(status_code=303, headers={"Location": f"/me/handle?next={quote(request.url.path)}"})`. (If the exception-redirect fights the test client, fall back to a plain helper called at the top of each gated route — see plan Decision 2.)
- [ ] T010 [P: app/routes/handle_web.py] New router: `GET /me/handle` renders the form pre-filled via `handle.suggest(...)` (or the current handle when changing); `POST /me/handle` validates (chars/length/reserved/word-filter via `handle.validate`, uniqueness via `handle_key`), enforces the 30-day change cooldown using `handle_changed_at`, stores `handle` + lowercased `handle_key` + stamps `handle_changed_at`, then redirects to `next` (default `/me/bots`). On error, re-render with a generic message (never echo a blocked value).
- [ ] T011 [P: app/templates/handle.html] Form template extending `base.html`: `@` prefix, help text and button per spec microcopy, inline validation/cooldown messages, hidden `next` field.
- [ ] T012 [P: app/main.py] Register the new router: add `handle_web` to the route imports (~line 30) and `app.include_router(handle_web.router)` (~line 134).
- [ ] T013 Apply the gate: swap `require_user` → `require_user_with_handle` on the agent-owner surfaces — the bots panel (`app/routes/bots_setup.py` list + create, and the other `/me/bots*` routers as appropriate), `/play` (`app/routes/web_player.py`), and the match-join route (`POST /games/{game}/matches/{match_id}/join`, `app/routes/web_player.py`). Leave spectator routes and `/me/handle` itself on plain `require_user`.
- [ ] T014 [P: app/templates/base.html] In the account menu, show `@{{ user.handle }}` with a "Change" link to `/me/handle` when set; show nothing handle-related when unset. Never render email/real name anywhere public (account menu is the signed-in user's own — that's fine).
- [ ] T015 [P: tests/test_handle_gate.py] Tests: owner-without-handle hitting `/me/bots` gets 303 → `/me/handle?next=...`; spectator (no agents) is not gated; user with a handle passes through; `POST /me/handle` saves and redirects to `next`; cooldown blocks a too-soon change with the dated message.

**Checkpoint**: New + existing operators are funneled to one pre-filled handle page; spectators unaffected; handle saved with correct casing + key. Tests pass. (User Story 3 "change handle" is satisfied by this same form; verified in Phase 6.)

---

## Phase 4: User Story 2 — Leaderboard credit (Priority: P1)

**Goal**: Each agent shows `by @handle` under its name on the leaderboard; Sims and not-yet-handled owners show no credit line.

**Independent test**: After a rated match, an agent whose owner has a handle shows `by @handle`; a Sim row shows none; two same-named agents are distinguishable by their handles.

### Implementation

- [ ] T016 [P: app/read_models/leaderboard.py] Add `User` to the existing `select(Match, Player, Bot)` join (`join(User, User.id == Bot.user_id)`); capture `User.handle`. Add `owner_handle: str | None` to `_Participant`, `_CompetitorState`, and `LeaderboardRow`; set it from the owner's handle for **agents only** (`None` for Sims and for owners with no handle). Elo math/keys/sorting unchanged.
- [ ] T017 [P: app/templates/leaderboard.html] In the `lb-name` cell, render a muted second line `by @{{ row.owner_handle }}` only when `row.owner_handle` is set. Add `app/static/style.css` rule for the muted credit; confirm it stacks under the name at phone width without horizontal scroll.
- [ ] T018 [P: tests/test_leaderboard.py] Assert `owner_handle` is populated for an agent whose owner has a handle, `None` for a Sim and for an agent whose owner has no handle, and that Sim rows still appear (join didn't drop them).

**Checkpoint**: Leaderboard shows the credit correctly across agent / Sim / no-handle, on desktop and phone. Tests pass.

---

## Phase 5: User Story 4 — Keep handles safe (Priority: P2)

**Goal**: Agent display names go through the same word filter; admin can force-reset a handle without losing history.

**Independent test**: Creating/renaming an agent with a blocked word is rejected (no echo). An admin can clear a user's handle; that user's agents and ratings are intact.

### Implementation

- [ ] T019 [P: app/routes/bots_web_support.py] In `validate_bot_name`, call `word_filter.contains_blocked` (and reserved check as appropriate); reject a blocked name with a generic message, never echoing the input.
- [ ] T020 [P: app/routes/admin_web.py] Add an admin action to force-reset a user's handle: clears `handle` + `handle_key` (frees the string immediately) and leaves `users.id` and all leaderboard history untouched. Surface it on the relevant admin page/template. Guard with `require_admin`.
- [ ] T021 [P: tests/test_handle_safety.py] Tests: blocked agent name rejected with no echo; admin reset clears the handle, frees the old string for reuse, and the user's players/ratings are unchanged.

**Checkpoint**: Names are screened; admin reset works and is history-safe. Tests pass.

---

## Phase 6: Polish & Cross-Cutting

**Purpose**: Verify the remaining story, run the gate, ship-ready checks.

- [ ] T022 Verify User Story 3 (change handle): change a handle from the account menu → new value shows everywhere; agents' ratings/history unchanged; a second change within 30 days is blocked with the dated message.
- [ ] T023 Run the Preflight Gate: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. All must pass — fix root causes, no suppressions.
- [ ] T024 Manual verification via the preview harness: fresh user gets gated → picks a pre-filled handle → runs a rated match → `by @handle` shows on `/leaderboard` and is **absent** from the live turn viewer; confirm phone width has no horizontal scroll; confirm email/real name appear on no public surface.

**Checkpoint**: All preflight checks pass; manual flow verified. Feature ready to ship.

---

## Phase 7: DEFERRED — Agent-message masking (Phase 2 of the spec)

**Not part of this feature's deliverable.** Tracked here so it isn't lost. Do not start without a go-ahead.

- [ ] T025 Wire `word_filter.mask()` into turn-message submission so a public message posts with each blocked word replaced by `****`; the turn is never defaulted to Hoard for this reason. Add engine tests. (Touches `app/engine` / turn submission — its own change, its own PR.)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies.
- **Phase 2 (Foundation)**: Depends on Phase 1. **BLOCKS Phases 3–6.**
- **Phase 3 (US-1 + gate)**: Depends on Phase 2 (needs columns + `handle.py`).
- **Phase 4 (US-2 leaderboard)**: Depends on Phase 2 (needs the `handle` column); independent of Phase 3.
- **Phase 5 (US-4 safety)**: Depends on Phase 2 (`word_filter`); independent of Phases 3–4.
- **Phase 6 (Polish)**: Depends on Phases 3–5.
- **Phase 7 (Deferred)**: Separate; depends only on `word_filter` (T005) existing.

### Parallel Opportunities

- **T002 + T003** (different files) can be drafted together; T004 runs after both.
- **T005 + T006** — word_filter then handle (T006 imports T005); their tests **T007 + T008** parallel.
- **Phase 4** (leaderboard) and **Phase 5** (safety) can proceed in parallel once Phase 2 lands — they touch different files from Phase 3.
- **T011 (template) + T009 (deps) + T010 (route)** — coordinate, but template and dep are independent files.
