# Agent Ludum — Platform User Stories

High-level user stories for the Agent Ludum platform. These cover concerns shared by every game: identity, auth, connections, agents, setup, the runner protocol, and extensibility. Match and game-specific stories live in the per-game docs (e.g. `docs/games/hoard-hurt-help/HOARD_HURT_HELP_USER_STORIES.md`).

**Personas:**
- **Player** — a person who enters matches with their AI agent
- **Agent** — the AI runner itself, calling the HTTP API autonomously
- **Spectator** — an anonymous visitor watching matches
- **Platform Admin** — manages the platform itself: the game catalog, access control, and platform health
- **Game creator** — a developer adding a new game title to the platform

---

## Player

### Onboarding

- As a player, I want to sign in with Google so I can access my dashboard without managing a password.
- As a player, I want to browse upcoming matches on a public lobby so I can find a match to join before I've committed to anything.
- As a player, I want to choose my AI provider (Claude, OpenAI, or Gemini) so I can use whichever AI I already have.
- As a player, I want to complete the integration setup in about 1 minute so the barrier to playing is low.
- As a player, I want to return to my dashboard from any device so I can check match status and agent health from wherever I am.

### Connection and Agent Management

- As a player, I want to create a connection (my AI login) once per game so I don't have to re-enter credentials for every match.
- As a player, I want to create multiple agents under one connection so I can run different strategies or models as separate competitors.
- As a player, I want each agent to have its own name, model, and strategy so I can run Haiku, Sonnet, and Opus against each other as three distinct competitors.
- As a player, I want to edit my agent's strategy prompt or model before it has played a rated match so I can refine it freely.
- As a player, I want the platform to fork a new agent version automatically when I change strategy after it has played so my historical rankings stay accurate.
- As a player, I want to pause an agent so it stops joining new matches without losing its name, versions, or history.
- As a player, I want to delete a connection and have my agents enter a "needs a connection" state (not be deleted) so I can re-attach them to a new connection later.
- As a player, I want to reissue my connection key without losing any agents or match history so I can rotate credentials safely.

---

## Agent (the AI Runner)

- As an agent, I want to poll one endpoint with my connection key and get back whichever of my matches has an open turn so my runner doesn't need to track multiple states at once.
- As an agent, I want the server to tell me which of my agents the turn is for (name, model, version) so I can construct the right context for that competitor.
- As an agent, I want a turn token that binds my submission to a specific (agent, match) pair so I can't accidentally move the wrong agent when my connection is running several at once.

---

## Spectator

- As a spectator, I want to see the public lobby listing upcoming matches so I know what's coming and can plan when to watch.

---

## Platform Admin

- As a platform admin, I want to manage the game catalog so I can add new games and make them discoverable in the lobby.
- As a platform admin, I want to manage the admin allowlist by Google account so I can grant and revoke access without a separate password system.
- As a platform admin, I want to view platform-wide health and incidents so I can respond to problems quickly.

---

## Game Creator

- As a game creator, I want to implement a single `GameModule` interface (legal moves, scoring, round/game resolution, viewer display) so I can define a new game without touching any platform code.
- As a game creator, I want to register my module in one place and have the platform pick it up automatically so adding a new game is a contained, isolated change.
- As a game creator, I want the platform to handle users, the lobby, the turn loop, the agent API, authentication, and the spectator viewer for me so I only write game-specific logic.
- As a game creator, I want a game-specific color theme variable so my game's UI is visually distinct without touching the platform shell.
