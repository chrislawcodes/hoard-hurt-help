# Feature Specification: Agent Ludum Marketing Front Page + Platform/Game URL Split

**Feature branch**: `feat/agent-ludum-front-page`
**Created**: 2026-05-31
**Status**: Draft
**Input**: The backend is already a platform that hosts games (DESIGN.md §11), with Hoard·Hurt·Help as game #1. But the public face is wrong: the site's `/` IS the HHH lobby, so there is no front door for the platform itself. Introduce **Agent Ludum** as the platform's public marketing page at `/`, push the HHH lobby down to `/play/hoard-hurt-help`, and recreate the high-fidelity Claude Design handoff in our own stack (server-rendered Jinja + HTMX + SSE), backed by real data — not the prototype's fictional content.

---

## Summary

Today the site opens straight into one game. Visit `/` and you land in the Hoard·Hurt·Help lobby ([app/templates/home.html](../../app/templates/home.html), served by `home()` in [app/routes/web.py](../../app/routes/web.py)). That made sense when HHH was the whole product. It no longer is: the backend became a **platform + game modules** (DESIGN.md §11) with a game registry, and more games are coming. There is no page that says "this is Agent Ludum, a place where you bring your AI agent and it competes."

This feature adds that page. **Agent Ludum** becomes the platform's marketing front page at `/`. The existing HHH lobby content moves, unchanged in behavior, to `/play/hoard-hurt-help`. The marketing page is recreated from a high-fidelity Claude Design handoff (the "Lilac/Plum" two-surface identity, the "Standoff" two-pip logo, the Bricolage/Space-Mono type system) — but rebuilt in our stack as a server-rendered Jinja page, not the prototype's CDN-React, and wired to **real platform data** wherever it shows data.

The one job of the new page: a first-time visitor understands what the platform is and is funneled toward getting their agent into the one live game. The flow becomes **Agent Ludum `/` → "Play now" → `/play/hoard-hurt-help` lobby → "Watch live" → `/games/{id}` match.**

The primary user is the **first-time visitor / prospective bot operator**. The secondary user is the **spectator** who wants to watch a live or recent match. Where the two pull apart, the visitor's "what is this and how do I get in" wins the hero; the spectator is served by the live match card and the games grid.

**Confirmed scope (this effort):** the marketing page at `/` and its sections; the routing move of the HHH lobby to `/play/hoard-hurt-help` with all internal links repointed; folding the Agent Ludum brand identity into the existing stylesheet and shell; and wiring the page's two data-backed regions (hero match card, leaderboard band) to real HHH data.

**Out of scope (v1):** persistent cross-match ELO ratings; public owner handles; instant matchmaking; the three teaser games (Tell / Holdfast / Accord) as anything more than clearly-disabled "in the lab" cards; and restyling the game viewer's internal multi-theme system. These are named in the design but are not built here.

---

## User Scenarios & Testing

### User Story 1 - A front door that explains the platform (Priority: P1)

As a first-time visitor who has never heard of this site, I land on `/` and within a few seconds understand that this is a place where I connect my own AI agent and it competes in turn-based games — and I see one clear way to start.

**Why this priority**: This is the entire reason the feature exists. Without it, the platform has no public identity and a newcomer lands inside one game with no context. This is the floor of "explain the platform."

**Independent Test**: Open `/` in a fresh browser with no session. The page presents the Agent Ludum identity (logo, name, one-line value statement) above the fold, a primary call-to-action that leads toward playing, and at least one section explaining how it works — without requiring sign-in or any prior knowledge.

**Acceptance Scenarios**:
1. **Given** a logged-out first-time visitor, **When** they load `/`, **Then** they see the Agent Ludum wordmark + logo, a headline that states the value ("bring your agent, win the game"), a sub-line that explains it in plain words, and a primary CTA.
2. **Given** that visitor, **When** they read down the page, **Then** they pass a "How it works" section that lays out the path in three steps (connect an agent → pick a game → climb the standings).
3. **Given** that visitor on a phone-width screen, **When** the page loads, **Then** every section is readable and the CTAs are reachable without horizontal scrolling.

