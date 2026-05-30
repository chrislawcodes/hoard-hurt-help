# Feature Specification: Turn-Based Game Framework

**Feature branch**: `004-game-framework`
**Created**: 2026-05-30
**Status**: Draft
**Input**: Generalize the app from a single Prisoner's Dilemma game into a platform that can host multiple **turn-based** LLM-agent games, with the current PD game refactored to run as the first "game module."

---

## Summary

Today the platform layer (bots, accounts, credentials, the `get_next_turn` loop, the runner, scheduling, lobby, the turn-loop orchestration, the agent API, spectator chrome, strategy profiles) is already game-agnostic — but the **game rules are hard-wired**: the move vocabulary (HOARD/HELP/HURT), scoring/resolution, and PD-flavored columns are baked into the engine, models, schemas, routes, and templates.

This feature draws a clean line between **platform** and **game**, introducing a **Game plugin contract**. Each game implements that contract; the platform supplies everything else. The current PD game is refactored to run *through* the contract as the first module (`hoard-hurt-help`), with **identical** behavior for players and bots.

**Confirmed scope (this effort):** define the contract + move PD behind it. A second game is **deferred** to a later effort. We accept the known risk that an interface designed against one game may need reshaping when game #2 arrives.

**Out of scope:** non-turn-based games (real-time, etc.); building a second game; renaming the product (HHH stays the brand; PD is the module `hoard-hurt-help`); any change to how bots connect/play.

---

## User Scenarios & Testing

### User Story 1 - PD runs unchanged through the new interface (Priority: P1)

As a player/bot owner, I keep playing Prisoner's Dilemma exactly as before — same moves, same scoring, same payloads — even though the rules now run through the new Game plugin layer.

**Why this priority**: A framework refactor must not regress the live game. This is the safety floor: behavior is preserved.

**Independent Test**: Run the existing PD game end-to-end (join, play, resolve, score, finalize) and confirm identical outcomes to before the refactor — the existing resolver/engine tests pass unchanged against the PD module.

**Acceptance Scenarios**:
1. **Given** a PD game with submitted moves, **When** a turn resolves, **Then** scores, round wins, the score floor, and the mutual-help bonus match the pre-refactor behavior exactly.
2. **Given** a connected bot, **When** it calls `get_next_turn` / `submit_action` for a PD game, **Then** the request/response shapes are unchanged.
3. **Given** the existing engine test suite, **When** it runs against the PD module, **Then** all tests pass without modification to their assertions.

---

### User Story 2 - Add a turn-based game by writing only a game module (Priority: P1)

As a developer, I can add a new turn-based game by implementing the Game plugin contract — its moves, turn/round structure, a resolve function, rules + agent payload, viewer, and config defaults — **without editing platform code**.

**Why this priority**: This is the whole point — the framework capability. Without it, this is just an internal refactor.

**Independent Test**: Implement a trivial throwaway game module (e.g. a stub that scores +1 per move) entirely within the game-module boundary, register it, and confirm a game of that type can be created, played by a bot, resolved, and scored — touching no platform files.

**Acceptance Scenarios**:
1. **Given** a new game module that implements the contract, **When** it is registered, **Then** games of its type can be created and appear in the lobby alongside PD games.
2. **Given** a registered new game type, **When** a bot plays it via `get_next_turn`/`submit_action`, **Then** the platform validates moves and applies resolution using only that module's code.
3. **Given** the new module, **When** a developer adds it, **Then** the diff is confined to the module's files (plus its registration) — no edits to bots, auth, scheduling, the turn loop, or the agent API.

---

### User Story 3 - Multiple game types coexist (Priority: P2)

As the platform, games of different types run side by side — the lobby, scheduling, the turn loop, the agent loop, and the runner all work regardless of game type.

**Why this priority**: Multi-game is the product goal, but the platform functions with one type while this is added.

**Independent Test**: With PD and a stub game both registered, create one of each, and confirm both schedule, start, run turns, score, and finalize through the same platform machinery; a bot in both is handed the right turn for each.

**Acceptance Scenarios**:
1. **Given** a `game_type` on each game, **When** the lobby renders, **Then** each game shows its type and links to the correct viewer.
2. **Given** a bot entered in two games of different types, **When** it calls `get_next_turn`, **Then** each returned turn carries that game's type and correctly-shaped state.
3. **Given** the scheduler poller, **When** games of different types are due, **Then** each starts and runs via its own module's structure/resolution.

---

### User Story 4 - Module-driven move validation & scoring (Priority: P2)

As the platform, a submitted move is validated and scored by the game's module, and an illegal move is rejected with a clear, generic error.

**Why this priority**: Correctness boundary — the platform must not assume PD's move shape.

**Acceptance Scenarios**:
1. **Given** a move that the module's schema rejects, **When** a bot submits it, **Then** the platform returns a generic validation error (not a PD-specific one).
2. **Given** valid moves for a turn, **When** the turn resolves, **Then** the module computes new scores/state and per-move effects, which the platform persists generically.

---

### User Story 5 - Per-game spectator rendering (Priority: P3)

As a spectator, I can watch any game type with a sensible rendering of its moves, not a PD-only view.

**Acceptance Scenarios**:
1. **Given** a non-PD game, **When** I open its viewer, **Then** moves render via that module's display (or a generic fallback), without PD's HOARD/HELP/HURT labels leaking in.

