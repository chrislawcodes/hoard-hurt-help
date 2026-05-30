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

- [X] T010 [app/engine/scheduler.py] Turn loop resolves the module via `games.get(game.game_type)` and calls `.resolve_turn / .award_round / .finalize` (replacing direct `resolver` imports). Skip a game whose type is unregistered (don't crash the poller — SC-004).
- [X] T011 [app/routes/agent_api.py] `POST /submit` packs the request into a generic `move` dict, calls `module.validate_move` then `module.record_submission`, replacing the inline HOARD/HELP/HURT validation. Note: the module's own error `code/message/details` are surfaced (e.g. `INVALID_TARGET`) rather than flattened to `INVALID_MOVE` — keeps PD's API responses identical (the regression gate; `test_agent_api.py:225,312` still pass).
- [X] T012 [app/routes/agent_api.py, app/routes/agent_next_turn.py] Build the agent payload's rules via `module.rules_text()` (dropped the `RULES_TEXT_V1`/`RULES_VERSION` imports; `rules_version` now reads `game.rules_version`, decoupling both routes from `app.engine.rules`).
- [X] T013 [app/routes/web.py] Viewer per-move display via `module.move_effect` with a generic fallback (`_move_effect_for(game_type, action)`; unknown type → no delta, doesn't crash the viewer). Templates unchanged — the context still exposes `actor_delta`/`target_delta`, so no PD labels live in the platform (SC-005).
- [X] T014 [app/schemas/agent.py] Validation is now module-owned (T011): the endpoint packs the request into a generic `move` dict the platform never interprets. The `Action` Literal is documented as PD's wire vocabulary; full free-form move JSON on the wire is deferred to game #2 (plan Decision: storage/wire generalization rides with the second game) — keeps the MCP `submit_action` tool, the runner, and the submit tests unchanged.

**Checkpoint**: a full PD game plays/scores **identically**; engine + API + lobby suites pass. This is the US1 regression gate.

---

## Phase 4: Conformance stub + regression gate

**Purpose**: Prove a game can be added touching only its module (SC-002), and prove PD is unchanged.

- [X] T015 [P: tests/test_stub_game.py] A `game_type="stub"` module (novel move `MOVE`, +1 per move) implementing the contract and registering itself on import; tests: it registers without disturbing PD, rejects an illegal move, and a 2-player stub game plays → resolves → scores → finalizes through the generic Turn/TurnSubmission/Player storage — touching only the module + its registration line (SC-002). 3 tests pass.
- [X] T016 Engine regression suite passes UNMODIFIED — the 5 files (`test_resolver`, `test_end_to_end`, `test_board_signals`, `test_opponent_stats`, `test_turn_summary`) = 42 pass, and `git diff origin/main...HEAD` shows **zero changes** to them or to the PD engine modules (`resolver/rules/game_records/opponent_stats/board_signals/turn_summary/game_insights`) or the PD storage (`turn.py`/`player.py`) (SC-001).
- [X] T017 No changes needed — the `Game.game_type` column default (`"hoard-hurt-help"`) fills it on every existing `Game(...)` construction, so the full api/lobby/admin suite passes untouched (146 total). No test churn was required.

**Checkpoint**: full `pytest -q` green; SC-001 + SC-002 demonstrated.

---

## Phase 5: Polish & docs

- [X] T018 [P: docs/writing-a-game-module.md] Guide: implement the `GameModule` contract, register on import, done — contract table, `GameError`, how the platform calls you, the shared-storage caveat, and a new-game checklist. Points at the PD module + the stub test as examples.
- [X] T019 [P: DESIGN.md] Added §11 "Game Framework": the platform-vs-game-module split, the registry seam, PD as a thin adapter over the unchanged engine, and the deferred storage/wire generalization (rides with game #2) with its rationale.
- [X] T020 Full Preflight Gate from repo root green: `ruff check .` PASS, `mypy app/ mcp_server/` PASS (53 files), `pytest -q` 146 passed. No suppressions. US1 (PD plays identically) covered by the regression suite; US2–US5 (game-agnostic submit/resolve/score/view) covered by the stub conformance test.
- [X] T021 [MEMORY.md + memory file] Wrote `game-framework-004.md` (platform+modules, PD=game #1, deferred storage/wire gen) and indexed it in MEMORY.md (also re-added the missing `feature-factory-no-manual-gates` index line). STATUS.md does not exist in this repo — skipped.

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
