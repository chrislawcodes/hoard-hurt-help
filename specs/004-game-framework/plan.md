# Implementation Plan: Turn-Based Game Framework

**Branch**: `004-game-framework` | **Date**: 2026-05-30 | **Spec**: [spec.md](spec.md)

## Summary

Introduce a **Game plugin contract** + a registry keyed by `Game.game_type`, and refactor Prisoner's Dilemma to run *behind* the contract as the first module (`hoard-hurt-help`). The PD module is a thin **adapter over the existing engine functions** — those functions don't move and aren't rewritten — so PD behaves identically and the existing engine tests pass unmodified. Platform code (scheduler turn loop, agent API, viewer) is rewired to call the module via the registry instead of importing PD directly.

## Technical Context

**Language/Version**: Python 3.14 (async).
**Primary Dependencies**: FastAPI, SQLAlchemy async, Alembic, Jinja2+HTMX, FastMCP. No new deps.
**Storage**: Postgres (prod) / SQLite-in-memory (tests). One small Alembic migration (`0004`).
**Testing**: pytest (async); test DB built from model metadata.
**Constraints**: PD must behave identically; **existing engine tests pass unmodified** (regression gate); no suppressions, full type annotations, async DB.
**Scale/Scope**: Large refactor of indirection; PD logic unchanged.

## Constitution Check

**Status**: PASS.
- [ ] No suppressions; full type annotations; async DB; no bare except.
- [ ] New contract/registry/adapter + the conformance stub have tests; pure resolve stays unit-testable.
- [ ] File structure: a new `app/games/` package (domain-named); `app/` vs `mcp_server/` separation intact.
- [ ] Data-critical migration (`0004`) flagged for review (Architecture Decision 4).

## Architecture Decisions

### Decision 1: A `Game` Protocol resolved from a registry by `game_type`

**Chosen**: Define a `Game` contract (`app/games/base.py`) and a registry (`app/games/__init__.py`, `register()` / `get(game_type)`). Platform code depends only on the contract. The contract surface (what the platform calls):
- `game_type: str`, `config_defaults() -> GameConfig` (rounds, turns_per_round, per-turn deadline, min/max players, simultaneous-vs-sequential)
- `rules_text() -> str`
- `validate_move(move, *, player, players) -> None` (raises a generic error on illegal moves)
- `record_submission(db, turn, player, move) -> TurnSubmission` (module maps a move to its storage)
- `resolve_turn(db, turn) -> None`, `award_round(db, game, round_num) -> None`, `finalize(db, game) -> None`
- `move_effect(submission) -> tuple[int, int | None]` (viewer per-move display) and a per-move agent-payload hook

**Rationale**: A Protocol/registry is the minimal, Pythonic plugin seam; the platform never imports a specific game.

### Decision 2: PD is an ADAPTER over the existing engine — don't move or rewrite it

**Chosen**: `app/games/hoard_hurt_help/game.py` implements the contract by **calling the existing functions** in `app/engine/{resolver,rules,game_records,opponent_stats,board_signals,turn_summary,game_insights}.py`. Those files stay where they are; their logic is untouched.

**Rationale**: This is the regression gate — the existing engine tests import `from app.engine.resolver import ...` and construct PD `TurnSubmission`s directly. Keeping the engine in place and unchanged means **tests pass unmodified** and PD outcomes are byte-identical. Moving the files later (into the module) is an optional cleanup, explicitly out of scope here.

**Alternatives**: Move the engine into the module now — rejected: it rewrites test imports (violates the gate) for no functional gain.

### Decision 3: Generalize the CONTRACT now; keep PD's typed storage; DEFER the move-JSON storage generalization

**Chosen**: Add `Game.game_type` now. **Keep** `TurnSubmission` (`action`, `target_player_id`, `points_delta`, …) and `Player`'s score columns exactly as they are — they become *PD's* storage, written/read only by the PD module. The platform treats moves abstractly via the contract. The spec's FR-006/FR-007 (a generic move-JSON column + score/state) are **deferred to when game #2 is built**.

**Rationale (important — this refines the spec):** We're shipping Option B (no second game yet). Generalizing the storage now would (a) have **no second consumer**, and (b) **break the "engine tests unmodified" gate** (those tests build typed `TurnSubmission`s). The *value* of the framework is the contract, which we deliver fully. A future game adds its own storage path (a generic `move`/`state` JSON column) at the moment it's built and can validate the shape against two real games. So the contract is storage-agnostic; PD's adapter uses the typed columns; game #2 introduces generic columns later.

