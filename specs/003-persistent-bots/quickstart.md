# Quickstart: Persistent Bots (manual testing)

## Prerequisites

- [ ] App running locally (FastAPI + MCP at `/mcp`).
- [ ] Migration `0003` applied (note: it clears throwaway game data — see data-model.md).
- [ ] Signed in with Google.
- [ ] An MCP client (Claude / Hermes) to paste the bot key into.

---

## US1 — Create a bot, get a stable key once

**Steps**:
1. Go to `/me/bots`, create a bot named "Atlas".
2. Note the one-time `sk_bot_...` key and the paste-once MCP snippet.
3. Reload `/me/bots/{id}`.

**Expected**:
- Plaintext key shown exactly once; after reload only the `…hint` and a "reissue" button appear.
- DB stores `key_lookup` (sha256) + `key_hint`, never the plaintext.

**Verify reissue**: click reissue → new key shown; an API call with the OLD key now returns `401 INVALID_KEY`.

---

## US2 — Connect once, play every game

**Steps**:
1. Paste Atlas's MCP snippet into the client once.
2. Enter Atlas into two games (US3) and let both start.
3. In the client, run the loop: `get_next_turn` → act with `submit_action(game_id,…)` → repeat.

**Expected**:
- `get_next_turn` returns the nearest-deadline open turn with its `game_id`.
- After acting in the urgent game and calling again, the second game's turn is returned.
- When neither game awaits Atlas, `get_next_turn` returns `status: waiting` with `next_poll_after_seconds` — not an error.
- Client config is never edited between games.

---

## US3 — Enter a bot into a game (no new key)

**Steps**:
1. Open a registering game's `/games/{id}/join`.
2. Choose bot "Atlas", an in-game name, and a strategy profile; submit.

**Expected**:
- A player is created for Atlas; **no plaintext key is shown**.
- Entering Atlas into the same game again → `DUPLICATE_ENTRY`.
- Entering a second bot "Borealis" into the same game works → two players, two independent connections (FR-012).

---

## US4 — Strategy profiles

**Steps**:
1. `/me/strategy-profiles`: create "Tit-for-tat" and "Always Hoard"; mark one default.
2. Enter a bot choosing "Tit-for-tat".
3. Edit "Tit-for-tat" text afterward.

**Expected**:
- New player's strategy equals the profile text at entry time.
- Editing the profile does **not** change the running player's strategy (copy-at-entry).

---

## US5 — Control panel & kill switch

**Steps**:
1. With Atlas in two active games, open `/me/bots`.
2. Pause Atlas; then resume.

**Expected**:
- Panel lists both games, Atlas's last action time, and per-game score.
- While paused, `get_next_turn` returns `bot_paused` and game-scoped fetches return `403 BOT_PAUSED`; resume restores play.
- Pulling Atlas from a registering game frees its seat.

---

## US6 — Caps

**Steps**:
1. Set Atlas `max_concurrent_games = 1`; it's already in one game.
2. Try to enter a second game.
3. (Admin) lower platform `max_concurrent_active_games` below current and try to start another.

**Expected**:
- Second entry refused with `BOT_CAP_REACHED` naming the cap.
- Platform-cap breach refused with a clear reason; full game → `GAME_FULL`.

---

## US7 — Stall safety

**Steps**:
1. Connect Atlas, then stop responding so it misses turns.

**Expected**:
- After `stall_threshold` consecutive missed (defaulted) turns, the panel flags Atlas as stalling with the count, and it is auto-paused or prominently recommended for pause, with a recorded reason.

---

## Troubleshooting

- **`401 INVALID_KEY`** → key reissued elsewhere; re-paste the current key from `/me/bots/{id}`.
- **`get_next_turn` always `waiting`** → bot is in no ACTIVE game, or it already submitted this turn, or it's paused.
- **`404 NOT_IN_GAME`** on a game-scoped call → that bot has no player in that game.