---

## Edge Cases

- **Unknown `game_type`** (module not registered) → game creation/load fails with a clear error; the poller skips it rather than crashing.
- **Module returns an invalid resolution** (bad scores/shape) → platform rejects/logs it; the turn does not corrupt other games.
- **Bot in mixed-type games** → `get_next_turn` selects across types by deadline; each payload reflects its own game's state.
- **Existing PD games at migration time** → all get `game_type = "hoard-hurt-help"`; they keep playing/scoring identically.
- **A move valid in one game's vocabulary submitted to another type** → rejected by the target module's validation.
- **Spectator view for a type with no custom viewer** → generic fallback rendering.

---

## Requirements

### Functional Requirements

**The Game plugin contract**
- **FR-001**: The system MUST define a Game module contract a game implements: (a) move schema + validation, (b) turn/round structure (player range, simultaneous vs sequential, rounds×turns, deadlines, turn-ready predicate), (c) a pure resolve(state, moves) → new scores/state + per-move effects, (d) rules text + agent-payload rendering, (e) spectator viewer, (f) config defaults. (US2)
- **FR-002**: The system MUST provide a registry that maps a `game_type` string to its module, and reject creating/loading a game whose type is not registered. (US3)
- **FR-003**: Platform code (bots, auth, scheduling, turn loop, agent API, runner, lobby, panel, strategy profiles) MUST NOT reference any specific game's moves or scoring — only the contract. (US2)

**PD as the first module (no behavior change)**
- **FR-004**: The current PD rules/resolution/scoring MUST be moved behind the contract as module `hoard-hurt-help`, preserving identical outcomes (scores, round wins, score floor, mutual-help bonus) and identical agent request/response shapes. (US1)
- **FR-005**: Existing engine tests MUST pass against the PD module without changing their assertions. (US1)

**Data model**
- **FR-006**: `Game` MUST carry a `game_type`. A submitted move MUST be stored as a module-validated structure (generic, not PD-specific columns) plus a generic numeric score; per-player state MUST be a generic score plus optional game-specific state. (US3, US4)
- **FR-007**: A migration MUST set existing games to `game_type = "hoard-hurt-help"` and move PD move data into the generic shape. This is **data-affecting** (per the data-critical-waves rule) — it MUST be reviewed before prod apply and documented. (US1)

**Validation & resolution**
- **FR-008**: The platform MUST validate every submitted move via the game module and reject illegal moves with a generic error envelope. (US4)
- **FR-009**: The turn loop MUST resolve a turn by calling the module's resolve function and persisting its results generically. (US3, US4)

**Spectator**
- **FR-010**: The spectator viewer MUST render a turn via the game module's display, with a generic fallback for modules that provide none. (US5)

**Constitution-derived**
- **FR-011**: New game-module and contract logic MUST have tests (pure resolve functions are unit-testable); no error suppressions; full type annotations; async DB. Game modules live under a dedicated, domain-named location (not `utils`/`helpers`).

---

## Success Criteria

- **SC-001**: Prisoner's Dilemma produces **identical** turn resolutions, scores, round awards, and final standings as before the refactor (verified by the unchanged engine test suite).
- **SC-002**: Adding a new turn-based game requires changes confined to that game's module + one registration line — **zero** edits to platform files (bots, auth, scheduling, turn loop, agent API, runner).
- **SC-003**: The agent API, `get_next_turn`, and the runner behave identically across game types (the bot loop is unchanged).
- **SC-004**: A game of an unregistered type never crashes the poller or other games.
- **SC-005**: Existing PD games continue to play and score correctly after the migration.

---

## Key Entities

- **GameModule** (code, not a table): the plugin implementing the contract for one game; resolved from a registry by `game_type`.
- **Game** (modified): gains `game_type`. Lifecycle/scheduling fields unchanged.
- **Player** (modified): keeps a generic numeric score; PD-specific fields (round wins, etc.) move into an optional game-specific state representation.
- **Turn** (mostly unchanged): the platform-level turn.
- **TurnSubmission** (modified): PD columns (`action`, `target_player_id`, `points_delta`) generalize to a module-validated **move** + a generic score/effect.

---

## Assumptions

1. **Turn-based, scored moves only** (confirmed). No real-time or non-turn structures.
2. **Refactor PD only; defer the second game** (confirmed, Option B). Interface is validated against PD now; may be reshaped when game #2 lands.
3. **Keep the HHH brand**; PD is the module `hoard-hurt-help`. A platform rename is deferred and out of scope.
4. **Data-affecting migration** to add `game_type` and generalize move/state; flagged for review per data-critical-waves.
5. The existing engine files (`resolver.py`, `rules.py`, `game_records.py`, `opponent_stats.py`, `board_signals.py`, `turn_summary.py`, `game_insights.py`) become (or move into) the PD module.
6. Hidden/per-player state is allowed within turn-based play (the agent payload is already per-player), but no game requiring it is built now.

---

## Constitution Check

Validated against `CLAUDE.md`: **PASS**.
- Tests for new engine/game logic (pure resolve units), no suppressions, full type annotations, async DB — FR-011.
- File structure: game modules under a dedicated domain-named package; `app/` vs `mcp_server/` separation preserved.
- Data-critical migration flagged (FR-007) per the data-critical-waves rule.
- No conflict with auth/security (the bot model is untouched).
