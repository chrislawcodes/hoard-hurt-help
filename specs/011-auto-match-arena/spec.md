# Feature 011 — Auto-Match Arena & Operator Join Page

**Status:** Draft  
**Created:** 2026-06-03  
**Source:** UX audit Issue #1 — specs/010-operator-ux/ux-issues.md

---

## Problem

First-time bot operators arrive at the Agent Ludum homepage, click "Play now →", and land in a spectator-first lobby. There is nothing to join immediately (games are admin-scheduled), no guidance on what to do next, and no dedicated view for operators. Many users dead-end before their bot ever plays a turn.

---

## What We're Building

Three interconnected pieces that all serve the same goal — make "I want my bot to play right now" a path that actually works:

1. **Practice Arena** — an always-available match against Sim bots that starts the moment a human joins.
2. **Auto-scheduled matches** — a match opens every 30 minutes on the clock; Sims fill any empty slots at start time; no human minimum.
3. **Operator join page** — a dedicated page (separate from the spectator lobby) that surfaces both match types, bot setup status, and active games.

---

## User Stories

---

### User Story 1 — Practice Arena: always something to join (Priority: P1)

As a first-time bot operator, I need a match I can join right now — without waiting for a scheduled game or an admin — so I can verify my bot is connected and watch it play.

**Why P1:** This is the primary unblock. Without it, new operators hit a dead end the moment they land in the lobby.

**Independent test:** With no upcoming admin-created games, a signed-in user with a bot can navigate to the operator join page, click "Join Practice Arena", and watch their bot play a match within 60 seconds.

**Acceptance scenarios:**

1. **Given** there is no Practice Arena match currently open, **When** the server starts (or a previous Practice Arena match completes), **Then** the system automatically creates a new Practice Arena match with Sim bots pre-registered.

2. **Given** a Practice Arena match is open with Sim bots registered, **When** a human player joins their bot to it, **Then** the match starts immediately — no countdown, no wait.

3. **Given** the match just started (triggered by a human join), **When** the system detects this, **Then** a new Practice Arena match is created immediately so the next human can join.

4. **Given** the Practice Arena starts with 1 human bot and 4 Sim bots, **When** the match begins, **Then** all 5 participants play normally under the existing game engine.

5. **Given** a user has no bot yet, **When** they view the Practice Arena card on the operator join page, **Then** they see a prompt to set up a bot first, not a broken join button.

---

### User Story 2 — Auto-scheduled matches: a real game every 30 minutes (Priority: P1)

As a bot operator, I need a recurring match on a predictable schedule so I can enter my bot in a competitive run without depending on an admin to schedule one.

**Why P1:** Removes the admin bottleneck for regular games entirely.

**Independent test:** At both :00 and :30 of any hour, a new auto-match appears in the lobby with an "Upcoming" badge and a join button. At start time, it begins with Sims filling any empty human slots.

**Acceptance scenarios:**

1. **Given** the system clock reaches a scheduled interval (every 30 minutes: :00 and :30), **When** no auto-match is currently open for that interval, **Then** the system creates a new auto-match in "upcoming" state.

2. **Given** an auto-match is open, **When** human bots join during the window, **Then** they are registered normally and appear in the lobby player count.

3. **Given** the scheduled start time arrives, **When** the match has fewer participants than its player limit, **Then** Sim bots are registered to fill remaining slots and the match starts.

4. **Given** the scheduled start time arrives, **When** zero humans joined, **Then** Sims fill all slots and the match still starts — no human minimum.

5. **Given** an auto-match is running, **When** the next 30-minute interval arrives, **Then** a new auto-match is created for the next window (two auto-matches may briefly exist: one active, one upcoming).

6. **Given** an admin creates a manual match, **When** the 30-minute interval also fires, **Then** both matches coexist independently — auto-scheduling does not suppress admin-created games.

---

### User Story 3 — Operator join page: a home for people who are here to play (Priority: P1)

As a bot operator, I need a dedicated page that shows me my bot's connection status, what I can join right now, and my active games — without having to navigate through the spectator lobby.

**Why P1:** Without this page, operators have no clear landing point and the spectator lobby continues to be the default for everyone.

**Independent test:** A signed-in user with a connected bot navigates to `/play`, sees the Practice Arena with a join button, sees the next auto-match with a countdown, and can join either in two clicks.

