# Feature Specification: Persistent Bots with Paste-Once Credentials

**Feature Branch**: `003-persistent-bots`
**Created**: 2026-05-29
**Status**: Draft
**Input**: Give bot owners a stable, paste-once credential and a self-serve web control panel, so they never re-copy-paste connection config from the site to start a new game. Introduce a `Bot` entity under each user. Lay the foundation for (but do not build) automatic enrollment into new games.

---

## Summary

Today a player authenticates with a per-game key (`sk_game_<hex>`) that is bound to a single game. Starting another game means a new key, a new connection snippet, and a client restart — the owner must re-copy-paste from the site every single time.

This feature removes that friction. Each user owns one or more **Bots**. A bot has **one stable credential** (`sk_bot_<hex>`), shown once and pasted into the MCP client a single time. From then on, the owner controls everything — which games the bot plays, its strategy, whether it is paused — from a **web control panel**, and the client configuration never changes again. The bot's play loop becomes game-agnostic: it asks the server "what's my next turn?" and plays whatever is waiting, across every game it is in.

**Decision on rollout (confirmed):** Fresh start, no migration. The per-game `sk_game_` key path is replaced, not kept alongside. There are no live external bots to preserve.

**Out of scope (next phase):** automatic enrollment of bots into new games (subscriptions / auto-join). The data model must leave a clean seam for it, but the auto-join behavior is NOT built here.

---

## User Scenarios & Testing

### User Story 1 - Create a bot and get a stable credential once (Priority: P1)

As a bot owner, I sign in, create a named bot, and receive a single stable credential shown exactly once, so I can paste it into my MCP client and never need another credential.

**Why this priority**: This is the foundation. Without a stable, account-level credential there is no way to escape per-game re-pasting. Everything else builds on it.

**Independent Test**: Sign in, create a bot named "Atlas", confirm a `sk_bot_` credential is displayed once with a copyable connection snippet. Reload the page and confirm the plaintext credential is no longer shown. Reissue the credential and confirm a new one appears and the old one stops working.

**Acceptance Scenarios**:

1. **Given** a signed-in user with no bots, **When** they create a bot with a valid name, **Then** the system issues a `sk_bot_<hex>` credential, shows it exactly once with a ready-to-paste connection snippet, and stores only a hash.
2. **Given** a bot whose credential was already shown, **When** the owner revisits the bot's page, **Then** the plaintext credential is not shown again — only a "reissue" action.
3. **Given** an existing bot, **When** the owner reissues the credential, **Then** a new credential is shown once, the previous credential is immediately rejected by the server, and the owner is warned that any connected client must be re-pasted.
4. **Given** a user, **When** they create a second bot, **Then** it gets its own independent credential, listed separately under "My Bots".

---

### User Story 2 - Connect once, play every game the bot is in (Priority: P1)

As a bot owner, after pasting the credential once, my bot finds and plays its turns across all of its active games on its own, with no further client changes.

**Why this priority**: This is the payoff of US1 and the core value of the whole feature: paste once, play anything. It also forces the game-agnostic loop that makes multi-game play possible.

**Independent Test**: Connect a bot once. Enter it into two games and start both. Confirm the bot, running a single repeating loop, retrieves and submits a valid action in each game in turn, without the client config being touched between games.

**Acceptance Scenarios**:

1. **Given** a connected bot that is in one or more active games, **When** it asks the system for its next turn, **Then** it receives the single most urgent open turn (the one with the nearest deadline) including which game it belongs to and everything needed to act.
2. **Given** a connected bot for which no game currently awaits its action, **When** it asks for its next turn, **Then** it receives a clear "nothing waiting" response with guidance on when to ask again — not an error.
3. **Given** a bot with an open turn in a specific game, **When** it submits an action for that game, **Then** the system resolves which of the bot's players that game corresponds to and records the action — without the bot supplying any credential beyond the connection credential.
4. **Given** a bot in two games each with an open turn, **When** it plays the more urgent one and asks again, **Then** the next request returns the second game's turn.

---

### User Story 3 - Enter a bot into a game from the web without a new credential (Priority: P1)

As a bot owner, I enter one of my existing bots into a game by selecting it on the site; no new credential is issued and I do not re-paste anything.

**Why this priority**: This replaces the current join-issues-a-new-key flow that causes the re-paste pain. Without it, US1/US2 cannot be reached through the normal path.

**Independent Test**: With a connected bot, open a registering game, choose "play as Atlas", and confirm a player slot is created for that game, no plaintext credential is shown, and the bot can immediately be driven into that game by its existing connection.

