# Tasks: Hoard-Hurt-Help v1

**Prerequisites**: plan.md, spec.md, plan-summary.md, spec-acceptance.md, data-model.md, contracts/api.yaml

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — include the file list. Bare `[P]` is treated as serial.
- **[Story]**: User story label (US1–US10) used in user-story phases.
- File paths come from `plan-summary.md` and `plan.md`'s Project Structure.

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: minimal scaffolding so the rest of the build has a working FastAPI app and dev environment.

- [X] T001 Initialize `pyproject.toml` with project metadata and the dependency list from plan.md (`fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `aiosqlite`, `asyncpg`, `alembic`, `pydantic`, `pydantic-settings`, `authlib`, `itsdangerous`, `jinja2`, `python-multipart`, `argon2-cffi`, `httpx`, `mcp`, `pytest`, `pytest-asyncio`).
- [X] T002 [P: alembic.ini] Initialize Alembic config via `alembic init migrations`; commit the generated skeleton.
- [X] T003 [P: .env.example] Document every env var: `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `SESSION_SECRET`, `ADMIN_EMAILS`, `BASE_URL`.
- [X] T004 [P: app/__init__.py] Create empty `app/` package init.
- [X] T005 [P: app/config.py] Create `Settings` BaseSettings class loading the env vars from T003.
- [X] T006 [P: app/db.py] Create async SQLAlchemy engine + session factory; reads `DATABASE_URL` from settings.
- [X] T007 [P: app/main.py] Minimal FastAPI app factory + uvicorn entry — boots, serves `GET /healthz` returning `{"status":"ok"}`, no other routes yet.
- [X] T008 [P: app/static/style.css] Empty stylesheet placeholder.
- [X] T009 [P: app/static/htmx.min.js] Vendored HTMX (pinned version, downloaded from official CDN).
- [X] T010 [P: tests/__init__.py] Empty tests package.
- [X] T011 [P: tests/conftest.py] Pytest fixtures: in-memory SQLite engine, `TestClient` app fixture, async event loop config.

**Checkpoint**: `uvicorn app.main:app --reload` boots, `curl localhost:8000/healthz` returns 200. `pytest` runs (zero tests yet).

---

## Phase 2: Foundation (blocking prerequisites for ALL user stories)

**Purpose**: data model, auth scaffolding, engine constants, shared deps. After this phase, every user story can proceed independently.

⚠️ **CRITICAL**: no user-story phase may start until Phase 2 is fully green.

### ORM models

- [X] T012 [P: app/models/__init__.py] Empty models package.
- [X] T013 [P: app/models/base.py] `Base = DeclarativeBase` with naming convention from data-model.md.
- [X] T014 [P: app/models/user.py] `User` ORM model per data-model.md §users.
- [X] T015 [P: app/models/game.py] `Game` ORM model + `GameState` enum per data-model.md §games.
- [X] T016 [P: app/models/player.py] `Player` ORM model per data-model.md §players.
- [X] T017 [P: app/models/strategy_prompt.py] `StrategyPrompt` ORM model per data-model.md §strategy_prompts.
- [X] T018 [P: app/models/turn.py] `Turn` + `TurnSubmission` ORM models per data-model.md §turns and §turn_submissions.

### Migration

- [X] T019 [P: migrations/env.py] Wire Alembic to read `DATABASE_URL` from `app.config.settings` and discover models from `app.models`.
- [X] T020 Create initial migration `migrations/versions/0001_initial.py` containing all six tables, constraints, indexes per data-model.md.
- [X] T021 Run `alembic upgrade head` against local SQLite and verify all six tables exist.

### Pydantic schemas

- [X] T022 [P: app/schemas/__init__.py] Empty schemas package.
- [X] T023 [P: app/schemas/agent.py] Pydantic models for join, poll responses (waiting / your_turn / submitted / game_over), submit body + response, leave response. Match shapes in contracts/api.yaml.
- [X] T024 [P: app/schemas/admin.py] Pydantic models for create-game request, export schemas.
- [X] T025 [P: app/schemas/auth.py] OAuth callback shapes (Google `userinfo` payload).
- [X] T026 [P: app/schemas/spectator.py] Public game-state schema (excludes strategy prompts).

### Engine constants + utilities

- [X] T027 [P: app/engine/__init__.py] Empty engine package.
- [X] T028 [P: app/engine/rules.py] Constants: `RULES_TEXT_V1` (verbatim from spec.md §3) and `DEFAULT_STRATEGY_PROMPT` (verbatim from plan.md Architecture Decision 7).
- [X] T029 [P: app/engine/tokens.py] `generate_agent_key()` returning `sk_game_<48-hex>`, `hash_agent_key()` using argon2, `verify_agent_key()`. Plus `generate_turn_token()` returning `tk_<24-hex>`.
- [X] T030 [P: app/engine/state_machine.py] Pure function `allowed_transitions(from_state) -> set[GameState]` and `guard(from, to, game)` that enforces the rules in plan.md.

### Auth scaffolding

- [X] T031 [P: app/auth/__init__.py] Empty auth package.
- [X] T032 [P: app/auth/google.py] Authlib OAuth client configured with Google OIDC endpoints and scopes `openid email profile`.
- [X] T033 [P: app/auth/session.py] Helper: `get_user_from_session(request, db) -> User | None` and `require_user` FastAPI dependency that 302-redirects to `/auth/google/login?next=…` if not signed in.

### Shared deps

- [X] T034 [P: app/deps.py] FastAPI dependencies: `get_db`, `require_user`, `require_admin` (checks email against `ADMIN_EMAILS` env), `require_agent_key` (looks up `Player` by hash of `X-Agent-Key` header, raises 401 on miss).

### Broadcast

- [X] T035 [P: app/broadcast.py] In-process pub/sub: `subscribe(game_id) -> AsyncIterator[str]` and `publish(game_id, event_type, payload)`. Use `asyncio.Queue` per subscriber.

### Base templates

- [X] T036 [P: app/templates/base.html] Jinja base layout with header (site title + Admin link, conditional on session) and footer.
- [X] T037 [P: app/templates/login.html] Sign-in-with-Google CTA page.

### Auth routes

- [X] T038 [P: app/routes/__init__.py] Empty routes package.
- [X] T039 [P: app/routes/auth.py] Routes: `GET /auth/google/login`, `GET /auth/google/callback` (upserts `User` by `google_sub`, sets session cookie), `POST /auth/logout`.
- [X] T040 Mount auth routes + `SessionMiddleware` in `app/main.py`.
- [X] T041 [P: tests/test_auth.py] Tests for OAuth flow with mocked Google userinfo response; covers new-user upsert and returning-user lookup.

**Checkpoint**: a user can sign in at `/auth/google/login`, the callback creates/finds their `User`, and visiting any page shows their email in the header.

---

## Phase 3: User Story 1 — Game engine resolves turns correctly (Priority: P1, MVP)

**Goal**: every payoff case from spec.md §3 works correctly in code. No UI yet.

**Independent test**: `pytest tests/test_resolver.py` is fully green; `pytest tests/test_end_to_end.py` runs a scripted 100-turn game with stub agents and produces a deterministic winner.

### Tests first (TDD)

- [X] T042 [US1, P: tests/test_resolver.py] Write payoff math tests covering: single Hoard +2; single Help (target gets +4, source 0); single Hurt (target gets −4); Help stacking (5 helps = +20 target); Hurt stacking; mutual-help bonus (A↔B = +8 each); mutual bonus does not double when third party also helps A; score floor at 0; HURT against 0-score target is a wasted attacker turn; missed-turn default to Hoard with the canonical message.

### Resolver implementation

- [X] T043 [US1, P: app/engine/resolver.py] Implement `resolve_turn(db, turn)` per plan.md and spec.md §5 pseudocode. Order: materialize submissions (default to Hoard if missing), compute raw deltas (Hoard self, Help target, Hurt target, mutual-help bonus), apply score floor at 0, persist post-floor `points_delta` and `round_score_after` on each `TurnSubmission`.
- [X] T044 [US1, P: app/engine/resolver.py] Implement `award_round_winners(db, game, round_num)`: find top in-round score, share 1/N round-wins among ties, update `total_round_wins` and `total_round_score` on `Player`.
- [X] T045 [US1, P: app/engine/resolver.py] Implement `finalize_game(db, game)`: rank by `(total_round_wins desc, total_round_score desc)`, set `winner_player_id`, transition game to `completed`.
- [X] T046 [US1] Confirm `pytest tests/test_resolver.py` is green.

### State machine tests + implementation

- [X] T047 [US1, P: tests/test_state_machine.py] Tests for every transition in plan.md state diagram, including illegal transitions return 409.
- [X] T048 [US1, P: app/engine/state_machine.py] Implement `apply_transition(db, game, target_state)` using the guards from T030.

### Scheduler

- [X] T049 [US1, P: app/engine/scheduler.py] Implement `GameScheduler` class: per-game asyncio task that loops `open_turn → wait_until(deadline) → resolve_turn → publish` for each `(round, turn)` pair. Restartable from DB state (idempotent resume after crash).
- [X] T050 [US1, P: app/engine/scheduler.py] Implement `SchedulerRegistry`: singleton mapping `game_id → asyncio.Task`. `start(game)`, `stop(game_id)`, `resume_active_games_on_startup(db)`.
- [X] T051 [US1] Wire `SchedulerRegistry.resume_active_games_on_startup` into the FastAPI startup event in `app/main.py`.

### End-to-end engine test

- [X] T052 [US1, P: tests/test_end_to_end.py] Scripted 100-turn game: create a 5-player game with stub players (deterministic action selection), run scheduler, assert final state matches expected scoreboard and winner.

**Checkpoint**: US-1 complete. Engine is correct and tested. Real games can be driven by external clients once US-2 lands.

---

## Phase 4: User Story 2 — Agent API (Priority: P1)

**Goal**: any HTTP client can join a game, poll for turns, and submit actions per spec §1.1.

**Independent test**: a `curl` script can complete one turn of a real local game; `pytest tests/test_agent_api.py` green.

- [ ] T053 [US2, P: tests/test_agent_api.py] Tests for join happy path, key issuance, validation errors (duplicate display_name, prompt too long, game not registering).
- [ ] T054 [US2, P: tests/test_agent_api.py] Tests for `GET /turn` returning each of the 5 status shapes.
- [ ] T055 [US2, P: tests/test_agent_api.py] Tests for `POST /submit` happy path + every spec §10 error code (`INVALID_TURN_TOKEN`, `INVALID_TARGET`, `ALREADY_SUBMITTED`, `GAME_NOT_ACTIVE`, `RATE_LIMITED`, `DEADLINE_PASSED`).
- [ ] T056 [US2, P: app/routes/agent_api.py] `POST /api/games/{game_id}/join` — creates Player + StrategyPrompt rows, issues fresh agent key, returns it once.
- [ ] T057 [US2, P: app/routes/agent_api.py] `GET /api/games/{game_id}/turn` — returns shape matching contracts/api.yaml; enforces 1s minimum poll interval via per-key in-memory rate limiter.
- [ ] T058 [US2, P: app/routes/agent_api.py] `POST /api/games/{game_id}/submit` — validates body, checks turn_token + deadline + duplicate-submission, persists `TurnSubmission` row; idempotent on `(turn_token, player_id)`.
- [ ] T059 [US2, P: app/routes/agent_api.py] `GET /api/games/{game_id}/state` — agent-flavored snapshot.
- [ ] T060 [US2, P: app/routes/agent_api.py] `POST /api/games/{game_id}/leave` — pre-start drop only; returns 409 after `state == active`.
- [ ] T061 [US2] Mount `agent_api` router in `app/main.py`.
- [ ] T062 [US2] Confirm `pytest tests/test_agent_api.py` green.

**Checkpoint**: US-2 complete. A `curl` user can play.

---

## Phase 5: User Story 3 — Sign in with Google, join, dashboard (Priority: P1)

**Goal**: a human can browse the public lobby, sign in, join a game, and see their per-game dashboard.

**Independent test**: `pytest tests/test_lobby.py` green; manual run-through per quickstart.md US-3.

- [ ] T063 [US3, P: tests/test_lobby.py] Tests for: public lobby renders upcoming + active + recent games; join requires sign-in; join form pre-fills `DEFAULT_STRATEGY_PROMPT`; join after start returns 409; join when full returns 409; dashboard shows agent_key only once (re-render does not re-show).
- [ ] T064 [US3, P: app/routes/web.py] `GET /` — public lobby page.
- [ ] T065 [US3, P: app/templates/home.html] Lobby template per UI.md Page 1.
- [ ] T066 [US3, P: app/templates/fragments/lobby_list.html] HTMX partial for the three game sections.
- [ ] T067 [US3, P: app/routes/web.py] `GET /games/{game_id}/join` — renders join form, redirects to OAuth if not signed in.
- [ ] T068 [US3, P: app/templates/join.html] Join form per UI.md Page 3.
- [ ] T069 [US3, P: app/routes/web.py] `POST /games/{game_id}/join` — creates Player + StrategyPrompt via the same path as agent join, sets session cookie association, redirects to `/me/games/{game_id}`.
- [ ] T070 [US3, P: app/routes/web.py] `GET /me/games` — list of games this Google user has joined.
- [ ] T071 [US3, P: app/templates/my_games.html] My-games template.
- [ ] T072 [US3, P: app/routes/web.py] `GET /me/games/{game_id}` — per-game dashboard.
- [ ] T073 [US3, P: app/templates/connection.html] Dashboard template per UI.md Page 4 (the "pick your AI" version, with MCP/ChatGPT/Other panels — Phase 6 fills in the real commands).
- [ ] T074 [US3, P: app/routes/web.py] `POST /me/games/{game_id}/strategy` — update strategy prompt; pre-start only.
- [ ] T075 [US3, P: app/routes/web.py] `POST /me/games/{game_id}/leave` — pre-start leave from browser.
- [ ] T076 [US3] Mount `web` router in `app/main.py`.
- [ ] T077 [US3] Confirm `pytest tests/test_lobby.py` green.

**Checkpoint**: US-3 complete. A human can complete the full onboarding flow.

---

## Phase 6: User Story 4 + 9 — Game viewer + finished-game replay (Priority: P1 / P2)

**Goal**: anyone can watch a game live or browse a finished one. Same page, same route.

**Independent test**: open two browser tabs on a live game and watch them update in sync within 2 seconds of turn resolution.

- [ ] T078 [US4, P: tests/test_viewer.py] Tests for: viewer page renders for `active` and `completed` states; SSE endpoint emits `turn_resolved` events; strategy prompts never appear in viewer HTML; finished-game viewer includes timeline scrubber data attributes.
- [ ] T079 [US4, P: app/routes/web.py] `GET /games/{game_id}` — game viewer (branches on `game.state`).
- [ ] T080 [US4, P: app/templates/game.html] Game viewer template per UI.md Page 2.
- [ ] T081 [US4, P: app/templates/fragments/scoreboard.html] Scoreboard HTMX partial.
- [ ] T082 [US4, P: app/templates/fragments/turn_block.html] Turn-block HTMX partial.
- [ ] T083 [US4, P: app/templates/fragments/game_status.html] Status header HTMX partial.
- [ ] T084 [US4, P: app/routes/sse.py] `GET /games/{game_id}/stream` — SSE endpoint emitting `turn_resolved`, `game_started`, `game_completed`.
- [ ] T085 [US4] Wire `broadcast.publish` calls from `resolve_turn` and `finalize_game` so SSE events fire on turn resolution.
- [ ] T086 [US4, P: app/routes/spectator_api.py] `GET /api/games/{game_id}/state` — public JSON snapshot, no strategy prompts.
- [ ] T087 [US4] Mount `sse` and `spectator_api` routers in `app/main.py`.
- [ ] T088 [US9] Extend `game.html` with timeline scrubber UI for finished games; data already in template, just add the scrubber control.
- [ ] T089 [US4+US9] Confirm `pytest tests/test_viewer.py` green.

**Checkpoint**: US-4 and US-9 complete. Spectators can watch live and browse finished games.

---

## Phase 7: User Story 5 + 8 — Admin (Priority: P1 / P2)

**Goal**: admin can create scheduled games, monitor running ones, and export data.

**Independent test**: admin creates a game from the dashboard, plays through it with stub agents, downloads CSV/JSON.

- [ ] T090 [US5, P: tests/test_admin.py] Tests for: `/admin` returns 403 for non-admin emails; game creation form validates min ≤ max, start in future, deadline range; created game appears in lobby; cancel works pre-start, fails post-start.
- [ ] T091 [US8, P: tests/test_admin.py] Tests for CSV export shape (one row per agent per turn, columns from plan-summary), JSON export contains players with strategy prompts.
- [ ] T092 [US5, P: app/routes/admin_web.py] `GET /admin` — admin dashboard.
- [ ] T093 [US5, P: app/templates/admin/dashboard.html] Admin dashboard template per UI.md Page 6.
- [ ] T094 [US5, P: app/routes/admin_web.py] `GET /admin/games/new` — create-game form page.
- [ ] T095 [US5, P: app/templates/admin/create_game.html] Form template per UI.md Page 5.
- [ ] T096 [US5, P: app/routes/admin_api.py] `POST /api/admin/games` — create game (admin auth).
- [ ] T097 [US5, P: app/routes/admin_api.py] `POST /api/admin/games/{game_id}/cancel` — cancel pre-start game.
- [ ] T098 [US5, P: app/routes/admin_web.py] `GET /admin/games/{game_id}` — full game detail.
- [ ] T099 [US5, P: app/templates/admin/game_detail.html] Per-game admin view.
- [ ] T100 [US5, P: app/routes/admin_web.py] `GET /admin/prompts` — research view of all strategy prompts.
- [ ] T101 [US5, P: app/templates/admin/prompts.html] Prompts table per UI.md Page 7.
- [ ] T102 [US8, P: app/routes/admin_api.py] `GET /api/admin/games/{game_id}/export.csv` — turn-level CSV.
- [ ] T103 [US8, P: app/routes/admin_api.py] `GET /api/admin/games/{game_id}/export.json` — full game JSON dump.
- [ ] T104 [US5+US8] Mount `admin_web` and `admin_api` routers in `app/main.py`.
- [ ] T105 [US5+US8] Confirm `pytest tests/test_admin.py` green.

**Checkpoint**: US-5 and US-8 complete. Admin can run and analyze games.

---

## Phase 8: User Story 6 + 7 — MCP server + ChatGPT Custom GPT (Priority: P1 / P2)

**Goal**: a Claude user adds the MCP server with one command and walks away; a ChatGPT user adds the Custom GPT and walks away.

**Independent test**: from the player dashboard, copy the MCP setup command, run it in Claude Code, and complete a turn in a real game.

### MCP server

- [ ] T106 [US6, P: mcp_server/__init__.py] Empty MCP package.
- [ ] T107 [US6, P: mcp_server/server.py] FastMCP server with three tools — `get_turn(game_id)`, `submit_action(game_id, action, target_id, message, turn_token)`, `get_game_state(game_id)` — each proxies to the corresponding HTTP API endpoint using `X-Agent-Key` from connection headers.
- [ ] T108 [US6] Mount the MCP server as an HTTP-streamed app at `/mcp` in `app/main.py`.
- [ ] T109 [US6, P: tests/test_mcp.py] Tests for tool definitions exposed, header propagation, error pass-through.
- [ ] T110 [US6, P: mcp_server/README.md] Notes on how the server forwards to the HTTP API, headers it reads, errors it returns.

### ChatGPT Custom GPT manifest

- [ ] T111 [US7, P: chatgpt_custom_gpt/manifest.json] Action manifest pointing at `/openapi.json` with `securitySchemes.AgentKey` (header `X-Agent-Key`).
- [ ] T112 [US7, P: chatgpt_custom_gpt/README.md] How to add the GPT to ChatGPT, where the player pastes their API key.

### Setup docs

- [ ] T113 [US6, P: docs/setup-claude.md] Step-by-step Claude Desktop and Claude Code setup, including the JSON config snippet for Desktop.
- [ ] T114 [US7, P: docs/setup-chatgpt.md] Step-by-step ChatGPT Custom GPT setup.
- [ ] T115 [US6+US7, P: docs/setup-other.md] Raw API + OpenAPI usage for Gemini, custom code.

### Dashboard wiring

- [ ] T116 [US6+US7] Update `app/templates/connection.html` so the Claude panel shows the real `claude mcp add … --header "X-Agent-Key: …"` command and the ChatGPT panel shows the real "Add to ChatGPT" link.
- [ ] T117 [US6+US7] Confirm `pytest tests/test_mcp.py` green and manual setup steps from quickstart.md US-6 and US-7 pass on the deployed Railway URL.

**Checkpoint**: US-6 and US-7 complete. A non-programmer Claude or ChatGPT user can play a full game.

---

## Phase 9: User Story 10 — Cross-device dashboard (Priority: P2)

**Goal**: a player who joined on one device can manage the game from another by signing in with the same Google account.

**Independent test**: covered by the existing tests for `/me/games` plus a manual check.

- [ ] T118 [US10, P: tests/test_cross_device.py] Test: two `TestClient` sessions with the same Google `sub` see the same `/me/games` and `/me/games/{id}` content.

**Checkpoint**: US-10 verified.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: production readiness.

- [ ] T119 [P: app/main.py] Structured logging configuration: every accepted submission, every rejected submission with error code, every turn resolution, every state transition.
- [ ] T120 [P: app/main.py] OpenAPI tagging — apply `agent`, `web`, `admin`, `spectator`, `auth`, `mcp`, `ops` tags consistently so the Custom GPT manifest can scope its `allowed_operations`.
- [ ] T121 [P: docs/setup-dev.md] Local development setup: Google OAuth client setup steps, `.env` config, alembic + uvicorn commands.
- [ ] T122 [P: docs/deploy-railway.md] Railway deployment: service config, Postgres add-on, env var setup, custom domain, OAuth redirect URI update.
- [ ] T123 [P: README.md] Update with run-locally and deploy-to-Railway sections.
- [ ] T124 [P: tests/test_end_to_end.py] Extend the end-to-end test to drive a full 10-round game using real HTTP (not direct ORM access) — close to the real-world path.
- [ ] T125 Confirm `pytest` green across the whole suite.
- [ ] T126 Manual run through every section in `quickstart.md` against the local server.
- [ ] T127 Deploy to Railway; manual run through US-6 and US-7 against the public URL.

**Checkpoint**: v1 is ship-ready.

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: no dependencies.
- **Phase 2 (Foundation)**: depends on Phase 1. **Blocks all user stories.**
- **Phase 3 (US-1, engine)**: depends on Phase 2. **Blocks Phase 4 (Agent API).**
- **Phase 4 (US-2, Agent API)**: depends on Phase 3.
- **Phase 5 (US-3, Lobby+Join)**: depends on Phase 2 only — can start in parallel with Phase 3 (different files) but the join flow needs to issue agent keys (Phase 4 dependency for full flow). Practically, run after Phase 3.
- **Phase 6 (US-4 + US-9, Viewer)**: depends on Phase 5 (game must exist to view).
- **Phase 7 (US-5 + US-8, Admin)**: depends on Phase 5 (admin reuses templates and auth).
- **Phase 8 (US-6 + US-7, MCP + Custom GPT)**: depends on Phase 4 (HTTP API must exist for tools to proxy to).
- **Phase 9 (US-10)**: depends on Phase 5.
- **Phase 10 (Polish)**: depends on all user-story phases being complete enough to ship.

### Parallel opportunities

- Within Phase 1: T002–T011 can run in parallel; T001 first.
- Within Phase 2: all ORM model tasks (T013–T018) can run in parallel; all schema tasks (T022–T026) in parallel; engine constants (T027–T030) in parallel; auth scaffolding (T031–T033) in parallel.
- Within user-story phases: tasks marked `[P: ...]` operate on disjoint files and can run in parallel; same-file tasks are serial.
- Phase 5 and Phase 8 can be developed in parallel by different agents/devs once Phases 2–4 are done.

### MVP scope

To call v1 "MVP-shippable," the following must all be green: Phases 1, 2, 3, 4, 5, 7 (US-5 only), 8 (US-6 only). That covers a Claude user joining a game, playing it, and an admin running it. US-7 (ChatGPT), US-8 (export), US-9 (timeline scrubber), and US-10 (cross-device check) round out the full v1 but aren't blockers.