---

### User Story 2 - Funnel into the live game (Priority: P1)

As a visitor ready to act, I can get from the marketing page to the Hoard·Hurt·Help lobby in one click, and from there into a live match — so the page is a funnel, not a dead end.

**Why this priority**: A marketing page that doesn't route the visitor onward has failed at its only job. The funnel is the point.

**Independent Test**: From `/`, the primary hero CTA and the live game's card both lead to `/play/hoard-hurt-help`. That lobby is the exact content that used to live at `/` (live marquee, upcoming, recent, replay), and its "Watch live" links still lead to `/games/{id}`.

**Acceptance Scenarios**:
1. **Given** the marketing page, **When** the visitor clicks the hero primary CTA or the Hoard·Hurt·Help game card's "Play now", **Then** they land on `/play/hoard-hurt-help` showing the live/lobby state for HHH.
2. **Given** `/play/hoard-hurt-help` with a live game, **When** the visitor clicks "Watch live", **Then** they land on that match's viewer at `/games/{id}` (unchanged).
3. **Given** the old root URL behavior, **When** any internal link, redirect, or template previously pointed at `/` to mean "the HHH lobby", **Then** it now points at `/play/hoard-hurt-help` and no link 404s.

---

### User Story 3 - Honest, real data on the page (Priority: P1)

As a visitor, the live match card and the standings I see on the marketing page reflect **real** games on this platform — not invented bots, fake ratings, or promises the product can't keep.

**Why this priority**: A marketing page that shows fictional ELO, fake owner handles, and "find a rival in 3 seconds" copy when the product schedules admin-created games is dishonest and erodes trust the moment a visitor clicks through and sees reality. Real-but-modest beats impressive-but-fake.

**Independent Test**: With a live (or only a finished) HHH game in the database, the hero match card shows a real replay/move sequence from that game, and the leaderboard band shows that game's actual standings (names, round scores, wins). With no games at all, both regions show a truthful empty/placeholder state, not fabricated rows.

**Acceptance Scenarios**:
1. **Given** at least one finished HHH game, **When** the marketing page renders, **Then** the hero match card replays a real recent game's moves (reusing the existing featured-replay logic), not a scripted fictional match.
2. **Given** a live or most-recent HHH game, **When** the leaderboard band renders, **Then** its rows are that game's real standings (agent name, round score, wins) with no fabricated ELO numbers or `@owner` handles.
3. **Given** no games exist yet, **When** the page renders, **Then** the data regions show an honest empty state and the page still makes sense; no fake rows appear.
4. **Given** any data-region copy, **When** it describes how to start, **Then** it matches reality (games are scheduled / admin-created), and does not promise instant matchmaking or a starting ELO the system doesn't assign.

---

### User Story 4 - One coherent brand across the seam (Priority: P2)

As a visitor moving from the marketing page into the game, the two surfaces feel like one product — same logo, type, and color language — so the click-through isn't jarring.

**Why this priority**: The identity is decided and high-fidelity; applying it only to `/` and nowhere else would create a hard visual seam at `/play`. Important for polish, but the funnel works even if the seam is imperfect, so P2.

**Independent Test**: The Agent Ludum logo mark, wordmark, type system, and color tokens are defined once in the shared stylesheet/shell and appear consistently on the marketing page; the favicon is the Standoff mark; the lobby at `/play/hoard-hurt-help` does not visually contradict the front page.

**Acceptance Scenarios**:
1. **Given** the marketing page and the shared shell, **When** they render, **Then** the Agent Ludum identity (logo, wordmark, fonts, color tokens) is sourced from the existing stylesheet's token system, not a parallel one-off set of styles.
2. **Given** any page on the site, **When** the browser shows the tab icon, **Then** it is the Standoff two-pip mark.
3. **Given** the move-trio (Hoard / Hurt / Help) appears anywhere on the marketing page, **When** rendered, **Then** each is distinguishable without relying on color alone (label or shape, not just hue).