**Acceptance Scenarios**:

1. **Given** a user with at least one bot and an open (registering) game, **When** they enter a bot into the game, **Then** a player is created for that bot in that game, with no new credential shown and no client reconfiguration required.
2. **Given** a user with two bots, **When** they enter both into the same game under distinct in-game names, **Then** both players are created and both are driven by their own connections. (A user fields multiple agents in one game by running multiple bots.)
3. **Given** a bot already entered into a game, **When** the owner tries to enter the same bot into the same game again, **Then** the system prevents a duplicate (one player per bot per game) with a clear message.
4. **Given** an in-game name already taken in that game, **When** the owner tries to use it, **Then** the system rejects it as taken.

---

### User Story 4 - Reusable strategy profiles (Priority: P2)

As a bot owner, I save named strategy profiles once and reuse them when entering games, so I don't rewrite strategy text every time.

**Why this priority**: A major quality-of-life win and part of the stated vision, but the system is usable without it (a player can carry inline strategy as today).

**Independent Test**: Create two strategy profiles, mark one as default, enter a bot into a game choosing a profile, and confirm the new player starts seeded with that profile's text. Edit a profile and confirm future entries use the new text (existing players are unaffected).

**Acceptance Scenarios**:

1. **Given** a signed-in user, **When** they create a named strategy profile, **Then** it is saved to their account and listed for reuse.
2. **Given** a user with several profiles, **When** they mark one as default, **Then** entering a bot into a game seeds the player from that profile unless another is chosen.
3. **Given** an existing profile, **When** the user edits it, **Then** players entered afterward use the new text and players already in games keep their own copy.
4. **Given** a profile is selected at entry, **When** the player is created, **Then** the player's strategy is a copy seeded from the profile (later profile edits do not retroactively change a running game).

---

### User Story 5 - Control panel: live status and kill switch (Priority: P2)

As a bot owner, I see what each of my bots is doing right now and can pause or pull a bot out, so I stay in control and can stop a misbehaving or costly bot.

**Why this priority**: Important for trust and safety, but the core paste-once play loop works without it.

**Independent Test**: With a bot in two active games, open the panel and confirm it lists both games, the bot's last action time, and current score per game. Pause the bot and confirm it stops being served new turns. Pull the bot from a registering game and confirm its player slot is removed.

**Acceptance Scenarios**:

1. **Given** a bot in one or more games, **When** the owner opens the control panel, **Then** they see each game the bot is in, its state, the bot's last action time, and its current score.
2. **Given** an active bot, **When** the owner pauses it, **Then** the system stops serving it new turns and the panel shows it paused; resuming restores normal play.
3. **Given** a bot in a registering (not yet started) game, **When** the owner pulls it out, **Then** its player slot is removed and the freed seat is available to others.
4. **Given** a bot in an already-started game, **When** the owner pulls it out, **Then** the system applies the same rule the engine uses for a leaving player today and surfaces the consequence clearly.

---

### User Story 6 - Concurrency caps (Priority: P2)

As a bot owner, I cap how many games a bot plays at once to protect my token budget; as the platform operator, I cap total concurrent games and players per game to protect the system.

**Why this priority**: Guards against runaway cost and overload. The system runs without it at small scale, but the caps are part of designing the foundation responsibly (and are required before auto-join can be safe later).

**Independent Test**: Set a bot's max-concurrent-games to 1, enter it into one game, then attempt a second and confirm the entry is refused with a clear message. As admin, set a platform max-players-per-game and confirm entries beyond it are refused.

**Acceptance Scenarios**:

1. **Given** a bot at its owner-set max concurrent games, **When** the owner tries to enter it into another game, **Then** the entry is refused with a message naming the cap.
2. **Given** the platform is at its max concurrent active games, **When** anyone tries to start/enter beyond it, **Then** the system refuses and explains why.
3. **Given** a game at its max players, **When** another bot tries to enter, **Then** the entry is refused as full (existing behavior preserved).

---

### User Story 7 - Stall safety surfacing (Priority: P3)

As a bot owner, I am told when a bot is stalling (missing turns), so I can fix or pause it before it ruins games.

**Why this priority**: Valuable safety net, especially ahead of auto-join, but not required for the foundation to deliver value.

**Independent Test**: Connect a bot, let it miss several consecutive turns, and confirm the panel flags the stall and either auto-pauses it or clearly recommends pausing.

**Acceptance Scenarios**:

1. **Given** a bot that misses several consecutive turns in a game, **When** the owner views the panel, **Then** the bot is flagged as stalling with the count of missed turns.
2. **Given** a stalling bot, **When** the configured threshold is crossed, **Then** the system either auto-pauses the bot or surfaces a prominent recommendation to pause it, and records why.

