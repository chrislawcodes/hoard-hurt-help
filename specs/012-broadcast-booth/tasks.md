# Tasks: Broadcast-booth viewer + animation speed controls

**Prerequisites**: plan.md, plan-summary.md, spec-acceptance.md, quickstart.md

## Format: `[ID] [P: file]? [Story]? Description`
- **[P: file]**: parallelizable (disjoint files). Bare/none = serial.
- **[USn]**: user story label.
- Paths are repo-relative within the worktree `/Users/chrislaw/hoard-hurt-help/_wt_booth`.

---

## Phase 1: Setup

- [X] T001 Confirm worktree `_wt_booth` on branch `feature/broadcast-booth` off latest `origin/main`, `.venv` symlinked.
- [X] T002 Seed a 16-bot game into the worktree DB (`/tmp/seed_viewer.py` → `G_9001` completed, `G_9002` active) and start the nested-worktree preview (launch config `cwd: _wt_booth`).

**Checkpoint**: harness ready to verify in-browser.

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: confirm the reusable per-turn data the booth depends on is present (no schema/build work).

- [X] T003 Verify each history turn already carries `headline`, `groups`, `summary`, `feed_actions` in `app/routes/web.py` `_game_view_context()` (shipped #122/#125). No new data needed; note any gap.

**Checkpoint**: data contract confirmed — user-story work can begin.

---

## Phase 3: User Story 1 — Pace control (Priority: P1) 🎯 MVP

**Goal**: speed dial (1×/2×/3×) + skip-talk + snappier default so a 16-bot game is watchable.

**Independent Test**: at 2×/3× each turn plays faster and fully resolves; skip-talk omits talk; default is snappier (SC-001).

### Implementation (all in `app/templates/fragments/robot_circle.html`)

- [X] T004 [US1] Add module-level `var speed` (default snappier than current) + `function ms(n){ return Math.round(n / speed); }`.
- [X] T005 [US1] Wrap EVERY `setTimeout(...)` delay and every `.animate(..., {duration})` / inline animation duration in `ms()` — sweep the whole script (walk, strike, gift/bat pickup, hoard, delta floats, tele rows, dim/restore timers, strikeAt/lockRing inline durations).
- [X] T006 [US1] Ensure `talkDurOf()` and `buildSchedule().totalDuration` are computed from `ms()`-scaled constants, and `scheduleNext()`'s delay (those + the trailing buffer, `ms(buffer)`) is fully scaled — the cut-off/stall risk.
- [X] T007 [US1] Add a `skipTalk` toggle: `talkDurOf()` returns 0 when set; `renderTurn()` skips the talk phase (speak loop + talk caption) and goes straight to scheduling actions; messages stay in the feed.
- [X] T008 [US1] Add the speed control (1×/2×/3×) + skip-talk toggle to the animation controls UI; reflect active state; speed change takes effect from the next turn without cutting the current one.
- [X] T009 [US1] Style the new controls in `app/static/style.css` (extend existing control styles; mobile-safe).
- [X] T010 [US1] Browser-verify (preview harness): step + autoplay at 1×/2×/3× across ≥3 turns (no cut-off/stall); skip-talk on/off; default pace; SC-001 rough timing. Capture a screenshot.

**Checkpoint**: US1 shippable on its own.

---

## Phase 4: User Story 2 — One synchronized story (Priority: P1)

**Goal**: stage + rail + now-strip + feed all reflect the same playhead turn.

**Independent Test**: step the playhead; all four regions describe the same turn and advance together (SC-003/SC-004); rail follows (not pinned latest).

- [X] T011 [US2] In `app/templates/fragments/robot_circle.html` `renderTurn()`, broadcast the current turn: write `data-rc-seq` on `#live-region` and dispatch `CustomEvent('rc:turn', {detail:{seq}})`.
- [X] T012 [US2] Retire #121's `railToLatest()` default so the rail follows the playhead (keep a latest-render path for the reduced-motion case in Phase 5).
- [X] T013 [US2] Add the now-strip element under the stage in `app/templates/fragments/live_region.html` (or the hero block) — headline slot + marquee-chip slot; reuse the animation's existing transport controls (do not duplicate).
- [X] T014 [US2] Feed coordinator JS in `app/templates/game.html`: on `rc:turn`, highlight the matching turn-block (`data-seq`) and fill the now-strip headline from that block's `turn.headline`; source the marquee chip from the animation's per-turn marquee.
- [X] T015 [US2] Lay out the booth in `app/static/style.css`: now-strip directly beneath the stage; visual bridge styling.
- [X] T016 [US2] Browser-verify: pause, step Prev/Next to the betrayal turn; confirm stage/rail/now-strip/highlighted-feed all match and advance together; scrub back shows earlier rail standings. Screenshot.

**Checkpoint**: the four regions are synchronized.

---

## Phase 5: User Story 3 — Story tells itself on load (Priority: P2)

**Goal**: autoplay on load (reduced-motion → paused at latest); feed current-turn-first, revealed up to playhead.

**Independent Test**: default load auto-plays from turn 1, feed current-at-top; reduced-motion opens at latest paused (SC-002/SC-005).

- [X] T017 [US3] Feed coordinator in `app/templates/game.html`: show only turn-blocks with `seq <= playhead`, current turn first with history descending; re-apply on `htmx:afterSwap` by re-reading `data-rc-seq` (survives SSE swaps). Reconcile with the existing view-switch + round-nav closures.
- [X] T018 [US3] In `app/templates/fragments/robot_circle.html`, enable autoplay-on-load by default for the viewer; if `prefers-reduced-motion: reduce`, do not auto-animate and render the latest turn (rail + feed current) paused.
- [X] T019 [US3] Browser-verify: default load auto-plays + feed current-first/reveal-up-to; force reduced-motion → opens at latest, no animation; live game (`G_9002`) advances to a new turn. Screenshot.

**Checkpoint**: the story unfolds on load.

---

## Phase 6: User Story 4 — Full record stays available (Priority: P3)

**Goal**: no regression to Cards/Compact; view + playhead survive a live swap.

- [X] T020 [US4] Browser-verify: switch feed Story/Cards/Compact (all render); simulate an SSE swap (`htmx.ajax` GET `.../live` into `#live-region`) and confirm chosen view + current-turn highlight/order are preserved (SC-007).

**Checkpoint**: secondary user not regressed.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T021 [P: app/static/style.css] Mobile (375px): stage → rail → now-strip → feed stack, no horizontal overflow (SC-006). Verify in preview.
- [ ] T022 Empty-game state: a game with no resolved turns shows the stage empty state + "waiting for the first move"; nothing auto-plays. Verify.
- [ ] T023 Run the preflight gate from the worktree: `ruff check . && mypy app/ mcp_server/ && pytest -q`. (Note: pre-existing `app/models/enum_types.py:68` mypy error on `origin/main`, mypy 2.1.0 — not from this feature; do not suppress.)
- [ ] T024 Run the full quickstart.md pass; capture before/after screenshots for the PR.
- [ ] T025 Open the PR (Validation section with exact commands + results) and ship via `/ship`.

---

## Dependencies & Execution Order

- **Phase 1 (Setup)** → **Phase 2 (Foundation)** → user stories.
- **US1 (Phase 3)** is independent and shippable alone (the MVP / original pain).
- **US2 (Phase 4)** depends on Foundation; builds the sync. **US3 (Phase 5)** depends on US2's coordinator + broadcast. **US4 (Phase 6)** is verification, depends on US2/US3.
- **Polish (Phase 7)** depends on the user stories being in place.

### Parallel Opportunities
- Most tasks touch the same two files (`robot_circle.html`, `game.html`) and CSS, so they are largely **serial** — parallelism is limited. T009/T015/T021 (CSS) can batch. Treat JS edits to a shared file as serial.

### Notes
- Verification is browser-first (preview harness): timing (Phase 3) and sync/SSE-swap (Phases 4–6) cannot be proven by preflight alone.
- This is one feature → one PR; phases are commit boundaries (`feature-implement` commits at each phase).
