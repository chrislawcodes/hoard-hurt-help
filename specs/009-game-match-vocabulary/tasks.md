# Tasks: Game/Match Vocabulary Disambiguation & Full Rename

**Prerequisites**: plan.md, plan-summary.md, spec.md, spec-acceptance.md, data-model.md

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: file]**: parallelizable (disjoint files). Bare `[P]` = serial.
- **[Story]**: US1–US5.
- Paths are repo-relative.

> **Dependency note**: this is a rename refactor. The model rename + token change + migration (Phase 2) are the technical foundation that US1/US2/US3/US4 all build on, even though US4 is P2. Phases 3–7 layer URLs, aliases, the code sweep, and docs on top.

---

## Phase 1: Setup

**Purpose**: Lock the audit baseline before touching anything.

- [X] T001 Capture a full reference list of every `game_id`, `game_type`, `Game`, and `generate_game_id` site: `grep -rn -e "game_id" -e "game_type" -e "\bGame\b" -e "generate_game_id" app/ mcp_server/ tests/ migrations/ docs/ DESIGN.md > specs/009-game-match-vocabulary/rename-audit-before.txt`. This is the checklist the implementation must drive to zero (except intentional keepers: `app/games/` dir, `GameState` enum, registry key string `"hoard-hurt-help"`).
- [X] T002 Back up the dev DB before any migration work: `cp hoardhurthelp.db hoardhurthelp.db.pre0018-bak` (skip if no dev DB present).

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: Rename the model, the ID allocator, and ship the atomic migration + dry-run preview. Everything else depends on this.

⚠️ **CRITICAL**: No URL, alias, or template work until this phase is green.

- [X] T003 [P: app/engine/match_id_rewrite.py] Create shared helper `app/engine/match_id_rewrite.py`: `swap_prefix(old: str) -> str` (`G_`↔`M_`), `to_match_id(game_id)`, `to_game_id(match_id)`, and `affected_tables()` returning `[("matches","id"),("players","match_id"),("turns","match_id"),("request_incidents","match_id")]`. Imported by both the migration and the preview script so the plan can't drift.
- [X] T004 [P: app/engine/tokens.py] In `app/engine/tokens.py`, rename `generate_game_id`→`generate_match_id` and change the prefix to `M_{n:04d}`; update the docstring.
- [X] T005 Rename `app/models/game.py`→`app/models/match.py` (`git mv`): class `Game`→`Match`, `__tablename__="matches"`, column `game_type`→`game`, FK constraint name `fk_games_winner_player_id_players`→`fk_matches_winner_player_id_players`. Keep the `GameState` enum name as-is (documented scope boundary).
- [X] T006 [P: app/models/player.py] In `app/models/player.py`: `game_id`→`match_id`, `ForeignKey("games.id")`→`ForeignKey("matches.id")`, unique constraints `uq_players_game_id_agent_id`→`uq_players_match_id_agent_id` and `uq_players_bot_id_game_id`→`uq_players_bot_id_match_id`.
- [X] T007 [P: app/models/turn.py] In `app/models/turn.py`: `Turn.game_id`→`match_id`, `ForeignKey("games.id")`→`ForeignKey("matches.id")`, unique `uq_turns_game_id_round_turn`→`uq_turns_match_id_round_turn`.
- [X] T008 [P: app/models/request_incident.py] In `app/models/request_incident.py`: `game_id`→`match_id` (plain string col + its index).
- [X] T009 [P: app/models/__init__.py] In `app/models/__init__.py`: export `Match` (replace `Game` export); keep `GameState` export.
- [X] T010 Create migration `migrations/versions/0018_rename_game_to_match.py` per data-model.md: atomic upgrade — batch-rename table/columns/constraints, drop match FKs, prefix-swap UPDATEs via the T003 helper, re-add FKs. All DDL wrapped in `op.batch_alter_table`. Document downgrade (reverse renames + `M_`→`G_`); if value-rewrite downgrade is infeasible under SQLite batch, mark forward-only in the docstring.
- [X] T011 [P: scripts/preview_match_id_migration.py] Create `scripts/preview_match_id_migration.py --dry-run --db <path>`: read-only; print the `G_xxxx→M_xxxx` mapping and per-table affected row counts using the T003 helper; change nothing. Exit non-zero if the DB is already migrated or empty-with-warning.

**Checkpoint**: `alembic upgrade head` runs clean on a seeded SQLite DB; models import; `generate_match_id` mints `M_`.

---

## Phase 3: User Story 3 - Existing matches survive the migration (Priority: P1)

**Goal**: Every `G_` match becomes `M_` with all relationships and counts intact.

**Independent Test**: Seed prod-shaped rows at `0017`, run `upgrade head`, assert IDs are `M_`, counts unchanged, zero orphans; dry-run counts equal applied counts.

