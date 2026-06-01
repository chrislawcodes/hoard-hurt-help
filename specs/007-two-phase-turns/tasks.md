# Tasks: Two-Phase Turns with Private Bot Reasoning

**Prerequisites**: plan.md, spec.md, plan-summary.md, data-model.md, contracts/two-phase-api.yaml, spec-acceptance.md
**Branch**: `feat/two-phase-negotiation`

## Format: `[ID] [P: file]? [Story]? Description`
- **[P: file]** — parallelizable (disjoint file set listed). Bare `[P]` = serial.
- **[USn]** — user story label (user-story phases only).
- ⚠️ = highest-risk task, extra review required.

---

## Phase 1: Setup

- [ ] T001 Confirm on `feat/two-phase-negotiation` (off latest `origin/main`); run baseline `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` to capture a green starting point.

---

## Phase 2: Foundation (Blocking Prerequisites)

⚠️ **CRITICAL**: No user-story work begins until this phase is complete.

- [ ] T002 [P: app/models/turn.py] Add `Turn.phase` (talk|act, default talk) + `Turn.talk_resolved_at`; add `TurnSubmission.thinking`; add new `TurnMessage` model — per data-model.md.
- [ ] T003 [P: app/models/__init__.py] Register `TurnMessage` so `Base.metadata` and migrations/autogen see the table.
- [ ] T004 Create Alembic migration `migrations/versions/00NN_two_phase_turns.py` — add the two `turns` columns, the `turn_submissions.thinking` column, and the `turn_messages` table + indexes. Adds-only, **no `batch_alter_table`**. (depends T002, T003)
- [ ] T005 Ensure `pytest tests/test_migrations.py` passes (upgrade head on SQLite); add a fixture/assertion for the new table if the suite enumerates tables. (depends T004)

**Checkpoint**: schema + migration ready; user-story work can begin.

---

## Phase 3: User Story 1 — Negotiate, then act (Priority: P1) 🎯 MVP

**Goal**: Each turn runs a talk phase then an act phase; bots can coordinate within one turn; payoff math unchanged.
**Independent Test**: Play a local game; every turn shows a talk round then an act round; at least one same-turn mutual-help pair appears.

- [ ] T006 [US1] Rewrite `app/engine/rules.py` `RULES_TEXT` public-chat + submission-contract sections for talk→act; bump `RULES_VERSION` to `v2`.
- [ ] T007 [US1] `app/engine/resolver.py`: split defaulting — missing talk → empty `turn_messages` row (`was_defaulted`); missing act → HOARD (as today). Payoff math unchanged.
- [ ] T008 ⚠️ [US1] `app/engine/scheduler.py`: two-phase `_run_game` — open talk → wait `_all_messaged`/talk deadline → broadcast `turn_talked` + set `talk_resolved_at` + transition `phase=act` + regen `turn_token`/reset `deadline_at` → wait `_all_submitted`/act deadline → `module.resolve_turn` → broadcast `turn_resolved`. Add `_all_messaged`; `_open_turn` sets `phase=talk`.
- [ ] T009 ⚠️ [US1] `app/engine/scheduler.py`: tri-state resume (`resolved_at`→done; `talk_resolved_at`→resume act; else talk) with no double reveal/resolve. (depends T008)
- [ ] T010 [US1] `app/games/base.py`: add `record_message` to the `GameModule` contract; implement in `app/games/hoard_hurt_help/game.py`; keep `record_submission` action-only.
- [ ] T011 [US1] `app/schemas/agent.py`: `CurrentTurn.phase` + `talk_messages` (public only); `HistoryTurn.messages`; `SubmitRequest.thinking`; new `MessageRequest {turn_token, message, thinking}`. **No `thinking` on any response shape.**
- [ ] T012 [US1] `app/routes/agent_api.py`: phase-aware `/turn` (report phase, current token, act-phase `talk_messages`); NEW `POST /message` (talk-only, idempotent on (turn,player), phase+deadline+token checks); `/submit` becomes act-only (409 in talk), persists `thinking`. (depends T011)
- [ ] T013 [US1] `app/routes/agent_next_turn.py`: include `phase`, current-turn `talk_messages`, and `history.messages` in the payload (no thinking). (depends T011)
- [ ] T014 [P: scripts/agentludum_agent.py] [US1] Claude runner: branch on `current.phase`; talk→ask `{message,thinking}`→`POST /message`; act→ask `{action,target_id,thinking}` given revealed talk→`POST /submit`.
- [ ] T015 [P: scripts/agentludum_agent_codex.py] [US1] Codex runner: same phase branching + thinking.
- [ ] T016 [P: scripts/agentludum_agent_gemini.py] [US1] Gemini runner: same.
- [ ] T017 [P: scripts/agentludum_bot.py] [US1] Stateless runner: same phase branching.
- [ ] T018 [P: scripts/bot.py] [US1] Random test bot: talk→canned message, act→random action.
- [ ] T019 [US1] Tests `tests/`: per-phase resolve-early + deadline defaulting (empty talk msg / HOARD act); two-phase loop integration; **SC-004** same-turn mutual-help (+8 each).
- [ ] T020 ⚠️ [US1] Tests `tests/`: resume tri-state — kill loop in talk / in act / after act; assert correct continuation, no double reveal or double-count. (depends T009)

