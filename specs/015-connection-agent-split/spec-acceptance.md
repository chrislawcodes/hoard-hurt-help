# Acceptance Criteria: Connection / Agent Split (015)

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | First-time: create an agent in one smooth flow | P1 |
| US-2 | One connection, many agents | P1 |
| US-3 | Benchmark several models on one login | P1 |
| US-4 | Bots are connectionless agents | P1 |
| US-5 | An agent is a model + strategy | P2 |
| US-6 | Manage a connection on its own page | P2 |
| US-7 | Leaderboard identity reads cleanly | P2 |
| US-8 | "Bot" is gone as the word for a user's player | P3 |

## Acceptance Scenarios

### US-1
- Given a user with zero connections and zero agents, When they start "New agent", Then the flow asks for a provider and shows a runner setup message (no "create a connection first" dead-end).
- Given the runner has connected, When the connection goes live, Then the user names the agent and chooses a model (constrained to the connection's provider), game defaulting to the only game.
- Given the agent is saved, When they land on its page, Then it shows ready + "find a match to join."
- Given a user who already has a connection, When they start "New agent", Then the flow skips provider/connect and goes straight to pick-connection + name + model + strategy.

### US-2
- Given a live connection with one agent, When the user creates a second agent on it, Then no new key is issued and no re-connect step appears.
- Given two agents on one connection in active matches, Then the single running runner plays turns for both.
- Given a paused connection, When turns come due for any of its agents, Then none play until resumed.

### US-3
- Given one connection, When agents are pinned to different models of that provider, Then each stores its own model and the runner uses each agent's model for that agent's turns.
- Given three model-variant agents with rated matches, When the leaderboard renders, Then three distinct rows each show the model they ran.
- Given an agent on a Claude connection, When picking a model, Then only Claude models are offered.

### US-4
- Given a Bot, When inspected, Then kind=bot and it has no connection link.
- Given a match needing fill, When Bots are seated, Then they take turns deterministically with no runner and no key.
- Given the leaderboard, Then Bots are clearly labeled and separable from AI agents.
- Given any `/me/connections` page, Then no Bot appears there.

### US-5
- Given an agent with a strategy, When it joins a match, Then it plays that strategy without re-typing.
- Given a match in progress, When the user edits that agent's strategy, Then the edit is blocked until the match is no longer active.
- Given a completed match, When the user later edits the strategy, Then the completed match still reflects the strategy it ran (snapshot).
- Given a strategy edit, When saved, Then the agent keeps its identity and standing.

### US-6
- Given a connection, When the key is reissued, Then a fresh setup message shows once and the old key works until the new one connects.
- Given a connection with agents in matches, When the user deletes it, Then they are warned and the action follows the block-and-explain rule.
- Given a connection page, Then it shows live runner/health status and lists the agents it powers.

### US-7
- Given a game section, Then each row corresponds to exactly one Agent.
- Given an AI agent row, Then it shows the agent's name and its model.
- Given the in-match display, Then the shown name derives from the agent's name.

### US-8
- Given the model layer, Then there is no `Bot` model class; concepts are `Connection` and `Agent`.
- Given user-facing copy, Then a user's AI player is never called a "bot"; "bot" labels only scripted opponents.
- Given the route surface, Then management lives under `/me/connections` and `/me/agents` (no `/me/bots`).

## Success Criteria
- SC-001: New user goes from nothing to a connected, match-ready agent in one uninterrupted flow.
- SC-002: Adding a 2nd/3rd agent to an existing connection needs zero re-connect steps.
- SC-003: Three model-variant agents on one connection = three labeled leaderboard rows, each driven by its own model.
- SC-004: Every Bot has no connection, never appears under connection management, yet plays and appears (labeled) on the leaderboard.
- SC-005: Editing strategy never changes a completed match's recorded strategy, and is impossible during an active match.
- SC-006: No leaderboard row / match view / management page calls a user's AI player a "bot".
- SC-007: Full preflight passes (`ruff`, `mypy app/ mcp_server/`, `pytest -q`), no suppressions.
- SC-008: No `/me/bots` route and no `Bot` model class remain.

## Key Constraints
- **Pre-launch, no data** — Why: lets us reshape + recreate the schema instead of a preserving backfill; the destructive migration is safe only because nothing is live.
- **Auth/turn-resolution is high-care** — Why: a past mid-deploy change to turn resolution froze a live game; this path needs the heaviest tests and an isolated slice.
- **kind=ai ⇒ has connection + model; kind=bot ⇒ no connection** — Why: the invariant is what keeps "a bot has no login" and "an AI agent is always powered" true.
- **Provider fixed per connection** — Why: the provider IS the login; switching it would invalidate every agent's model.
- **Model on agent, provider on connection** — Why: one login must field many models for benchmarking.
- **Implementation on hold** — Why: ~6 concurrent bot/sim/leaderboard branches overlap these files; building now means heavy conflicts.
