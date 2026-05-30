# Tasks: Live Connection Handshake for Bot Onboarding

**Prerequisites**: plan.md, spec.md, plan-summary.md, data-model.md, spec-acceptance.md

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: can run in parallel (file scope listed). Bare `[P]` = serial.
- **[Story]**: user story label (user-story phases only).
- Exact file paths from plan-summary.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: branch + dev environment ready.

- [X] T001 Confirm on branch `005-bot-onboarding-handshake` off `origin/main`; dev server runs on :8766 against a fresh model-built DB (per quickstart Troubleshooting).

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: schema, persisted signal, the activity/state helper, and the per-bot channel convention — everything the stories build on.

⚠️ No user-story work begins until this phase is complete.

- [X] T002 Add `first_connected_at: Mapped[datetime | None]` (nullable `DateTime(timezone=True)`) to `app/models/bot.py`. Acceptance: model field exists; `create_all` builds the column.
- [X] T003 Create migration `migrations/versions/0005_add_bot_first_connected_at.py` — `upgrade` = `op.add_column("bots", ...)`, `downgrade` = `op.drop_column`. Acceptance: additive only, no `drop_constraint`, no backfill; downgrade reverses it.
- [X] T004 Create `app/engine/bot_activity.py` with `OnboardingState` enum and `async def compute_onboarding_state(db, bot) -> OnboardingState` implementing the precedence table (playing → in_game_no_move → connected_pregame → connected_no_game → waiting_in_game → waiting). Acceptance: returns correct state from `first_connected_at` + players + non-defaulted submissions.
- [X] T005 In `app/engine/bot_activity.py` add `async def mark_connected(db, bot)` (set `first_connected_at` + publish `connected` to `bot:{id}` only on `NULL→now`) and `async def mark_first_move(db, bot_id)` (publish `moved` to `bot:{id}` only if no prior non-defaulted submission). Acceptance: each publishes at most once; reuses `app/broadcast.publish`.
- [X] T006 [P: tests/test_bot_onboarding.py] Foundation tests: table-driven `compute_onboarding_state` over all six states; `mark_connected`/`mark_first_move` publish-once semantics (capture via `broadcast`). Acceptance: tests pass against in-memory SQLite.

**Checkpoint**: Foundation ready — user stories can begin.

---

## Phase 3: User Story 1 - Confirm the bot connected, live (Priority: P1) 🎯 MVP

**Goal**: detail page flips Waiting → ✓ Connected with no reload, and is correct on first paint/reload.

**Independent Test**: open a fresh bot's page (Waiting); make one authenticated agent call; panel updates to Connected in place; reload still shows Connected.

- [X] T007 [US1] Hook first-connection in `app/deps.py::require_bot`: after resolving the bot, `await mark_connected(db, bot)`. Acceptance: first authed call sets `first_connected_at` + emits one `connected`; later calls do neither.
- [X] T008 [US1] Add owner-scoped `GET /me/bots/{bot_id}/stream` to `app/routes/bots_web.py` (require_user + `_owned_bot`) streaming `subscribe(f"bot:{bot_id}")`, mirroring `app/routes/sse.py`. Acceptance: owner gets event-stream; non-owner gets 404.
- [X] T009 [US1] Add owner-scoped `GET /me/bots/{bot_id}/status` to `app/routes/bots_web.py` rendering `bots/_status.html` from `compute_onboarding_state`. Acceptance: returns the correct state fragment; non-owner 404; no key in output.
- [X] T010 [US1] Create `app/templates/bots/_status.html` with the `waiting` and `connected_no_game` blocks (icon + text, not color-only). Acceptance: each state renders its copy.
- [X] T011 [US1] Wire `app/templates/bots/detail.html`: SSE-connected wrapper (`sse-connect=".../stream"`) with an inline-rendered `#bot-status` that `hx-get=".../status"` on `sse:connected`/`sse:moved`; correct first paint. Acceptance: live update on connect, no double-fetch flash on load.
- [X] T012 [P: tests/test_bot_onboarding.py] [US1] Tests: `/status` and `/stream` owner-scoping; first-connect via a simulated agent call flips state; first paint correct without events. Acceptance: tests pass.

**Checkpoint**: US1 functional and testable independently.

---

## Phase 4: User Story 2 - Guided from connected to playing (Priority: P1)

**Goal**: a connected, gameless bot is never a dead end.

**Independent Test**: connected bot with no games shows "last step: join a game" + a Join action; empty-Games copy matches; the action reaches the join path.

- [X] T013 [US2] In `app/templates/bots/_status.html`, finalize the `connected_no_game` block with a primary "Join a game →" action linking to the join path (surface next open game if readily available). Acceptance: action present and routes correctly.
- [X] T014 [US2] Update the empty Games state in `app/templates/bots/detail.html` to "Connected but not in a game yet — that's the last step. Join a game →" (shown when connected + no games). Acceptance: copy replaces the generic "no games" line for connected bots.
- [X] T015 [P: tests/test_bot_onboarding.py] [US2] Test: connected + no-games renders the join guidance in both the panel and the Games empty state. Acceptance: tests pass.