**Checkpoint**: a game plays talk→act end-to-end with correct scoring.

---

## Phase 4: User Story 2 — Private thinking, spectators only (Priority: P1)

**Goal**: Thinking captured both phases, visible to spectators, invisible to every agent endpoint.
**Independent Test**: Fetch all agent endpoints (zero thinking) and the spectator API (thinking present).

- [ ] T021 [US2] Confirm persistence: `/message` writes `turn_messages.thinking`, `/submit` writes `turn_submissions.thinking`. (app/routes/agent_api.py)
- [ ] T022 ⚠️ [US2] Audit every agent surface for leaks: `app/schemas/agent.py`, `app/routes/agent_api.py`, `app/routes/agent_next_turn.py` — confirm history/chat/opponent-history responses carry no `thinking`.
- [ ] T023 [US2] `app/schemas/spectator.py`: add `SpectatorMessage`/`SpectatorAction`/`SpectatorTurn` (with `thinking`); `SpectatorState.history -> list[SpectatorTurn]`; stop importing the agent `HistoryTurn`.
- [ ] T024 [US2] `app/routes/spectator_api.py`: build rich history from `turn_messages` + `turn_submissions` incl. thinking; legacy fallback to `turn_submissions.message` when a turn has no `turn_messages`. (depends T023)
- [ ] T025 ⚠️ [US2] Tests `tests/`: **SC-002** leak sweep — game with known thinking strings; fetch `/turn`, `/next-turn`, history, chat, opponent-history; assert zero thinking text and no thinking field on any agent schema.
- [ ] T026 [US2] Tests `tests/`: **SC-003** spectator API exposes thinking for both phases.

**Checkpoint**: thinking visible to spectators, provably invisible to agents.

---

## Phase 5: User Story 3 — Viewer presents talk, act, reasoning (Priority: P2)

**Goal**: Viewer/analysis show talk round → act round, reasoning collapsed-by-default per bot.
**Independent Test**: Open a finished game; see both rounds; expand a bot's reasoning; a legacy game still renders.

- [ ] T027 [US3] `app/routes/web.py`: feed two-phase history (messages round + actions round + thinking) to the live/watch/analysis views.
- [ ] T028 [P: app/templates/, app/static/style.css] [US3] Templates: render talk round then act round; per-bot reasoning collapsed-by-default toggle; styling.
- [ ] T029 [US3] Legacy render: fall back to `turn_submissions.message` when no `turn_messages`; no reasoning toggles for legacy turns. (app/routes/web.py + templates)
- [ ] T030 [US3] Tests `tests/`: viewer renders talk→act + reasoning toggles for a new game; **legacy single-phase game still renders**.

