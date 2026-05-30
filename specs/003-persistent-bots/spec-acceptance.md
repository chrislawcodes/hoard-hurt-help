# Acceptance Criteria: Persistent Bots

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Create a bot and get a stable credential once | P1 |
| US-2 | Connect once, play every game the bot is in | P1 |
| US-3 | Enter a bot into a game without a new credential | P1 |
| US-4 | Reusable strategy profiles | P2 |
| US-5 | Control panel: live status and kill switch | P2 |
| US-6 | Concurrency caps | P2 |
| US-7 | Stall safety surfacing | P3 |

## Acceptance Scenarios

### US-1
- Given a signed-in user with no bots, When they create a bot with a valid name, Then the system issues a `sk_bot_<hex>` credential, shows it exactly once with a ready-to-paste snippet, and stores only a hash.
- Given a bot whose credential was already shown, When the owner revisits the bot's page, Then the plaintext is not shown again — only a "reissue" action.
- Given an existing bot, When the owner reissues, Then a new credential is shown once, the previous credential is immediately rejected, and the owner is warned connected clients must re-paste.
- Given a user, When they create a second bot, Then it gets its own independent credential, listed separately.

### US-2
- Given a connected bot in one or more active games, When it asks for its next turn, Then it receives the single most urgent open turn (nearest deadline) incl. which game and everything needed to act.
- Given no game awaits it, When it asks for its next turn, Then it gets a clear "nothing waiting" response with retry guidance — not an error.
- Given an open turn in a game, When it submits for that game, Then the system resolves which player slot that is — without any credential beyond the connection credential.
- Given two games each with an open turn, When it plays the more urgent and asks again, Then the next request returns the second game's turn.

### US-3
- Given a user with a bot and an open game, When they enter the bot, Then a player is created with no new credential and no client reconfiguration.
- Given two bots, When both are entered under distinct in-game names, Then both players are created and driven by their own connections.
- Given a bot already in a game, When entering the same bot again, Then a duplicate is prevented with a clear message.
- Given an in-game name already taken, When trying to reuse it, Then it is rejected as taken.

### US-4
- Given a signed-in user, When they create a named profile, Then it is saved and listed for reuse.
- Given several profiles, When one is marked default, Then entry seeds from it unless another is chosen.
- Given an existing profile, When edited, Then later entries use new text and players already in games keep their copy.
- Given a profile selected at entry, When the player is created, Then its strategy is a copy seeded from the profile.

### US-5
- Given a bot in games, When the owner opens the panel, Then they see each game, its state, the bot's last action time, and current score.
- Given an active bot, When paused, Then it stops being served new turns; resuming restores play.
- Given a bot in a registering game, When pulled out, Then its slot is removed and the seat freed.
- Given a bot in a started game, When pulled out, Then the engine's existing leaving-player rule applies and the consequence is surfaced.

### US-6
- Given a bot at its max concurrent games, When entering another, Then refused with a message naming the cap.
- Given the platform at max concurrent active games, When starting/entering beyond it, Then refused with a reason.
- Given a game at max players, When another bot enters, Then refused as full.

### US-7
- Given a bot missing several consecutive turns, When the owner views the panel, Then it is flagged stalling with the missed count.
- Given a stalling bot crossing the threshold, Then the system auto-pauses it or prominently recommends pausing, recording why.

## Success Criteria
- SC-001: No-bot → bot-taking-a-turn with exactly ONE credential paste; additional games need zero further pastes/client changes.
- SC-002: A bot in N games is driven by one repeating "get next turn → act → repeat" loop with no reconfiguration.
- SC-003: Reissuing a credential rejects the previous one on the next request.
- SC-004: Auth does not slow down as bots/players grow (no full-table scan).
- SC-005: Owner can see every game a bot is in and when it last acted, and stop it from new turns in one action.
- SC-006: Exceeding a per-bot or platform cap is refused with a message naming the cap; no over-cap entry succeeds.
- SC-007: Two agents in one game is achievable by running two bots, each acting independently.

## Key Constraints
- **One player per (bot, game)** — *Why: keeps `submit_action(game_id)` unambiguous; users field multiple agents via multiple bots.*
- **Fresh start, no migration** — *Why: confirmed; the `sk_game_` path is removed, not kept; 0003 clears throwaway game data.*
- **Indexed credential lookup (sha256), not a full scan** — *Why: SC-004; argon2's slow KDF is pointless on 192-bit random tokens.*
- **Strategy copied at entry** — *Why: editing a profile must never mutate a game in progress.*
- **Paused bots served no turns** — *Why: the kill switch must actually stop play.*
- **No suppressions / full types / async DB / no bare except** — *Why: project constitution; enforced by preflight (ruff, mypy, pytest).*
- **Auto-join is out of scope** — *Why: deferred to a later phase; only a clean data-model seam is required now.*
