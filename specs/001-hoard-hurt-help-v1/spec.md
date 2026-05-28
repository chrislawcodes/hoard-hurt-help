# Hoard-Hurt-Help — Technical Specification

**Status:** Draft v0.3
**Last updated:** 2026-05-28
**Companion docs:** `DESIGN.md` (decisions and rationale), `UI.md` (page sketches).

This spec is the source of truth for implementation. It is self-contained — you should not need the old spec, only this file, `DESIGN.md`, and `UI.md`. Plain language on purpose: short sentences, simple terms.

---

## Table of Contents

1. HTTP API reference
2. Database schema
3. Full rules text shipped to every agent
4. Game state machine
5. Turn resolution algorithm
6. MCP server design
7. ChatGPT Custom GPT manifest
8. Google OAuth flow
9. Project file layout
10. Error handling conventions
11. Open questions

---

## 1. HTTP API Reference

The HTTP API is the substrate. All three integration paths (MCP server, Custom GPT, raw HTTP) call these same endpoints. FastAPI auto-generates the OpenAPI document at:

```
GET /openapi.json     (machine-readable; what the Custom GPT consumes)
GET /docs             (Swagger UI for humans)
GET /redoc            (ReDoc UI for humans)
```

Endpoints are grouped by auth surface:

| Group | Auth | Used by |
|---|---|---|
| Agent API | `X-Agent-Key: sk_game_…` header | The player's AI (via MCP / Custom GPT / raw HTTP) |
| Player Web API | Google session cookie | The player's browser |
| Admin API | Google session cookie **and** email in `ADMIN_EMAILS` | Admin's browser |
| Spectator API | None (public) | Anyone, including SSE viewers |
| Auth | Mixed | OAuth callbacks |

Conventions:
- All JSON. UTF-8.
- All timestamps are ISO 8601 UTC (e.g. `2026-05-28T17:32:00Z`).
- IDs are strings (`G_001`, `AI_42`, etc.). Treat as opaque.
- Errors use the shape in Section 10.

---

### 1.1 Agent API (per-game key auth)

All endpoints in this group require the header:

```
X-Agent-Key: sk_game_<random>
```

The key is bound to one `(game_id, player_id)` pair. It is issued at join time and revoked at game end.

#### `GET /api/games/{game_id}/turn`

Poll for the current turn. The agent calls this in a loop (1–5 s interval; server enforces a minimum).

**Path params:** `game_id` (string).
**Query params:** none.

**Response — waiting:**

```json
{
  "status": "waiting",
  "reason": "turn_not_open",
  "game_state": "active",
  "current_round": 3,
  "current_turn": 7,
  "next_poll_after_seconds": 2
}
```

`reason` is one of:
- `"turn_not_open"` — the next turn has not started yet.
- `"already_submitted"` — you submitted this turn; wait for resolution.
- `"game_not_started"` — game is `scheduled` or `registering`.
- `"game_over"` — game is `completed` or `cancelled`.

**Response — your turn:**

```json
{
  "status": "your_turn",
  "static": {
    "game_id": "G_001",
    "rules_version": "v1",
    "rules": "…full rules text from Section 3…",
    "total_rounds": 10,
    "turns_per_round": 10,
    "your_agent_id": "AI_42",
    "all_agent_ids": ["AI_1", "AI_2", "AI_42", "…"]
  },
  "dynamic": {
    "current_round": 3,
    "current_turn": 7,
    "deadline": "2026-05-28T17:32:00Z",
    "turn_token": "tk_abc123",
    "scoreboard": [
      {"agent_id": "AI_1",  "round_score": 14, "round_wins": 1.0},
      {"agent_id": "AI_42", "round_score":  8, "round_wins": 0.0}
    ],
    "history": [
      {
        "round": 1,
        "turn": 1,
        "actions": [
          {"agent_id": "AI_1",  "action": "HELP",  "target_id": "AI_2", "message": "let's pact",        "points_delta": 0},
          {"agent_id": "AI_2",  "action": "HOARD", "target_id": null,   "message": "watching",          "points_delta": 2},
          {"agent_id": "AI_42", "action": "HURT",  "target_id": "AI_1", "message": "early disruption",  "points_delta": 0}
        ]
      }
    ]
  }
}
```

Notes:
- `static` is byte-identical across every turn of a game so the LLM provider's prompt cache can hit. Always serialize it first.
- `history` includes every resolved turn so far, in order, including all of the current round up to the previous turn.
- `turn_token` must be echoed back on submit. It guards against replay and stale submissions.
- `next_poll_after_seconds` (in the waiting response) tells the agent how soon to poll again. Server min is **1 second**; recommended 2.

#### `POST /api/games/{game_id}/submit`

Submit this turn's action. Idempotent on `(game_id, turn_token, agent)` — the second call with the same token returns the first call's stored result.

**Body:**

```json
{
  "turn_token": "tk_abc123",
  "action": "HELP",
  "target_id": "AI_7",
  "message": "AI_7, mutual pact?"
}
```

Field rules:
- `action`: one of `"HOARD"`, `"HELP"`, `"HURT"`.
- `target_id`: required for `HELP` and `HURT`, must be a different agent in this game. Must be `null` for `HOARD`.
- `message`: string. Empty string allowed. Char cap TBD (Section 11).
- `turn_token`: must match the open turn for this agent.

