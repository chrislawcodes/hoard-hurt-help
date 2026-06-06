# Tasks: Connection / Agent Split (015)

**Prerequisites**: spec.md, plan.md, plan-summary.md, data-model.md, contracts/endpoints.md, spec-acceptance.md

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: paths]** — parallelizable; file scope listed so overlapping tasks run serially.
- **[USn]** — user story (see spec-acceptance.md).
- Phases map 1:1 to the plan's vertical slices and are ordered so the preflight is green at each phase boundary.

> ⛔ **IMPLEMENTATION ON HOLD.** Do not start coding these until the ~6 concurrent bot/sim/leaderboard branches have merged and `main` is quiet. When resuming, re-verify the spec against whatever those branches changed (especially `engine/sims/*`, `leaderboard.py`, `bots_*` routes).

---

## Phase 1: Setup

**Purpose**: Branch + ground truth. (Branch `015-connection-agent-split` already exists in an isolated worktree.)

- [ ] T001 Re-sync the worktree onto the freshest `origin/main` (after concurrent branches land); skim the diff for `app/models/bot.py`, `app/engine/sims/*`, `app/read_models/leaderboard.py`, `app/routes/bots_*` and reconcile any drift with the plan.

---

## Phase 2: Foundation — Slice 0: Models + Schema (Priority: P1) ⛔ BLOCKS ALL

**Purpose**: New schema in place; nothing reads it yet. End state: models import, `create_all` builds the schema, `alembic upgrade head` passes on SQLite (`tests/test_migrations.py`).

⚠️ **CRITICAL**: No other phase can begin until this is complete.

- [ ] T002 [P: app/models/connection.py] Create `Connection` model + `ConnectionProvider` / `ConnectionStatus` enums per data-model.md (provider, key_lookup/prev_key_lookup/key_hint, status/paused, first_connected_at/last_seen_at/runner_pid, max_concurrent_games/stall_threshold; no model, no name).
- [ ] T003 [P: app/models/agent.py] Create `Agent` model + `AgentKind`(ai/bot) / `AgentStatus` enums (user_id, nullable connection_id, kind, name, game, model, status, archived_at, `bot_*` config fields formerly `sim_*`); add `UNIQUE(user_id,name)` and `UNIQUE(user_id,bot_profile_id)`.
- [ ] T004 [app/models/player.py] Repoint `Player`: `bot_id`→`agent_id` FK→agents.id; rename string `agent_id`→`seat_name`; add `strategy_snapshot` (Text, nullable); update constraints to `UNIQUE(agent_id,match_id)` + `UNIQUE(match_id,seat_name)`. (Depends T003.)
- [ ] T005 [app/models/strategy_prompt.py] Repoint `StrategyPrompt.player_id`→`agent_id` FK→agents.id. (Depends T003.)
- [ ] T006 [app/models/bot.py, app/models/__init__.py] Delete `bot.py`; update `__init__` to export `Connection`/`Agent` and drop `Bot`. (Depends T002–T005.)
- [ ] T007 [migrations/versions/0023_connection_agent_split.py] Destructive reshape migration: drop `strategy_prompts`,`players`,`bots`; create `connections`,`agents`,`players`(new),`strategy_prompts`(agent_id); recreate indexes/constraints. Use `op.batch_alter_table` for any in-place op. (Depends T002–T006.)
- [ ] T008 [tests/test_migrations.py, tests/conftest.py] Update test bootstrap so `create_all` + `alembic upgrade head` pass on the reshaped schema; add an Agent/Connection fixture factory to replace bot fixtures. (Depends T007.)

**Checkpoint**: models import, schema builds, migration tests green. (Other suites still red — expected; later phases fix them.)

---

## Phase 3: Slice 1 — Auth + Turn Resolution (Priority: P1) 🎯 HIGH-CARE · SERIAL

**Goal**: A connection key authenticates; the runner gets the most urgent turn across all the connection's agents, told which agent/model/strategy each turn is for. Delivers the core of US2 & US3.
**Independent Test**: with one connection and N agents in matches, `/api/agent/next-turn` returns the right turn and names the right agent + model; a paused connection yields none.

⚠️ **Do NOT split across parallel agents — this is the riskiest unit (past mid-deploy freeze). Tasks are serial.**

