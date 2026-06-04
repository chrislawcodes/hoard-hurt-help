# Feature 009 — Game/Match Vocabulary Disambiguation & Full Rename

- **Feature branch**: `feature/game-match-rename`
- **Created**: 2026-06-03
- **Status**: Draft → Planning
- **Input**: Disambiguate the overloaded word "game." Lock vocabulary, then do a full rename across code, URLs, DB, the agent/MCP contract, and docs.

## Summary

The word "game" means three different things in this codebase, and two of them collide:

- **Platform** = Agent Ludum (already unambiguous).
- **Game** = a *title/design* in the catalog (Hoard Hurt Help). Lives in `app/games/<module>/` and the `Game.game_type` column. This is the correct, surviving meaning of "game."
- **Match** = a *single played match* (today the `Game` model, IDs like `G_0016`). This is the meaning that gets renamed to **Match**.

After this feature: a **Game** is a title you can play; a **Match** is one play of it (`M_0016`); a Match has rounds and turns. URLs nest as `/games/<game>/matches/<match_id>`.

This is a rename/refactor with a data migration. It changes **no** game rules, payoffs, art, or the (still PD-shaped) move format.

## User Scenarios & Testing

### User Story 1 — Spectator understands what they're looking at (Priority: P1)

As a visitor to Agent Ludum, I need the words and URLs to clearly separate "the game (Hoard Hurt Help)" from "a single match," so I'm never confused about whether I'm looking at a title or one play of it.

**Why this priority**: This is the entire point of the feature. Without consistent vocabulary in the UI and URLs, nothing is disambiguated.

**Independent Test**: Browse `/games` → pick Hoard Hurt Help → land on `/games/hoard-hurt-help` → open a match at `/games/hoard-hurt-help/matches/M_0016`. Every page calls the title a "game" and the single play a "match." No page uses "game" to mean a single play.

**Acceptance Scenarios**:

1. **Given** the catalog page, **When** I visit `/games`, **Then** I see a list of games (titles), with Hoard Hurt Help among them.
2. **Given** a game home, **When** I visit `/games/hoard-hurt-help`, **Then** I see the game's description, lobby, and a list of its matches (live + finished), each labeled "match."
3. **Given** a match, **When** I visit `/games/hoard-hurt-help/matches/M_0016`, **Then** the viewer, analysis, and join pages all refer to it as a "match" and show the `M_0016` ID.
4. **Given** any old URL (`/play/hoard-hurt-help`, `/games/G_0016`, `/games/M_0016/analysis`), **When** I visit it, **Then** I'm 301-redirected to the new canonical nested URL.

### User Story 2 — Live bots keep playing through the rename (Priority: P1)

As an operator of a bot that is mid-match when this ships, I need my bot's existing API/MCP calls to keep working, so an in-flight match doesn't break.

**Why this priority**: The agent API and MCP server are a public contract. A hard rename mid-match would crash live bots and corrupt running matches. Non-negotiable.

**Independent Test**: With a bot polling `/api/games/{id}/turn` and using MCP `get_game_state` / `submit_action` against an old-style request, run a full match through the deploy. The bot completes the match without changing its code. Equivalent calls against the new `match_id`-named paths/tools also work.

**Acceptance Scenarios**:

1. **Given** a bot calling `GET /api/games/{id}/turn` (old path), **When** the renamed build is live, **Then** the call still resolves (alias/forward) and returns a valid turn.
2. **Given** a bot calling `POST /api/games/{id}/submit` with the old payload shape, **When** it submits, **Then** the action is recorded.
3. **Given** an MCP client calling `get_game_state` / `submit_action` with old argument names, **When** the build is live, **Then** the tools accept old and new argument names.
4. **Given** the new canonical paths (`/api/matches/{match_id}/...`) and renamed MCP arguments, **When** a bot uses them, **Then** they work identically.
5. **Given** a response body, **When** a bot reads it, **Then** it contains the new `match_id` field AND (for the deprecation window) the legacy `game_id` field with the same value.

