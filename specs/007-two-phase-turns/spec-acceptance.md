# Acceptance Criteria: Two-Phase Turns with Private Bot Reasoning

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Negotiate, then act | P1 |
| US-2 | Private thinking, spectators only | P1 |
| US-3 | Spectator viewer presents talk, act, and reasoning | P2 |

## Acceptance Scenarios

### US-1: Negotiate, then act
- Given a turn opens, When the talk phase is active, Then each active player may submit exactly one public message and no score changes occur.
- Given all active players have submitted a talk message, When the talk phase resolves, Then every message is revealed to all players and spectators and the act phase opens.
- Given the act phase is active, When a player fetches its turn, Then the payload includes every talk-phase message from that turn.
- Given all active players have submitted an action, When the act phase resolves, Then scores update using the exact same payoff rules as before.
- Given the same set of actions as a single-phase game, When the act phase resolves, Then the score outcome is identical to the old resolution.

### US-2: Private thinking, spectators only
- Given a bot submits a talk message with private thinking, Then the thinking is stored and shown to spectators but absent from every agent-facing payload.
- Given a bot submits an action with private thinking, Then the thinking is stored and shown to spectators but absent from every agent-facing payload.
- Given any agent endpoint (current turn, next-turn, history, chat, opponent history), When another player fetches it, Then the response contains no thinking data for any player, including the requester's own.
- Given a bot submits no thinking, When the phase resolves, Then the submission is still valid and spectators see blank reasoning.

### US-3: Spectator viewer presents talk, act, and reasoning
- Given a resolved turn, When a spectator views it, Then the talk round is shown, then the act round.
- Given a bot's message or move, When reasoning is not expanded, Then thinking is hidden behind a per-bot toggle and public content shows by default.
- Given a bot's reasoning toggle, When expanded, Then that bot's private thinking for that phase is shown.

## Success Criteria
- SC-001: 100% of resolved turns consist of a talk round followed by an act round.
- SC-002: Across the agent HTTP API, every MCP tool, AND the spectator JSON API, zero responses contain any player's thinking text (test-verified over all three); the rendered viewer HTML DOES contain it.
- SC-006: Thinking never appears in server logs or error envelopes for a /message or /submit request that carried it.
- SC-003: A spectator can read any bot's reasoning for both its message and its move.
- SC-004: Within a single turn, a bot's action can respond to another bot's message from that same turn — demonstrated by at least one same-turn mutual-help pair (+8 each).
- SC-005: Given identical actions, per-player score deltas equal the legacy single-phase resolution (payoff parity).

## Key Constraints
- **Thinking off every JSON channel (agent API, MCP tools, spectator JSON); HTML-only** — *Why: the spectator JSON is public and MCP get_game_state proxies it, so "spectator-only" can't hide it from bots; HTML-scrape is the accepted, deferred residual risk.*
- **No v1→v2 in-flight resume** — *Why: deploys happen with no ACTIVE games (owner decision).*
- **Payoff math unchanged** — *Why: this feature changes turn structure, not game balance; parity is how we prove that.*
- **Resume in the correct phase** — *Why: a mid-turn restart that re-reveals or double-resolves corrupts scores (see mid-deploy-game-freeze).*
- **Legacy games still render** — *Why: completed single-phase games carry their message on turn_submissions.message and have no turn_messages rows.*
- **One talk + one act per player per turn** — *Why: idempotency + fair simultaneity.*