- [ ] T009 [US2] [app/deps.py] `require_bot`→`require_connection` (header `X-Connection-Key`, prefix `sk_conn_`); resolve via `connections.key_lookup`/`prev_key_lookup`; `CONNECTION_PAUSED` / `INVALID_KEY`. Add `require_agent_player` (connection→agent→player for a match_id).
- [ ] T010 [US2] [app/engine/connection_activity.py] Rename `bot_activity.py`→`connection_activity.py`; `mark_seen` stamps the **connection** (first_connected_at/last_seen_at, retire superseded key). Update imports.
- [ ] T011 [US3] [app/routes/agent_next_turn.py] Turn resolution: fan out over the connection's active `kind=ai` agents' players; pick most urgent via existing ordering; payload adds `agent_id`/`agent_name`/`model`/`seat_name`/`strategy` per contracts/endpoints.md. `report-pid` stamps `connections.runner_pid`.
- [ ] T012 [US2] [app/routes/agent_api.py] Resolve the acting player via `require_agent_player` for submit/message/leave/turn/state/chat/standings/history; `NOT_IN_GAME` if the connection has no agent in that match. Auth header switch.
- [ ] T013 [P: tests/test_agent_next_turn_fanout.py] [US3] Tests: single agent/one match; one connection / multiple agents / multiple matches → correct agent+model identified; paused connection → waiting; urgency ordering preserved. (Covers SC-002, SC-003.)
- [ ] T014 [P: tests/test_connection_auth.py] [US2] Tests: valid/invalid/missing key; graceful reissue overlap; paused connection 403; `mark_seen` heartbeat. (Covers the auth half.)

**Checkpoint**: auth + next-turn suites green; agent API resolves players via connection→agent.

---

## Phase 4: Slice 2 — Bots as Connectionless Agents (Priority: P1) 🎯

**Goal**: Scripted opponents are `kind=bot` agents with no connection; they seat into matches and rank, badged, on the leaderboard. Delivers US4.
**Independent Test**: a bot has no connection, plays deterministically, appears labeled on the leaderboard, never under `/me/connections`.

- [ ] T015 [P: app/engine/sims/runtime.py, app/engine/sims/service.py] [US4] Operate on `kind=bot` agents (read `bot_*` config from the agent); deterministic play unchanged; rename sim→bot in symbols/strings.
- [ ] T016 [P: app/engine/sims/seating.py] [US4] Seat bots (kind=bot agents) to fill matches; no key/runner involved.
- [ ] T017 [US4] [app/read_models/leaderboard.py] A row = an Agent; distinguish `ai` vs `bot`; keep the agents/bots/both views (replaces the agents/sims/both concept). (Depends Phase 2.)
- [ ] T018 [P: tests/test_bot_agents.py] [US4] Tests: bot has null connection + kind=bot; deterministic play; leaderboard labels it; invariant `kind=bot ⇒ no connection` and `kind=ai ⇒ connection+model` enforced. (Covers SC-004.)

**Checkpoint**: bot seating + deterministic play + leaderboard ai/bot suites green.

---

## Phase 5: Slice 3 — Connection & Agent Management (Priority: P2)

**Goal**: `/me/connections` and `/me/agents` pages; combined create flow; strategy on the agent; leaderboard identity. Delivers US1, US5, US6, US7.
**Independent Test**: see quickstart US1/US5/US6/US7.

### Connections (US6)
- [ ] T019 [P: app/routes/connections_setup.py] [US6] `/me/connections` list + create (pick provider → setup message) + detail (runner status, agents it powers).
- [ ] T020 [P: app/routes/connections_credentials.py] [US6] reissue/revoke key (graceful overlap), report runner health.
- [ ] T021 [P: app/routes/connections_lifecycle.py] [US6] pause/resume/delete a connection; **block delete while it powers agents** (clear message).
- [ ] T022 [P: app/templates/connections/list.html, app/templates/connections/detail.html, app/templates/connections/_health_badge.html, app/templates/connections/_reconnect.html] [US6] Connection templates (from `bots/` split); drop the MCP-direct "Advanced" section.

