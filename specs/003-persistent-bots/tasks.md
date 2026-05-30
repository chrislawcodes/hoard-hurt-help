# Tasks: Persistent Bots with Paste-Once Credentials

**Prerequisites**: plan.md, spec.md, plan-summary.md, spec-acceptance.md, data-model.md, contracts/

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — file scope listed. Bare `[P]` (no list) = serial.
- **[USn]**: User story label (user-story phases only).
- Paths come from `plan-summary.md`.

---

## Phase 1: Setup

**Purpose**: Sync and learn the test harness before touching code.

- [ ] T001 Sync branch: `git fetch origin main && git rebase origin/main` on `003-persistent-bots` (per always-rebase rule).
- [ ] T002 Read existing test fixtures (test DB/session, signed-in-user helper, existing agent-API tests) under `tests/` and note how the SQLite in-memory schema is built (model metadata vs. migrations) — this dictates whether the 0003 data-clear affects tests.

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: New models, credential primitives, bot auth, the data-affecting migration, and reworking the existing game-scoped endpoints onto bot auth. The fresh-start cutover drops `players.agent_key_hash`, so the existing play path does not work until T012 lands — this whole phase is one coherent unit.

⚠️ **CRITICAL**: No user-story phase can begin until this phase is complete.

- [ ] T003 [P: app/models/bot.py] Create `Bot` model (key_lookup unique-indexed, key_hint, status enum, paused_at/reason, max_concurrent_games, stall_threshold) per data-model.md.
- [ ] T004 [P: app/models/strategy_profile.py] Create `StrategyProfile` model (user_id, name unique-per-user, prompt_text, is_default, timestamps).
- [ ] T005 [app/models/player.py] Modify `Player`: add `bot_id` FK (NOT NULL, indexed), add `UNIQUE(bot_id, game_id)`, drop `agent_key_hash`.
- [ ] T006 [app/models/__init__.py] Register `Bot` and `StrategyProfile` so metadata/Alembic see them.
- [ ] T007 [P: app/engine/tokens.py] Add `generate_bot_key()` (`sk_bot_<48 hex>`), `bot_key_lookup()` (sha256 hex), `bot_key_matches()` (`hmac.compare_digest`). Keep argon2 helpers; add a comment that sha256 is correct for high-entropy tokens (research.md Q1).
- [ ] T008 [P: tests/test_bot_tokens.py] Unit tests: key format, lookup determinism, match/mismatch constant-time path.
- [ ] T009 [app/deps.py] Add `require_bot(...) -> Bot` (indexed lookup; 401 INVALID_KEY; 403 BOT_PAUSED) and a `resolve_player(bot, game_id) -> Player` helper (404 NOT_IN_GAME via `UNIQUE(bot_id, game_id)`). Remove the all-players scan `require_agent_key`.
- [ ] T010 [P: tests/test_bot_auth.py] Tests: valid/invalid/missing key, paused bot rejected, (bot,game_id) resolution incl. not-in-game.
- [ ] T011 [migrations/versions/0003_persistent_bots.py] Data-affecting migration (down_revision `0002`): create `bots`, `strategy_profiles`; FK-safe clear of turn_submissions→turns→strategy_prompts→players; `batch_alter_table('players')` add `bot_id` NOT NULL + `UNIQUE(bot_id, game_id)`, drop `agent_key_hash`. Add the ⚠️ data-critical header comment; provide a downgrade.
- [ ] T012 [app/routes/agent_api.py] Rework game-scoped endpoints (`/turn`, `/submit`, history, chat, standings, turn_detail) to use `require_bot` + `resolve_player`; move rate-limit key from `player.id` to `bot.id`; return 404 NOT_IN_GAME / 403 BOT_PAUSED. Behavior/response shapes unchanged otherwise.
- [ ] T013 [tests/] Update existing agent-API tests to mint a Bot + bot key (no per-game key) and assert unchanged turn/submit behavior.

**Checkpoint**: Models, migration, bot auth, and the existing play path (on bot keys) all green. `ruff` + `mypy app/ mcp_server/` + `pytest -q` pass.

---

## Phase 3: User Story 1 - Create a bot and get a stable credential once (Priority: P1) 🎯 MVP

**Goal**: A signed-in user creates a named bot, sees the `sk_bot_` key + paste-once snippet exactly once, and can reissue.

**Independent Test**: Create "Atlas" → key shown once + snippet; reload → only hint + reissue; reissue → new key, old key 401s.

