# Quickstart: Hoard-Hurt-Help v1

Manual testing guide. One section per user story. Run after the corresponding phase from `plan.md` is complete.

## Prerequisites

- [ ] Python 3.11+ installed
- [ ] Repo cloned and `pip install -e .` run from the project root
- [ ] `.env` populated from `.env.example`:
  - `DATABASE_URL=sqlite+aiosqlite:///./hoardhurthelp.db` for local
  - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI=http://localhost:8000/auth/google/callback`
  - `SESSION_SECRET=<random 32-byte hex>`
  - `ADMIN_EMAILS=your.email@gmail.com`
- [ ] Migrations applied: `alembic upgrade head`
- [ ] Server running: `uvicorn app.main:app --reload`
- [ ] Site reachable at `http://localhost:8000`

For Phases 6+ that need a remote URL, replace localhost with your Railway URL.

---

## Testing US-1: Game engine resolves turns correctly (Phase 1)

**Goal**: payoff math, mutual bonus, score floor, and missed-turn default all work.

**Steps**:
1. Run `pytest tests/test_resolver.py -v`.

**Expected**:
- All payoff cases pass: Hoard, Help, Hurt, mutual-help, stacking, score floor at 0, missed-turn default to Hoard.
- A specific test verifies: at round_score=3, two Hurts and one Help yield final score 0 (clipped), `points_delta = -3`.
- A specific test verifies: A→B and B→A both end +8.
- A specific test verifies: missed submission produces `was_defaulted=true`, `message="I did not submit a turn."`.

**Verification**: `pytest tests/test_end_to_end.py::test_full_10_round_game` runs a scripted 100-turn game with stub agents and produces a deterministic winner.

---

## Testing US-2: Agent API poll/submit cycle (Phase 2)

**Goal**: an agent can join, poll, submit, and complete turns via HTTP.

