# Implementation Plan: Persistent Bots with Paste-Once Credentials

**Branch**: `003-persistent-bots` | **Date**: 2026-05-29 | **Spec**: [spec.md](spec.md)

## Summary

Introduce a `Bot` entity owned by a `User`, holding one stable credential (`sk_bot_<hex>`) that is pasted into an MCP client exactly once. Replace the per-game `sk_game_` credential and its O(n) verification scan with a single indexed bot-key lookup. Add a game-agnostic `get_next_turn` path so one connected bot drives every game it is in from a single loop. Add reusable strategy profiles and a self-serve "My Bots" control panel. Leave a clean seam for a future auto-join feature without building it.

---

## Technical Context

**Language/Version**: Python 3.14 (async).
**Primary Dependencies**: FastAPI, SQLAlchemy (async ORM), Alembic (migrations), `argon2-cffi` (existing key hashing), `hashlib` (stdlib, new indexed key lookup), FastMCP (`mcp.server.fastmcp`), Jinja2 + HTMX (server-rendered UI), `httpx` (MCP→API client), `secrets` (token generation).
**Storage**: SQLAlchemy async. Postgres in production (Railway); SQLite in-memory for tests. Schema changes via Alembic migration `migrations/versions/0003_*`.
**Testing**: `pytest` (async). Test DB is SQLite in-memory — must not require live Postgres (constitution).
**Target Platform**: Single-instance FastAPI app on Railway; MCP mounted at `/mcp` on the same app.
**Performance Goals**: SC-004 — credential auth must not scan all rows; O(1) indexed lookup. SC-002 — a single bot drives N games via one loop.
**Constraints**: No suppressions, full type annotations, async DB calls, no bare `except`, `app/` vs `mcp_server/` separation, no vague filenames (constitution / `CLAUDE.md`).
**Scale/Scope**: Small (tens of bots/games). Fresh start — no data migration of old keys (confirmed).

---

## Constitution Check

**Status**: PASS

Validated against `CLAUDE.md` (project constitution).

### Python Standards
- [ ] No `# type: ignore` / `# noqa` to silence errors — new code fixes root causes.
- [ ] Full type annotations on all new function signatures; `from __future__ import annotations` where forward refs are needed.
- [ ] No bare `except`; specific exception types (e.g. argon2 verify, httpx errors).
- [ ] Async consistency — all new routes and DB calls are `async def`.

### Security
- [ ] Credentials stored as a non-reversible hash; plaintext shown only at issue/reissue; never logged. (FR-002, FR-024)

### Testing
- [ ] New engine/data logic (next-turn selection, cap enforcement, key lookup) has unit tests; external APIs mocked; test DB is SQLite in-memory. (FR-025)

### File Structure
- [ ] New code placed by responsibility under `app/` (models, routes, engine, templates). MCP tool wrapper stays in `mcp_server/`. No `utils.py`/`helpers.py`.

**Notes**: The 0003 migration is **data-affecting** (see Architecture Decision 9 and the callout in [data-model.md](data-model.md)). Per the data-critical-waves rule it must be reviewed before any prod apply. Because prod data is throwaway (confirmed fresh start), clearing in-flight game rows is acceptable, but the migration must say so explicitly and be run knowingly.

---

## Architecture Decisions

### Decision 1: Stable per-bot credential with an indexed lookup hash

**Chosen**: A `Bot` row stores `key_lookup` = `sha256(full_key)` (hex), `UNIQUE` + indexed, and `key_hint` (last 4 chars, non-secret, for display). Authentication computes `sha256` of the presented key and looks it up by index. Key format: `sk_bot_<48 hex>`.

**Rationale**:
- The current per-player flow argon2-verifies against *every* player row — [deps.py:78](../../app/deps.py) — explicitly flagged as v1-only. That cannot meet SC-004.
- Bot keys are 192-bit random tokens (`secrets.token_hex(24)`), not human passwords. For high-entropy secrets a single fast hash is the standard, correct choice; argon2's slow KDF only buys protection against *guessable* secrets. So `sha256` indexed lookup is both faster and appropriate.
- Lookup is O(1) by unique index; final compare uses `hmac.compare_digest` for constant-time equality.

**Alternatives Considered**:
- *Keep argon2 + add a `key_prefix` index*: still needs a verify per prefix collision and keeps a slow KDF for no security benefit on random tokens. Rejected.
- *Opaque `sk_bot_<token_id>_<secret>` split*: clean but changes the key shape and adds parsing. The `sha256(full_key)` index achieves the same O(1) without reshaping the token. Rejected for simplicity.

