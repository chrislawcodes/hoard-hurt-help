# Feature Specification: Live Connection Handshake for Bot Onboarding

**Feature branch**: `005-bot-onboarding-handshake`
**Created**: 2026-05-30
**Status**: Draft
**Input**: After a first-time operator creates a bot and copies the paste-once setup message, the bot detail page gives no feedback — they can't tell if their AI connected, and a freshly connected bot sits in zero games doing nothing visible. Close the loop from "credentials issued" to "confirmed playing" with a real-time status panel on the setup page.

---

## Summary

The bot detail / setup page (`GET /me/bots/{id}`) issues a connection code inside a copy-paste setup message and explains how to wire up a runner or MCP client. It does this well. But the page is **static after the copy**: the operator pastes the message into their AI and then has no idea whether it worked. Worse, a brand-new bot is in **no games**, and the runner only plays games the operator has entered — so even a flawless connection produces nothing visible, and the page offers only a small "Browse the lobby" link buried below an empty Games list.

This feature adds a **live status panel** to the setup page that confirms, in real time, that the bot connected; guides the operator to the one remaining step (get it into a game); and celebrates the first move with a link to watch it. It turns "I pasted something and I hope it worked" into a visible, guided path to the bot's first turn.

The primary user is the **first-time operator**; the returning operator is secondary and must not be slowed down (the panel quiets once a bot is established).

**Confirmed scope (this effort):** the live status panel and its states on the bot detail page, the real-time signal that powers it (first connection + first move), the supporting additive data, and the smaller copy/safety fixes that sit alongside it (key-safety reminder, empty-Games copy).

**Out of scope:** redesigning the setup message itself or the per-client snippets; auto-joining a bot into a game without the operator's action (the panel *links* to joining; one-click auto-join is a possible later enhancement); changes to how the runner or MCP clients connect; the lobby or game-viewer designs.

---

## User Scenarios & Testing

### User Story 1 - Confirm the bot connected, live (Priority: P1)

As a first-time operator who just pasted the setup message, I see the page confirm — without reloading — that my bot connected, so I know my paste actually worked.

**Why this priority**: This is the core miss today. Without visible confirmation, the operator is left guessing, which is where first-timers abandon. This is the floor of "confirmed playing."

**Independent Test**: Create a bot, then make one authenticated agent call using its connection code (as the runner/AI would). The bot detail page, left open, flips from "Waiting for your bot to connect…" to a "✓ Connected" state with no manual reload.

**Acceptance Scenarios**:
1. **Given** a freshly created bot whose detail page is open, **When** nothing has connected yet, **Then** the page shows a calm "Waiting for your bot to connect… keep your AI running" state.
2. **Given** that page is open, **When** the bot makes its first authenticated agent call, **Then** the page updates in place to a "✓ Connected" state within a few seconds, no reload.
3. **Given** the bot has connected before, **When** the operator opens the detail page later, **Then** it shows the connected state directly (the confirmation is durable, not just a one-time animation).

---

### User Story 2 - Guided from connected to playing (Priority: P1)

As a first-time operator whose bot just connected, I'm told the one remaining step — get the bot into a game — and given a direct way to do it, so a connected-but-idle bot is never a dead end.

**Why this priority**: A connected bot in zero games produces nothing visible. Without this bridge, even a successful connection feels like failure. This is the step the current page omits.

**Independent Test**: With a connected bot that is in no games, the status panel shows a "last step: get it into a game" message with a prominent action that leads to the join path; the empty Games section carries the same message instead of a buried link.

**Acceptance Scenarios**:
1. **Given** a connected bot in no games, **When** I view the panel, **Then** it says the last step is to get the bot into a game and shows a primary "Join a game →" action.
2. **Given** a connected bot in no games, **When** I look at the Games section, **Then** its empty state reads "Connected but not in a game yet — that's the last step. Join a game →", not a generic "no games" line.
3. **Given** I follow the "Join a game" action, **When** I land on the join path, **Then** I can enter this bot into an open game.

---

### User Story 3 - See the first move (the win) (Priority: P1)

As a first-time operator, when my bot takes its first move I see the page say so and offer to watch it live, so the onboarding ends on a clear success.

**Why this priority**: The first visible move is the payoff that proves "confirmed playing." It's the moment that converts a nervous first-timer into a believer.

**Independent Test**: With the bot in a game, have it submit its first action. The detail page, left open, advances to a "✓ made its first move — Watch it live →" state linking to that game's viewer.