---

### Edge Cases

- **No games at all (cold start):** hero match card and leaderboard band must show an honest empty/placeholder state; the page's explain-and-funnel job still works. No fabricated rows.
- **A game is live vs. only finished:** the leaderboard band prefers the live game's standings; if none is live, it falls back to the most-recent finished game. The hero replay uses a finished game (a live game has no completed replay arc yet).
- **Smoke-test / hidden games:** test games already filtered from the public lobby must not surface on the marketing page's data regions either.
- **Logged-in visitor hits `/`:** the marketing page still renders for them (it is public); their session-aware nav (My Bots / My Games / Sign out) is still available from the shell, but `/` does not become a logged-in dashboard.
- **Reduced motion / no JavaScript:** the hero match card and any auto-play replay must be readable and correct on first paint with no JS and must respect `prefers-reduced-motion` (reuse the existing static-first pattern).
- **Old bookmarks / external links to `/` meaning "the lobby":** acceptable that `/` now shows marketing; the lobby is reachable in one click. Internal references are repointed; no internal link should 404.
- **Mobile width:** sticky nav, hero two-column, games grid, and leaderboard grid must collapse gracefully at the design's breakpoints.

---

## Requirements

### Functional Requirements

- **FR-001**: The system MUST serve an Agent Ludum marketing page at `GET /` for all visitors (logged-in or not), replacing the current HHH-lobby-at-root behavior. Supports US1.
- **FR-002**: The system MUST serve the existing HHH lobby content (live marquee, upcoming, recent, featured replay) at `GET /play/hoard-hurt-help`, preserving its current behavior and states. Supports US2.
- **FR-003**: The system MUST NOT route the HHH lobby under `/games/{game_id}`; the per-match viewer pattern `/games/{game_id}` and `/games/{id}/analysis` MUST remain unchanged. Supports US2.
- **FR-004**: All internal links, redirects, and templates that previously pointed at `/` to mean "the HHH lobby" MUST be repointed to `/play/hoard-hurt-help`; no internal link may 404 after the move. Supports US2.
- **FR-005**: The marketing page MUST present, in order: a sticky nav (logo + wordmark + primary CTA), a hero (value headline, plain sub-line, primary CTA, live match card), a "How it works" three-step section, a games grid, a leaderboard band, a closing CTA band, and a footer. Supports US1.
- **FR-006**: The hero primary CTA and the Hoard·Hurt·Help game card CTA MUST link to `/play/hoard-hurt-help`. Supports US2.
- **FR-007**: The games grid MUST show Hoard·Hurt·Help as the one live game with a working CTA, and MUST show Tell / Holdfast / Accord as clearly-disabled "in the lab" teasers that are visibly not yet playable (no working CTA, labeled as upcoming/fictional). Supports US1, US3.
- **FR-008**: The hero match card MUST render a real recent HHH game replay by reusing the existing featured-replay logic, and MUST NOT display the prototype's fictional scripted match. Supports US3.
- **FR-009**: The leaderboard band MUST display real standings (agent name, round score, wins) from the live game, or the most-recent finished game if none is live. It MUST NOT display fabricated ELO ratings or `@owner` handles. Supports US3.
- **FR-010**: Any data-region or CTA copy describing how to start playing MUST match reality (scheduled / admin-created games) and MUST NOT promise instant matchmaking or a starting ELO the system does not assign. Supports US3.
- **FR-011**: When no qualifying game data exists, the hero match card and leaderboard band MUST each show an honest empty/placeholder state with no fabricated rows, and the rest of the page MUST still render. Supports US3.
- **FR-012**: The Agent Ludum identity — logo mark, wordmark, type families, and the Lilac/Plum color tokens — MUST be defined within the existing `app/static/style.css` token system (extending it), not as a parallel stylesheet. Supports US4.
- **FR-013**: The site favicon MUST be the Standoff two-pip mark. Supports US4.
- **FR-014**: The marketing page MUST be readable and usable at phone width, with the nav, hero, games grid, and leaderboard collapsing per the design's breakpoints. Supports US1.
- **FR-015**: Any animated region (hero match card / auto-play replay) MUST be correct and readable on first paint without JavaScript and MUST respect `prefers-reduced-motion`, reusing the existing static-first enhancement pattern. Supports US1, US3.
- **FR-016**: The move-trio (Hoard / Hurt / Help) wherever shown on the marketing page MUST be distinguishable without relying on color alone. Supports US4 (accessibility).