- [X] T012 [P: tests/test_migrations.py] [US3] Extend `tests/test_migrations.py`: build SQLite DB, seed a `G_` match + players + turns + turn_submissions + a request_incident, run `upgrade head`, assert table `matches`, columns renamed, all IDs `M_`, every FK resolves, per-table counts preserved, zero rows `LIKE 'G\_%'`.
- [X] T013 [P: tests/test_match_id_preview.py] [US3] Create `tests/test_match_id_preview.py`: run the preview script against the seeded DB; assert it prints the mapping + counts, leaves the DB unchanged, and its counts equal the post-`upgrade` counts (SC-005).

**Checkpoint**: migration + preview proven on prod-shaped fixtures.

---

## Phase 4: User Story 1 - Spectator sees game (title) vs match (play) (Priority: P1) 🎯 MVP

**Goal**: Nested URLs + human copy cleanly separate title from single play; old URLs 301.

**Independent Test**: Browse `/games`→`/games/hoard-hurt-help`→`/games/hoard-hurt-help/matches/M_xxxx`; old URLs redirect.

- [X] T014 [US1] In `app/routes/web.py`: add `/games` catalog route; move lobby `/play/hoard-hurt-help`→`/games/hoard-hurt-help`; restructure match pages under `/games/{game}/matches/{match_id}` (viewer, `/live`, `/analysis`, `/analysis/rounds/{n}`, `/join`).
- [X] T015 [US1] In `app/routes/web.py`: add 301 redirect handlers — `/play/{game}`→`/games/{game}`, and legacy `/games/{old_id}`(+sub-paths)→`/games/{game}/matches/{match_id}` using `to_match_id()` to swap `G_`→`M_`; 404 only if the resolved match truly doesn't exist.
- [X] T016 [P: app/routes/sse.py] [US1] In `app/routes/sse.py`: move the stream to the nested match path; keep the old path as a thin alias.
- [X] T017 [US1] Update templates for the nested URL structure and vocabulary: `app/templates/agent_ludum.html` (catalog/home links) and the game/match viewer, join, analysis, and lobby templates — "game"=title, "match"=single play; render `M_` IDs. (Serial: shared partials.)
- [X] T018 [P: tests/test_match_urls.py] [US1] Create `tests/test_match_urls.py`: assert `/games` catalog renders; `/games/hoard-hurt-help/matches/M_xxxx` renders; `/play/hoard-hurt-help`→301; `/games/G_xxxx`(+`/analysis`)→301 to the `M_` nested URL.

**Checkpoint**: full catalog→game→match flow works; no old-link 404s.

---

## Phase 5: User Story 2 - Live bots keep playing (aliases) (Priority: P1)

**Goal**: Old agent API paths/params and MCP arg names keep working; responses carry both keys.

**Independent Test**: Same bot completes a turn via `/api/games/...` and via `/api/matches/...`; MCP accepts old+new args; responses contain `match_id` and `game_id`.

- [X] T019 [US2] In `app/schemas/agent.py`: make `match_id` canonical with a `game_id` request alias (validator/`AliasChoices`); ensure response models emit BOTH `match_id` and legacy `game_id` (same value) for the deprecation window.
- [X] T020 [P: app/schemas/spectator.py] [US2] Same alias + dual-key treatment in `app/schemas/spectator.py`.
- [X] T021 [US2] In `app/routes/agent_api.py`: register canonical `/api/matches/{match_id}/...` handlers; mount the existing `/api/games/{game_id}/...` paths as aliases to the same handlers. Rename internal vars `game_id`→`match_id`.
- [ ] T022 [P: app/routes/agent_next_turn.py] [US2] In `app/routes/agent_next_turn.py`: rename to `match_id`, keep `/next-turn` response shape carrying both keys; alias old path if path changes.
- [X] T023 [P: app/routes/spectator_api.py] [US2] In `app/routes/spectator_api.py`: canonical `/api/matches/...` + `/api/games/...` alias; rename internals.
- [X] T024 [P: app/routes/admin_api.py] [US2] In `app/routes/admin_api.py`: match-scoped admin endpoints renamed + aliased; export filenames/labels use match.
- [X] T025 [P: app/routes/admin_web.py] [US2] In `app/routes/admin_web.py`: admin match pages renamed; internals `game_id`→`match_id`.
- [X] T026 [US2] In `mcp_server/server.py`: keep tool NAMES stable (`get_game_state`, `submit_action`, etc.); accept both `game_id` and `match_id` arguments (new canonical); update `mcp_server/README.md`.
- [X] T027 [P: tests/test_match_api_aliases.py] [US2] Create `tests/test_match_api_aliases.py`: old path == new path results; POST with `game_id` body and `match_id` body both recorded; responses contain both keys; MCP tool accepts old+new arg names.

**Checkpoint**: an unchanged bot survives the deploy boundary.

---

## Phase 6: User Story 4 - Internal code consistently says "match" (Priority: P2)