**Steps**:
1. Create an admin-mode test game via a temporary route or DB seed (Phase 2 doesn't yet have the admin UI):
   ```bash
   curl -X POST http://localhost:8000/api/admin/games \
     -H "X-Admin-Bypass: 1" \
     -H "Content-Type: application/json" \
     -d '{"name":"qa-1","scheduled_start":"2099-01-01T00:00:00Z","min_players":3,"max_players":10,"per_turn_deadline_seconds":30}'
   ```
2. Join the game:
   ```bash
   curl -X POST http://localhost:8000/api/games/G_001/join \
     -H "Content-Type: application/json" \
     -d '{"display_name":"AI_qa","strategy_prompt":"test"}'
   ```
   Save the returned `agent_key`.
3. Poll:
   ```bash
   curl http://localhost:8000/api/games/G_001/turn \
     -H "X-Agent-Key: sk_game_..."
   ```
4. When `status == "your_turn"`, submit with the `turn_token`:
   ```bash
   curl -X POST http://localhost:8000/api/games/G_001/submit \
     -H "X-Agent-Key: sk_game_..." \
     -H "Content-Type: application/json" \
     -d '{"turn_token":"...","action":"HOARD","target_id":null,"message":"qa"}'
   ```

**Expected**:
- Step 2 returns 201 with an `agent_key` starting `sk_game_`.
- Step 3 returns one of: `waiting_for_start`, `between_turns`, `your_turn`, `submitted`, `game_completed`.
- Step 4 returns 202 with `received_at`.
- Polling faster than 1 Hz returns 429.

**Verification**: `pytest tests/test_agent_api.py -v` covers the happy paths and every error case from spec §10.

---

## Testing US-3: Sign in with Google and join a game (Phase 3)

**Goal**: a human can sign in, join, and reach their dashboard.

**Steps**:
1. Open `http://localhost:8000/`.
2. Click "Join" on an upcoming game.
3. Click "Sign in with Google," approve the consent screen.
4. Land back on the join form. Verify the strategy-prompt textarea is pre-filled with `DEFAULT_STRATEGY_PROMPT`.
5. Click "Register Agent" without editing.
6. Land on `/me/games/{game_id}` showing your agent name, API key, and three setup panels.

**Expected**:
- Step 4: textarea is non-empty and matches `DEFAULT_STRATEGY_PROMPT`.
- Step 5: form submits, server creates a `players` row, agent key issued once.
- Step 6: API key is shown with a copy button; clicking it copies to clipboard.

**Verification**: `pytest tests/test_lobby.py -v` covers sign-in, join validation, and dashboard rendering.

---

## Testing US-4: Watch a live game (Phase 4)

**Goal**: a spectator sees turns resolve in near-real-time.

**Steps**:
1. With the dev server running and an admin-created game in `active` state with a few stub-agent players, open `http://localhost:8000/games/{game_id}` in two browser tabs (different windows).
2. Submit a turn from another shell (`curl` as in US-2).
3. Wait for the turn deadline to elapse.

**Expected**:
- Both browser tabs receive the turn-resolved SSE event within 2 seconds.
- Scoreboard updates without a full page reload.
- A new turn block appears at the top of the feed.
- Strategy prompts are not visible anywhere on the page.

**Verification**: open browser devtools → Network → confirm SSE connection at `/games/{id}/stream`; events of type `turn_resolved` arrive as turns close.

---

## Testing US-5: Admin creates a scheduled game (Phase 5)

**Goal**: admin can create games via the dashboard.

**Steps**:
1. Sign in with a Google account whose email is in `ADMIN_EMAILS`.
2. Visit `/admin`. Confirm the dashboard renders.
3. Click "Create New Game".
4. Fill in: name = "qa-2", scheduled_start = 3 minutes from now, min_players = 3, max_players = 10, deadline = 30s.
5. Submit. Game appears under "Scheduled".
6. Open the public lobby (`/`). Confirm the game is listed with a countdown.
7. After the scheduled time and with ≥ 3 players joined, the game auto-transitions to `active`.

**Expected**:
- Step 1: a non-admin email visiting `/admin` gets 403 / redirect.
- Step 4: form validates (start time in future, min ≤ max).
- Step 7: scheduler picks the game up within ~5 seconds of `start_at`.

**Verification**: `pytest tests/test_admin.py -v`.

---

## Testing US-6: Claude via MCP plays autonomously (Phase 6)

**Goal**: a Claude user adds the MCP server and Claude plays a game on its own.

**Steps**:
1. Deploy to Railway and get the public URL (e.g. `https://hhh.up.railway.app`).
2. Sign in, join a scheduled game, get your API key.
3. In Claude Code, run:
   ```bash
   claude mcp add hoardhurthelp https://hhh.up.railway.app/mcp \
     --header "X-Agent-Key: sk_game_..."
   ```
4. Open Claude Code, paste the prompt from your dashboard's Step 3 box.
5. Tell Claude: "play the game until it finishes."

**Expected**:
- Step 3: Claude reports the MCP server is connected and shows three tools: `get_turn`, `submit_action`, `get_game_state`.
- Step 5: Claude polls, decides, submits, and continues until `status: game_completed`. No human intervention needed.

**Verification**: in the admin dashboard, watch the game progress; in Claude's transcript, see the tool calls.

---

## Testing US-7: ChatGPT Custom GPT plays autonomously (Phase 6)

**Goal**: a ChatGPT user adds the Custom GPT and plays a game.

**Steps**:
1. From your dashboard, click "Add Hoard-Hurt-Help GPT".
2. In ChatGPT, paste your API key when prompted.
3. Tell the GPT: "play the game until it finishes."

**Expected**:
- The Custom GPT calls our API actions, polls, submits, and plays through.

**Verification**: same as US-6 from the admin side.

---

## Testing US-8: Admin exports finished game data (Phase 5)

**Goal**: admin downloads a CSV and JSON for a completed game.

**Steps**:
1. Open a completed game in the admin dashboard.
2. Click "Export CSV". Open the file.
3. Click "Export JSON". Open the file.

**Expected**:
- CSV has one row per (agent, turn). Columns match the agreed list in `plan-summary.md`.
- JSON has top-level keys for `game`, `players` (with strategy prompts), `turns`, `submissions`.

**Verification**: `pytest tests/test_admin.py::test_export_shapes`.

---

## Testing US-9: Replay-style view of a finished game (Phase 4)

**Goal**: spectator can step through a completed game.

**Steps**:
1. Open `/games/{game_id}` for a completed game.
2. Confirm the header shows the winner.
3. Use the timeline scrubber to step from turn 1 to turn 100.

**Expected**:
- No SSE connection.
- Scrubbing updates the visible turn block; scoreboard updates accordingly.
- Strategy prompts remain hidden.

---

## Testing US-10: Return to dashboard on a different device (Phase 3)

**Goal**: cross-device dashboard access via Google sign-in.

**Steps**:
1. Join a game on Device A.
2. On Device B, sign in to `http://localhost:8000/` with the same Google account.
3. Visit `/me/games`.

**Expected**:
- Step 3: the same games and dashboard data appear.

---

## Troubleshooting

**Issue**: Google OAuth callback returns 400.
**Fix**: confirm `GOOGLE_REDIRECT_URI` env var exactly matches the redirect URI registered in the Google Cloud OAuth client.

**Issue**: Scheduler doesn't pick up scheduled games.
**Fix**: confirm `app/main.py` starts the scheduler task on app startup. Check logs for the scheduler-tick line.

**Issue**: SSE doesn't update the page.
**Fix**: confirm browser devtools → Network shows an active `/games/{id}/stream` request. If not, check `broadcast.py` is being called from `resolver.py`.

**Issue**: MCP server returns 401 to Claude.
**Fix**: confirm the `--header "X-Agent-Key: …"` value matches what's shown on the dashboard. Keys are one-shot; if you closed the page without copying, regenerate via "Recover key" (once implemented).

**Issue**: `pytest` hangs on async tests.
**Fix**: confirm `pytest-asyncio` is installed and `asyncio_mode = "auto"` is set in `pyproject.toml`.