- [ ] T014 [app/routes/bots_web.py] Create routes: `POST /me/bots` (issue key, store sha256 + hint, flash plaintext once), `GET /me/bots/{id}` (detail, no plaintext), `POST /me/bots/{id}/reissue` (overwrite key_lookup → old key dies; warn re-paste). All `require_user`.
- [ ] T015 [P: app/templates/bots/detail.html] Detail template: one-time key block + paste-once MCP snippet (the `get_next_turn` loop), reissue button, key_hint.
- [ ] T016 [app/main.py] Register the bots_web router.
- [ ] T017 [P: tests/test_bot_create_reissue.py] [US1] Tests: create shows key once; detail hides it; reissue invalidates old key; second bot is independent; bot names unique per user.

**Checkpoint**: US1 fully functional and testable.

---

## Phase 4: User Story 2 - Connect once, play every game (Priority: P1) 🎯 MVP

**Goal**: One connected bot drives all its active games via a single `get_next_turn` → act loop.

**Independent Test**: Bot in two started games; loop returns nearest-deadline turn each time; returns `waiting` when idle; never needs reconfiguration.

- [ ] T018 [P: app/engine/next_turn.py, tests/test_next_turn_engine.py] [US2] Pure selector: from candidate open turns pick nearest `deadline_at`, tie-break game_id then round.turn; skip already-submitted; + unit tests.
- [ ] T019 [app/routes/agent_next_turn.py] [US2] `GET /api/agent/next-turn` (require_bot): gather bot's active non-paused players, find open turns in ACTIVE games, apply selector, return YourTurn payload (+ game_id) reusing `build_turn_summary`/`TurnStatic`, else `waiting` with `next_poll_after_seconds`. Rate-limit per bot.id.
- [ ] T020 [app/schemas/agent.py, app/main.py] [US2] Add NextTurn response schema (reuse WaitingResponse/TurnStatic/summary); register router.
- [ ] T021 [mcp_server/server.py] [US2] Add `get_next_turn()` tool wrapping the endpoint; update the connect/setup-prompt wording to the multi-game loop (`get_next_turn` → `submit_action(game_id,…)` → repeat).
- [ ] T022 [P: tests/test_next_turn_api.py] [US2] Integration (SQLite in-memory, mock nothing internal): multi-game urgency order, already-submitted skip, no-active-games waiting, paused → bot_paused.

**Checkpoint**: US2 fully functional; a single loop plays multiple games.

---

## Phase 5: User Story 3 - Enter a bot into a game without a new credential (Priority: P1) 🎯 MVP

**Goal**: Entering a game means picking one of my bots; no key shown, no re-paste.

**Independent Test**: Enter "Atlas" into a registering game → player created, no key; duplicate entry blocked; second bot "Borealis" works.

- [ ] T023 [app/routes/web.py] [US3] Rework `GET/POST /games/{game_id}/join`: form selects a bot (+ in-game name + optional strategy profile); POST creates `Player(bot_id, game_id)`; guards: `DUPLICATE_ENTRY` (UNIQUE bot,game), `INVALID_DISPLAY_NAME` (name taken), `GAME_FULL`. No credential issued; redirect to `/me/bots/{id}`.
- [ ] T024 [P: app/templates/join.html] [US3] Replace per-game key/snippet UI with bot + profile pickers.
- [ ] T025 [P: tests/test_enter_game.py] [US3] Tests: entry creates player without key; duplicate blocked; name collision; two bots in one game act independently (FR-012).

**Checkpoint**: US3 fully functional. **All P1 (MVP) stories complete.**

---

## Phase 6: User Story 4 - Reusable strategy profiles (Priority: P2)

**Goal**: Save named strategies; seed a player from one at entry; edits don't touch running games.

**Independent Test**: Create two profiles + default; enter choosing one → player seeded; edit profile → running player unchanged.

- [ ] T026 [P: app/routes/strategy_profiles_web.py, app/templates/strategy_profiles.html] [US4] CRUD routes + UI (`/me/strategy-profiles`); enforce one default per user; register router.
- [ ] T027 [app/routes/web.py] [US4] On entry, copy chosen/default profile text into a new per-player `StrategyPrompt` (copy-at-entry; falls back to inline/empty if no profile).
- [ ] T028 [P: tests/test_strategy_profiles.py] [US4] Tests: CRUD, single-default invariant, seed-at-entry copy, post-edit isolation of running games.

**Checkpoint**: US4 functional.

---

## Phase 7: User Story 5 - Control panel & kill switch (Priority: P2)

**Goal**: See each bot's games/last-action/score; pause/resume; pull out of a game.