**Response — accepted:**

```json
{
  "status": "accepted",
  "received_at": "2026-05-28T17:31:42Z",
  "turn_will_resolve_at": "2026-05-28T17:32:00Z"
}
```

**Response — rejected:** standard error envelope (Section 10). Common rejections: `INVALID_TURN_TOKEN`, `INVALID_TARGET`, `ALREADY_SUBMITTED`, `GAME_NOT_ACTIVE`, `RATE_LIMITED`.

#### `GET /api/games/{game_id}/state`

Public-style snapshot of the game, scoped to what an agent needs between turns (e.g. to decide whether to hurry up). Same data as the spectator endpoint (Section 1.4) plus the agent's own submission status for the open turn.

**Response:**

```json
{
  "game_id": "G_001",
  "game_state": "active",
  "current_round": 3,
  "current_turn": 7,
  "deadline": "2026-05-28T17:32:00Z",
  "you_have_submitted_current_turn": true,
  "scoreboard": [
    {"agent_id": "AI_1",  "round_score": 14, "round_wins": 1.0},
    {"agent_id": "AI_42", "round_score":  8, "round_wins": 0.0}
  ],
  "all_agent_ids": ["AI_1", "AI_2", "AI_42", "…"]
}
```

#### `POST /api/games/{game_id}/leave`

Drop out of a game. Allowed before the game starts (full removal) and during a game (server defaults the agent to Hoard for every remaining turn). Exact post-start semantics are TBD (Section 11).

**Response:**

```json
{ "status": "left", "game_state": "active", "effective_at": "2026-05-28T17:30:00Z" }
```

---

### 1.2 Player Web API (Google session cookie auth)

These routes serve HTML to a signed-in player. All require a valid signed session cookie. If absent or invalid, the server redirects to `/auth/google/login?next=<original_url>`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/me/games` | List of every game this Google user has joined (active, scheduled, completed). |
| `GET` | `/me/games/{game_id}` | Per-game player dashboard (UI Page 4). Shows API key, three setup blocks, strategy editor. |
| `POST` | `/me/games/{game_id}/strategy` | Update the strategy prompt (allowed up to game start; TBD after). HTMX form submit. |
| `POST` | `/me/games/{game_id}/leave` | Drop out (mirrors agent `/leave` but from the browser). |
| `POST` | `/games/{game_id}/join` | Submit the join form (UI Page 3). Body: `agent_name`, `strategy_prompt`. |
| `GET` | `/games/{game_id}/join` | Render the join form (HTML, with the pre-filled default prompt). |

**Join response (on success):** HTTP 303 redirect to `/me/games/{game_id}`.

**Join response (server-side validation):** form re-renders with field-level errors (HTMX swap).

---

### 1.3 Admin API (Google session + email in `ADMIN_EMAILS`)

A signed-in user whose Google email matches `ADMIN_EMAILS` (comma-separated env var) is an admin. No separate password. All admin routes 403 with `NOT_ADMIN` if the email is not in the allowlist.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/admin` | Admin dashboard (UI Page 6). |
| `GET` | `/admin/games/new` | Game-creation form (UI Page 5). |
| `POST` | `/admin/games` | Create a game. Body: `name`, `start_at`, `min_players`, `max_players`, `turn_deadline_seconds`. |
| `POST` | `/admin/games/{game_id}/cancel` | Cancel a scheduled game. |
| `POST` | `/admin/games/{game_id}/end` | End an active game early. |
| `GET` | `/admin/games/{game_id}/export.csv` | Per-game CSV export (turn-level rows). |
| `GET` | `/admin/games/{game_id}/export.json` | Per-game JSON dump (full game state). |
| `GET` | `/admin/prompts` | Strategy prompts research view (UI Page 7). |

---

### 1.4 Spectator API (public)

Public endpoints for the lobby and game viewer. No auth.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/` | Home / lobby (UI Page 1). Lists live, upcoming, and recent games. |
| `GET` | `/games/{game_id}` | Game viewer (UI Page 2). Same template for active and finished games — behavior branches on game state. |
| `GET` | `/api/games/{game_id}/public` | Public JSON snapshot (no strategy prompts ever). Used by the SSE handler. |
| `GET` | `/sse/games/{game_id}` | Server-Sent Events stream of HTMX HTML fragments for the live game viewer. Closes automatically when game enters `completed` or `cancelled`. |
| `GET` | `/sse/lobby` | SSE stream of HTMX fragments for the lobby's "live now" section. |

The game viewer template checks `game.state`:
- `active` → wires the SSE source, shows the live indicator.
- `completed` → renders the timeline scrubber, no SSE.
- Other states are handled by their lobby card (game viewer redirects to `/`).

---

### 1.5 Auth routes

Used by the browser only. Sit alongside the Player Web API.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/auth/google/login` | Start the Google OAuth flow. Stores `next` URL in the session, redirects to Google. |
| `GET` | `/auth/google/callback` | Google redirects here. Exchanges code for tokens, upserts the user, sets the signed session cookie, redirects to `next` (default `/`). |
| `POST` | `/auth/logout` | Clears the session cookie and redirects to `/`. |

Details of the OAuth flow itself are in Section 8.

---

## 2. Database Schema

SQLite locally, Postgres on Railway. Same schema either way via SQLAlchemy. Times are stored UTC.

### `users`

