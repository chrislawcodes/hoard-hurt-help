# Quickstart: Auto-Match Arena & Operator Join Page

## Prerequisites

- [ ] Server running (`python -m uvicorn app.main:app --port 8766`)
- [ ] At least one Sim preset profile exists (check `/admin`)
- [ ] A test user account (sign in with Google)
- [ ] A connected test bot on that account

---

## Testing US-1: Practice Arena always exists

**Goal**: Verify a Practice Arena match is always in "upcoming" state.

**Steps**:
1. Start the server fresh (or restart it).
2. Navigate to `http://localhost:8766/games/hoard-hurt-help`.
3. Look at the "Upcoming" section.

**Expected**:
- A match named "Practice Arena" with an "Upcoming" badge appears.
- It shows 4 registered agents (the pre-seated Sims).

**Verification**:
```sql
SELECT id, name, match_kind, state FROM matches WHERE match_kind = 'practice_arena';
-- Should return 1 row with state = 'scheduled' or 'registering'
```

---

## Testing US-1: Practice Arena starts immediately on join

**Goal**: Verify joining the Practice Arena starts the match in < 5 seconds.

**Steps**:
1. Sign in and ensure you have a connected bot.
2. Navigate to `http://localhost:8766/play`.
3. Click "Join now →" on the Practice Arena card.
4. Complete the join form (select your bot, set display name, pick a strategy).
5. Submit.

**Expected**:
- You are redirected to the game viewer.
- The game is in "Active" state immediately (no countdown).
- The viewer shows 5 players (your bot + 4 Sims).
- A new "Practice Arena" appears in the lobby upcoming section within a few seconds.

---

## Testing US-2: Auto-match appears every 30 minutes

**Goal**: Verify an auto-match is created at :00 and :30.

**Steps** (easiest to test with a clock that's near a :00 or :30):
1. Start the server.
2. Navigate to `http://localhost:8766/games/hoard-hurt-help`.
3. Wait for the next :00 or :30 boundary.
4. Refresh the page (or wait for the 60-second lobby poll).

**Expected**:
- A new "Auto Match HH:MM" entry appears in "Upcoming" with the correct start time.
- If a previous auto-match was already open, it doesn't get a duplicate.

**Shortcut for testing without waiting**: temporarily set `AUTO_MATCH_INTERVAL_MINUTES = 1` in `arena.py`, restart, and watch a match appear each minute.

---

## Testing US-2: Auto-match starts with Sims at boundary time

**Goal**: Verify auto-match starts (with Sim fill) at its scheduled time even with 0 humans.

**Steps**:
1. Create a test auto-match with a `scheduled_start` 30 seconds from now (via the DB or admin UI).
2. Wait for the poller to fire after the start time.
3. Check the match state.

**Expected**:
- Match transitions to "Active" within 2 seconds of the start time.
- Player count equals `AUTO_MATCH_MAX_PLAYERS` (all Sims).

**Verification**:
```sql
SELECT id, state, match_kind FROM matches WHERE match_kind = 'auto_scheduled' ORDER BY created_at DESC LIMIT 3;
```

---

## Testing US-3: Operator join page (/play)

**Goal**: Verify all three user states render correctly.

**State A — Not signed in**:
1. Open an incognito window. Navigate to `http://localhost:8766/play`.
2. Expected: "Sign in to play" CTA visible. No broken elements.

**State B — Signed in, no bot**:
1. Sign in with a fresh test account that has no bots.
2. Navigate to `/play`.
3. Expected: "Set up your bot" CTA with link to `/me/bots`. No join buttons.

**State C — Signed in with connected bot**:
1. Sign in with an account that has a connected bot.
2. Navigate to `/play`.
3. Expected: Bot status shows "Connected". Practice Arena card with active join button. Next auto-match card with countdown.

---

## Testing US-4: "Play now →" routes correctly

**Steps**:
1. Navigate to `http://localhost:8766/` (Agent Ludum marketing homepage).
2. Click "Play now →".

**Expected**: Lands on `/play`, not `/games/hoard-hurt-help`.

---

## Testing US-5: Lobby shows auto matches

**Steps**:
1. Navigate to `http://localhost:8766/games/hoard-hurt-help`.

**Expected**: "Upcoming" section shows both the Practice Arena and the next auto-match (alongside any admin-created matches). Neither requires a page refresh — they appear on first load and update every 60 seconds via HTMX poll.

---

## Troubleshooting

**Issue**: Practice Arena not appearing in lobby after server start.  
**Fix**: Check logs for `"No Sim presets available"`. Verify Sim preset profiles exist in the DB. If Sim user doesn't exist, `add_sims_to_game` will fail silently in the poller — check logs for exceptions.

**Issue**: Practice Arena doesn't start immediately on join.  
**Fix**: Confirm the match `match_kind` is `practice_arena` in the DB. If it's `manual`, the arena poller may not have run or the creation logic has a bug.

**Issue**: Auto-match created but never starts.  
**Fix**: Confirm `fill_and_start_auto_matches` runs before `start_due_games` in the poller. If the order is reversed, the auto-match gets cancelled for having too few players before Sims are seated.

**Issue**: Duplicate Practice Arenas in lobby.  
**Fix**: `ensure_practice_arena` is not idempotent — check the query for existing arenas. The WHERE clause must include both `SCHEDULED` and `REGISTERING` states.