**Checkpoint**: US2 functional.

---

## Phase 5: User Story 3 - See the first move (the win) (Priority: P1)

**Goal**: first move ends onboarding on a clear, live win; reload shows the calm playing state.

**Independent Test**: bot in a game submits its first action; panel updates in place to "made its first move — Watch live →"; reload shows the calm playing state, not a re-run celebration.

- [X] T016 [US3] Call `await mark_first_move(db, player.bot_id)` in `app/routes/agent_api.py::agent_submit` after `record_submission`/commit (first non-defaulted submission only). Acceptance: first move emits one `moved`; later moves don't.
- [X] T017 [US3] Verify the MCP submit path (`mcp_server/`) and route it through `mark_first_move` too (or confirm it shares the agent_submit path). Acceptance: a first move via MCP also emits `moved` exactly once.
- [X] T018 [US3] Add `in_game_no_move` and `playing` blocks to `app/templates/bots/_status.html`: "✓ In '[game]'. Waiting for its first move…" and the calm "Playing in '[game]'. Watch live →"; add a one-shot flourish class applied only on the live `sse:moved` event in `detail.html`. Acceptance: states render; flourish fires live but not on plain reload.
- [X] T019 [P: tests/test_bot_onboarding.py] [US3] Tests: first-move detection (once); `in_game_no_move` vs `playing` resolution; watch link targets the right game. Acceptance: tests pass.

**Checkpoint**: P1 MVP complete (connect → guide → first move).

---

## Phase 6: User Story 4 - Failed connection is caught (Priority: P2)

**Goal**: a slow/bad paste gets a recovery path, not silence (passive — see Decision 5).

**Independent Test**: in the `waiting` state past a short delay, the panel surfaces a "taking too long? the code may be wrong — reissue" nudge; reissue → paste → connects normally.

- [X] T020 [US4] In the `waiting` block (`app/templates/bots/_status.html`) add a timed/secondary "Taking longer than expected? The code may be wrong — reissue and paste again." nudge with a reissue affordance. Acceptance: nudge appears in waiting state and links to reissue; no false "connected/invalid" claims.

**Checkpoint**: US4 functional.

---

## Phase 7: User Story 5 - Don't lose the key (Priority: P2)

**Goal**: the paste-once cliff has a visible safety net.

**Independent Test**: the fresh-key view shows a quiet "won't show again — lost it? reissue" line; reissue produces a new message and invalidates the old code.

- [X] T021 [US5] Add a quiet reminder line next to the fresh-key setup message in `app/templates/bots/detail.html` noting the code shows only once and pointing to reissue. Acceptance: line present in fresh-key state; key itself never re-rendered (FR-011).

**Checkpoint**: US5 functional.

---

## Phase 8: User Story 6 - Don't slow the returning operator (Priority: P3)

**Goal**: established bots don't get the first-run block.

**Independent Test**: a bot that has connected and moved shows only a quiet status line on its detail page.

- [X] T022 [US6] Ensure the `playing` block in `app/templates/bots/_status.html` is a quiet one-line status (no large waiting/celebration block) and that `detail.html` renders it compactly for established bots. Acceptance: established bot shows the quiet line only.

**Checkpoint**: US6 functional.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T023 [P: app/static/style.css] Add status-panel styles reusing the lobby live badge/dot; single-column, full-width actions on mobile; state conveyed by icon+text, not color alone (FR-012). Acceptance: panel looks consistent; legible at 375px.
- [X] T024 Verify in the live preview (`:8766`): walk waiting → connected → join → first-move with a simulated agent call; check phone width; confirm no key leaks and status is owner-only. Acceptance: quickstart scenarios pass in-browser.
- [X] T025 Run the Preflight Gate from repo root: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. Acceptance: all green; no suppressions added.
- [X] T026 Complete the checklists in `specs/005-bot-onboarding-handshake/checklists/`. Acceptance: all items checked or explicitly justified.

---

## Dependencies & Execution Order

### Phase Dependencies
- **Setup (P1)**: none.
- **Foundation (P2)**: depends on Setup — BLOCKS all stories (model/migration/helper/channel).
- **User Stories (P3–P8)**: depend on Foundation. P1 stories (US1→US2→US3) are the MVP and are lightly ordered (US2/US3 templates extend the US1 fragment). P2/P3 are independent after Foundation.
- **Polish (P9)**: after the desired stories.

### User Story Dependencies
- **US1**: independent after Foundation (the core live loop).
- **US2, US3**: build on US1's `_status.html` fragment + routes; testable independently once Foundation + US1 routes exist.
- **US4, US5, US6**: independent after Foundation (US4/US6 touch the fragment; US5 touches the fresh-key view).

### Parallel Opportunities
- Test tasks (`T006`, `T012`, `T015`, `T019`) are `[P]` on `tests/test_bot_onboarding.py` — but since they share one file, run them serially (append-only) per the bare-[P] rule.
- `T023` (CSS) is genuinely parallel to template/route work.