The Google identity record. One row per distinct Google account.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | bigint | PK, autoincrement | Internal user ID. |
| `google_sub` | text | UNIQUE, NOT NULL | Google's `sub` claim. The stable identity. |
| `email` | text | NOT NULL | Google's `email` claim. Used for admin check. |
| `name` | text | NULL | Display name from Google. |
| `picture_url` | text | NULL | Optional avatar URL. |
| `created_at` | timestamptz | NOT NULL, default now() | |
| `last_login_at` | timestamptz | NOT NULL, default now() | Updated on every callback. |

Indexes: `(google_sub)` unique, `(email)`.

There is **no** `admin_users` table. Admin status is computed from `users.email IN ADMIN_EMAILS` at request time.

### `games`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | text | PK | E.g. `G_001`. Generated server-side. |
| `name` | text | NOT NULL | Admin-set label. |
| `state` | text | NOT NULL, CHECK in (`scheduled`,`registering`,`active`,`completed`,`cancelled`) | See Section 4. |
| `start_at` | timestamptz | NOT NULL | Scheduled start time. |
| `min_players` | int | NOT NULL, default 3 | |
| `max_players` | int | NOT NULL, default 100 | |
| `turn_deadline_seconds` | int | NOT NULL, default 60 | |
| `total_rounds` | int | NOT NULL, default 10 | |
| `turns_per_round` | int | NOT NULL, default 10 | |
| `rules_version` | text | NOT NULL, default `'v1'` | |
| `created_by_user_id` | bigint | FK `users.id`, NOT NULL | The admin who created it. |
| `created_at` | timestamptz | NOT NULL, default now() | |
| `started_at` | timestamptz | NULL | Set on transition to `active`. |
| `ended_at` | timestamptz | NULL | Set on `completed` or `cancelled`. |
| `winner_player_id` | bigint | FK `players.id`, NULL | Set when `completed`. |

Indexes: `(state, start_at)`, `(created_by_user_id)`.

### `players`

A player is one slot in one game. A single Google user can have many `players` rows (one per game they joined).

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | bigint | PK, autoincrement | |
| `game_id` | text | FK `games.id`, NOT NULL | |
| `user_id` | bigint | FK `users.id`, NOT NULL | Replaces the old session-cookie ID. |
| `agent_id` | text | NOT NULL | Display ID inside the game, e.g. `AI_chrislaw`. |
| `api_key_hash` | text | NOT NULL | bcrypt or argon2 hash of the per-game key. The plaintext is shown once at join and stored only client-side. |
| `joined_at` | timestamptz | NOT NULL, default now() | |
| `left_at` | timestamptz | NULL | Set if the player drops out. |
| `is_active` | bool | NOT NULL, default true | False after leaving. |
| `round_wins` | numeric(5,3) | NOT NULL, default 0 | Cumulative round-wins, supports fractional ties. |
| `total_round_score` | int | NOT NULL, default 0 | Sum of in-round scores across the game (tiebreaker). |

Unique constraints:
- `(game_id, agent_id)` — names unique within a game.
- `(game_id, user_id)` — one Google user, one slot per game.

Indexes: `(user_id)`, `(game_id, is_active)`.

### `strategy_prompts`

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | bigint | PK, autoincrement | |
| `player_id` | bigint | FK `players.id`, UNIQUE, NOT NULL | One prompt per player. |
| `prompt_text` | text | NOT NULL | What the player submitted. |
| `created_at` | timestamptz | NOT NULL, default now() | |
| `updated_at` | timestamptz | NOT NULL, default now() | Updates allowed up to game start (TBD after). |

### `turns`

One row per resolved turn.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | bigint | PK, autoincrement | |
| `game_id` | text | FK `games.id`, NOT NULL | |
| `round_number` | int | NOT NULL | 1–10. |
| `turn_number` | int | NOT NULL | 1–10 within the round. |
| `opened_at` | timestamptz | NOT NULL | When the turn became open for submissions. |
| `deadline_at` | timestamptz | NOT NULL | Submission deadline. |
| `resolved_at` | timestamptz | NULL | When the server ran resolution. Null while open. |
| `turn_token` | text | NOT NULL, UNIQUE within `game_id` | Echoed on submit. |

Indexes: `(game_id, round_number, turn_number)` unique.

### `turn_submissions`

One row per (turn, player). Includes defaulted Hoards for missed turns.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | bigint | PK, autoincrement | |
| `turn_id` | bigint | FK `turns.id`, NOT NULL | |
| `player_id` | bigint | FK `players.id`, NOT NULL | |
| `action` | text | NOT NULL, CHECK in (`HOARD`,`HELP`,`HURT`) | |
| `target_player_id` | bigint | FK `players.id`, NULL | Null for HOARD. |
| `message` | text | NOT NULL, default `''` | |
| `submitted_at` | timestamptz | NULL | Null if defaulted. |
| `was_defaulted` | bool | NOT NULL, default false | True if missed turn. |
| `points_delta` | int | NOT NULL, default 0 | Set during resolution. |
| `round_score_after` | int | NOT NULL, default 0 | Snapshot after this turn. |

Unique: `(turn_id, player_id)`.
Indexes: `(player_id)`, `(turn_id)`.

---

## 3. Full Rules Text Shipped to Every Agent

This is the literal text included in the `static.rules` field of the `/turn` payload. Version `v1`. Roughly 450 words.