**Acceptance scenarios:**

1. **Given** a user is not signed in, **When** they visit `/play`, **Then** they see a "Sign in to play" prompt alongside a brief explanation of what Agent Ludum is — not a blank or broken page.

2. **Given** a user is signed in but has no bot, **When** they visit `/play`, **Then** they see a clear "Set up your bot" CTA linking to `/me/bots`, explaining this is required before joining.

3. **Given** a user is signed in with a connected bot, **When** they visit `/play`, **Then** they see:
   - Their bot's connection status (connected / not connected)
   - Practice Arena card: "Join now →" (always available)
   - Next auto-match card: start time countdown + "Join →"
   - Their active or upcoming games (if any), each with "Watch →"

4. **Given** a user is signed in with a bot that is not yet connected, **When** they visit `/play`, **Then** the Practice Arena join button is disabled with the message "Connect your bot first" and a link to the bot detail page.

5. **Given** a user visits `/play` and joins the Practice Arena, **When** the join succeeds, **Then** they are redirected to the game viewer for that match.

6. **Given** multiple bots exist on the account, **When** the user joins from `/play`, **Then** they can select which bot to play as (same bot selector as the existing join form).

---

### User Story 4 — "Play now →" routes to the operator page (Priority: P2)

As a first-time visitor to the Agent Ludum homepage, I need "Play now →" to take me somewhere that tells me what to do — not a spectator lobby with no clear next step.

**Why P2:** The operator page is the fix; this story is the wire that connects it to the marketing homepage. Low code cost but depends on US3.

**Independent test:** Click "Play now →" on the Agent Ludum homepage. Arrival page shows either "Sign in to play" (not signed in) or bot status + join options (signed in).

**Acceptance scenarios:**

1. **Given** a visitor clicks "Play now →" on the homepage, **When** they are not signed in, **Then** they land on `/play` which shows a sign-in prompt alongside context explaining what they're signing in for.

2. **Given** a visitor clicks "Play now →", **When** they are already signed in with a bot, **Then** they land on `/play` showing the Practice Arena and next auto-match immediately.

3. **Given** the spectator lobby (`/games/hoard-hurt-help`) still exists, **When** a user navigates to it directly, **Then** it behaves identically to today — the change only affects where "Play now →" links.

---

### User Story 5 — Lobby shows auto-match and Practice Arena alongside admin games (Priority: P2)

As a spectator or operator browsing the lobby, I can see auto-matches and the Practice Arena in the upcoming section without needing to know they're a different type.

**Why P2:** Operators may arrive via the lobby; they should see these matches there too. Also keeps the lobby feeling alive.

**Independent test:** The lobby upcoming section shows the Practice Arena and the next auto-match alongside any admin-created games. No visual distinction required at this stage.

**Acceptance scenarios:**

1. **Given** a Practice Arena match is open, **When** any user views the HHH lobby, **Then** the Practice Arena appears in the upcoming section like any other upcoming match.

2. **Given** an auto-match is open, **When** any user views the HHH lobby, **Then** the auto-match appears in the upcoming section with the correct start time.

3. **Given** neither type has an admin-created match alongside them, **When** a user views the lobby, **Then** the upcoming section still shows the Practice Arena and/or the next auto-match rather than being empty.

---

## Edge Cases

- **Practice Arena still open when server restarts** — on startup, system checks for an existing open Practice Arena before creating a new one. No duplicates.
- **Practice Arena fills with humans before any join triggers start** — if max_players humans join before the first join triggers the start, the match starts on the last join. (Unlikely in practice but must not crash.)
- **Auto-match interval fires while previous auto-match is still in "upcoming" state** — system checks for an open auto-match before creating a new one; skips if one already exists.
- **No Sim bots available** — if no Sim bot profiles exist, Practice Arena creation fails gracefully with an admin alert rather than creating a game that can never start.
- **User joins Practice Arena, then it starts before they submit their strategy** — this cannot happen because the Practice Arena starts on join, and the strategy is set during the join form submission. No race condition.
- **User tries to join a Practice Arena that just started** (race between UI and server) — server returns a "match already started" error; the join page refreshes and shows the new open Practice Arena.
- **Clock skew / delayed poller** — if the 30-minute poller fires 30 seconds late, the auto-match is created with its start time set to the original :00/:30 boundary, not the late fire time. Already-passed start time triggers immediate Sim fill and start.
- **Admin deletes an auto-match or Practice Arena** — system recreates the appropriate type on next poller tick.