### Agents (US1, US5, US7)
- [ ] T023 [P: app/routes/agents_setup.py] [US1] `/me/agents` list + **combined create flow** (no connection → provider→connect→name+model inline; has connection → pick-connection→name+model+strategy) + detail.
- [ ] T024 [P: app/routes/agents_lifecycle.py] [US5] rename/pause/delete agent; `set-model` (constrained to connection provider); `strategy` edit — **blocked during an active match**; write the per-match `strategy_snapshot` at match start.
- [ ] T025 [P: app/routes/agents_status.py] [US1] agent onboarding/health fragments (from `bots_status`).
- [ ] T026 [P: app/templates/agents/list.html, app/templates/agents/detail.html, app/templates/agents/_status.html] [US1] Agent templates incl. the combined create flow + state-driven detail.
- [ ] T027 [US7] [app/routes/web_player.py] Join uses an agent; strategy comes from the agent + snapshot; `seat_name` derives from agent name. (Depends Phase 2–3.)
- [ ] T028 [P: app/routes/web_lobby.py, app/routes/admin_web.py, app/routes/web_viewer.py, app/routes/nav_context.py, app/routes/auth.py] [US7] Update agent/connection references; **two nav entries** (Connections, Agents); in-match display name from agent.
- [ ] T029 [app/routes/bots_setup.py, app/routes/bots_lifecycle.py, app/routes/bots_status.py, app/routes/bots_credentials.py, app/routes/bots_web_support.py, app/templates/bots/] Delete the superseded bots routes + templates; update router registration. (Depends T019–T028.)
- [ ] T030 [P: tests/test_agent_management.py, tests/test_connection_management.py] [US1][US5][US6] Tests: combined create flow; model constrained by provider; strategy active-match block + snapshot; delete-connection blocked while powering agents. (Covers SC-001, SC-005.)

**Checkpoint**: management pages work; routers register; these suites green.

---

## Phase 6: Slice 4 — Runner + MCP (Priority: P2)

**Goal**: The runner keys by connection and drives each agent's model; MCP naming updated; MCP-direct path gone.

- [ ] T031 [scripts/agentludum_agent.py] Key by connection (`--key sk_conn_…`); read `agent_id`/`agent_name`/`model` from each next-turn payload; keep one session per (agent, match) with that agent's model.
- [ ] T032 [P: mcp_server/server.py] Header/key naming (`X-Connection-Key`); tools proxy the same agent API (no MCP-direct connect path lives here to remove).
- [ ] T033 [P: tests/test_runner_payload.py] Test the runner's per-agent model/session selection against a mocked next-turn payload (mock the model CLIs per constitution — no live calls).

**Checkpoint**: runner + MCP suites green.

---

## Phase 7: Slice 5 — Rename Sweep + Preflight (Priority: P3)

**Goal**: No `Bot` class, no `/me/bots`, no user-facing "bot" for a user's player; whole preflight green. Delivers US8 + SC-006/007/008.

- [ ] T034 [US8] Sweep: `grep -rin "bot" app/ mcp_server/ scripts/ app/templates/` and the running copy; fix residual symbols/strings so a user's AI player is never "bot"; "bot" only labels scripted opponents.
- [ ] T035 [US8] Confirm no `/me/bots` route and no `Bot` model class remain; nav shows Connections + Agents only.
- [ ] T036 Update `MEMORY`/`DESIGN.md`/`UI.md` references and any spec cross-links to the new vocabulary (no stale "Sim"/"bot=user player").
- [ ] T037 Full preflight from repo root: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` — all green, no suppressions. (Covers SC-007.)
- [ ] T038 Run the quickstart.md manual passes for US1–US8 against the dev server. (Covers SC-001…SC-008 end-to-end.)

**Checkpoint**: feature complete, preflight green, ready for PR (do not merge without `/ship`).

---

## Dependencies & Execution Order

### Phase Dependencies
- **Phase 1 (Setup)**: re-sync first.
- **Phase 2 (Slice 0)**: BLOCKS every later phase.
- **Phase 3 (Slice 1)**: after Phase 2. **Serial, single-agent — highest risk.**
- **Phase 4 (Slice 2)**: after Phase 2; can overlap Phase 3 if staffed separately (different files), but leaderboard task T017 reads new models only.
- **Phase 5 (Slice 3)**: after Phases 2–3 (join/turn paths must exist).
- **Phase 6 (Slice 4)**: after Phase 3 (payload shape) ; T031 depends on T011.
- **Phase 7 (Slice 5)**: last; needs all prior phases.

### Parallel Opportunities
- Within Phase 2: T002 ∥ T003 (different files); T004/T005 after T003.
- Within Phase 4: T015 ∥ T016 (T017 after models).
- Within Phase 5: route/template tasks marked [P] touch disjoint files; T029 (deletes) and T027 (shared web_player) are serial.
- Phase 3 has **no** [P] — keep it serial.

### Critical-path note
Slice 1 (Phase 3) is the load-bearing, highest-risk unit. Land it on its own, with T013/T014 green, before building management UI on top of it.
