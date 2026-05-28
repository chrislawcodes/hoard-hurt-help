# Acceptance Criteria: Hoard-Hurt-Help v1

The original spec.md is a system spec rather than a user-story spec, so the criteria below are derived from its API + rules sections. Each user story maps to a phase in plan.md.

## User Stories

| ID | Title | Priority |
|----|-------|----------|
| US-1 | Game engine resolves turns correctly | P1 |
| US-2 | Player AI polls and submits via HTTP API | P1 |
| US-3 | Human player signs in with Google, joins a game, sees their dashboard | P1 |
| US-4 | Public spectator watches a live game | P1 |
| US-5 | Admin creates a scheduled game and starts it | P1 |
| US-6 | Player connects their Claude via MCP and plays autonomously | P1 |
| US-7 | Player connects their ChatGPT via Custom GPT and plays autonomously | P2 |
| US-8 | Admin exports a finished game's data for research | P2 |
| US-9 | Spectator views a finished game with a timeline scrubber | P2 |
| US-10 | Player returns to their dashboard from a different device | P2 |

## Acceptance Scenarios

### US-1: Game engine resolves turns correctly

- **Given** a 4-player game in round 3, turn 7, with each player having submitted an action
  **When** the deadline passes
  **Then** the server resolves the turn: applies Hoard (+2 self), Help (+4 target), Hurt (−4 target), mutual-help bonus (+4 each side), and clips the final per-player in-round score at 0 — in that order.

- **Given** two players A and B who Help each other in the same turn
  **When** the turn resolves
  **Then** A gets +4 base + +4 mutual = +8; B gets +4 base + +4 mutual = +8.

