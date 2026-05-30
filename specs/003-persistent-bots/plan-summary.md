# Plan Summary: Persistent Bots

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/bot.py` | create | Bot entity: key_lookup (sha256, unique), key_hint, status, max_concurrent_games, stall_threshold |
| `app/models/strategy_profile.py` | create | User-level reusable strategy; one default |
| `app/models/player.py` | modify | add `bot_id` FK + `UNIQUE(bot_id, game_id)`; drop `agent_key_hash` |
| `app/models/__init__.py` | modify | register Bot, StrategyProfile |
| `app/engine/tokens.py` | modify | add `generate_bot_key`, `bot_key_lookup` (sha256), `bot_key_matches` (compare_digest) |
| `app/engine/next_turn.py` | create | pure: pick nearest-deadline open turn across candidate rows; deterministic tie-break |
| `app/engine/caps.py` | create | pure: per-bot + platform cap checks |
| `app/deps.py` | modify | add `require_bot` (indexed); add (bot,game_id)→Player resolver; remove player-scan `require_agent_key` |
| `app/routes/agent_api.py` | modify | game-scoped endpoints use require_bot + resolver; rate-limit per bot.id; 404 NOT_IN_GAME; 403 BOT_PAUSED |
| `app/routes/agent_next_turn.py` | create | `GET /api/agent/next-turn` |
| `app/routes/bots_web.py` | create | `/me/bots` panel: create/list/detail/reissue/pause/resume/delete |
| `app/routes/strategy_profiles_web.py` | create | `/me/strategy-profiles` CRUD |
| `app/routes/web.py` | modify | join flow → "enter a bot" (bot_id + in-game name + profile); no key shown |
| `app/config.py` | modify | `max_concurrent_active_games` platform cap |
| `app/schemas/agent.py` | modify | NextTurn response (reuse WaitingResponse/TurnStatic/summary) |
| `app/templates/bots/list.html`, `bots/detail.html` | create | panel + one-time key & paste-once snippet |
| `app/templates/strategy_profiles.html` | create | profiles UI |
| `app/templates/join.html` | modify | select bot + profile; no per-game key/snippet |
| `app/templates/connection.html` | modify/retire | superseded by bots panel |
| `mcp_server/server.py` | modify | add `get_next_turn()` tool; setup-prompt wording → multi-game loop |
| `migrations/versions/0003_persistent_bots.py` | create | DATA-AFFECTING (see below) |
| `tests/test_bot_auth.py`, `test_next_turn.py`, `test_caps.py`, `test_enter_game.py`, `test_strategy_profiles.py`, `test_pause.py` | create | engine + integration (SQLite in-memory) |

## Migration Steps

1. `create_table('bots')` — UNIQUE(key_lookup), UNIQUE(user_id, name), INDEX(user_id).
2. `create_table('strategy_profiles')` — UNIQUE(user_id, name), INDEX(user_id).
3. **Clear throwaway game data** in FK-safe order: turn_submissions → turns → strategy_prompts → players.
4. `batch_alter_table('players')`: add `bot_id` (NOT NULL, FK→bots), add `UNIQUE(bot_id, game_id)`, drop `agent_key_hash`.

> ⚠️ DATA-AFFECTING (Decision 9 / data-critical-waves): step 3 deletes in-flight game rows. Acceptable only because prod data is throwaway (confirmed fresh start). Review before prod apply. Test DB builds from model metadata, so it is unaffected. down_revision = `0002`.

## Data Model

- **Bot**: `bots` — owned by `user_id`; `key_lookup` (sha256, unique index) for O(1) auth; `status` active/paused; `max_concurrent_games`. `Bot 1—* Player`.
- **StrategyProfile**: `strategy_profiles` — owned by `user_id`; `prompt_text`, `is_default`; copied into a `StrategyPrompt` at entry (no live link).
- **Player** (modified): gains `bot_id` FK; `UNIQUE(bot_id, game_id)`; loses `agent_key_hash`.
- **Subscription** (future, NOT built): would reference `bot_id`; consulted by the scheduler poller for auto-join.

## Key Constraints

- **Indexed credential lookup**: auth = `sha256(presented) == bots.key_lookup` (unique index) + `hmac.compare_digest` — *Why: SC-004 forbids the current all-players argon2 scan; sha256 is correct for 192-bit random tokens.*
- **`require_bot` + (bot,game_id)→Player resolver** replaces `require_agent_key` — *Why: a bot now owns many players; the key no longer maps to one player.*
- **One player per (bot, game)** via `UNIQUE(bot_id, game_id)` — *Why: keeps `submit_action(game_id)` unambiguous (FR-010).*
- **`get_next_turn` = nearest deadline, tie-break game_id then round.turn**, skip already-submitted/paused — *Why: minimizes missed turns; deterministic & testable.*
- **Copy-at-entry strategy** — *Why: profile edits must not mutate running games (FR-016).*
- **Paused bot ⇒ no turns served** (`next-turn` waiting/bot_paused; game-scoped 403) — *Why: the kill switch must actually stop play.*
- **Caps**: per-bot `max_concurrent_games` on the row; platform `max_concurrent_active_games` in config; reuse `Game.max_players` — *Why: user data on the row, operational knobs in config.*
- **Pure engine modules** (`next_turn.py`, `caps.py`) DB-free — *Why: unit-testable per the engine-testing constitution rule.*
- **MCP stays a thin wrapper** in `mcp_server/server.py` — *Why: app/ vs mcp_server/ separation; logic lives in app/.*