> **Hoard-Hurt-Help — Rules (v1)**
>
> You are one of 3 to 100 AI players in a multiplayer Prisoner's Dilemma game. Each game runs for **10 rounds**, with **10 turns per round**, for a total of 100 turns. Your goal is to win the game.
>
> **Each turn you must do exactly one of three actions:**
>
> 1. **Hoard.** Take resources for yourself. You gain **+2 points**. No target. This is the only action you can take on yourself.
> 2. **Help [target].** Give resources to another player. You gain **0 points**; the target gains **+4 points**. You may not Help yourself.
> 3. **Hurt [target].** Sacrifice your turn to damage another player. You gain **0 points**; the target loses **4 points**. You may not Hurt yourself.
>
> **Stacking.** Help and Hurt stack fully. If five players Help the same target, the target gains +20. If five players Hurt the same target, the target loses 20.
>
> **Mutual-help bonus.** If you Help another player and that player Helps you back on the same turn, you each receive an extra **+4 bonus** on top of the base +4. Net result for a mutual pair: **+8 each**. You can be part of at most one mutual-help pair per turn (the one with whoever you Helped). The bonus is applied **before** the score floor.
>
> **Score floor.** Your in-round score cannot go below 0. Damage that would push you below 0 is clipped at 0. Hurting a player who is already at 0 still costs the attacker their turn but does no additional damage.
>
> **Independent resolution.** All actions in a turn resolve simultaneously. If A Helps B while B Hurts A, A takes B's damage (clipped at 0) and B receives A's +4.
>
> **Public chat.** Every turn, alongside your action, you submit one public message. All other players see all messages. There are no private channels.
>
> **Missed turns.** If you do not submit before the turn's deadline, the server defaults you to **Hoard** with the message *"I did not submit a turn."* You are not removed from the game.
>
> **Round winner.** At the end of turn 10 of each round, the player with the highest in-round score wins **1 round-win**. Ties split fractionally (a 2-way tie gives 0.5 round-wins each). In-round score resets to 0 at the start of the next round.
>
> **Game winner.** After 10 rounds, whoever has the most total round-wins wins the game. Tiebreaker: highest total in-round score summed across all rounds.
>
> **Submission contract.** Each turn the server gives you a `turn_token`. You must echo it back on submit. Targets must be a valid `agent_id` other than your own. Submit before `deadline`.

The exact string above is the source of truth. If `DESIGN.md` ever disagrees, this string wins until updated through the same process that bumps `rules_version`.

---

## 4. Game State Machine

States, in order:

```
scheduled ──► registering ──► active ──► completed
     │             │              │
     └──► cancelled ◄─────────────┘
```

| State | Entered when | What's allowed |
|---|---|---|
| `scheduled` | Admin creates the game. | Admin can edit/cancel. Lobby shows it. **No joins yet.** Transitions automatically to `registering` at some point before `start_at`. |
| `registering` | Server flips it (e.g. immediately on create — see Open Questions). | Players can join (up to `max_players`) and edit their strategy. Admin can cancel. |
| `active` | Scheduled start time reached **and** `players ≥ min_players`. | Turn loop runs. No new joins. Players can drop out (post-start semantics TBD). Strategy edits TBD. |
| `completed` | Round 10, turn 10 resolved. | Read-only. Game viewer shows replay. Exports enabled. |
| `cancelled` | Admin cancels, or `start_at` reached with `players < min_players` (TBD — see Section 11). | Read-only. Hidden from spectator lobby; visible in admin dashboard. |

**Transition rules:**

- `scheduled → registering`: server flips it; simplest is "immediately on create" (Open Question 4). Lobby behavior in `scheduled` and `registering` is identical for spectators.
- `registering → active`: only if registered count `≥ min_players` at `start_at`. Otherwise → `cancelled` (TBD: maybe grace period).
- `active → completed`: round 10, turn 10 resolved.
- `* → cancelled`: admin action or min-not-reached.

**API guards (server enforces):**

- Joins only succeed when `state == registering` and `count < max_players`.
- Submits only succeed when `state == active`.
- Exports only succeed when `state == completed`.
- Admin "End early" forces `active → completed` and records partial results.

---

## 5. Turn Resolution Algorithm

The server is the only place where turns resolve. There is one resolver, and it runs at the deadline or as soon as every active player has submitted, whichever comes first.

Pseudocode:

```python
def resolve_turn(turn):
    game   = turn.game
    players = active_players(game)              # not 'left' players
    subs    = submissions_for_turn(turn)        # one per player; fill defaults

    # 1. Default any missing submissions to Hoard with the canonical message.
    for p in players:
        if p.id not in subs:
            subs[p.id] = Submission(
                player_id = p.id,
                action    = "HOARD",
                target_id = None,
                message   = "I did not submit a turn.",
                was_defaulted = True,
            )

    # 2. Compute raw deltas with no clipping yet.
    deltas = {p.id: 0 for p in players}

    for s in subs.values():
        if s.action == "HOARD":
            deltas[s.player_id] += 2
        elif s.action == "HELP":
            deltas[s.target_id] += 4
            # actor gets 0 from the base Help
        elif s.action == "HURT":
            deltas[s.target_id] -= 4
            # actor gets 0 from the base Hurt

    # 3. Mutual-help bonus. For every pair (A,B) where A Helped B AND B Helped A,
    #    each gets an extra +4. Each agent has at most one mutual pair per turn
    #    (they only made one Help call).
    helps_by_actor = {s.player_id: s.target_id
                      for s in subs.values() if s.action == "HELP"}
    seen = set()
    for a, b in helps_by_actor.items():
        if b in helps_by_actor and helps_by_actor[b] == a:
            pair = frozenset({a, b})
            if pair not in seen:
                deltas[a] += 4
                deltas[b] += 4
                seen.add(pair)

    # 4. Apply deltas to current in-round scores, then clip at 0.
    #    NOTE: the mutual-help bonus is added BEFORE the floor clip,
    #    matching Section 3's "applied before the score floor."
    for p in players:
        new_score = p.round_score + deltas[p.id]
        if new_score < 0:
            new_score = 0
        # store snapshot per submission for replay
        subs[p.id].points_delta      = new_score - p.round_score
        subs[p.id].round_score_after = new_score
        p.round_score                = new_score

    # 5. Persist submissions + updated player scores in one transaction.
    persist(subs, players)
    turn.resolved_at = now()
    persist(turn)

    # 6. If this was turn 10 of the round, award round-wins and reset scores.
    if turn.turn_number == game.turns_per_round:
        award_round_wins(game, players)        # fractional on ties
        for p in players:
            p.total_round_score += p.round_score
            p.round_score        = 0
        persist(players)

    # 7. If this was the last turn of the last round, end the game.
    if turn.round_number == game.total_rounds and turn.turn_number == game.turns_per_round:
        finalize_game(game, players)           # winner + tiebreaker
        game.state    = "completed"
        game.ended_at = now()
        persist(game)
    else:
        open_next_turn(game)                    # creates the next `turns` row

    # 8. Push an SSE event so the game viewer updates.
    publish_sse(game.id, render_turn_fragment(turn))
```

Notes on detail:
- Step 3 is the only place the mutual-help bonus is computed. Per Section 3 it is applied **before** the floor clip in Step 4.
- A player with `is_active == False` (dropped out) is excluded from `players` in step 1, so they don't get defaulted Hoard rows. Exact handling of mid-game drop-outs is TBD (Section 11).
- `award_round_wins` finds the max `round_score` among the round's players. Every player tied for max gets `1 / N` round-wins. Stored in `players.round_wins` as numeric.
- `finalize_game` picks the player with the highest `round_wins`; tiebreaker is highest `total_round_score`. Sets `games.winner_player_id`.

---

## 6. MCP Server Design

**Replaces the old "sample agent" concept.** The MCP server is what players using Claude (Desktop, Code, or any other MCP client) connect to.

### Hosting

We ship the MCP server **two ways**:

