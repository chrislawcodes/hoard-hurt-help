# Feature 007 — Two-Phase Turns with Private Bot Reasoning ("Negotiate-then-Act")

- **Feature branch**: `feat/two-phase-negotiation`
- **Created**: 2026-06-01
- **Status**: Draft (revised after senior-TL adversarial review)
- **Input**: Restructure every Hoard-Hurt-Help turn into a public **talk** phase followed by an **act** phase (replacing the single-phase turn where message and action are submitted together), and add a per-phase **"thinking"** field that is rendered on the human viewer but is kept out of every programmatic channel a bot can call.

## Background & Motivation

Today a bot submits its public message and its action **together**, blind and simultaneously. Two problems follow:

1. **Talk can't negotiate.** A message can only ever be a one-way signal about a *future* turn — you can't propose a deal and see the response before you commit. In practice bots narrate their own move instead of bargaining.
2. **Coordination is off-by-one.** Two bots that want to cooperate can't agree to act on the *same* turn, so they ping-pong and never lock in a joint move.

Splitting the turn into **talk → act** lets bots say their piece, *see what everyone else said*, and then choose a move in light of it — real negotiation within a single turn. Adding a **thinking** field exposes the gap between what a bot *says* and what it privately *intends*, which is the headline value for human spectators.

### Threat model for "thinking" (revised after review)

A bot must not gain an unfair advantage by autonomously reading rivals' reasoning **during** a game. The bot's deliberate channels are the **agent HTTP API** and the **MCP tools** it calls. The earlier "spectators see it, bots never" framing was not enforceable, because the spectator JSON API and the public viewer are reachable by anything, and the MCP `get_game_state` tool proxies the public spectator endpoint straight to bots.

**Accepted model for this version:** thinking is rendered **only in the server-rendered human viewer/analysis HTML**. It is kept out of every programmatic channel (the agent HTTP API, all MCP tools, and the spectator JSON API). The residual risk — a bot operator wiring a custom HTML scraper to lift reasoning mid-game — is **accepted and deferred**; a full lockdown (human-auth gating or reveal-after-completion) is future work.

## User Scenarios & Testing

### User Story 1 — Negotiate, then act (Priority: P1)

As a **game**, each turn must run a public talk round (everyone broadcasts a message, no action) and then an act round (everyone chooses an action after seeing all the messages), so bots can coordinate or bluff within the same turn and the resulting moves reflect the negotiation.

**Why this priority**: This is the core structural change. Without it, nothing else in the feature exists.

**Independent Test**: Run a full game and confirm every turn resolves as a talk round (messages recorded, no score change) immediately followed by an act round (moves recorded, scores change), and that a bot's action can reference another bot's just-said message.

**Acceptance Scenarios**:

1. **Given** a turn opens, **When** the talk phase is active, **Then** each active player may submit exactly one public message and no score changes occur.
2. **Given** all active players have submitted a talk message, **When** the talk phase resolves, **Then** every message is revealed to all players and spectators (a `turn_talked` event fires) and the act phase opens.
3. **Given** the act phase is active, **When** a player fetches its turn, **Then** the payload includes every talk-phase message from that turn.
4. **Given** the talk phase resolved early because all submitted, **When** a slower player submits a talk message after resolution, **Then** it is rejected (stale/closed phase).
5. **Given** all active players have submitted an action, **When** the act phase resolves, **Then** scores update using the exact same payoff rules as before (HOARD +2, HELP +4 to target, HURT −4 to target, mutual-help bonus, floor at 0).
6. **Given** the same set of actions as a single-phase game, **When** the act phase resolves, **Then** the per-player score deltas are identical to the old single-phase resolution.

### User Story 2 — Thinking on the viewer, off the bot channels (Priority: P1)

As a **human watching the viewer**, I can read each bot's private reasoning behind both what it said and what it did; as a **competing bot**, none of the tools or APIs I call ever return another bot's reasoning.

**Why this priority**: This is the second headline of the feature *and* it is security-sensitive — handing reasoning to a bot through its own API/tools would let it read rivals' plans. The programmatic channels must be clean from day one.

**Independent Test**: Play a game; confirm the human viewer/analysis HTML surfaces each bot's thinking for both phases, and confirm that the agent HTTP API, every MCP tool, and the spectator JSON API all return **no** thinking for any player.

**Acceptance Scenarios**:

1. **Given** a bot submits a talk message with thinking, **When** any bot calls the agent API, an MCP tool, or the spectator JSON API, **Then** the response contains no thinking for any player, including the requester's own.
2. **Given** a bot submits an action with thinking, **When** any of those programmatic channels is called, **Then** the response contains no thinking.
3. **Given** a finished or in-progress turn, **When** a human loads the viewer/analysis page, **Then** each bot's reasoning for both phases is present in the rendered HTML.
4. **Given** a `/message` or `/submit` request carrying thinking, **When** the server logs the request or returns an error, **Then** the thinking text appears in neither the logs nor the error body.
5. **Given** a bot submits no thinking, **When** the phase resolves, **Then** the submission is valid and the viewer shows blank reasoning.

### User Story 3 — Viewer presents talk, act, and reasoning (Priority: P2)

As a **human spectator**, the game viewer and analysis pages present, for each turn, the talk round and then the act round, with each bot's reasoning available but out of the way.

**Why this priority**: The data is useless without a way to read it, but the underlying capture/segregation (US1, US2) must be right first.

**Independent Test**: Open the viewer for a finished game; confirm each turn shows the talk round then the act round, that messages and moves are the default visible content, and that each bot's reasoning is collapsed by default and expandable per bot.

**Acceptance Scenarios**:

1. **Given** a resolved turn, **When** a spectator views it, **Then** the talk round (messages) is shown, then the act round (moves and score deltas).
2. **Given** a bot's message or move, **When** the spectator has not expanded reasoning, **Then** the reasoning is hidden behind a per-bot toggle and the public content is shown by default.
3. **Given** a live game, **When** the talk phase resolves, **Then** the live viewer reveals the talk messages at that moment (driven by the `turn_talked` event), not only when the act phase resolves.

### Edge Cases

- **Missed talk phase** → the player defaults to an **empty public message**; the player still participates in the act phase.
- **Missed act phase** → the player defaults to **HOARD** (unchanged from today).
- **Nobody submits in the talk phase before the deadline** → all messages default to empty and the act phase opens normally.
- **Late submission after early resolve** → a talk message after the talk phase resolved, or an action after the act phase resolved, is rejected.
- **Duplicate submission** → a second talk message, or a second action, for the same player in the same phase is rejected; the first valid one stands.
- **Player leaves between phases** → a player who leaves mid-turn is excluded from the resolve-early quorum for the remaining phase(s) of that turn, their missing submission defaults (empty message / HOARD), and they take no part in subsequent turns.
- **Live reveal** → talk messages reach live spectators when the talk phase resolves, via a dedicated `turn_talked` event.
- **Mid-game restart** → on resume the loop knows which phase (talk or act) it died in and continues from there without double-revealing or double-resolving. (Cross-version v1→v2 resume is out of scope — see Assumptions.)
- **A bot tries to read rival reasoning** → no agent API response, no MCP tool result, and no spectator JSON response carries thinking; only the rendered HTML does.

## Requirements

### Functional Requirements

- **FR-001**: Each turn MUST run a talk phase and then an act phase, in that order.
- **FR-002**: In the talk phase, each active player MUST be able to submit exactly one public message and MUST NOT take a game action.
- **FR-003**: The talk phase MUST resolve as soon as every active (non-left) player has submitted a message, or when the talk deadline passes, whichever comes first.
- **FR-004**: When the talk phase resolves, all talk-phase messages MUST be revealed to every player and spectator, and a `turn_talked` event MUST be broadcast for live viewers.
- **FR-005**: In the act phase, each active player MUST be able to submit one action and target, and the act-phase turn payload MUST include all of that turn's talk-phase messages.
- **FR-006**: The act phase MUST resolve as soon as every active player has submitted an action, or when the act deadline passes, and the payoff calculation MUST be unchanged from the current rules.
- **FR-007**: Each player MUST be able to attach an optional "thinking" rationale to both its talk submission and its act submission.
- **FR-008**: Thinking MUST be rendered on the human viewer and analysis pages (server-rendered HTML), available to anyone loading those pages.
- **FR-009**: Thinking MUST NOT appear in any programmatic channel a bot can call: the agent HTTP API (`/turn`, `/next-turn`, history, chat, opponent history, and any agent detail route), **any MCP tool** (including `get_game_state`, `get_chat`, `get_turn_detail`, `get_opponent_history`), or the **public spectator JSON API**.
- **FR-010**: A player that does not submit in the talk phase by its deadline MUST default to an empty public message; a player that does not submit in the act phase by its deadline MUST default to HOARD.
- **FR-011**: The turn payload to a bot MUST report the current phase ("talk" or "act") and provide a phase-appropriate submission token, and a talk submission and an act submission MUST be distinct operations (at most one of each per player per turn).
- **FR-012**: A submission to the wrong phase, with a stale token, after the phase has resolved, or a duplicate, MUST be rejected; the first valid submission per phase is authoritative.
- **FR-013**: The spectator viewer and analysis pages MUST present, per turn, the talk round followed by the act round, with each bot's reasoning collapsed by default and expandable per bot.
- **FR-014**: The bot runners shipped with the project MUST play both phases — a message in the talk phase, an action in the act phase — and MAY supply a thinking rationale in each.
- **FR-015**: On a mid-game restart, the turn loop MUST resume in the correct phase of the turn it stopped on, without re-revealing messages or double-applying payoffs.
- **FR-016**: A player who leaves mid-turn MUST be excluded from the resolve-early quorum for the remaining phase(s) of that turn and from all subsequent turns; their missing submissions default per FR-010.
- **FR-017**: The server MUST NOT write thinking to request/access logs or error responses; the request bodies of `/message` and `/submit` MUST be treated as sensitive (not logged verbatim).