**Tradeoff**: Pro — minimal risk, tests unmodified, framework still real. Con — `TurnSubmission` keeps PD-named columns until game #2; documented as "PD's storage," not platform fields.

### Decision 4: `0004` migration — `game_type` only (small, data-affecting)

**Chosen**: Migration `0004` adds `games.game_type` (NOT NULL) and backfills existing rows to `"hoard-hurt-help"`. No move-data rewrite (per Decision 3).

**Rationale**: A column-add + backfill is data-affecting but benign (no row deletion, no move rewrite) — far smaller than `0003`. Flag per data-critical-waves; review before prod. Test DB builds from metadata, so unaffected.

### Decision 5: Prove the contract with a conformance stub (de-risks n=1)

**Chosen**: Add a trivial **stub game** as a test double (in `tests/`) that implements the contract (e.g. "+1 per move"), register it, and assert a game of that type can be created, played by a bot, resolved, and scored — touching only the stub. This is the executable check for SC-002 and partial insurance against the n=1 interface risk. It is a test fixture, not a shipped product game.

## Project Structure

```
app/
├── games/
│   ├── __init__.py              - NEW: registry (register / get(game_type)) + register PD on import
│   ├── base.py                  - NEW: Game Protocol + GameConfig dataclass + GameError
│   └── hoard_hurt_help/
│       ├── __init__.py          - NEW
│       └── game.py              - NEW: PD adapter — implements the contract by calling app/engine/*
├── engine/                      - UNCHANGED (resolver, rules, game_records, opponent_stats,
│                                  board_signals, turn_summary, game_insights) — PD's logic, tests intact
├── engine/scheduler.py          - MODIFY: turn loop calls registry.get(game.game_type).resolve_turn /
│                                  award_round / finalize instead of importing resolver directly
├── models/game.py               - MODIFY: add game_type column
├── routes/agent_api.py          - MODIFY: submit uses module.validate_move + record_submission;
│                                  payload uses module.rules_text() (was RULES_TEXT_V1 import)
├── routes/agent_next_turn.py    - MODIFY: same payload-via-module change
├── routes/web.py + templates    - MODIFY: viewer move-effect via module.move_effect (generic fallback)
├── routes/admin_*.py            - MODIFY: game creation sets game_type (default hoard-hurt-help)
└── schemas/agent.py             - MODIFY: the HOARD/HELP/HURT Literal becomes PD-module-owned; the
                                   platform submit schema takes a generic move validated by the module

migrations/versions/0004_game_type.py  - NEW: add games.game_type, backfill "hoard-hurt-help"

tests/
├── (unchanged) test_resolver, test_end_to_end, test_board_signals, test_opponent_stats,
│    test_turn_summary  - REGRESSION GATE: pass without edits
├── test_game_registry.py        - NEW: register/get, unknown type rejected
├── test_stub_game.py            - NEW: conformance stub — create/play/resolve touches only the module
└── (existing api/lobby/viewer tests) - updated only where they assert game_type / creation
```

**Structure Decision**: All game logic moves behind `app/games/`. PD's engine files stay in `app/engine/` (unchanged) and are *called by* the PD adapter — the platform calls the adapter via the registry.

## Phasing (build order)

1. **Contract + registry + PD adapter** (`app/games/`): define `Game`/`GameConfig`/registry; PD adapter delegating to existing engine. Register PD. No platform behavior change yet.
2. **`game_type`**: model column + migration `0004` + backfill; game creation sets it (default `hoard-hurt-help`).
3. **Wire the platform to the registry**: scheduler turn loop, agent API submit (validate + record), agent payload (rules text), viewer (move effect) — all via `registry.get(game.game_type)`. PD behavior identical.
4. **Tests**: registry + conformance stub (SC-002); confirm the engine regression suite passes unmodified; fix only the existing tests that must set `game_type` on game creation.
5. **Docs/polish**: a short "writing a game module" doc; update `DESIGN.md`.

## Architecture Compliance

- **Regression gate** (spec US1/SC-001): engine tests unmodified — Decisions 2 & 3.
- **Framework capability** (US2/SC-002): contract + registry + conformance stub.
- **Data-critical** (global rule): Decision 4 flags the `0004` migration.
- **Testing/types/async/no-suppressions** (`CLAUDE.md`): verified by preflight (`ruff`, `mypy app/ mcp_server/`, `pytest`).
