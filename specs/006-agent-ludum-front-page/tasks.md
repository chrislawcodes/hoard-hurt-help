# Tasks: Agent Ludum Marketing Front Page + Platform/Game URL Split

**Prerequisites**: plan.md, plan-summary.md, spec-acceptance.md

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — file scope listed. Bare `[P]` is treated as serial.
- **[Story]**: User story (US1, US2, US3, US4).
- Paths are repo-relative, from plan.md.

**Scope reminder (from plan):** UI + routing only. No DB, no migrations, no new dependencies. Real data only — no fabricated ELO / `@handles` / instant-matchmaking copy. Teaser games are disabled. Lobby path is `/play/hoard-hurt-help` (NOT `/games/hoard-hurt-help`).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Brand foundation every section depends on.

- [X] T001 Confirm feature branch `feat/agent-ludum-front-page` is checked out (already created).
- [X] T002 [P: app/static/style.css] Add the 3 Google fonts (Bricolage Grotesque, Space Grotesk, Space Mono) and the Agent Ludum token scope to `app/static/style.css`: an `.al` block (Lilac — light) and a nested `.al-plum` block (Plum — dark arena) defining the design tokens from plan/handoff (`--bg`, `--bg-2`, `--surface`, `--ink`, `--ink-soft`, `--line`, `--line-bold`, `--brand` orange, `--brand-2` violet, `--accent`, `--on-brand`, fixed `--hoard`/`--hurt`/`--help`, radii, shadows). Extend the existing variable system; do NOT add a 15th `data-theme` and do NOT create a parallel stylesheet.
- [X] T003 [P: app/static/favicon.svg] Replace `app/static/favicon.svg` with the Standoff two-pip mark (orange `#e2640e` rounded tile, two `#fff6ec` pips, faint divider, `#241c33` stroke) per the handoff geometry. `base.html` already references `/static/favicon.svg`.

---

## Phase 2: Foundation (Blocking Prerequisites)

**Purpose**: The routing move — required before any user story can be tested.

⚠️ **CRITICAL**: No user story work begins until this phase is complete.

- [X] T004 [app/routes/web.py] Rename the lobby handler so it serves `GET /play/hoard-hurt-help` (keep the exact same context-building and `home.html` render — behavior preserved). Give it a clear function name (e.g. `hoard_hurt_help_lobby`).
- [X] T005 [app/routes/web.py] Add a new `async def` handler for `GET /` that gathers public-page data via the existing helpers — `_featured_replay(...)` for the hero card and `_top_standings(...)` for the leaderboard band (live game, else most-recent finished) — and renders `agent_ludum.html` with `{user, is_admin, featured, standings, has_live, ...}`. (Template is built in Phase 3/5; a minimal render is fine here.)

**Checkpoint**: Lobby reachable at `/play/hoard-hurt-help`; `/` routes to the new marketing handler.

---

## Phase 3: User Story 1 - A front door that explains the platform (Priority: P1) 🎯 MVP

**Goal**: A first-time visitor lands on `/` and understands the platform + sees one clear way to start.

**Independent Test**: `GET /` (logged out) shows the Agent Ludum logo + wordmark, a value headline + plain sub-line, a primary CTA, and a 3-step "How it works".

### Implementation for User Story 1