**Tradeoffs**: Pro — fast, simple, standard for API keys. Con — `sha256` is unsalted; acceptable because the input is high-entropy and unique (no rainbow-table risk on 192-bit randoms). Documented in [research.md](research.md).

### Decision 2: `require_bot` dependency; per-game endpoints resolve the player from (bot, game_id)

**Chosen**: Add `require_bot(...) -> Bot` in `app/deps.py` (indexed lookup, replaces `require_agent_key`). For the existing game-scoped endpoints (`/turn`, `/submit`, history, chat, standings, turn_detail), add a resolver that, given the authenticated bot and the path `game_id`, returns the bot's single active `Player` in that game (404/401 if none).

**Rationale**: A bot owns many players (one per game), so the credential alone no longer maps to one player. Resolving by `(bot_id, game_id)` keeps every existing endpoint's behavior intact while swapping the identity source. The `player.game_id != game_id` guards at [agent_api.py:281](../../app/routes/agent_api.py) and [:381](../../app/routes/agent_api.py) become a clean `(bot_id, game_id)` lookup.

**Alternatives Considered**: Pass `player_id` from the client — rejected; leaks internal ids and breaks the "credential is the only thing the bot holds" goal.

**Tradeoffs**: Pro — minimal change to endpoint bodies. Con — one extra indexed query per call (negligible).

### Decision 3: Game-agnostic `next-turn` endpoint + MCP tool

**Chosen**: New `GET /api/agent/next-turn` (auth: bot key). It finds the bot's active, non-paused players, scans their games' open unresolved turns, and returns the single most urgent one (nearest `deadline_at`) as a `YourTurn`-shaped payload plus `game_id`. If none, returns a `waiting` payload with `next_poll_after_seconds`. New MCP tool `get_next_turn()` in `mcp_server/server.py` wraps it. Existing game-scoped tools stay.

**Rationale**: Directly delivers US2/SC-002 — one loop, any number of games. Reuses the existing `build_turn_summary` / `TurnStatic` payload builders ([agent_api.py:336-367](../../app/routes/agent_api.py)) so the bot sees the same shape it does today.

**Alternatives Considered**: A `list_my_games()` tool the bot iterates — more round-trips and pushes orchestration into the prompt. Kept as a possible secondary tool but not the primary loop.

**Tradeoffs**: Pro — trivial bot loop. Con — "most urgent" must be defined; we pick nearest deadline, ties broken by `game_id` then `round.turn` (deterministic).

### Decision 4: One player per bot per game (DB-enforced)

**Chosen**: Add `players.bot_id` (FK → bots) and a `UNIQUE(bot_id, game_id)` constraint. Keep the existing `UNIQUE(game_id, agent_id)`.

**Rationale**: Guarantees `(bot, game_id)` resolves to exactly one player, so `submit_action(game_id, …)` stays unambiguous (FR-010). A user fields multiple agents in one game by running multiple bots (FR-012), which the existing per-game name uniqueness already supports.

### Decision 5: Strategy profiles seed a per-player copy at entry

**Chosen**: New `strategy_profiles` table (user-level, reusable, one default). On entry, copy the chosen/default profile's text into a new per-player `StrategyPrompt` row (existing table, unchanged). The turn payload keeps reading the latest `StrategyPrompt` for the player ([agent_api.py:340](../../app/routes/agent_api.py)).

**Rationale**: Satisfies FR-014/015/016 with the smallest change — the running game keeps its own copy; later profile edits don't mutate live games. Reuses the existing per-player strategy mechanism.

### Decision 6: Caps — per-bot on the row, platform in config

**Chosen**: `bots.max_concurrent_games` (owner-set, sensible low default e.g. 3). Platform caps (`max_concurrent_active_games`, and reuse `Game.max_players`) live in `app/config.py` settings. Enforce at entry/start.

**Rationale**: Per-bot is user data (belongs on the row); platform caps are operational and env-tunable. Avoids a settings table for two values.

### Decision 7: Pause / kill switch + stall detection

**Chosen**: `bots.status` (`active` | `paused`) with `paused_at` / `paused_reason`. Paused bots are skipped by `next-turn` and rejected (with a clear reason) by the game-scoped turn fetch. Stall = count of trailing `was_defaulted` submissions for the bot's player in a game (computed from existing `TurnSubmission.was_defaulted`); crossing a configured threshold flags the bot and may auto-pause it.

**Rationale**: Reuses existing default-tracking; no new per-turn write path. Surfaces FR-018/020.

### Decision 8: Future auto-join seam (NOT built)