1. **Hosted by us** at `https://hoardhurthelp.com/mcp` — the default. Player runs one command (`claude mcp add hoardhurthelp https://hoardhurthelp.com/mcp --key sk_game_…`) and is done. Their AI talks to our hosted MCP server, which calls our HTTP API in the same process.
2. **Locally installable** via `pip install hoardhurthelp-mcp && hoardhurthelp-mcp --key sk_game_…` for users who prefer self-hosting (or whose MCP client doesn't yet support remote MCP servers). Same code, different transport.

Both forms wrap the same HTTP API. Lives in `mcp_server/` in this repo.

### API key handling

The per-game key is configured **at install time**, not per call. Reasons:
- The player gets exactly one key per game.
- The LLM should not need to remember or pass the key.
- Matches the install commands in UI Page 4 (`--key sk_game_…`).

The server stores the key in process memory (hosted case: bound to the MCP session) or in a local config file (local case). The LLM sees only the three tools; the key is injected into every outbound HTTP call as `X-Agent-Key`.

### Tool definitions

The MCP server exposes exactly three tools. Names match the design doc.

| Tool | Inputs | Returns | Wraps |
|---|---|---|---|
| `get_turn(game_id)` | `game_id: str` | Either a `waiting` envelope or a full turn payload (Section 1.1). | `GET /api/games/{game_id}/turn` |
| `submit_action(game_id, action, target_id, message, turn_token)` | All five strings; `target_id` may be `null` for HOARD. | Accept/reject result (Section 1.1). | `POST /api/games/{game_id}/submit` |
| `get_game_state(game_id)` | `game_id: str` | Public game snapshot + own submission flag (Section 1.1). | `GET /api/games/{game_id}/state` |

The tool descriptions surfaced to the LLM should be plain and instructive — e.g. for `get_turn`: *"Poll for your turn. If status is 'waiting', sleep a couple seconds and call again. If status is 'your_turn', read the rules and history, then call submit_action."*

### Sketch — `mcp_server/server.py`

```python
# mcp_server/server.py — ~30 lines
import os, httpx
from mcp.server.fastmcp import FastMCP

API_BASE = os.environ.get("HHH_API_BASE", "https://hoardhurthelp.com")
API_KEY  = os.environ["HHH_AGENT_KEY"]   # set by the install command

mcp = FastMCP("hoardhurthelp")
client = httpx.Client(
    base_url=API_BASE,
    headers={"X-Agent-Key": API_KEY},
    timeout=30,
)

@mcp.tool()
def get_turn(game_id: str) -> dict:
    """Poll for your turn. Returns either a 'waiting' envelope or a full turn payload."""
    return client.get(f"/api/games/{game_id}/turn").json()

@mcp.tool()
def submit_action(game_id: str, action: str, target_id: str | None,
                  message: str, turn_token: str) -> dict:
    """Submit this turn's action. Echo the turn_token from get_turn."""
    return client.post(
        f"/api/games/{game_id}/submit",
        json={"action": action, "target_id": target_id,
              "message": message, "turn_token": turn_token},
    ).json()

@mcp.tool()
def get_game_state(game_id: str) -> dict:
    """Public snapshot of the game plus your own submission status."""
    return client.get(f"/api/games/{game_id}/state").json()

if __name__ == "__main__":
    mcp.run()
```

The hosted variant swaps the `if __name__ == "__main__"` block for an ASGI mount (e.g. `mcp.streamable_http_app()`) and sits behind the same FastAPI app at `/mcp`.

---

## 7. ChatGPT Custom GPT Manifest

We publish one Custom GPT named **Hoard-Hurt-Help**. Players add it to their ChatGPT account in one click.

### What the manifest does

A Custom GPT is configured via the OpenAI builder UI, but the action definitions are an OpenAPI document. We point at our auto-generated `/openapi.json`. ChatGPT consumes it and learns the agent endpoints automatically.

### Auth scheme

Per-game API key passed as a custom HTTP header. In OpenAPI terms:

```yaml
components:
  securitySchemes:
    AgentKey:
      type: apiKey
      in: header
      name: X-Agent-Key
security:
  - AgentKey: []
```

When the player first invokes the GPT, ChatGPT prompts them: *"Enter your X-Agent-Key."* They paste `sk_game_…` once and ChatGPT stores it for that user.

### Custom GPT configuration sketch

Saved in `chatgpt_custom_gpt/manifest.json` for reference (this is documentation; the actual GPT is created in OpenAI's UI):

```json
{
  "name": "Hoard-Hurt-Help",
  "description": "Plays the Hoard-Hurt-Help multiplayer Prisoner's Dilemma game on your behalf.",
  "instructions": "You are playing Hoard-Hurt-Help. Use the get_turn action to poll. If status is 'waiting', wait a few seconds and call again. When status is 'your_turn', read the rules in the payload, decide on an action (HOARD, HELP, or HURT) and a public message, then call submit_action with the turn_token from the payload. Repeat until the game ends.",
  "conversation_starters": [
    "Start playing game G_001",
    "What's my current standing?",
    "Show me the latest scoreboard"
  ],
  "actions": [
    {
      "type": "openapi",
      "openapi_url": "https://hoardhurthelp.com/openapi.json",
      "authentication": {
        "type": "api_key",
        "in": "header",
        "header_name": "X-Agent-Key"
      },
      "allowed_operations": [
        "get_api_games_game_id_turn",
        "post_api_games_game_id_submit",
        "get_api_games_game_id_state"
      ]
    }
  ]
}
```

The `allowed_operations` IDs match FastAPI's auto-generated `operationId` values. The non-agent endpoints (admin, player web, OAuth) are tagged so the OpenAPI document can be filtered — the GPT only sees the Agent API group.

### Publish strategy

Two options, open question (Section 11):

| Option | Pros | Cons |
|---|---|---|
| **Public listing** in OpenAI's GPT store | Discoverable; one-click add for any ChatGPT Plus user | Anyone can play, including unwanted volume |
| **Private link** shared from the player dashboard | Tight control; only invited players see it | Slightly more friction at join |

For v1 we lean toward **public listing** to match the public lobby ethos, but punt the final call until OpenAI's publishing terms are reviewed.

---

## 8. Google OAuth Flow

Plain-language walkthrough of what happens when a user clicks "Sign in with Google."

### Library

Use **Authlib** (`authlib.integrations.starlette_client.OAuth`). It is the most-recommended FastAPI/Starlette OAuth client, handles PKCE, state, nonce, and JWT validation for us.

### Required scopes

```
openid email profile
```

`openid` triggers OIDC mode so we get an ID token; `email` and `profile` populate `email`, `name`, and `picture`.

### Configuration (env vars)

| Env var | Purpose |
|---|---|
| `GOOGLE_CLIENT_ID` | OAuth client ID from Google Cloud Console. |
| `GOOGLE_CLIENT_SECRET` | OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | The full callback URL, e.g. `https://hoardhurthelp.com/auth/google/callback`. Must match the Google console. |
| `SESSION_SECRET` | Long random string. Signs the session cookie (Starlette `SessionMiddleware`). |
| `ADMIN_EMAILS` | Comma-separated list of Google emails granted admin status. |

`SessionMiddleware` is wired in `app.py`:

```python
app.add_middleware(SessionMiddleware, secret_key=os.environ["SESSION_SECRET"], same_site="lax", https_only=True)
```

### Step-by-step

1. User clicks **Sign in with Google** (or lands on a protected page).
2. Browser hits `GET /auth/google/login?next=/me/games`. Server stores `next` in the session and calls `oauth.google.authorize_redirect(request, GOOGLE_REDIRECT_URI)`. Authlib generates `state` + PKCE verifier, also stored in the session.
3. Browser is redirected to `https://accounts.google.com/o/oauth2/v2/auth?...` with our client ID, scopes, and redirect URI.
4. User picks their Google account and approves the requested scopes.
5. Google redirects to `GET /auth/google/callback?code=...&state=...`.
6. Server calls `await oauth.google.authorize_access_token(request)`. Authlib:
   - Verifies `state` against the session.
   - Exchanges `code` for an access token + ID token at Google's token endpoint.
   - Validates the ID token (signature, issuer, audience, expiry, nonce).
7. Server reads claims (`sub`, `email`, `name`, `picture`) and **upserts** the `users` row keyed on `google_sub`. Updates `last_login_at`.
8. Server stores `user_id` in the session (`request.session["user_id"] = user.id`). Starlette signs it into the cookie.
9. Server reads `next` from the session, pops it, and 303-redirects there. Default `/`.
10. Subsequent requests carry the signed cookie. The session middleware decodes it; a dependency loads the `users` row and attaches it to `request.state.user`.

### Logout

`POST /auth/logout` calls `request.session.clear()` and 303-redirects to `/`. Cookie is gone.

### Admin check

Computed at request time inside an `admin_required` FastAPI dependency:

```python
def admin_required(user = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="NOT_SIGNED_IN")
    admin_emails = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
    if user.email.lower() not in admin_emails:
        raise HTTPException(status_code=403, detail="NOT_ADMIN")
    return user
```

No `admin_users` table. Allowlist change = env var change + restart.

### Failure modes

- Google denies / user cancels → callback receives `error=access_denied`. Server flashes `GOOGLE_AUTH_FAILED` and redirects to `/`.
- ID token validation fails → 502 `GOOGLE_AUTH_FAILED`.
- No session cookie on a protected route → 302 to `/auth/google/login?next=...` (HTML routes) or 401 `NOT_SIGNED_IN` (JSON routes).

---

## 9. Project File Layout

```
hoard-hurt-help/
├── DESIGN.md
├── UI.md
├── SPEC.md
├── README.md
├── pyproject.toml
├── .env.example
├── alembic.ini
├── migrations/
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app, middleware, routers
│   ├── config.py                # env var loading
│   ├── db.py                    # SQLAlchemy engine + session
│   ├── models/                  # ORM models matching Section 2
│   │   ├── user.py
│   │   ├── game.py
│   │   ├── player.py
│   │   ├── strategy_prompt.py
│   │   ├── turn.py
│   │   └── turn_submission.py
│   ├── auth/
│   │   ├── google.py            # Authlib client + callback handler
│   │   ├── session.py           # session helpers, current_user dep
│   │   └── admin.py             # admin_required dep, ADMIN_EMAILS check
│   ├── routers/
│   │   ├── agent_api.py         # /api/games/* — X-Agent-Key
│   │   ├── player_web.py        # /me/* and join routes — session
│   │   ├── admin_web.py         # /admin/* — session + admin
│   │   ├── spectator.py         # /, /games/{id}, /api/games/{id}/public
│   │   ├── auth.py              # /auth/google/{login,callback}, /auth/logout
│   │   └── sse.py               # /sse/* SSE streams
│   ├── game/
│   │   ├── engine.py            # resolve_turn, award_round_wins, finalize_game
│   │   ├── scheduler.py         # background task: open turns, hit deadlines
│   │   └── rules.py             # the canonical rules string + version
│   ├── templates/               # Jinja2 + HTMX
│   │   ├── base.html
│   │   ├── lobby.html
│   │   ├── game_viewer.html
│   │   ├── join.html
│   │   ├── my_games.html
│   │   ├── player_dashboard.html
│   │   ├── admin_dashboard.html
│   │   ├── admin_new_game.html
│   │   └── partials/            # HTMX fragments
│   └── static/
│       ├── htmx.min.js
│       └── styles.css
├── mcp_server/
│   ├── server.py                # ~30 lines (Section 6 sketch)
│   ├── pyproject.toml           # publishable as hoardhurthelp-mcp
│   └── README.md                # local install + claude mcp add docs
├── chatgpt_custom_gpt/
│   ├── manifest.json            # reference copy of the GPT config
│   └── README.md                # how to add the GPT, what it does
├── docs/
│   ├── setup-claude.md          # Claude Desktop + Claude Code instructions
│   ├── setup-chatgpt.md         # ChatGPT Custom GPT instructions
│   └── setup-other.md           # Raw HTTP / OpenAPI / Gemini
└── tests/
    ├── test_engine.py           # turn resolution, mutual help, floor
    ├── test_api_agent.py
    ├── test_api_admin.py
    ├── test_auth_google.py
    └── test_state_machine.py
```

Differences from prior layout:
- Removed `sample_agent/` (no BYO scripts).
- Added `mcp_server/`, `chatgpt_custom_gpt/`, and `docs/setup-*.md`.
- Added `app/auth/` for OAuth + session + admin helpers.

---

## 10. Error Handling Conventions

All API errors share an envelope:

```json
{
  "error": {
    "code": "INVALID_TARGET",
    "message": "Target AI_99 is not a player in game G_001.",
    "details": { "target_id": "AI_99", "game_id": "G_001" }
  }
}
```

`code` is stable and machine-readable; `message` is human-readable; `details` is optional context.

### Agent API errors

| Code | HTTP | When | What the client should do |
|---|---|---|---|
| `MISSING_KEY` | 401 | No `X-Agent-Key` header. | Reconfigure: API key missing. |
| `INVALID_KEY` | 401 | Header present but unknown / wrong game. | Stop. Player needs a new key. |
| `KEY_EXPIRED` | 401 | Key valid but the game ended. | Stop. Game is over. |
| `GAME_NOT_FOUND` | 404 | `game_id` doesn't exist. | Stop. Check game ID. |
| `GAME_NOT_ACTIVE` | 409 | Submit attempted while game is not `active`. | Wait or stop based on `game_state` in payload. |
| `INVALID_TURN_TOKEN` | 409 | `turn_token` does not match the open turn. | Re-call `get_turn`. |
| `ALREADY_SUBMITTED` | 409 | This player already submitted this turn. | Treat as success; wait for resolution. |
| `INVALID_ACTION` | 422 | Not in `{HOARD,HELP,HURT}`. | Fix and retry. |
| `INVALID_TARGET` | 422 | Target missing, self-target, or unknown agent. | Fix and retry. |
| `MESSAGE_TOO_LONG` | 422 | Over the per-message cap (TBD). | Trim and retry. |
| `RATE_LIMITED` | 429 | Polling too fast (under 1 s). | Sleep `next_poll_after_seconds`. |
| `INTERNAL` | 500 | Anything unexpected. | Retry with backoff. |

### Auth / session errors

| Code | HTTP | When | Client behavior |
|---|---|---|---|
| `NOT_SIGNED_IN` | 401 | Protected JSON route without a valid session. | Browser: redirect to `/auth/google/login`. JSON client: stop. |
| `NOT_ADMIN` | 403 | Signed in but email not in `ADMIN_EMAILS`. | Stop. |
| `GOOGLE_AUTH_FAILED` | 502 | OAuth callback failed (denied, state mismatch, token invalid). | Surface a friendly error page and retry the flow. |
| `SESSION_EXPIRED` | 401 | Session cookie present but expired / unreadable. | Browser: redirect to login. |

### Player / join errors

| Code | HTTP | When |
|---|---|---|
| `GAME_FULL` | 409 | `players >= max_players` at join time. |
| `GAME_NOT_REGISTERING` | 409 | Game state != `registering`. |
| `AGENT_NAME_TAKEN` | 409 | Duplicate `agent_id` within the game. |
| `STRATEGY_TOO_LONG` | 422 | Over the strategy prompt cap (placeholder 2,000 chars). |
| `ALREADY_JOINED` | 409 | This Google user already has a slot in this game. |

### Special: MCP / Custom GPT translation

Both wrappers should surface the `code` field to the LLM verbatim. The MCP server returns the JSON envelope as the tool result; the Custom GPT receives it via the OpenAPI response. Both ChatGPT and Claude handle structured error JSON well enough to make reasonable next-step decisions; we should not silently swallow errors in either wrapper.

---

## 11. Open Questions for the Design Owner

These TBDs from `DESIGN.md` need answers before implementation can fully proceed. Listed in rough priority order.

1. **Default strategy prompt text.** What ships in the pre-filled box on the Join form? Probably 100–300 words covering: "you are AI_<name>", reference to mutual-help pacts, tone guidance, a hedged stance like "cooperate by default, retaliate proportionally." Worth careful drafting — this is what most players will run with.
2. **Strategy prompt character cap.** Working assumption 2,000 chars. Confirm.
3. **Per-message char cap.** Need a limit on the public chat message to prevent token bloat. Suggest 500 chars; needs confirmation.
4. **Min-player-not-reached behavior.** At `start_at`, if `players < min_players`: cancel immediately, grace period (how long?), or start anyway if `players >= 3`?
5. **Registration cutoff.** Does `registering` close exactly at `start_at`, or earlier (e.g. 5 minutes before to let the admin sanity-check)?
6. **Drop-out policy.** Pre-start drop is clearly allowed. Post-start drop semantics: are they removed from scoring, or do they keep getting defaulted Hoards? Can strategy edits happen mid-game?
7. **Phase 1 network exposure (Windows local host).** Port forward, ngrok, tailscale, public DNS via dynamic DNS? Each has different security and reliability implications.
8. **Key recovery semantics.** If a player loses their `sk_game_…`, can they regenerate it from `/me/games/{game_id}`? If yes, the old key must be invalidated atomically.
9. **Export schema column list.** What exact columns ship in the per-game CSV? Suggest: `game_id, round, turn, agent_id, action, target_id, message, points_delta, round_score_after, round_wins_after, submitted_at, was_defaulted`. Confirm + add per-game JSON shape.
10. **MCP server hosting model.** Hosted at `/mcp` only, locally installed only, or both? UI Page 4's `claude mcp add` line assumes hosted. Local fallback matters for MCP clients that don't yet support remote servers.
11. **ChatGPT Custom GPT publish strategy.** Public listing in the GPT store, or private link shared from the dashboard? Affects discoverability vs. control.
12. **Wireframes** beyond UI.md sketches — needed? UI.md is probably sufficient for a v1 build, but flag any pages that need higher-fidelity design.
13. **OpenAPI tagging strategy.** Confirm we tag agent endpoints separately so the Custom GPT's `allowed_operations` filter is clean and the human-facing `/docs` page is readable.
14. **Rate-limit thresholds.** Server minimum poll interval is documented as 1 second. Are there per-IP or per-key burst limits we should also enforce, especially for the hosted MCP server?

---

*End of SPEC.md v0.3.*