**Acceptance Scenarios**:
1. **Given** a connected bot now in a game that has not yet moved, **When** I view the panel, **Then** it shows "✓ In '[game name]'. Waiting for its first move…".
2. **Given** that bot, **When** it submits its first action, **Then** the panel updates in place to "✓ [bot name] just made its first move. Watch it live →" linking to the game viewer.
3. **Given** the bot has already moved in a past game, **When** I open the detail page, **Then** the panel reflects the "playing"/established state rather than re-running the first-move celebration.

---

### User Story 4 - Failed connection is caught (Priority: P2)

As an operator whose paste went wrong, I see a clear message that the connection failed and how to fix it, instead of waiting forever on "Waiting…".

**Why this priority**: A wrong or stale code is a common first-timer error. Silent waiting is the worst outcome; a clear recovery path prevents abandonment. Not P1 only because the happy path must exist first.

**Acceptance Scenarios**:
1. **Given** a bot detail page open, **When** an authenticated call is attempted with an invalid/stale code for this bot, **Then** the panel shows "We saw a failed connection (invalid code). Reissue and paste the new message." with a reissue affordance.
2. **Given** the operator reissues from that state, **When** they paste the new message and the bot connects, **Then** the panel proceeds to the connected state normally.

---

### User Story 5 - Don't lose the key (Priority: P2)

As a first-time operator, I'm reminded the code shows only once and given an easy way to get a fresh one, so fumbling the copy isn't a dead end.

**Why this priority**: The paste-once model is correct for security but unforgiving; a small reminder and a nearby reissue prevent a stuck state. Secondary to the live confirmation.

**Acceptance Scenarios**:
1. **Given** the fresh-key setup message is shown, **When** I read near it, **Then** a quiet line notes the code won't be shown again and points to reissue if it's lost.
2. **Given** I lost the code, **When** I reissue, **Then** a new setup message is shown and the old code stops working.

---

### User Story 6 - Don't slow the returning operator (Priority: P3)

As a returning operator managing an established bot, the onboarding panel doesn't dominate the page or nag me, so the screen stays fast for repeat use.

**Why this priority**: Protects the secondary user. The first-timer wins, but the design must not patronize people who already know the ropes.

**Acceptance Scenarios**:
1. **Given** a bot that has connected and played before, **When** I open its detail page, **Then** the onboarding panel is absent or collapsed to a quiet status line, not a large waiting/celebration block.

---

## Edge Cases

- **Page opened before any connection** → calm "Waiting…" state, not an error; it must be obvious the operator should leave their AI running.
- **Connection happens while the page is closed** → on next open, the page shows the correct current state (connected / in a game / playing). Live push is an enhancement over a correct first paint, never a replacement for it.
- **Bot connects but the operator never joins a game** → the panel stays on the "last step: join a game" message indefinitely; it never silently looks "done".
- **Bot is paused** → the panel reflects reality (e.g. not "waiting to connect" for a paused, already-established bot); paused state takes precedence over onboarding nudges.
- **Reissue after connecting** → the old code failing must not be misread as the bot disconnecting; reissue is an operator action, distinct from a bad-key error.
- **Multiple browser tabs / devices open on the same bot** → each reflects the same state; a stale tab should not show "Waiting…" after the bot has connected (correct first paint covers this on reload).
- **Spectators / other users** → connection status is private to the bot's owner; it must never leak on public pages.
- **No open games to join** when the operator follows the prompt → the join path handles "nothing to join right now" gracefully (out of scope to build here, but the prompt must not lead to a dead end).

---

## Requirements

### Functional Requirements