---

## Edge Cases

- **Lost credential** → owner reissues; the old credential is rejected immediately and connected clients must re-paste once.
- **Bot in zero games** asks for next turn → "nothing waiting" response with a suggested wait, never an error.
- **Multiple open turns at once** → return the single most urgent (nearest deadline); ties broken deterministically.
- **Reissue while connected and mid-game** → old credential dies instantly; the bot's connection fails until re-pasted, and the owner is warned before confirming.
- **Paused bot whose turn comes up** → treated as no submission for that turn (same as a non-responding player today); not served future turns until resumed.
- **Per-bot cap reached** → entry refused with the cap named.
- **Platform cap reached** → entry/start refused with a clear reason.
- **Duplicate entry** (same bot, same game) → prevented; one player per bot per game.
- **In-game name collision** → rejected as taken (per-game name uniqueness preserved).
- **Deleting a bot that is in active games** → blocked, or requires pulling it from those games first; never silently abandons live players.
- **Bot fielding two players in one game** → not supported via a single bot; the owner runs two bots instead (keeps "submit for this game" unambiguous).

---

## Requirements

### Functional Requirements

**Bots & credentials**
- **FR-001**: The system MUST let a signed-in user create, name, list, and delete bots they own. (US1)
- **FR-002**: Each bot MUST have exactly one stable connection credential of the form `sk_bot_<hex>`, shown in plaintext exactly once at issue/reissue and otherwise stored only as a secure hash. (US1)
- **FR-003**: The system MUST let an owner reissue a bot's credential; reissue MUST invalidate the previous credential immediately and warn that connected clients need re-pasting. (US1)
- **FR-004**: The system MUST NOT regenerate or change a bot's credential on page load or any read — only on explicit reissue. (US1)

**Authentication & resolution**
- **FR-005**: The system MUST authenticate agent/MCP requests by the bot credential, resolving it to the owning bot. The previous per-game (`sk_game_`) credential path MUST be removed (fresh start, no migration). (US1, US2)
- **FR-006**: Credential lookup MUST NOT require scanning all players/bots; it MUST use an indexed lookup (e.g. by credential prefix) so it does not degrade as bots and players grow. (US2)

**Game-agnostic play loop**
- **FR-007**: The system MUST provide a way for a connected bot to retrieve its single most urgent pending turn across ALL of its active games, including which game the turn belongs to and the full payload needed to act. (US2)
- **FR-008**: When no game currently awaits the bot's action, the retrieval MUST return a clear "nothing waiting" result with guidance on when to retry — not an error. (US2)
- **FR-009**: The system MUST let a bot submit an action for a given game and resolve, from the bot credential plus the game, exactly which player slot the action belongs to. (US2)
- **FR-010**: A bot MUST have at most one player per game, so that a game identifier unambiguously identifies the bot's player in that game. (US2, US3)

**Entering games**
- **FR-011**: The system MUST let an owner enter one of their bots into an open (registering) game from the web without issuing a new credential or requiring client reconfiguration. (US3)
- **FR-012**: The system MUST allow a user to field multiple agents in one game by entering multiple distinct bots, each under a name unique within that game. (US3)
- **FR-013**: The system MUST prevent entering the same bot into the same game twice. (US3)

**Strategy profiles**
- **FR-014**: The system MUST let a user create, edit, list, and delete named strategy profiles owned by their account. (US4)
- **FR-015**: The system MUST let a user mark one profile as default and seed a new player's strategy from a chosen (or default) profile at entry time. (US4)
- **FR-016**: A player's strategy MUST be an independent copy at entry; later edits to a profile MUST NOT alter players already in games. (US4)

**Control panel & safety**
- **FR-017**: The system MUST show, per bot, the games it is in, each game's state, the bot's last action time, and its current score. (US5)
- **FR-018**: The system MUST let an owner pause and resume a bot; a paused bot MUST NOT be served new turns. (US5)
- **FR-019**: The system MUST let an owner pull a bot out of a game, applying the existing engine rule for a leaving player when the game has already started. (US5)
- **FR-020**: The system MUST flag a bot that misses a configurable number of consecutive turns, and either auto-pause it or prominently recommend pausing, recording the reason. (US7)

**Caps**
- **FR-021**: The system MUST enforce an owner-set maximum number of concurrent games per bot, refusing entry beyond it with a clear message. (US6)
- **FR-022**: The system MUST enforce platform-level caps (maximum concurrent active games; maximum players per game), refusing entry/start beyond them with a clear reason. (US6)

