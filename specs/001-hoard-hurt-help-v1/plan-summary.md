# Plan Summary: Hoard-Hurt-Help v1

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `pyproject.toml` | create | Python package + all deps |
| `alembic.ini` | create | Migrations config |
| `.env.example` | create | Sample env vars |
| `app/main.py` | create | FastAPI app factory + uvicorn entry; mounts `/mcp` |
| `app/config.py` | create | pydantic-settings env loader |
| `app/db.py` | create | Async SQLAlchemy engine + session |
| `app/deps.py` | create | FastAPI deps (current_user, admin_only, agent_key) |
| `app/models/base.py` | create | Declarative base |
| `app/models/user.py` | create | User table |
| `app/models/game.py` | create | Game + state enum |
| `app/models/player.py` | create | Player (per-game) |
| `app/models/strategy_prompt.py` | create | StrategyPrompt |
| `app/models/turn.py` | create | Turn + TurnSubmission |
| `app/schemas/agent.py` | create | Pydantic models for agent API |
| `app/schemas/admin.py` | create | Pydantic models for admin API |
| `app/schemas/auth.py` | create | Pydantic models for OAuth |
| `app/schemas/spectator.py` | create | Public state schema |
| `app/engine/rules.py` | create | RULES_TEXT_V1 + DEFAULT_STRATEGY_PROMPT |
| `app/engine/resolver.py` | create | resolve_turn, award_round_winners, finalize_game |
| `app/engine/scheduler.py` | create | Per-game asyncio turn-loop task |
| `app/engine/tokens.py` | create | agent_key + turn_token generation/hashing |
| `app/engine/state_machine.py` | create | State transitions |
| `app/routes/agent_api.py` | create | /api/games/.../turn|submit|state|leave |
| `app/routes/admin_api.py` | create | /api/admin/games + exports |
| `app/routes/spectator_api.py` | create | /api/games/{id}/state public |
| `app/routes/web.py` | create | HTMX pages: lobby, viewer, join, /me/* |
| `app/routes/admin_web.py` | create | Admin HTML pages |
| `app/routes/auth.py` | create | OAuth login + callback + logout |
| `app/routes/sse.py` | create | /games/{id}/stream |
| `app/auth/google.py` | create | authlib OAuth client config |
| `app/auth/session.py` | create | SessionMiddleware helpers |
| `app/broadcast.py` | create | In-process pub/sub for SSE |
| `app/templates/base.html` | create | Base layout |
| `app/templates/home.html` | create | Public lobby |
| `app/templates/game.html` | create | Live + finished viewer |
| `app/templates/join.html` | create | Join form |
| `app/templates/connection.html` | create | Page 4 — pick-your-AI setup |
| `app/templates/my_games.html` | create | List of player's games |
| `app/templates/login.html` | create | Sign-in CTA |
| `app/templates/admin/dashboard.html` | create | Admin dashboard |
| `app/templates/admin/create_game.html` | create | Game creation form |
| `app/templates/admin/game_detail.html` | create | Per-game admin view |
| `app/templates/admin/prompts.html` | create | All-prompts research view |
| `app/templates/fragments/*.html` | create | HTMX partials (scoreboard, turn_block, game_status, lobby_list) |
| `app/static/style.css` | create | Site styles |
| `app/static/htmx.min.js` | create | Pinned HTMX |
| `mcp_server/server.py` | create | FastMCP server, 3 tools |
| `mcp_server/README.md` | create | Claude setup notes |
| `chatgpt_custom_gpt/manifest.json` | create | Action manifest pointing at /openapi.json |
| `chatgpt_custom_gpt/README.md` | create | ChatGPT setup notes |
| `docs/setup-claude.md` | create | Step-by-step Claude setup |
| `docs/setup-chatgpt.md` | create | Step-by-step ChatGPT setup |
| `docs/setup-other.md` | create | Raw API setup |
| `migrations/env.py` | create | Alembic env |
| `migrations/versions/0001_initial.py` | create | Initial schema migration |
| `tests/conftest.py` | create | Async DB fixtures |
| `tests/test_resolver.py` | create | Payoff math tests |
| `tests/test_state_machine.py` | create | State transition tests |
| `tests/test_agent_api.py` | create | Agent API tests |
| `tests/test_auth.py` | create | OAuth flow tests (mocked) |
| `tests/test_admin.py` | create | Admin API tests |
| `tests/test_lobby.py` | create | Lobby flow tests |
| `tests/test_end_to_end.py` | create | Full-game tests with stub agents |

## Migration Steps

1. `alembic init migrations` — set up migrations skeleton.
2. Edit `migrations/env.py` to read `DATABASE_URL` from `app.config.settings`.
3. Create `0001_initial.py` with all six tables: `users`, `games`, `players`, `strategy_prompts`, `turns`, `turn_submissions`. Use SQLAlchemy types that are portable across SQLite + Postgres (no JSONB, no ARRAY).
4. `alembic upgrade head` on dev SQLite to verify; same against a Railway Postgres before deploy.

## Data Model

- **User**: `users` — `id (PK)`, `google_sub (UNIQUE, indexed)`, `email (UNIQUE)`, `created_at`. Identifies a human across all games.
- **Game**: `games` — `id (PK)`, `name`, `state (enum)`, `scheduled_start`, `min_players`, `max_players`, `per_turn_deadline_seconds`, `current_round`, `current_turn`, `rules_version`, `winner_player_id (FK NULL)`. The game's runtime state.
- **Player**: `players` — `id (PK)`, `game_id (FK)`, `user_id (FK)`, `agent_id` (display name), `agent_key_hash`, `model_self_report (NULL)`, `total_round_wins (REAL)`, `total_round_score (INT)`. One row per player-in-a-game.
- **StrategyPrompt**: `strategy_prompts` — `id (PK)`, `player_id (FK)`, `prompt_text`, `created_at`. Versioned per edit.
- **Turn**: `turns` — `id (PK)`, `game_id (FK)`, `round`, `turn`, `turn_token (UNIQUE)`, `opened_at`, `deadline_at`, `resolved_at (NULL)`. One row per turn.
- **TurnSubmission**: `turn_submissions` — `id (PK)`, `turn_id (FK)`, `player_id (FK)`, `action`, `target_player_id (FK NULL)`, `message`, `points_delta`, `round_score_after`, `was_defaulted (BOOL)`, `submitted_at (NULL)`. One row per agent per turn.

UNIQUE constraints: `(games.id)`, `(users.google_sub)`, `(users.email)`, `(players.game_id, players.agent_id)`, `(players.game_id, players.user_id)`, `(turns.game_id, turns.round, turns.turn)`, `(turns.turn_token)`, `(turn_submissions.turn_id, turn_submissions.player_id)`.

## Key Constraints

- **Score floor applied to final delta**: clip the in-round score at 0 *after* summing all incoming Hoard/Help/Hurt and the mutual-help bonus, not per-incoming-Hurt. — *Why: matches the spec rules text and prevents an attacker order-dependence that would otherwise leak through the API to attentive players.*
- **Mutual-help bonus before floor clip**: compute the +4 mutual bonus into the raw delta, then floor. — *Why: spec contract; doing it after the floor would silently change the payoff in edge cases where a player is near zero.*
- **Per-game API key, hashed at rest**: server stores only `argon2(agent_key)`. Verification on every request. — *Why: leaked DB doesn't leak keys; key is shown to the player once at join time.*
- **Static prefix of turn payload is byte-identical across turns**: `rules`, `game_id`, `your_agent_id`, `all_agent_ids`, `total_rounds`, `turns_per_round` must be serialized identically every turn. — *Why: hits LLM-provider prompt caches and dramatically reduces token cost for players.*
- **Server min poll interval ≥ 1s per agent key**: enforce server-side, return 429 on faster polling. — *Why: prevents one runaway agent loop from saturating the server.*
- **Idempotent submit on `(game_id, turn_token, player_id)`**: a second submit with the same token returns the first submit's stored result. — *Why: clients (MCP tools, custom GPTs) may retry; we must not double-count.*
- **Scheduler resumes from DB on process restart**: never trust in-memory state across restarts. — *Why: Railway redeploys mid-game would otherwise drop the turn loop.*
- **No drop-outs once a game is `active`**: `/leave` returns 409 after start; missed turns default to Hoard. — *Why: locks the cohort for clean research data; matches spec rules text shown to agents.*
- **Registration cutoff = `start_at`**: server rejects joins at `now >= start_at`. — *Why: deterministic UX; admin and players see the same wall-clock cutoff.*
- **Soft min-player target, hard floor of 3**: `min_players` from game creation is shown in the lobby but not enforced at start. Server starts any game with ≥ 3 players at `start_at`. — *Why: maximize the chance a game runs; 3 is the rules-mechanical minimum.*
- **Strategy prompts are private to player + admin**: never sent in turn payloads, never shown to spectators, even post-game. — *Why: clean research baseline (agents play blind to each other's declared strategies) + protects player IP.*
- **OAuth scopes limited to `openid email profile`**: nothing else. — *Why: minimum needed to identify the user; no Drive/Gmail access required.*