### User Story 3 — Existing matches survive the ID migration intact (Priority: P1)

As the site owner, I need every historical match (`G_0001`…`G_NNNN`) migrated to the new `M_` IDs with all its players, turns, submissions, and strategy prompts still linked correctly, so no past match is lost or orphaned.

**Why this priority**: This is a data-affecting migration on the production DB. Getting it wrong loses or corrupts real match history.

**Independent Test**: On a production-shaped fixture DB, run the migration in `--dry-run`, confirm the planned row counts, run it live, then verify: every former `G_` match now has an `M_` PK; row counts for matches/players/turns/submissions/strategy_prompts are unchanged; every foreign key resolves; `winner_player_id` and all relationships still point to the right rows.

**Acceptance Scenarios**:

1. **Given** a DB with `G_`-prefixed matches, **When** I run the migration with `--dry-run`, **Then** it prints the exact rewrite plan and row counts and changes nothing.
2. **Given** the same DB, **When** I run the migration live, **Then** every match PK becomes `M_xxxx` and every `match_id`/`game_id` foreign key is rewritten to match.
3. **Given** the migrated DB, **When** I count rows in matches, players, turns, submissions, and strategy prompts, **Then** counts equal the pre-migration counts.
4. **Given** the migrated DB, **When** I open any migrated match, **Then** its players, turn history, scores, winner, and strategy prompts are all present and correct.
5. **Given** the migration runs on SQLite (dev/test) and Postgres-shaped prod data, **When** it applies constraint changes, **Then** it uses batch mode and completes without a constraint error.

### User Story 4 — Internal code consistently says "match" for a single play (Priority: P2)

As a developer (or another agent) working in this repo, I need the model, columns, routes, schemas, and engine code to call a single play a "match," so the code stops contradicting itself (today `app/games/` = titles but `Game` = a single play).

**Why this priority**: Important for long-term clarity and to prevent the bug-prone ambiguity, but the user-facing value (US1–US3) lands first.

**Independent Test**: Grep the codebase: the `Match` model exists, `match_id` is the foreign key on players/turns/submissions/strategy prompts, the title slug column is `game`, and `app/games/` module-dir semantics are untouched. `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` passes.

**Acceptance Scenarios**:

1. **Given** the models, **When** I inspect them, **Then** the single-play model is `Match` with PK `M_xxxx`, and the title is referenced by a `game` slug column.
2. **Given** the foreign keys, **When** I inspect player/turn/submission/strategy-prompt models, **Then** they reference `match_id`.
3. **Given** `app/games/`, **When** I inspect it, **Then** it still holds game *modules* (titles) and its meaning is unchanged.
4. **Given** the full preflight gate, **When** I run it, **Then** ruff, mypy, and pytest all pass with no suppressions added.

### User Story 5 — Docs reflect the new vocabulary (Priority: P3)

As a future contributor, I need `DESIGN.md` and `docs/writing-a-game-module.md` to use "game = title" and "match = single play" consistently, so I learn the right vocabulary.

**Why this priority**: Valuable for onboarding but does not affect runtime behavior.

**Independent Test**: Read both docs; they define and use the locked vocabulary, and any code/URL examples match the renamed reality.

**Acceptance Scenarios**:

1. **Given** `DESIGN.md`, **When** I read the data-model and routing sections, **Then** they describe `Match`/`match_id`/`M_` and nested `/games/<game>/matches/<match_id>` URLs.
2. **Given** `docs/writing-a-game-module.md`, **When** I read it, **Then** "game" means the title/module and "match" means a single play throughout.

## Edge Cases

