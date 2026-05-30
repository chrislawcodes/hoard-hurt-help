# Tasks: Scalable bot-facing game state (free summary + pull detail)

**Prerequisites**: plan.md, spec.md, data-model.md, contracts/bot-state-summary-api.yaml

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: file]** — parallelizable; file list shows its scope. Tasks with disjoint file sets can run together.
- **[USn]** — user story label (user-story phases only).
- Paths are repo-relative, from plan.md.

**Locked decisions** (carry through every task):
- v1 reads **NO message text** for any computed signal; the compliance / "who-follows-public-deals" signal is **deferred to v2** (leave a hook).
- The push payload **replaces** `history` with `summary`; full history moves behind a pull endpoint. Tests + prompts are updated in this feature.
- Server emits **facts only** — no "trust score" or other judgments.

---

## Phase 1: Setup

**Purpose**: baseline before changes.

- [X] T001 Establish preflight baseline. Result: ruff clean; pytest 81 passed; **mypy has 8 pre-existing errors** from venv mypy version-drift on unrelated files (resolver.py, scheduler.py, auth/google.py) + 1 "unused type: ignore" in agent_api.py:208 (will be cleaned up as part of US1's rewrite of that file). New code must add ZERO new mypy errors; the 7 unrelated ones are flagged to Chris, not fixed here (out of scope / one-feature-per-branch).

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: the Pydantic shapes every later phase depends on. All in `app/schemas/agent.py` → serial.

⚠️ No user-story work begins until this phase is complete.

- [X] T002 [US-shared] Add summary sub-shapes to `app/schemas/agent.py`: `YourSituation`, `StandingRow`, `StandingsView`, `DeltaAction`, `TurnDelta`, `StyleMix`, `OpponentStat`, `OpponentsAggregate`, `Alliance`, `BoardSignals`, `SummaryFlags`, `DirectedMessage`, and the top-level `TurnSummary` (per data-model.md).
- [X] T003 Add pull response shapes to `app/schemas/agent.py`: extend/relabel `HistoryAction` to carry `round`/`turn`; add `OpponentHistoryResponse`, `ChatLine` + `ChatTranscriptResponse`, `TurnDetailResponse`, `FullStandingsResponse`.
- [X] T004 Change `YourTurnResponse` in `app/schemas/agent.py` to `{ status, static, summary }`; remove the `TurnDynamic` (history) model and any now-dead references. Keep `TurnStatic` unchanged.

**Checkpoint**: shapes compile (`mypy app/`); user stories can begin.

---

## Phase 3: User Story 1 — Bounded free summary replaces full history (Priority: P1) 🎯 MVP

**Goal**: `get_turn` returns a small bounded `summary` (situation, compressed standings, delta, opponent short-list, messages-for-you) and no `history`; payload stays flat as bots scale.

**Independent Test**: a bot calls `get_turn` past turn 1 and receives a capped `summary`; turn-1 has an empty delta and no errors; a large game keeps the opponent list capped.

### Implementation for User Story 1

- [X] T005 [P: app/engine/opponent_stats.py] [US1] Create `opponent_stats.py`: per-opponent tallies (`helped_you`, `hurt_you`), reciprocity (`returned_help`/`returned_hurt` = next-turn mirror, per research Q1), `StyleMix`, and short-list selection (interacted + top `TOP_THREATS` + `NEIGHBOR_RADIUS` neighbors + flagged, capped at `MAX_SHORTLIST`, deterministic `agent_id` tiebreak) + the `OpponentsAggregate` line. Constants module-level. Pure functions over passed-in rows (no DB).
- [X] T006 [P: tests/test_opponent_stats.py] [US1] Unit-test `opponent_stats`: tallies, reciprocity both directions, short-list cap + selection reasons, aggregate counts, standings ties, turn-1 empty, players with `left_at`, defaulted-HOARD turns.
- [X] T007 [P: app/engine/board_signals.py] [US1] Create `board_signals.py` with `cooperation_temperature` (help/(help+hurt) over current round) + `temperature_label`. Alliances/surging/pattern-flags return empty here (filled in US4). Constants module-level.
- [X] T008 [P: tests/test_board_signals.py] [US1] Unit-test cooperation temperature: cooperative / hostile / mixed / empty-round.
- [X] T009 [app/engine/turn_summary.py] [US1] Create `turn_summary.py` assembling `TurnSummary`: `YourSituation`, compressed `StandingsView` (leaders incl. ties + neighbors + total), `TurnDelta` (involving_you + `others_summary` string), `opponents` + `opponents_aggregate` (from `opponent_stats`), `board_signals` (temperature), `SummaryFlags` (`messages_for_you_count`), and `messages_for_you` (directed via `target_player_id` + last `MAX_BROADCASTS` public; exclude the defaulted "I did not submit a turn." message). Depends on T005, T007.
- [X] T010 [P: tests/test_turn_summary.py] [US1] Unit-test assembly + edge cases: turn 1 empty delta, tiny 3-bot game (no aggregate), large game (aggregate non-zero), standings ties, left players excluded, defaulted turns. Depends on T009.
- [X] T011 [app/routes/agent_api.py] [US1] Wire the summary into `GET /turn`: load resolved submissions/players, aggregate with SQL `GROUP BY`, build full detail only for the short-list, return `{ static, summary }`. Remove `_build_history` from the turn path (retain the function for reuse by the pull endpoint in US3). Depends on Phase 2 + T009.
- [X] T012 [tests/test_agent_api.py] [US1] Update existing payload tests: replace the `dynamic.turn_token` assert (~line 205) with `summary.your_situation.turn_token`; drop any `history` reliance; assert the new `summary` fields. Depends on T011.
- [X] T013 [tests/test_mcp.py] [US1] Update the MCP `get_turn` test for the new `summary` shape. Depends on T011.

**Checkpoint**: US1 is independently testable — a bot plays from the summary alone; preflight green.

---

## Phase 4: User Story 2 — Setup prompts + rules text (Priority: P1)

**Goal**: every client setup message and the rules/strategy text make bots use the summary, pull sparingly, and read+respond to messages aimed at them (fixes the original "bots don't talk" problem).

**Independent Test**: each of the 5 join-page blocks and `docs/setup-*.md` references the summary + pull tools + read/respond-to-messages; a bot run with the new prompt addresses other bots.

- [X] T014 [P: app/engine/rules.py] [US2] Update `RULES_TEXT_V1` "Public chat" section, `DEFAULT_STRATEGY_PROMPT`, and `STRATEGY_PRESETS`: frame the message field as a persuasion channel (not a caption), tell bots to track opponents via the provided stats and to read/answer directed messages.
- [X] T015 [app/templates/join.html] [US2] Update all 5 setup blocks (Claude, Hermes, OpenClaw, Codex, Other/HTTP): explain the `summary` shape, name the 4 pull tools, instruct read+respond to messages aimed at you, and pull detail only when the strategy needs it.
- [X] T016 [P: docs/setup-claude.md, docs/setup-hermes.md, docs/setup-codex.md, docs/setup-openclaw.md, docs/setup-other.md] [US2] Update the 5 setup docs to match join.html.
- [X] T017 [tests/test_lobby.py] [US2] Add a light render check: `GET /games/{id}/join` contains the new stable keywords (e.g. "summary", "messages", a pull-tool name) for at least the Claude block. (Stable substrings only — not brittle full-text.)

**Checkpoint**: US2 done — prompts/docs consistent; SC-005 satisfied.

---

## Phase 5: User Story 3 — Pull-on-demand detail (Priority: P2)

**Goal**: opt-in tools/endpoints for full history vs an opponent, full chat, a specific turn, and full standings; rate-limited.

**Independent Test**: each pull returns correct complete data; bad input → error envelope; >1/s → RATE_LIMITED.

- [X] T018 [app/routes/agent_api.py] [US3] Add a reusable per-key pull rate-limit dependency (1 Hz, separate bucket from `/turn`) returning the `RATE_LIMITED` envelope. Depends on Phase 2.
- [X] T019 [app/routes/agent_api.py] [US3] Add the 4 pull endpoints — `GET /history/opponents/{opponent_id}`, `GET /chat?since=R.T`, `GET /turns/{round}/{turn}`, `GET /standings` — reusing `_build_history`-style logic; `INVALID_TARGET`/`NOT_FOUND` envelopes for bad opponent/turn. Depends on T018.
- [X] T020 [P: mcp_server/server.py] [US3] Add 4 MCP pull tools (`get_opponent_history`, `get_chat`, `get_turn_detail`, `get_standings`) calling the new endpoints; update `get_turn` + `submit_action` docstrings to describe the `summary` and the persuasion/messaging expectation. Depends on T019 (contract).
- [X] T021 [tests/test_agent_api.py] [US3] Tests: each pull returns correct data; `since` cursor filters; unknown opponent/turn → envelope; over-rate → 429. Depends on T019.
- [X] T022 [tests/test_mcp.py] [US3] Tests for the 4 MCP pull tools. Depends on T020.

**Checkpoint**: US3 done — detail reachable on demand; SC-004 satisfied.

---

## Phase 6: User Story 4 — Whole-board signals (Priority: P2)

**Goal**: alliances, surging, and "there's more" flags the server uniquely computes.

**Independent Test**: a mutual-help cluster shows as an alliance; a deviator sets a pattern-break flag; a fast climber appears in surging.

- [X] T023 [app/engine/board_signals.py] [US4] Extend `board_signals.py`: alliances (mutual-help connected components, `ALLY_MIN_HELPS`/`MAX_ALLIANCES`), surging (rank-jump `SURGE_RANK_JUMP`/`SURGE_WINDOW`/`MAX_SURGING`, round-gain fallback), pattern-break detection, and `new_alliance`. Action-only. Depends on T007.
- [X] T024 [tests/test_board_signals.py] [US4] Tests: alliance clusters, surging, pattern-break, new_alliance, caps honored. Depends on T023.
- [X] T025 [app/engine/turn_summary.py] [US4] Wire full `board_signals` (alliances/surging) and richer `SummaryFlags` (`pattern_breaks`, `new_alliance`) into the assembled summary. Depends on T023.
- [X] T026 [tests/test_turn_summary.py] [US4] Tests for signals/flags surfaced in the assembled summary. Depends on T025.

**Checkpoint**: US4 done — board signals present.

---

## Phase 7: User Story 5 — Tunable near/far detail (Priority: P3)

**Goal**: short-list cap + aggregate stay correct and tunable from 10→100 bots.

**Independent Test**: lowering the cap is honored; aggregate covers all non-short-list opponents (nothing dropped).

- [X] T027 [app/engine/opponent_stats.py] [US5] Confirm cap/threat/neighbor constants are module-level and easily tunable; ensure the aggregate line covers every opponent not in the short-list at large N (no silent drop). Add a `log`/comment note if any cap truncates coverage.
- [X] T028 [tests/test_opponent_stats.py] [US5] Tests: configured cap honored; aggregate `count` + action totals account for all remaining opponents at large N.

**Checkpoint**: US5 done — large-N behavior verified.

---

## Phase 8: Polish & Cross-Cutting

- [ ] T029 Run the preflight gate: `ruff check . && mypy app/ mcp_server/ && pytest -q`. Fix all issues at the root cause — no `# type: ignore`/`# noqa`/swallowed exceptions (SC-006).
- [ ] T030 Local end-to-end check per quickstart: run a scripted game and verify SC-001 (payload bounded, doesn't grow with turns), SC-002 (legal move from summary alone), SC-003 (directed messages appear next turn).
- [ ] T031 [P: STATUS.md] Update `STATUS.md` if present: mark feature done; note v2 hooks (message-NLP/compliance tier, resolve-time denormalized counters, trimming `static.rules`).
- [ ] T032 **DATA-CRITICAL (post-deploy, after merge)**: run quickstart §Post-Deploy — point a real/test bot at prod, confirm it polls, receives `summary` (not `history`), submits a move; watch agent-endpoint logs ~10 min for error spikes. "Deployed" ≠ "working."

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (P1)** → no deps.
- **Foundation (P2)** → after Setup; **BLOCKS all user stories** (shapes).
- **US1 (P3 phase)** → after Foundation. MVP.
- **US2** → after Foundation; independent of US1 code (text only) but ship alongside US1 for a coherent payload+prompt change.
- **US3** → after Foundation; route tasks (T018/T019) are serial with US1's T011 (same file `agent_api.py`).
- **US4** → after US1 (extends `board_signals.py` + `turn_summary.py`).
- **US5** → after US1 (extends `opponent_stats.py`).
- **Polish** → after the user stories you intend to ship.

### Same-file serial chains (do NOT parallelize)
- `app/schemas/agent.py`: T002 → T003 → T004
- `app/routes/agent_api.py`: T011 → T018 → T019
- `app/engine/board_signals.py`: T007 → T023
- `app/engine/opponent_stats.py`: T005 → T027
- `app/engine/turn_summary.py`: T009 → T025

### Parallel opportunities
- T005/T006, T007/T008 (distinct files) can run together early in US1.
- T020 (`mcp_server/server.py`) parallels route tests.
- T016 (5 doc files) parallels T014/T015.

### MVP scope
- Ship **Foundation + US1 + US2** for the minimum coherent release (bounded payload + prompts that use it). US3/US4 add pull + signals; US5 is tuning.