### Success Criteria

- **SC-001**: In a completed game, 100% of resolved turns consist of a talk round followed by an act round.
- **SC-002**: Across the agent HTTP API, every MCP tool, and the spectator JSON API, **zero** responses contain any player's thinking text (test-verified over all three channel types); the rendered viewer HTML **does** contain it.
- **SC-003**: A human can read any bot's reasoning for both its message and its move on the viewer/analysis pages.
- **SC-004**: A talk phase causes no score change; the act-phase payload contains every talk message of that turn; a talk submission after early resolve is rejected; and within a single turn a bot's action can respond to another bot's message — demonstrated by at least one same-turn mutual-help pair (+8 each).
- **SC-005**: Given an identical set of actions, the per-player score deltas after the act phase are identical to the legacy single-phase resolution (payoff parity).
- **SC-006**: Thinking text never appears in server logs or error envelopes for a `/message` or `/submit` request that carried it.

### Key Entities

- **Turn** — carries a phase indicator (talk | act) and a deadline window for each phase. One turn still maps to one (round, turn) slot.
- **Talk message** — one per player per turn: a public message plus an optional thinking rationale (the rationale is stored but only ever rendered to HTML).
- **Action submission** — one per player per turn: action, optional target, score delta, plus an optional thinking rationale (stored, HTML-only). Extends today's submission; the message it used to carry now lives in the talk message.

## Assumptions

- **One talk round per turn.** Multi-round back-and-forth negotiation is out of scope.
- **Thinking visibility**: rendered only in the human viewer/analysis HTML; kept out of the agent API, all MCP tools, and the spectator JSON API. A bot scraping the HTML is an accepted, deferred risk; full lockdown is future work.
- **Deadlines**: the existing per-turn deadline value is reused for each phase, so total per-turn wall-clock (and model cost — ~2 calls per bot per turn) can be up to roughly double. A separate talk deadline is a later option.
- **Deploys happen with no ACTIVE games** (operational assumption). Therefore no v1→v2 in-flight migration is needed; the migration only has to handle not-yet-started and completed games, and the within-game phase resume covers restarts of two-phase games.
- **Missed phases**: empty message for a missed talk, HOARD for a missed act.
- **Replacement, not opt-in**: the single-phase structure is replaced outright for the PD game (the only module today).
- **Legacy completed games still render** (they have a message on `turn_submissions.message` and no `turn_messages`); the viewer falls back to it.

## Constitution Check (CLAUDE.md)

- **Data segregation** — thinking is kept off every programmatic bot channel (agent API, MCP tools, spectator JSON); enforced by FR-009 and the SC-002 multi-channel leak test, plus log redaction (FR-017/SC-006). The "never to bots" guarantee is explicitly scoped to programmatic channels; HTML scraping is a documented, accepted residual risk. **PASS** (with the scope made honest after review).
- **Async consistency, full type annotations, no suppressions, specific exceptions** — apply to the implementation. **PASS.**
- **Testing** — new resolution/segregation/resume logic and the migration are covered (SC-001…SC-006); migration passes `tests/test_migrations.py`. **PASS.**

Overall: **PASS** — the segregation requirement is now scoped to what is actually enforceable, with the residual risk documented and deferred per the owner's decision.
