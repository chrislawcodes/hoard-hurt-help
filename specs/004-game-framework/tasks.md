# Tasks: Turn-Based Game Framework

**Prerequisites**: plan.md, plan-summary.md, data-model.md, contracts/game-plugin.md, spec-acceptance.md

## Format: `[ID] [P: file]? Description`
- **[P: files]** = parallelizable (disjoint files). Bare/none = serial.
- This is a refactor, so phases follow the build order. The regression gate (engine tests pass unmodified) is the spine — re-run it after every phase.

---

## Phase 1: Foundation — the contract, registry, and PD adapter

**Purpose**: Stand up the plugin seam and make PD an adapter over the unchanged engine. No platform behavior change yet.

⚠️ **CRITICAL**: blocks everything else.

- [X] T001 [P: app/games/base.py] Define the `Game` Protocol, `GameConfig` dataclass, and `GameError` per contracts/game-plugin.md.
- [X] T002 [app/games/__init__.py] Registry: `register(module)`, `get(game_type) -> Game` (raises `GameError` on unknown), `known_types()`. Register the PD module on import.
- [X] T003 [P: app/games/hoard_hurt_help/__init__.py] Package init for the PD module.
- [X] T004 [app/games/hoard_hurt_help/game.py] PD adapter implementing the contract by **delegating to the unchanged** `app/engine/*`: `config_defaults()` (10×10, 60s, 3–100, simultaneous), `rules_text()`→`rules.RULES_TEXT_V1`, `validate_move()` (HOARD/HELP/HURT + target rules), `record_submission()`→write `TurnSubmission`, `resolve_turn`/`award_round`/`finalize`→`resolver.*`, `move_effect()`→current `_move_effect` logic. Do NOT modify `app/engine/*`.
- [X] T005 [P: tests/test_game_registry.py] Tests: `register`/`get` round-trip, `known_types` includes `hoard-hurt-help`, unknown type → `GameError`.

**Checkpoint**: `app/games/` imports; PD module registered; `ruff`+`mypy`+`pytest` green (engine suite untouched).

---

## Phase 2: `game_type` on Game + migration

**Purpose**: Record which module runs a game. (Storage generalization deferred — TurnSubmission/Player unchanged.)

- [X] T006 [app/models/game.py] Add `game_type: Mapped[str]` (String(64), NOT NULL, indexed).
- [X] T007 [migrations/versions/0004_game_type.py] Add `games.game_type`, backfill `"hoard-hurt-help"`, set NOT NULL + index (batch mode for SQLite). ⚠️ Data-affecting (benign — column add + backfill); add the data-critical header comment; down_revision `0003`.
- [X] T008 Game creation sets `game_type` — covered by the model column default `"hoard-hurt-help"`; explicit per-type selection in admin deferred to game #2.
- [X] T009 [P: tests/test_game_type.py] Test that a created game has `game_type` set and defaults correctly.

**Checkpoint**: schema has `game_type`; existing tests still green (metadata build includes the column).

---

## Phase 3: Wire the platform to the contract (serves US1, US3, US4, US5)

**Purpose**: Platform calls the module via the registry — never PD directly. PD behavior stays identical.

- [ ] T010 [app/engine/scheduler.py] Turn loop resolves the module via `games.get(game.game_type)` and calls `.resolve_turn / .award_round / .finalize` (replacing direct `resolver` imports). Skip a game whose type is unregistered (don't crash the poller — SC-004).
- [ ] T011 [app/routes/agent_api.py] `POST /submit` calls `module.validate_move` (→ generic `400 INVALID_MOVE` on failure) then `module.record_submission`, replacing the inline HOARD/HELP/HURT validation.
- [ ] T012 [app/routes/agent_api.py, app/routes/agent_next_turn.py] Build the agent payload's rules via `module.rules_text()` (replace the direct `RULES_TEXT_V1` import).
- [ ] T013 [app/routes/web.py, app/templates/fragments/*, app/templates/game.html] Viewer per-move display via `module.move_effect` with a generic fallback (no PD labels hard-coded — SC-005).
- [ ] T014 [app/schemas/agent.py] Generalize the submit schema to a module-validated move; the HOARD/HELP/HURT `Literal` becomes PD-module-owned (platform schema is generic).

**Checkpoint**: a full PD game plays/scores **identically**; engine + API + lobby suites pass. This is the US1 regression gate.

---

## Phase 4: Conformance stub + regression gate

**Purpose**: Prove a game can be added touching only its module (SC-002), and prove PD is unchanged.

- [ ] T015 [P: tests/test_stub_game.py] A trivial stub module (`game_type="stub"`, e.g. +1 per move) implementing the contract; test: create a stub game, a bot plays it via `get_next_turn`/`submit`, it resolves+scores — touching only the module + registration (SC-002).
- [ ] T016 Run the engine regression suite UNMODIFIED — `pytest tests/test_resolver.py tests/test_end_to_end.py tests/test_board_signals.py tests/test_opponent_stats.py tests/test_turn_summary.py`; all pass with **zero edits to those files** (SC-001).
- [ ] T017 [tests/] Update ONLY the existing api/lobby/admin tests that create games, to set/expect `game_type` — no assertion changes beyond that.

**Checkpoint**: full `pytest -q` green; SC-001 + SC-002 demonstrated.

---

## Phase 5: Polish & docs

- [ ] T018 [P: docs/writing-a-game-module.md] Short guide: implement the `Game` contract, register it, done — point at the PD module + the stub as examples.
- [ ] T019 [P: DESIGN.md] Record the platform-vs-game-module architecture + the deferred storage generalization (rides with game #2).
- [ ] T020 Full Preflight Gate from repo root: `ruff check . && mypy app/ mcp_server/ && pytest -q`; fix root causes (no suppressions). Walk quickstart.md US1–US5.
- [ ] T021 [P: MEMORY.md + memory file, STATUS.md] Record the framework landing + the deferred storage generalization.

---

## Dependencies & Execution Order

- **Phase 1** (contract/registry/adapter): no deps — BLOCKS everything.
- **Phase 2** (game_type/migration): after Phase 1 (creation uses the registry default).
- **Phase 3** (wiring): after Phases 1–2 — this is where regression risk lives; re-run the engine suite continuously.
- **Phase 4** (stub + gate): after Phase 3.
- **Phase 5** (polish): last.

### Within-phase parallel
- T001 ∥ (then T002 needs T001); T003 ∥ T005.
- T010 / T011+T012 (same file agent_api → serial) / T013 / T014 touch different files — T010, T013, T014 can parallelize; T011 & T012 share `agent_api.py` → serial.

### The spine
Re-run the **engine regression suite after each phase**. If PD behavior changes, the wiring (Phase 3) is wrong — fix before proceeding.