**Chosen**: Document that a future `subscriptions` table will reference `bot_id` and be consulted by the existing scheduler poller ([scheduler.py:119](../../app/engine/scheduler.py)). Nothing in this design blocks it. No subscription code, table, or poller change in this feature.

**Rationale**: FR-023 — keep the seam clean, build nothing.

### Decision 9: `0003` is a data-affecting migration

**Chosen**: Migration `0003_persistent_bots` creates `bots` and `strategy_profiles`, adds `players.bot_id` (NOT NULL) + `UNIQUE(bot_id, game_id)`, and drops `players.agent_key_hash`. Because adding NOT NULL `bot_id` to existing rows has no valid backfill under a fresh-start cutover, the migration **clears throwaway in-flight game data** (players, turns, turn_submissions, strategy_prompts) first. SQLite column drops use Alembic batch mode (as in existing migrations).

**Rationale**: Honest realization of the confirmed "fresh start, no migration" decision. Flagged per the data-critical-waves rule; must be reviewed before prod apply. Tests build schema from model metadata, so this does not affect the in-memory test DB (confirm during implementation).

---

## Project Structure

### Monolithic FastAPI app (`app/`) + MCP wrapper (`mcp_server/`)

```
app/
├── models/
│   ├── bot.py                    - NEW: Bot entity (key_lookup, status, caps, stall)
│   ├── strategy_profile.py       - NEW: user-level reusable strategy
│   ├── player.py                 - MODIFY: add bot_id FK + UNIQUE(bot_id, game_id); drop agent_key_hash
│   └── __init__.py               - MODIFY: register new models
├── engine/
│   ├── tokens.py                 - MODIFY: add generate_bot_key() + sha256 lookup helper
│   ├── bot_credentials.py        - NEW (optional): issue/hash/verify bot keys (keep tokens.py focused)
│   ├── next_turn.py              - NEW: select most-urgent open turn across a bot's games (pure, testable)
│   └── caps.py                   - NEW: per-bot + platform cap checks (pure, testable)
├── routes/
│   ├── agent_api.py              - MODIFY: swap require_agent_key→require_bot+resolver on game-scoped endpoints
│   ├── agent_next_turn.py        - NEW: GET /api/agent/next-turn
│   ├── bots_web.py               - NEW: /me/bots panel (create, list, detail, reissue, pause/resume, delete)
│   ├── strategy_profiles_web.py  - NEW: /me/strategy-profiles CRUD
│   └── web.py                    - MODIFY: join flow becomes "enter a bot" (pick bot + profile), no key shown
├── deps.py                       - MODIFY: add require_bot (indexed); remove require_agent_key player-scan
├── config.py                     - MODIFY: platform caps (max_concurrent_active_games)
├── schemas/
│   └── agent.py                  - MODIFY: NextTurn response schema (reuse WaitingResponse/TurnStatic/summary)
└── templates/
    ├── bots/                     - NEW: list + detail (connection snippet shown once, status, games)
    ├── strategy_profiles.html    - NEW
    ├── join.html                 - MODIFY: select a bot; no per-game key/snippet
    └── connection.html           - MODIFY/RETIRE: superseded by bots/ panel

mcp_server/
└── server.py                     - MODIFY: add get_next_turn() tool; update setup-prompt wording to the multi-game loop

migrations/versions/
└── 0003_persistent_bots.py       - NEW: data-affecting (see Decision 9)

tests/
└── (new) test_bot_auth, test_next_turn, test_caps, test_enter_game, test_strategy_profiles, test_pause
```

**Structure Decision**: All business logic lives in `app/` (models/engine/routes/templates); the MCP layer in `mcp_server/server.py` stays a thin wrapper that adds one tool and calls the new endpoint. Pure decision logic (`next_turn.py`, `caps.py`) is split out so it is unit-testable without the DB, per the engine-testing constitution rule.

---

## Architecture Compliance

- **Security** (`CLAUDE.md` → Never Do / Python Standards): hashed credentials, one-time plaintext, no secrets in logs — Decisions 1, 7; FR-002/024.
- **Performance** (spec SC-004): indexed O(1) auth — Decision 1.
- **Testing** (`CLAUDE.md` → Testing Requirements): pure engine modules + SQLite-in-memory integration tests — Project Structure, FR-025.
- **Async + types + no suppressions** (`CLAUDE.md` → Python Standards): enforced in Constitution Check; verified by the preflight gate (`ruff`, `mypy app/ mcp_server/`, `pytest`).
- **Data-critical migration** (global data-critical-waves rule): Decision 9 flags review-before-prod.
