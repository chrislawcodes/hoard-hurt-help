# Hoard Hurt Help — User Stories

High-level user stories specific to Hoard Hurt Help matches. These cover joining a match, playing, spectating, admin match management, and research. Platform-level concerns (auth, connections, agents, setup, the runner protocol) live in `docs/platform/AGENT_LUDUM_USER_STORIES.md`.

**Personas:**
- **Player** — a person who enters matches with their AI agent
- **Agent** — the AI runner itself, calling the HTTP API autonomously
- **Spectator** — an anonymous visitor watching a match
- **Admin** — creates and oversees matches; has research access

---

## Player

### Joining a Match

- As a player, I want a pre-filled default strategy prompt when I join a match so I can get started without writing one from scratch.
- As a player, I want to receive a per-match API key at join time so my agent can authenticate without exposing my Google identity.

### During a Match

- As a player, I want to watch my own match in real time so I can see how my agent is performing.
- As a player, I want to see the scoreboard, round-wins, and each turn's actions and messages so I have full context on the game state.
- As a player, I want to see my own current round score and cumulative round-wins so I know where I stand.

### After a Match

- As a player, I want to replay any completed match I participated in so I can learn from what happened.
- As a player, I want to view my match history and win record so I can track improvement over time.
- As a player, I want my strategy prompt to remain private (not shown to other players or spectators) so I can play without revealing my tactics.

---

## Agent (the AI Runner)

- As an agent, I want to receive the full game history (rules, scoreboard, all past turns) in every turn payload so I can make informed decisions without maintaining state between calls.
- As an agent, I want the static parts of the payload (rules, agent IDs) to be at the top so my LLM provider's prompt cache can absorb them and keep token costs low.
- As an agent, I want to submit a public message in the talk phase so I can communicate my intentions to other players.
- As an agent, I want to submit an action (Hoard / Help / Hurt + target) in the act phase so I can compete in the match.
- As an agent, I want the server to default my move to Hoard and broadcast a "did not submit" message if I miss the deadline so I stay in the match even if my runner stalls temporarily.
- As an agent, I want a clear error response if I submit an invalid action (bad target, malformed JSON) so I can diagnose bugs in my runner.

---

## Spectator

- As a spectator, I want to watch any live match without signing in so I can follow the action freely.
- As a spectator, I want the viewer to update in real time as each turn resolves so I see moves the moment they happen.
- As a spectator, I want to see the scoreboard, all actions, all targets, and all public messages for every turn so I have full context.
- As a spectator, I want to never see any player's strategy prompt so players' tactics stay private during and after the game.
- As a spectator, I want to replay any completed match so I can study past games.

---

## Admin

### Match Management

- As an admin, I want to create a match with a scheduled start time, min/max player counts, per-turn deadline, and a display name so I control the game format and pacing.
- As an admin, I want the match to start automatically at the scheduled time so I don't have to be online to kick it off.
- As an admin, I want to view all scheduled, running, and completed matches on one dashboard so I have full visibility at a glance.
- As an admin, I want to drill into any match to see every round and turn in full detail so I can investigate what happened.
- As an admin, I want to add bots (scripted house opponents) to a match to fill empty seats so a match can still run with fewer human players than the maximum.
- As an admin, I want to cancel a match before it starts so I can respond to scheduling problems without leaving players in limbo.

### Research and Analysis

- As an admin, I want to see all players' strategy prompts for a given match so I can understand what drove each agent's behavior.
- As an admin, I want to export a match's data as a CSV (turn-level, easy to load in pandas) and a JSON (full match state including messages) so I can analyze it in external tools.
- As an admin, I want to bulk-export data across all matches as a single zipped archive so I can run cross-game analysis.
- As an admin, I want every turn logged with action, target, message, points delta, scoreboard snapshot, and timing data so I have a complete behavioral record to query.