**Foundation for the future (not built now)**
- **FR-023**: The data model MUST leave a clean seam for a future auto-join/subscription feature (a per-bot rule that enrolls the bot into new games automatically) without requiring a redesign of bots, players, or credentials. The auto-join behavior itself MUST NOT be implemented in this feature.

**Constitution-derived**
- **FR-024**: Credentials MUST be stored hashed and never logged in plaintext; the plaintext MUST appear only in the one-time issue/reissue response. (Security)
- **FR-025**: New game logic and data transformations introduced by this feature MUST have tests, and MUST NOT use error suppressions to pass checks. (Testing & standards)

---

## Success Criteria

- **SC-001**: A new owner can go from "no bot" to "a bot taking a turn in a game" by pasting exactly one credential, one time. Starting any additional game afterward requires zero further credential pastes or client changes.
- **SC-002**: After connecting once, a bot in N concurrent games is fully driven by a single repeating "get next turn → act → repeat" loop, with no client reconfiguration between games.
- **SC-003**: Reissuing a credential rejects the previous credential on the very next request.
- **SC-004**: Authenticating a request does not get slower as the number of bots/players grows (no full-table scan).
- **SC-005**: An owner can see, for any of their bots, every game it is in and when it last acted, and can stop it from being served new turns within one action.
- **SC-006**: Attempting to exceed a per-bot or platform cap is refused with a message that names the cap; no over-cap entry succeeds.
- **SC-007**: A bot fielding two agents in one game is achievable by running two bots, and each acts independently.

---

## Key Entities

- **User** (existing): a signed-in person (Google account). Owns bots and strategy profiles.
- **Bot** (new): owned by a User. Has a display name, one stable credential (stored hashed, with an indexed lookup handle), an enabled/paused state, an owner-set max-concurrent-games cap, and stall tracking. Connects to the MCP client once.
- **Player** (existing, modified): a bot's participation in a single game. Now belongs to a Bot (and through it a User). At most one per (bot, game). Holds the in-game name, score, and a copy of its strategy text.
- **StrategyProfile** (new): a named, reusable strategy owned by a User; one may be the default. Seeds a player's strategy at entry; not linked live to running players.
- **Game** (existing): unchanged in shape; gains platform-cap enforcement at entry/start.
- **Platform configuration** (new or extended): max concurrent active games and max players per game.
- **Subscription** (future, NOT built): a per-bot auto-enroll rule. Named here only so the model reserves a clean place for it.

---

## Assumptions

1. **One player per bot per game.** A bot has at most one slot in a given game; this keeps "submit for this game" unambiguous. Owners field multiple agents in one game by running multiple bots — matching the stated "multiple bots per game."
2. **Fresh start, no migration** (confirmed). The `sk_game_` per-game credential path is removed, not kept in parallel. Existing throwaway players can be recreated.
3. **Bots belong to a User.** Creating/managing bots requires sign-in. The non-browser direct-join path is reworked to fit the bot model (a bot is the unit that joins), rather than minting anonymous per-game keys; exact shape is a planning decision, but no new credential is shown per game.
4. **"Most urgent" = nearest deadline.** When several turns are open for a bot, the one whose turn deadline is soonest is returned first; ties are broken deterministically.
5. **Reissue is allowed at any time** (account-level credential), unlike the old pre-game-only re-key. The trade-off (it breaks live connections until re-pasted) is surfaced to the owner.
6. **Strategy is copied at entry.** Editing a profile never changes a game already in progress.
7. **Stall threshold and per-bot/platform cap defaults** are configurable; sensible defaults are chosen in planning (e.g. a low default concurrent-games cap to bound cost).

---

## Constitution Check

Validated against `CLAUDE.md` (project constitution):

- **No suppressions / type annotations / async DB / no bare except** → reflected in FR-025 and carried into planning; spec introduces no requirement that conflicts. **PASS**
- **Security (credentials)** → FR-002, FR-024 require hashed storage and one-time plaintext display, consistent with the existing argon2, show-once philosophy. **PASS**
- **Testing (game logic in `app/engine/`)** → FR-025 mandates tests for new logic; the game-agnostic turn resolution and caps are testable units. **PASS**
- **File structure (`app/` vs `mcp_server/`, no vague filenames)** → no conflict; planning will place new code by responsibility. **PASS**
- **PR workflow / one feature per branch** → work is on `003-persistent-bots`; auto-join deliberately deferred to keep the branch scoped. **PASS**

Result: **PASS** — no constitutional conflicts.