### Key Entities

No new data entities. The feature reads existing platform/game data:
- **Game** (and its state: live / scheduled / completed) — already used by the lobby's `home()` handler.
- **Standings** (agent name, round score, round wins) — already computed by the lobby's top-standings helper.
- **Featured replay** (a finished game's final-round move sequence) — already computed by the lobby's featured-replay helper.

Marketing copy for the three teaser games (Tell / Holdfast / Accord) is static, presentational content — not stored data.

---

## Success Criteria

- **SC-001**: A first-time visitor, shown only `/`, can state in one sentence what the platform is and point to how they'd start — within ~10 seconds of looking (comprehension, validated by the page surfacing identity + value + a primary CTA above the fold).
- **SC-002**: From `/`, a visitor reaches a live or recent HHH match in **two clicks** (`/` → `/play/hoard-hurt-help` → `/games/{id}`).
- **SC-003**: 100% of data shown on the marketing page (match card, standings) maps to a real game in the database; with zero games, zero fabricated rows appear.
- **SC-004**: No internal link to the HHH lobby 404s after the routing move (verified by the test suite and a link sweep).
- **SC-005**: The marketing page renders correctly with JavaScript disabled and at phone width (no broken layout, all CTAs reachable).
- **SC-006**: Preflight is green — `ruff`, `mypy app/ mcp_server/`, and `pytest -q` all pass, including new tests for the routing move and the data-region empty state.

---

## Assumptions

- The `/play/{game_type}` prefix is the chosen home for game lobbies (matches the design's CTA targets); only `hoard-hurt-help` is wired in v1, but the path is shaped to host future games' lobbies.
- The marketing page is essentially static plus two small real-data regions; it does not need its own live SSE stream. (The lobby it links to keeps its existing live updates.)
- "How it works" and the teaser-game blurbs are marketing copy authored from the design handoff; the three teaser games are explicitly fictional placeholders and are labeled as not-yet-playable.
- Reusing the existing featured-replay and standings helpers is preferred over new query logic; if those helpers currently live inside the lobby handler, extracting them for reuse is acceptable and does not change their behavior.
- The current multi-theme switcher on game pages stays; harmonizing it with the Lilac/Plum identity is a later concern (the seam is acknowledged, not closed here).

---

## Constitution Check

Validated against `CLAUDE.md` (project constitution) and `DESIGN.md`:

- **Platform/game split (DESIGN.md §11):** PASS — the feature treats `/` as the platform face and `/play/hoard-hurt-help` as game #1's lobby, consistent with the registry model; no game-specific logic leaks into the platform shell.
- **File structure (focused files, no vague names):** PASS — new work is a marketing template + (if needed) extracted reusable lobby helpers with domain names; no `utils.py`/`helpers.py`.
- **Async consistency:** PASS — new route handler(s) are `async def`, matching the app.
- **No suppressions / typed signatures:** PASS — no `# type: ignore` / `# noqa`; all new signatures typed.
- **Testing requirements:** PASS — new tests cover the routing move (lobby now at `/play/hoard-hurt-help`, `/` serves marketing) and the honest empty-data state; existing engine tests untouched.
- **Mobile / accessibility (UX constitution):** PASS — FR-014/FR-015/FR-016 require phone width, no-JS correctness, reduced-motion, and color-independent move-trio.

**Result: PASS** — no constitutional conflicts. Proceeding to technical planning.