- [X] T006 [US1] Create `app/templates/agent_ludum.html` with the page shell and an `.al` wrapper, plus the sticky **Nav**: Standoff logo + "Agent Ludum" wordmark (Bricolage 800) + a primary CTA "Enter the arena →" linking to `/play/hoard-hurt-help`. Keep session-aware links (Sign in / My Bots / Sign out) available.
- [X] T007 [US1] Add the **Hero** to `app/templates/agent_ludum.html`: "NEW · Hoard·Hurt·Help is live" pill, value headline ("Bring your agent. / Win the game."), plain sub-line, primary CTA "Enter your agent →" → `/play/hoard-hurt-help` + a secondary "▶ Watch a match", and the mono meta row. Leave a slot for the live match card (filled in US3).
- [X] T008 [US1] Add the **How it works** 3-step section to `app/templates/agent_ludum.html`: 01 Connect your agent · 02 Pick a game · 03 Climb the standings (reword the handoff's "leaderboard" line to "standings", no ELO claim).
- [X] T009 [US1] Add the **Games grid** to `app/templates/agent_ludum.html`: Hoard·Hurt·Help as the one LIVE card with "Play now →" → `/play/hoard-hurt-help`; Tell / Holdfast / Accord as clearly-disabled "In the lab" teasers (no working CTA, visibly not-yet-playable, labeled as upcoming/fictional).
- [X] T010 [US1] Add the closing **CTA band** + **Footer** to `app/templates/agent_ludum.html` (footer link columns may point to existing routes or `#`; do not invent broken promises in copy).
- [X] T011 [P: app/static/style.css] [US1] Add the marketing-page component styles under the `.al` scope in `app/static/style.css`: nav, hero grid, step cards, game cards (+ disabled teaser state), pills, buttons (2px outline + hard offset shadow on Lilac, dropped on Plum), and the leaderboard grid. Reuse existing spacing/scale vars where possible.
- [X] T012 [US1] Verify responsive collapse in `agent_ludum.html` + `style.css` at the design breakpoints (900px, 560px): nav center links hide, hero/how-it-works/games grid/leaderboard stack; no horizontal scroll at phone width.

**Checkpoint**: `/` renders the full marketing page (data regions may be placeholder until US3).

---

## Phase 4: User Story 2 - Funnel into the live game (Priority: P1)

**Goal**: One click from `/` to the HHH lobby; lobby still funnels into a match; no internal link 404s.

**Independent Test**: Marketing CTAs land on `/play/hoard-hurt-help`; "Watch live" there → `/games/{id}`; repointed links resolve.

### Implementation for User Story 2

- [ ] T013 [US2] Confirm every marketing CTA in `app/templates/agent_ludum.html` (nav, hero primary, HHH game card, CTA band) targets `/play/hoard-hurt-help`.
- [ ] T014 [P: app/templates/my_games.html] [US2] Repoint `my_games.html` "Browse the lobby →" link from `/` to `/play/hoard-hurt-help`.
- [ ] T015 [P: app/templates/bots/_status.html, app/templates/bots/detail.html] [US2] Repoint the "Find a game to join →" / "Browse the lobby →" links in `bots/_status.html` and `bots/detail.html` from `/` to `/play/hoard-hurt-help`.
- [ ] T016 [P: app/templates/join.html] [US2] Repoint `join.html` "Cancel" from `/` to `/play/hoard-hurt-help`; review the "← Home" link (may stay `/` = marketing home, but make the intent deliberate).
- [ ] T017 [US2] Sweep `app/templates/` and `app/routes/` for any remaining `href="/"` or redirect-to-`/` that means "the lobby"; repoint to `/play/hoard-hurt-help`. Leave true "home" links (site-title, logout) pointing at `/`.

**Checkpoint**: 2-click funnel works; no internal link 404s.

---

## Phase 5: User Story 3 - Honest, real data on the page (Priority: P1)

**Goal**: The match card and standings reflect real games; empty state is honest; no fabricated content.

**Independent Test**: With a seeded finished game, real agent names/scores appear; with zero games, honest empty regions and no fake rows.

### Implementation for User Story 3

- [X] T018 [US3] Wire the hero match card slot in `agent_ludum.html` to the real `featured` replay — reuse the `fragments/featured_replay.html` markup pattern and the existing static-first auto-play script from `home.html` (line ~108). Render it inside the `.al-plum` surface. Do NOT port the prototype's `match-sim.jsx`.
- [X] T019 [US3] Wire the leaderboard band in `agent_ludum.html` to the real `standings` (agent name, round score, wins) on the `.al-plum` surface. Remove ALL fabricated rows, ELO numbers, and `@owner` handles from the handoff design.
- [X] T020 [US3] Reword every data-region and CTA sub-copy in `agent_ludum.html` to match reality (games are scheduled / admin-created). Delete "your agent starts at ELO 1500" and "matchmaking finds you a rival in ~3s"; replace with truthful copy (e.g. how scheduled games work / how to get a bot in).
- [X] T021 [US3] Implement honest empty states in `agent_ludum.html` for the match card and the leaderboard when no qualifying game exists: a calm placeholder, no fabricated rows, and the rest of the page still renders.

**Checkpoint**: Every data row maps to a real game; empty state is honest.

---

## Phase 6: User Story 4 - One coherent brand across the seam (Priority: P2)

**Goal**: The identity is one token system; favicon is the Standoff mark; move-trio is distinguishable without color.

**Independent Test**: Marketing tokens come from the `.al` scope; favicon is the two-pip mark; Hoard/Hurt/Help chips carry labels.

### Implementation for User Story 4

- [ ] T022 [US4] Audit `agent_ludum.html` so the identity (logo, wordmark, fonts, colors) is sourced from the `.al` token scope in `style.css` — move any one-off inline color/font literals into the scope. No parallel styling system.
- [ ] T023 [US4] Confirm the favicon renders as the Standoff mark site-wide, and ensure the move-trio (Hoard / Hurt / Help) chips on the marketing page carry a text label (or shape), so they are distinguishable without relying on color alone.

**Checkpoint**: One coherent brand; accessible move-trio.

---

## Phase 7: Tests, Polish & Preflight

**Purpose**: Lock the behavior and pass the gate.

- [ ] T024 [P: tests/test_agent_ludum_routing.py] [US2] Add `tests/test_agent_ludum_routing.py`: `GET /` returns 200 and contains an Agent Ludum marker + a CTA with `href="/play/hoard-hurt-help"`; `GET /play/hoard-hurt-help` returns 200 and contains a lobby marker (move legend / marquee); `GET /games/{id}` still returns 200 for a seeded game.
- [ ] T025 [P: tests/test_agent_ludum_data.py] [US3] Add `tests/test_agent_ludum_data.py`: with zero games, `GET /` is 200, shows the honest empty regions, and contains no fabricated rows (assert absence of `ELO`/`@` leak); with one finished game seeded, the real agent name appears in the rendered standings.
- [ ] T026 [US2] Extend the routing test (or add a focused assertion) to sweep that the repointed lobby links resolve (no `href="/"`-means-lobby leftovers; repointed targets return non-404).
- [ ] T027 Run the Preflight Gate from repo root and fix root causes until green: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. No suppressions.

**Checkpoint**: Preflight green — feature ready for `/ship`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies. T002 and T003 are parallel (different files).
- **Foundation (Phase 2)**: Depends on Setup. BLOCKS all user stories. T004 then T005 (same file, serial).
- **User Stories (Phase 3–6)**: Depend on Foundation.
  - US1 is the MVP (the page itself).
  - US2 (funnel/links), US3 (real data), US4 (brand audit) build on US1's template but are independently testable.
- **Tests + Preflight (Phase 7)**: Depend on the stories they cover; T027 runs last.

### User Story Dependencies

- **US1 (P1)**: Independent after Foundation — delivers the rendered page.
- **US2 (P1)**: Needs the route move (Foundation) + the CTAs in the US1 template.
- **US3 (P1)**: Needs the US1 hero/leaderboard slots + the Foundation handler passing `featured`/`standings`.
- **US4 (P2)**: An audit pass over the US1 template + the Setup tokens/favicon.

### Parallel Opportunities

- T002 ∥ T003 (style.css vs favicon.svg).
- T014 ∥ T015 ∥ T016 (separate template files) — but all are serial against any task editing the same file.
- T024 ∥ T025 (separate test files).
- Within `agent_ludum.html` (T006–T010, T013, T018–T023) tasks edit the **same file** → run serially.
- T011 edits `style.css` → serial against T002.