**Checkpoint**: spectators can watch the negotiation and read reasoning.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T031 [P: app/engine/turn_summary.py, app/engine/opponent_stats.py, app/engine/game_insights.py, app/engine/board_signals.py] Audit readers of `turn_submissions.message`; point at `turn_messages` (legacy fallback).
- [ ] T032 Sync the served runner files (the `/agentludum_bot.py` and `/runners/{name}` web routes in `app/routes/web.py`) so operators download the two-phase runners.
- [ ] T033 Full preflight: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` — fix root causes, no suppressions.
- [ ] T034 Run quickstart.md US-1/US-2/US-3 validations on the local harness; confirm SC-001…SC-005.

---

## Dependencies & Execution Order

- **Phase 1 Setup** → **Phase 2 Foundation** (BLOCKS everything).
- **Phase 3 (US1)** depends on Foundation. T008→T009→T020; T011→T012/T013; runners T014–T018 parallel after T011/T012.
- **Phase 4 (US2)** depends on Foundation; T022 can run alongside US1; T023→T024; T025 after T012/T013/T022.
- **Phase 5 (US3)** depends on T024 (spectator history shape).
- **Phase 6 Polish** after US1–US3.

### Parallel Opportunities
- Foundation: T002 ∥ T003.
- US1 runners: T014 ∥ T015 ∥ T016 ∥ T017 ∥ T018 (distinct files).
- Polish: T031 analysis modules in parallel.

### High-risk (extra review): T008, T009, T020 (turn-loop + resume), T035, T038 (thinking segregation).

---

## Revisions from adversarial review (2026-06-01)

Codex (senior-TL) returned DO-NOT-PROCEED on two blockers; owner decisions reshaped the segregation + resume tasks. Apply these in place of / in addition to the originals.

**Changed**
- T009/T020 (resume): scope to WITHIN a two-phase game only. **Drop** any v1→v2 cross-version resume work — deploys happen with no ACTIVE games (spec Assumptions). Remove the "legacy in-flight turn" branch.
- T023 (spectator schema): REVERSED — the spectator JSON schema gets the two-phase shape (messages + actions) **with NO thinking**. Do not add a thinking-carrying spectator type. (Decision 1 revised.)
- T024 (spectator_api): build two-phase history WITHOUT thinking; keep the legacy `turn_submissions.message` fallback.
- T027–T029 (viewer): thinking is read from the DB in `web.py` and rendered into the HTML templates only (collapsed-by-default per bot). It must NOT pass through the spectator JSON.

**Added**
- [ ] T035 ⚠️ [US2] `tests/`: **SC-002** multi-channel leak sweep — assert NO thinking in (a) every agent HTTP endpoint, (b) every MCP tool result (`get_game_state`, `get_chat`, `get_turn_detail`, `get_opponent_history`), (c) the spectator JSON API — and assert the rendered viewer HTML DOES contain it.
- [ ] T036 [US1] `app/engine/scheduler.py` + `app/routes/sse.py` + live template: emit a `turn_talked` SSE event at talk-resolution; live viewer subscribes and reveals talk then (FR-004). Test: talk shows at talk-resolution, not act-resolution.
- [ ] T037 [US1] Leave-between-phases rule (FR-016): a player who leaves mid-turn is excluded from quorum + defaulted for the rest of the turn and removed from later turns; one consistent rule across scheduler + resolver. Test it.
- [ ] T038 ⚠️ [US2] Log redaction (FR-017/SC-006): treat `/message` and `/submit` bodies as sensitive — no access/debug log or error envelope echoes `thinking`. Test that thinking never appears in logs/error bodies.
- [ ] T039 [US1] Strengthen acceptance tests (SC-004): assert no score change during talk; act payload contains ALL talk messages; a talk submission AFTER early resolve is rejected; an act decision depends on same-turn talk.