- **FR-001**: The bot detail page MUST show a connection status panel whose state reflects the bot's real progress: waiting-to-connect, connected, in-a-game-awaiting-first-move, first-move-made/playing, and failed-connection.
- **FR-002**: When the bot makes its first successful authenticated agent call, the open detail page MUST update to the connected state without a manual reload, within a few seconds of the call.
- **FR-003**: When the bot submits its first action, the open detail page MUST update in place to the "first move made" state with a link to that game's viewer.
- **FR-004**: The page MUST render the correct current state on first paint (load), independent of any live update — a reload always shows the truth.
- **FR-005**: In the connected-but-not-in-a-game state, the panel MUST present the next step ("get it into a game") with a primary action that leads to the join path.
- **FR-006**: The Games section's empty state MUST tell a connected operator that joining a game is the last step (replacing the generic "no games" copy), and link to the join path.
- **FR-007**: When an authenticated call with an invalid/stale code for this bot is detected, the panel MUST show a failed-connection message with a reissue affordance; this MUST be visibly distinct from a deliberate reissue.
- **FR-008**: Near the fresh-key setup message, the page MUST show a quiet reminder that the code is shown only once and how to reissue if lost.
- **FR-009**: For a bot that has already connected and played, the onboarding panel MUST be absent or reduced to a quiet status line — it MUST NOT show the large waiting/celebration block.
- **FR-010**: Connection status MUST be visible only to the bot's owner and MUST NOT appear on public pages.
- **FR-011**: The feature MUST preserve the paste-once credential model — the plaintext code is never re-rendered or recoverable after issue.
- **FR-012**: The panel MUST be legible and operable on a phone (single column, full-width actions) and MUST NOT rely on color alone to convey state (icon/text carry meaning too).
- **FR-013**: Any new persisted data MUST be added via an additive, dry-run-safe migration that requires no backfill (absence of a value = "never connected") and is verified by row-count check post-migration.

### Key Entities

- **Bot connection signal**: the persisted facts that let the page show the truth on first paint — at minimum "has this bot ever connected" and "has it ever moved" (e.g. first-seen / first-move markers on the bot, or values derived from existing player/turn/agent-call data). Owner-scoped. Additive only.
- **Per-owner live event**: a real-time notification, scoped to the bot's owner, emitted when the bot first connects and when it first moves, so an open detail page can update without reload. Carries no secret.

---

## Success Criteria

- **SC-001**: A first-time operator who pastes the setup message and leaves their AI running sees on-page confirmation that the bot connected, without reloading or navigating away.
- **SC-002**: From the setup page, a connected operator can always tell what to do next; a connected-but-idle bot never presents as a dead end or a finished state.
- **SC-003**: The operator can get from "just connected" to "watching my bot's first move" without having to leave the setup page to work out the steps themselves.
- **SC-004**: A failed paste (bad/stale code) results in a clear on-page recovery message rather than indefinite waiting.
- **SC-005**: A returning operator opening an established bot is not shown the first-run waiting/celebration block.
- **SC-006**: No regression to credential security: the connection code remains unrecoverable after its one-time display.

---

## Assumptions

- **"Join a game" is a link, not an auto-join.** For this effort the connected-state action routes the operator to the existing join path (ideally surfacing the next open game). Automatically entering the bot into a game on the operator's behalf is deferred.
- **First connection = first successful authenticated agent call** using the bot's code (the runner/MCP client calling the agent API). First move = first submitted action. These are the natural, already-existing signals.
- **The persisted signal is additive and backfill-free**: a bot with no recorded connection is simply treated as "never connected" (NULL), which is correct for all existing bots, so no production backfill is required.
- **Live updates reuse the platform's existing real-time mechanism** (server-sent HTML/state updates already used for spectating); this feature does not introduce a new transport.
- **Detecting a bad key for *this* bot** is best-effort: it relies on an authenticated agent call presenting a code that maps to this owner/bot context but is stale. If a wrong code cannot be attributed to this bot, the panel simply stays in "Waiting…" (covered by FR-004's correct-on-reload guarantee).

---

## Constitution Check

Validated against `CLAUDE.md` (the project constitution):

- **UX / communication**: Feature is explicitly user-centered with plain-language microcopy; success criteria are user outcomes, not implementation. **PASS**
- **Async consistency**: Any new endpoints/DB access will be `async` (enforced in plan/implement). **PASS (to enforce downstream)**
- **No suppressions / type annotations**: Implementation must carry full type annotations and avoid `# type: ignore` / `# noqa`. **PASS (to enforce downstream)**
- **Testing**: New behavior (state machine, signal detection) is testable; game-logic untouched. Tests required, DB is in-memory SQLite. **PASS (to enforce downstream)**
- **Data-critical**: A migration is involved. Per the data-critical rule, it MUST be additive, dry-run-safe, backfill-free, and verified by row count post-migration (captured in FR-013 and the plan). **PASS with explicit guardrail**
- **File structure**: New code lands in `app/`; no vague filenames; status-fragment logic kept with the bots web routes or a domain-named module. **PASS (to enforce downstream)**

**Result: PASS** — proceed to technical planning.
