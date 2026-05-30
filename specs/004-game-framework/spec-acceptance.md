# Acceptance Criteria: Turn-Based Game Framework

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | PD runs unchanged through the new interface | P1 |
| US-2 | Add a turn-based game by writing only a module | P1 |
| US-3 | Multiple game types coexist | P2 |
| US-4 | Module-driven move validation & scoring | P2 |
| US-5 | Per-game spectator rendering | P3 |

## Acceptance Scenarios

### US-1
- Given a PD game with submitted moves, When a turn resolves, Then scores, round wins, score floor, and mutual-help bonus match pre-refactor behavior exactly.
- Given a connected bot, When it calls get_next_turn / submit_action for a PD game, Then request/response shapes are unchanged.
- Given the existing engine test suite, When it runs against the PD module, Then all tests pass without modifying their assertions.

### US-2
- Given a new module implementing the contract, When registered, Then games of its type can be created and appear in the lobby alongside PD.
- Given a registered type, When a bot plays via get_next_turn/submit, Then the platform validates moves and resolves using only that module.
- Given the new module, When added, Then the diff is confined to the module's files + its registration.

### US-3
- Given game_type on each game, When the lobby renders, Then each shows its type and links to the right viewer.
- Given a bot in two games of different types, When it calls get_next_turn, Then each turn carries its game's type and correctly-shaped state.

### US-4
- Given a move the module rejects, When a bot submits it, Then a generic validation error is returned (not a PD-specific one).
- Given valid moves, When the turn resolves, Then the module computes scores/state which the platform persists generically.

### US-5
- Given a non-PD game, When viewed, Then moves render via that module's display (or a generic fallback) with no PD labels leaking.

## Success Criteria
- SC-001: PD produces identical resolutions/scores/round awards/final standings (verified by the unchanged engine suite).
- SC-002: Adding a turn-based game = changes confined to its module + one registration line; zero platform edits.
- SC-003: Agent API, get_next_turn, and the runner behave identically across game types.
- SC-004: An unregistered game_type never crashes the poller or other games.
- SC-005: Existing PD games keep playing/scoring after the migration.

## Key Constraints
- **PD behaves identically; engine tests pass UNMODIFIED** — *Why: a framework refactor must not regress the live game; this is the regression gate.*
- **PD is an adapter over the unchanged engine** — *Why: keeping app/engine/* in place preserves the tests' imports and PD's exact math.*
- **Storage generalization deferred (keep PD's typed columns); only add Game.game_type** — *Why: Option B defers game #2, so generic move/state storage has no consumer yet and would break the tests-unmodified gate.*
- **Platform depends only on the contract** — *Why: that's the whole point — a new game plugs in without platform edits.*
- **0004 migration is data-affecting (benign: column add + backfill)** — *Why: review before prod per data-critical-waves.*
- **No suppressions / full types / async DB** — *Why: project constitution; preflight enforces.*