**Goal**: Drive the remaining `game_id`/`Game` references (engine, deps, sims, hhh module) to `match_id`/`Match`; preflight green.

**Independent Test**: grep audit clean (minus keepers); `ruff && mypy && pytest` green.

- [X] T028 [P: app/engine/scheduler.py] [US4] `app/engine/scheduler.py`: `Game`→`Match`, `game_id`→`match_id`, `game_type` reads→`game`.
- [X] T029 [P: app/engine/resolver.py] [US4] `app/engine/resolver.py`: `Game`→`Match`, `game_id`→`match_id`.
- [X] T030 [P: app/engine/next_turn.py] [US4] `app/engine/next_turn.py`: `game_id`→`match_id`.
- [X] T031 [P: app/engine/bot_activity.py] [US4] `app/engine/bot_activity.py`: `game_id`→`match_id`.
- [X] T032 [P: app/engine/game_insights.py] [US4] `app/engine/game_insights.py`: `game_id`→`match_id` (keep the "insights" filename).
- [X] T033 [P: app/engine/sims/service.py, app/engine/sims/seating.py, app/engine/sims/types.py] [US4] sims modules: `game_id`→`match_id`.
- [X] T034 [US4] `app/games/base.py` + `app/games/__init__.py`: keep the registry key string `"hoard-hurt-help"` and the `game_type` registry concept; only update reads of the renamed match column (`match.game`). Do NOT change `app/games/` directory semantics (FR-016).
- [X] T035 [P: app/games/hoard_hurt_help/game.py] [US4] `app/games/hoard_hurt_help/game.py`: `Game`→`Match`, `game_id`→`match_id`.
- [X] T036 [P: app/deps.py, app/broadcast.py, app/request_logging.py] [US4] `app/deps.py`, `app/broadcast.py`, `app/request_logging.py`: `game_id`→`match_id`.
- [X] T037 [P: app/routes/bots_web.py] [US4] `app/routes/bots_web.py` + remaining bot/admin templates (`bots/_status.html`, `bots/detail.html`, `admin/prompts.html`, `admin/incidents.html`, `admin/incident_detail.html`): `game_id`→`match_id`, copy uses "match" for a play.
- [ ] T038 [US4] Re-run the T001 grep; reconcile `rename-audit-before.txt` to a clean `rename-audit-after.txt` — every remaining hit must be an intentional keeper (`app/games/` dir, `GameState`, registry key string, the legacy-alias code paths). Document keepers inline in the audit file.

**Checkpoint**: grep audit clean; preflight green.

---

## Phase 7: User Story 5 - Docs reflect new vocabulary (Priority: P3)

**Goal**: DESIGN.md + writing-a-game-module.md teach "game"=title, "match"=play.

- [X] T039 [P: DESIGN.md] [US5] `DESIGN.md`: update data-model + routing sections to `Match`/`match_id`/`M_` and nested `/games/{game}/matches/{match_id}` URLs; document the alias deprecation window + the `GameState` keeper.
- [X] T040 [P: docs/writing-a-game-module.md] [US5] `docs/writing-a-game-module.md`: "game"=title/module, "match"=single play throughout; fix code/URL examples.

---

## Phase 8: Polish & Cross-Cutting

- [X] T041 Run the full preflight gate from repo root: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. Fix root causes; no `# type: ignore`/`# noqa`/swallowed exceptions.
- [ ] T042 Walk `quickstart.md` end-to-end on the dev DB (migration dry-run → apply → URL flow → bot alias checks).
- [X] T043 Re-run the leak tests (agent API + MCP + spectator JSON) to confirm the rename changed names only, not visibility (MEMORY: spectator-channel-is-bot-reachable).
- [ ] T044 Fill in the Post-Deploy Verification checklist in plan.md as a PR `Validation` section (commands run + pass/fail).

---

## Dependencies & Execution Order

### Phase Dependencies
- **Phase 1 Setup**: no deps.
- **Phase 2 Foundation**: depends on Setup — BLOCKS all stories.
- **Phase 3 (US3)**: depends on Foundation (migration exists).
- **Phase 4 (US1)** & **Phase 5 (US2)**: depend on Foundation; independent of each other.
- **Phase 6 (US4)**: depends on Foundation; finishes the sweep US1/US2 started.
- **Phase 7 (US5)**: depends on the rename being settled.
- **Phase 8 Polish**: depends on all desired stories.

### Parallel Opportunities
- T003/T004 parallel (different files). T006–T009 parallel (different model files) after T005.
- Within Phase 6, all `[P]`-marked single-file edits parallelize.
- Test-authoring tasks (T012, T013, T018, T027) parallelize with each other.

### Implementation order (recommended serial spine)
T001→T002→T003/T004→T005→T006–T009→T010→T011→T012/T013→T014–T018→T019–T027→T028–T038→T039/T040→T041–T044.