- **Old URL with old ID** (`/games/G_0016`): redirect must map `G_0016` → its new `M_` ID, not just swap the path. → Redirect resolves the migrated ID and 301s to the canonical nested URL.
- **Old URL after ID migration**: a bookmarked `G_0016` no longer exists as a PK. → The redirect layer keeps a `G_*`→`M_*` lookup (or deterministic mapping) so old links never 404 during the deprecation window.
- **Bot sends `game_id` in a JSON body** while server expects `match_id`: → Request schema accepts both; `game_id` is treated as an alias of `match_id`.
- **Response consumed by an old bot** that reads `game_id`: → Responses include both `match_id` and legacy `game_id` for the deprecation window.
- **MCP tool called with old arg name**: → Tool accepts old and new arg names; documents new as canonical.
- **Migration interrupted partway**: → Migration is a single transaction (or idempotent), so a crash leaves the DB either fully old or fully new, never half-rewritten.
- **ID collision risk**: a `G_`→`M_` rewrite must not collide with any natively-minted `M_` IDs. → The ID allocator and migration must agree on the numbering so no two matches share an `M_` ID.
- **`/next-turn` cross-game poll**: the cross-match poll endpoint must be renamed/aliased consistently (it returns match references). → Both old and new shapes work.
- **Empty DB / fresh install**: migration must no-op cleanly with zero `G_` rows.
- **`app/games/` confusion**: the rename must NOT touch the game-module directory or registry semantics — only the single-play model. → Verified by grep and tests.

## Requirements

### Functional Requirements

- **FR-001**: The single-play SQLAlchemy model MUST be named `Match`, with primary keys of the form `M_NNNN`. Supports US3, US4.
- **FR-002**: The title slug column on a match MUST be named `game` (renamed from `game_type`), holding values like `hoard-hurt-help`. Supports US4.
- **FR-003**: Foreign keys to a match on the player, turn, turn-submission, and strategy-prompt models MUST be named `match_id` (renamed from `game_id`). Supports US4.
- **FR-004**: A data migration MUST rewrite every existing `G_`-prefixed match primary key AND all foreign-key references to the corresponding `M_` ID, preserving all relationships. Supports US3.
- **FR-005**: The migration MUST provide a `--dry-run` mode that prints the rewrite plan and affected row counts and changes nothing. Supports US3.
- **FR-006**: The migration MUST use SQLite batch mode (`op.batch_alter_table`) for any constraint/PK/FK changes so `alembic upgrade head` succeeds on SQLite dev/test DBs. Supports US3.
- **FR-007**: The migration MUST be verified against production-shaped fixtures, with pre/post row-count assertions for matches, players, turns, submissions, and strategy prompts. Supports US3.
- **FR-008**: The migration MUST be atomic or idempotent so an interruption never leaves a half-rewritten DB. Supports US3.
- **FR-009**: User-facing URLs MUST follow the nested structure: `/games` (catalog), `/games/<game>` (game home + lobby), `/games/<game>/matches/<match_id>` (and `/analysis`, `/join`, `/live`, `/stream` beneath it). Supports US1.
- **FR-010**: Old URLs (`/play/<game>`, `/games/<old-or-new-id>`, and their sub-paths) MUST 301-redirect to the new canonical nested URLs, resolving `G_`→`M_` IDs so no old link 404s during the deprecation window. Supports US1.
- **FR-011**: The agent API MUST expose canonical `match_id`-named paths AND keep the old `/api/games/{id}/...` paths working as aliases for a deprecation window. Supports US2.
- **FR-012**: Agent API request schemas MUST accept both `match_id` and legacy `game_id` (treated as the same value). Supports US2.
- **FR-013**: Agent API response bodies MUST include the new `match_id` field AND, for the deprecation window, the legacy `game_id` field with the same value. Supports US2.
- **FR-014**: MCP tools (`get_game_state`, `submit_action`, and any other match-scoped tools) MUST accept both old and new argument names, with the new names documented as canonical. Supports US2.
- **FR-015**: All HTML templates and human-facing copy MUST use "game" only for the title and "match" only for a single play. Supports US1.
- **FR-016**: The rename MUST NOT change the meaning of the `app/games/` module directory or the game-module registry (these correctly mean "title"). Supports US4.
- **FR-017**: The rename MUST NOT change game rules, payoffs, scoring, art, or the PD-shaped move format. (Out of scope guardrail.)
- **FR-018**: The full preflight gate (`ruff check .`, `mypy app/ mcp_server/`, `pytest -q`) MUST pass with no new `# type: ignore` / `# noqa` / swallowed exceptions. Supports US4.
- **FR-019**: The match-ID allocator and the migration MUST agree on `M_` numbering so no two matches ever share an `M_` ID. Supports US3, edge cases.
- **FR-020**: `DESIGN.md` and `docs/writing-a-game-module.md` MUST be updated to the locked vocabulary and renamed reality. Supports US5.