---

## Functional Requirements

- **FR-001**: The system MUST maintain exactly one Practice Arena match in "upcoming" state at all times. If none exists, it MUST create one before the next request cycle completes.
- **FR-002**: A Practice Arena match MUST start immediately when the first human player joins, without waiting for a scheduled start time.
- **FR-003**: When a Practice Arena match starts, the system MUST immediately create a new replacement Practice Arena match in "upcoming" state.
- **FR-004**: Sim bots MUST be pre-registered in the Practice Arena match at creation time, filling all slots not reserved for human players. The number of Sims MUST be configurable (default: enough to bring total to 5 participants).
- **FR-005**: The system MUST create a new auto-scheduled match at every 30-minute boundary (:00 and :30) if no auto-match is already open for that window.
- **FR-006**: At the scheduled start time of an auto-match, the system MUST register Sim bots to fill any unfilled player slots, then start the match — regardless of how many humans joined.
- **FR-007**: Admin-created matches MUST continue to work exactly as today; auto-scheduling MUST NOT suppress or interfere with them.
- **FR-008**: A route MUST exist at `/play` that serves the operator join page.
- **FR-009**: The operator join page MUST display the user's bot connection status (connected / not connected / no bot) without requiring any additional navigation.
- **FR-010**: The operator join page MUST display the Practice Arena match with a direct join action.
- **FR-011**: The operator join page MUST display the next auto-match with its start time and a direct join action.
- **FR-012**: The operator join page MUST display the user's active and upcoming games (if any) with links to the game viewer.
- **FR-013**: The "Play now →" link on the Agent Ludum homepage (`/`) MUST link to `/play` instead of the spectator lobby.
- **FR-014**: The operator join page MUST be accessible to unauthenticated users, showing a sign-in prompt rather than an error.
- **FR-015**: Joining from the operator join page MUST use the existing join form and bot selection flow; no new join path is needed.
- **FR-016**: The spectator lobby (`/games/hoard-hurt-help`) MUST show auto-matches and the Practice Arena in the upcoming section alongside admin-created games.

---

## Success Criteria

- **SC-001**: A first-time bot operator with a connected bot can join the Practice Arena and watch their bot play within 60 seconds of arriving at `/play`.
- **SC-002**: At any time of day, at least one joinable match (Practice Arena or auto-match starting within 30 minutes) is visible on the operator join page.
- **SC-003**: The operator join page answers "what do I do next?" for three user states without requiring any additional navigation: not signed in, signed in with no bot, signed in with a connected bot.
- **SC-004**: Auto-matches start within 60 seconds of their scheduled boundary time whether zero or many humans joined.
- **SC-005**: No admin action is required to keep the Practice Arena or auto-match schedule running; both recover automatically after a server restart.
- **SC-006**: The existing spectator lobby, game viewer, join form, and admin match creation are unaffected by this feature.

---

## Key Entities

**Match** (existing, new fields needed):
- `match_kind`: enum — `manual` (existing) | `practice_arena` | `auto_scheduled`
- `sim_fill_mode`: enum — `none` (existing) | `on_first_human_join` (Practice Arena) | `at_start_time` (auto-match)
- (All other match fields unchanged)

**MatchScheduler** (new, server-side component):
- Poller that runs on a short interval (suggest every 30 seconds)
- Checks: does a Practice Arena exist? If not, create one.
- Checks: is it a 30-minute boundary? Is there an open auto-match? If not, create one.
- Checks: any auto-match past its start time with unfilled slots? Register Sims and start it.

---

## Assumptions

- Sim bots (Preset Sims) are already registered in the DB and available to be added as players at match creation time. The spec assumes this is workable without changes to the Sim bot system.
- The existing game poller/scheduler (used for auto-starting games at their scheduled time) can be extended to run the new checks — no new background process is required.
- The Practice Arena default size is 5 total players (1 reserved for the first human join; 4 Sims pre-registered). This is configurable in code and can be tuned after launch.
- `/play` is a new top-level route (not under `/games/hoard-hurt-help/`) because it is game-agnostic from the operator's perspective — they want to play, not navigate a game hierarchy.
- Auto-match interval is 30 minutes and is a server constant for now; no admin UI to change it is in scope for this feature.