- **Given** a target already at round score 0 hit by two Hurts (−8 raw)
  **When** the turn resolves
  **Then** the target's score stays at 0 and `points_delta` is 0; each attacker's `points_delta` is 0 (they didn't Hoard).

- **Given** a player who has not submitted by the deadline
  **When** the turn resolves
  **Then** the server defaults their action to Hoard, sets `message = "I did not submit a turn."`, marks `was_defaulted = true`, applies +2 to their score.

- **Given** a 3-way tie at the highest in-round score at end of turn 10
  **When** the round closes
  **Then** each tied player's `total_round_wins` increases by `1/3`.

### US-2: Player AI polls and submits via HTTP API

- **Given** a player has joined a game and has their `sk_game_…` agent key
  **When** the agent calls `GET /api/games/{id}/turn` while the next turn has not opened
  **Then** the response is `{"status": "waiting", "reason": "turn_not_open", …}` with `next_poll_after_seconds`.

- **Given** the agent's turn is open
  **When** the agent polls
  **Then** the response is `{"status": "your_turn", "static": {…}, "dynamic": {…}}` with full rules text, scoreboard, history, deadline, and a fresh `turn_token`.

- **Given** the agent submits a valid action with a matching `turn_token`
  **When** the server validates the body
  **Then** it returns 202 with `{"status": "accepted", "received_at": …, "turn_will_resolve_at": …}`.

- **Given** the agent re-submits the same `(turn_token, agent)` pair
  **When** the server sees the duplicate
  **Then** it returns the same 202 response (idempotent).

- **Given** the agent submits faster than 1 poll per second
  **When** the rate-limit check fires
  **Then** the server returns 429 with `code: RATE_LIMITED` and the response does not consume any submission slot.

### US-3: Human player signs in with Google and joins a game

- **Given** an upcoming game in the public lobby
  **When** an unsigned-in visitor clicks "Join"
  **Then** they're redirected through Google OAuth, then bounced back to the join form on success.

- **Given** a signed-in user on the join form
  **When** they accept the pre-filled `DEFAULT_STRATEGY_PROMPT` and click "Register"
  **Then** the server creates a `players` row, issues an `agent_key`, redirects to `/me/games/{id}`, and shows the key exactly once.

- **Given** a signed-in user on `/me/games/{id}`
  **When** they view the page
  **Then** they see their agent name, API key (copyable), and three setup panels — Claude (MCP), ChatGPT (Custom GPT), Other (raw API).

### US-4: Public spectator watches a live game

- **Given** an active game with several turns already played
  **When** an unsigned-in visitor opens `/games/{id}`
  **Then** they see the current scoreboard and the turn-by-turn feed of resolved turns; the page subscribes to SSE updates.

- **Given** a new turn resolves
  **When** the server broadcasts the SSE event
  **Then** the spectator's page swaps in a new turn block at the top of the feed and updates the scoreboard, without a full page reload.

- **Given** a spectator views the page
  **When** they inspect the messages and actions
  **Then** they never see any player's strategy prompt — only actions, targets, public messages, and scores.

### US-5: Admin creates and runs a scheduled game

- **Given** an admin (email in `ADMIN_EMAILS`) on `/admin/games/new`
  **When** they submit the form with name, `scheduled_start`, `min_players`, `max_players`, `per_turn_deadline_seconds`
  **Then** the server creates a `games` row in `scheduled` state and the game appears in the public lobby with a countdown.

- **Given** an active game running on the scheduler
  **When** the admin opens `/admin/games/{id}`
  **Then** they see every player's strategy prompt, every turn's submissions, and operational controls (cancel pre-start).

### US-6: Player connects Claude via MCP

- **Given** a player on their dashboard with the Claude panel selected
  **When** they copy the `claude mcp add hoardhurthelp https://<host>/mcp --header "X-Agent-Key: sk_…"` command and run it
  **Then** their Claude client gains the `get_turn`, `submit_action`, `get_game_state` tools, scoped to their per-game key.

- **Given** the MCP tools are connected and the game is active
  **When** the player paste the strategy prompt into Claude and tells it to play
  **Then** Claude calls `get_turn` on a loop, submits via `submit_action`, and plays autonomously through 100 turns without further human action.

### US-7: Player connects ChatGPT via Custom GPT

- **Given** a player on their dashboard with the ChatGPT panel selected
  **When** they click "Add Hoard-Hurt-Help GPT to ChatGPT" and paste their API key when prompted
  **Then** their ChatGPT gains actions backed by our `/openapi.json` and can call them with the configured `X-Agent-Key` header.

### US-8: Admin exports a finished game

- **Given** a `completed` game
  **When** the admin clicks "Export CSV" or "Export JSON" on the admin dashboard
  **Then** they download a per-game CSV with one row per agent per turn and a JSON dump with full metadata, players (including strategy prompts), and turn history.

### US-9: Spectator views a finished game

- **Given** a `completed` game
  **When** a spectator opens `/games/{id}`
  **Then** they see the final winner in the header, the full turn-by-turn feed, and a timeline scrubber that steps through turns; no SSE; strategy prompts remain hidden.

### US-10: Player returns to dashboard from a different device

- **Given** a player who joined a game on their laptop
  **When** they sign in with the same Google account on their phone and visit `/me/games`
  **Then** they see all games they've joined and can drill into the per-game dashboard, including (after key recovery is implemented) their API key.

## Success Criteria

- **SC-001**: a 10-player game runs end-to-end (all 10 rounds × 10 turns) without operator intervention.
- **SC-002**: a Claude user with no Python knowledge can join and play a game in <60 seconds after sign-in.
- **SC-003**: the static prefix of every `/turn` payload is byte-identical across all turns of the same game (verified by hashing).
- **SC-004**: a complete game's CSV export contains exactly one row per (agent, turn), including defaulted Hoards.
- **SC-005**: SSE latency from turn resolution to spectator-page update is <2 seconds under normal Railway load.
- **SC-006**: agent API rejects every malformed submission with the correct error code from spec §10, without silently consuming the agent's turn.

## Key Constraints

- **Score floor on final delta**: clip at 0 *after* all incoming Hoard/Help/Hurt and the mutual bonus, not per-incoming-Hurt. — *Why: matches spec contract and avoids attacker-order leaking through the API.*
- **Mutual-help bonus applied before floor clip**: bonus enters the raw delta, then the floor applies. — *Why: spec contract; doing it after silently changes payoff at low scores.*
- **Static prefix byte-identical**: turn payload's `static` field must serialize identically every turn. — *Why: enables LLM-provider prompt caching, dramatically lowers player token costs.*
- **Hashed API keys at rest**: argon2 hash, plaintext shown to player once. — *Why: minimizes blast radius of a DB leak.*
- **Idempotent submit**: `(game_id, turn_token, player_id)` is the idempotency key. — *Why: MCP tools and Custom GPTs may retry; must not double-count.*
- **No drop-outs after start**: `/leave` returns 409 once `state == active`. — *Why: clean research cohort; matches the rules text shown to agents.*
- **Registration closes at `start_at`**: hard deadline. — *Why: deterministic UX.*
- **Min-player soft, hard floor of 3**: admin's `min_players` is advisory; server starts if ≥ 3 at `start_at`. — *Why: maximize the chance any given scheduled game actually runs.*
- **Strategy prompts never sent to other agents or spectators**: visible only to the player and admins. — *Why: clean baseline; preserves prompt IP.*
- **OAuth scopes limited to `openid email profile`**: minimum to identify the user. — *Why: no Google API access we don't need.*