### Key Entities

- **Game (title)**: a playable title in the catalog (e.g. Hoard Hurt Help). Identified by a slug (`hoard-hurt-help`). Implemented as a game module under `app/games/<module>/`. Referenced from a match by the `game` slug column. *Not* a DB table change in this feature beyond the column rename.
- **Match (single play)**: one play of a game, start to finish. Was the `Game` model; becomes `Match`. PK `M_NNNN`. Holds `game` (title slug), `state`, `current_round/turn`, scheduling, `winner_player_id`, etc.
- **Player**: one bot's participation in a match. FK `match_id`.
- **Turn / TurnSubmission**: structure within a match. FK `match_id` (turn) and existing turn/player FKs (submission).
- **StrategyPrompt**: versioned strategy for a player within a match. FK `match_id` (where applicable) / `player_id`.

## Success Criteria

- **SC-001**: A new visitor can go catalog → game → match and at every step the title is called "game" and a single play is called "match," with zero pages using "game" to mean a single play.
- **SC-002**: 100% of old URLs (sampled across `/play/...`, `/games/G_xxxx`, and sub-paths) resolve via 301 to the correct new nested URL with no 404s.
- **SC-003**: A bot that does not change its code completes a full match across the deploy boundary using the old API/MCP shapes.
- **SC-004**: After migration, row counts for matches, players, turns, submissions, and strategy prompts exactly equal the pre-migration counts, and every foreign key resolves (zero orphans).
- **SC-005**: `--dry-run` output matches the actual changes applied when run live (same plan, same counts).
- **SC-006**: The preflight gate passes with no added suppressions.

## Assumptions

- The "game = title" meaning (the `app/games/` module dir and registry) is already correct and stays; only the single-play model is wrong and gets renamed. (Confirmed by Chris.)
- Chris chose to **rewrite all** `G_`→`M_` IDs (not leave historical rows as `G_`), accepting the data-migration risk in exchange for full consistency.
- Chris chose the **nested** URL structure as canonical.
- A deprecation window for the old agent/MCP shapes is acceptable; the old shapes can be removed in a later, separate change once bots have migrated. The length of that window is an operational decision, not part of this feature.
- The deprecation removal of old aliases is explicitly a **future** task, not part of feature 009.
- Spectator JSON / leak-test surfaces (agent API + MCP + spectator JSON) must continue to expose only what they exposed before — the rename changes names, not visibility.

## Constitution Check

Validated against `CLAUDE.md` (project constitution) and `~/.claude/rules/data-critical-waves.md`:

- **PR via feature branch** (`feature/game-match-rename`), no direct push to main. ✓
- **No suppressions**: FR-018 forbids new ignores/noqa. ✓
- **Type annotations / async consistency**: enforced by preflight (mypy) and existing standards. ✓
- **Test new game logic in `app/engine/`**: this feature changes *names*, not logic; existing engine tests must keep passing, and migration tests are required. ✓
- **Data-critical waves rule**: `--dry-run` (FR-005), production-shaped fixtures (FR-007), real prod value/format confirmation, batch mode (FR-006), and a post-deploy verification checklist are all mandated. ✓ — the plan stage must include the post-deploy checklist (deployed commit on prod, migration applied + row counts, UI/API end-to-end, no error spikes for 10 min).
- **Public contract safety**: the agent/MCP alias requirement (FR-011–FR-014) protects live bots. ✓

**Result: PASS** (with the data-critical post-deploy checklist owed by the plan stage).

---

✓ Spec complete — proceeding to technical planning (`feature-plan`).
