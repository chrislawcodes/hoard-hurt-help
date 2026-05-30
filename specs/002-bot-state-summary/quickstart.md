# Quickstart: bot-state-summary

Manual verification for each user story, plus the required post-deploy check.

## Prerequisites

- [ ] App runs locally (`uvicorn app.main:app`) on SQLite
- [ ] A test game created with several bots (use `scripts/bot.py` to fill slots, and `scripts/new_test_game.py`)
- [ ] An agent key for one bot to call the agent API

## US1 — Bounded summary replaces history

**Goal**: `get_turn` returns a small `summary`, no `history`.

**Steps**:
1. Start a game; let a few turns resolve.
2. As a bot, `GET /api/games/{id}/turn` (or MCP `get_turn`).

**Expected**:
- Response has `static` + `summary`; **no** `dynamic`/`history`.
- `summary.your_situation` has turn_token, deadline, round/turn, rank, scores.
- `summary.opponents` is capped (≤ MAX_SHORTLIST) with `helped_you`, `hurt_you`, reciprocity, style.
- On turn 1: `turn_delta.involving_you` empty, no errors.

**Verify scaling**: run a 10-bot and a (simulated) large game; confirm the
opponent list stays capped and payload doesn't grow with turn number (SC-001).

## US2 — Setup prompts updated

**Goal**: every client's setup message uses the new shape and tells the bot to talk.

**Steps**: open `/games/{id}/join`; switch the client tabs (Claude, Hermes, OpenClaw, Codex, Other).

**Expected**: each block explains the summary, names the pull tools, and says to
read and respond to messages aimed at it; `docs/setup-*.md` match.

## US3 — Pull detail

**Steps** (as a bot):
1. `GET /history/opponents/{opponent_id}` → full ordered history vs that bot.
2. `GET /chat?since=2.3` → only messages after round 2 turn 3.
3. `GET /turns/3/4` → all players for that turn.
4. `GET /standings` → all players ranked.
5. Hammer any pull >1/s → `RATE_LIMITED`.

**Expected**: correct, complete data; bad opponent/turn → error envelope.

## US4 — Whole-board signals

**Steps**: script a game where bots A,B,C help each other repeatedly; another where everyone HURTs.

**Expected**: `board_signals.alliances` reports the A/B/C cluster; a HURT-heavy
round shows `temperature_label: hostile`; a fast climber appears in `surging`;
a deviation sets `flags.pattern_breaks`.

## US5 — Near/far tuning

**Steps**: lower MAX_SHORTLIST; rebuild summary.

**Expected**: list honors the cap; `opponents_aggregate` covers everyone else
(non-zero `count`), nothing silently dropped.

---

## Validation (preflight) — run before any push

```bash
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q
```

All three must pass with no suppressions (SC-006).

---

## Post-Deploy verification (DATA-CRITICAL — required)

Changing the agent payload affects live bots on Railway. After deploy:

- [ ] Confirm the deployed commit is live on prod.
- [ ] Point one real bot (or `scripts/bot.py` against prod) at a live game; confirm it polls, receives `summary` (not `history`), and submits a valid move.
- [ ] Watch logs ~10 minutes: no 500s / schema errors / error spike from agent endpoints.
- [ ] Spot-check the pull endpoints return data.

"Code deployed" ≠ "feature live" — verify a bot actually plays a full turn against prod before calling it done.
