# Research: Persistent Bots

## Question 1: How to make credential auth O(1) without weakening security?

**Context**: Today `require_agent_key` ([deps.py:78](../../app/deps.py)) selects *all* players and argon2-verifies the presented key against each hash — O(n) and explicitly flagged as v1-only. SC-004 requires this not to degrade with scale.

**Options Investigated**:

1. **Keep argon2, add an indexed `key_prefix`**
   - Pros: keeps existing hashing.
   - Cons: prefix collisions still need per-candidate argon2 verify; argon2's slow KDF buys nothing on a 192-bit random token; the prefix leaks bytes of the secret.

2. **`sha256(full_key)` stored as a UNIQUE indexed `key_lookup`** (chosen)
   - Pros: exact-match O(1) by unique index; correct primitive for high-entropy API keys (argon2 is for *guessable* human passwords); no salt needed because input is unique and 192-bit random; constant-time final compare via `hmac.compare_digest`.
   - Cons: unsalted hash — irrelevant here (no rainbow table beats 192-bit randoms).

3. **Opaque split token `sk_bot_<token_id>_<secret>`** (indexed id + argon2 secret)
   - Pros: classic "API key id + secret" shape.
   - Cons: reshapes the key, adds parsing; no real gain over option 2 for our scale.

**Decision**: Option 2. Generate `sk_bot_<48 hex>`, store `sha256(key)` (unique index) + a 4-char display hint, verify by hashing the presented key and matching the index.

**Rationale**: Meets SC-004 directly, is the standard treatment for random API tokens, and stays within the constitution's "no security theater / fix the root cause" spirit. argon2 remains available but is unnecessary for these tokens; documented so a future reviewer doesn't read the change as a downgrade.

---

## Question 2: How do the existing game-scoped endpoints map a bot key to a player?

**Context**: A bot owns many players (one per game). The old key→single-player assumption no longer holds.

**Decision**: Add `require_bot -> Bot` (indexed lookup). For each game-scoped endpoint, resolve the bot's active `Player` via `SELECT ... WHERE bot_id = :bot AND game_id = :game AND left_at IS NULL`. Backed by `UNIQUE(bot_id, game_id)` so it returns exactly one (or 404 `NOT_IN_GAME`). The rate-limiter key moves from `player.id` to `bot.id`.

**Rationale**: Smallest change to endpoint bodies; preserves every existing response shape; the unique constraint makes resolution unambiguous (FR-010).

---

## Question 3: Defining "most urgent" for `get_next_turn`

**Context**: A bot may have open turns in several games at once.

**Decision**: Return the open, unresolved turn with the nearest `deadline_at` among the bot's active, non-paused players whose game is ACTIVE and where the player has not already submitted (mirroring the `already_submitted` skip at [agent_api.py:327](../../app/routes/agent_api.py)). Ties broken by `game_id`, then `round.turn`. If none, return `waiting` with `next_poll_after_seconds`.

**Rationale**: Nearest deadline minimizes missed turns across games. Deterministic tie-break keeps the loop predictable and testable (`app/engine/next_turn.py` as a pure function over candidate rows).

---

## Question 4: Where do platform caps live?

**Context**: Per-bot cap is user data; platform caps are operational.

**Decision**: Per-bot `max_concurrent_games` on the `bots` row; platform `max_concurrent_active_games` in `app/config.py` settings (env-tunable); reuse `Game.max_players` for the per-game cap. No settings table for two values.

**Rationale**: Matches how the project already configures operational knobs; avoids premature schema.