**Independent Test**: Panel lists games + last action + score; pause stops new turns (next_turn `bot_paused`, game-scoped 403); pull from registering game frees seat.

- [ ] T029 [app/routes/bots_web.py, app/templates/bots/list.html] [US5] `GET /me/bots` panel: per bot, games + state + last action time + current score (aggregate from players/submissions).
- [ ] T030 [app/routes/bots_web.py] [US5] `POST /me/bots/{id}/pause|resume|delete` (delete blocked while in ACTIVE games); wire pause enforcement (skip in next_turn selector + 403 in game-scoped path — already honored via require_bot). Reuse existing `/me/players/{id}/leave` for pull-out.
- [ ] T031 [P: tests/test_pause_panel.py] [US5] Tests: paused bot served no turns; resume restores; delete guard; panel status fields.

**Checkpoint**: US5 functional.

---

## Phase 8: User Story 6 - Concurrency caps (Priority: P2)

**Goal**: Enforce per-bot max concurrent games and platform caps.

**Independent Test**: Bot at cap → second entry `BOT_CAP_REACHED`; platform cap breach refused; full game `GAME_FULL`.

- [ ] T032 [P: app/engine/caps.py, tests/test_caps_engine.py] [US6] Pure cap checks: per-bot active-game count vs `max_concurrent_games`; platform active-game count vs config; + unit tests.
- [ ] T033 [app/config.py, app/routes/web.py] [US6] Add `max_concurrent_active_games` setting; enforce per-bot + platform caps at entry (and game start where relevant) with clear error codes.
- [ ] T034 [P: tests/test_caps_api.py] [US6] Tests: per-bot cap refusal names the cap; platform cap refusal; existing GAME_FULL preserved.

**Checkpoint**: US6 functional.

---

## Phase 9: User Story 7 - Stall safety surfacing (Priority: P3)

**Goal**: Flag/auto-pause bots that miss consecutive turns.

**Independent Test**: Bot misses `stall_threshold` turns → panel flags count; auto-pause or prominent recommendation, reason recorded.

- [ ] T035 [app/engine/next_turn.py or app/engine/stall.py, app/routes/bots_web.py, app/templates/bots/list.html] [US7] Compute trailing `was_defaulted` count per bot's player; at threshold set `status=paused, paused_reason="auto: stalled"` (or surface a prominent recommendation); show count in panel.
- [ ] T036 [P: tests/test_stall.py] [US7] Tests: threshold detection from defaulted submissions; auto-pause + reason; below-threshold no-op.

**Checkpoint**: US7 functional.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T037 [app/templates/connection.html, app/routes/web.py] Retire/redirect the old per-game connection dashboard to the bots panel.
- [ ] T038 [P: DESIGN.md, STATUS.md, docs/] Update architecture notes + any guide pages + onboarding text to the bot/paste-once model; note auto-join as the next phase.
- [ ] T039 Run the full Preflight Gate from repo root: `ruff check . && mypy app/ mcp_server/ && pytest -q`; fix root causes (no suppressions). Then walk quickstart.md US1–US7.
- [ ] T040 [P: MEMORY.md + memory file] Update project memory (agent-key-stability follow-up now done; bot-credential model) and STATUS.md.

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (P1)**: none.
- **Foundation (P2)**: depends on Setup — **BLOCKS all user stories** (cutover breaks the play path until T012).
- **User Stories (P3–P9)**: depend on Foundation.
  - US1, US2, US3 (all P1) are independent of each other after Foundation; deliver MVP together.
  - US4 depends on Foundation (StrategyProfile model) + touches US3's entry flow (T027 after T023).
  - US5, US6, US7 independent after Foundation (US7 reads the same defaulted data US5 surfaces).
- **Polish (P10)**: after the targeted stories are done.

### Within-Phase Notes
- Foundation: T003/T004/T007/T008 parallel; T005→T006; T009 after T005; T011 after T003/T004/T005; T012 after T009; T013 after T012.
- Caps engine (T032) and cap enforcement (T033) gate US3/US4 entry once present — entry-path tasks should call the cap check once T032/T033 land (acceptable to land caps before finishing all P1 if staffing allows, but P1 MVP can ship with only per-game GAME_FULL).

### Parallel Opportunities
- `[P]`-marked tasks with disjoint file lists can run together (e.g. T003 + T004 + T007 + T008; test files alongside their module).
- The three P1 stories (US1/US2/US3) can be split across people after Foundation.

### Suggested MVP cut
Foundation + US1 + US2 + US3 = paste-once, play-any-game-you-joined. US4–US7 layer on quality, control, and safety.
