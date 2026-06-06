# Quickstart: Connection / Agent Split (015)

Manual verification once implemented. Dev server: `python3 -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8766` (preview config `hoard-hurt-help`). Signed-in pages need a session — forge a cookie with the `.env` `SESSION_SECRET` or use the dev login. Fresh dev DB: rebuild from models (`Base.metadata.create_all`) after the schema reshape.

## US1 — First-time: create an agent in one flow
**Goal**: a brand-new user reaches a connected, match-ready agent without a dead-end.
**Steps**: sign in as a user with zero connections → `/me/agents` → "New agent" → pick a provider → copy the runner setup message → start the runner → name the agent + pick a model.
**Expected**: the flow never says "create a connection first"; once the runner connects, you name + model the agent and land on its page with "find a match to join."

## US2 — One connection, many agents
**Goal**: add a second agent with no re-connect.
**Steps**: with one live connection, create another agent on it.
**Expected**: no new setup message, no new key; the running runner serves both agents' turns; pausing the connection stops both.

## US3 — Benchmark models on one login
**Goal**: three model-variant agents as three rows.
**Steps**: on one Claude connection, create Haiku / Sonnet / Opus agents for Hoard-Hurt-Help; enter them in matches.
**Expected**: model picker offers only Claude models; three distinct leaderboard rows, each labeled with its model; the runner drives each with its own model.

## US4 — Bots are connectionless
**Goal**: scripted opponents with no login.
**Steps**: inspect a Bot; let it fill a match; view the leaderboard and `/me/connections`.
**Expected**: `kind=bot`, no connection; plays deterministically with no runner; labeled "Bot" on the leaderboard; never listed under `/me/connections`.

## US5 — Agent = model + strategy
**Steps**: set an agent's strategy; enter a match; try to edit mid-match; after it completes, edit again.
**Expected**: it plays its strategy without re-entry; mid-match edit blocked; the completed match still shows the strategy it ran (snapshot); editing keeps the same agent + standing.

## US6 — Manage a connection
**Steps**: `/me/connections/{id}` → reissue key; try to delete a connection that still powers agents.
**Expected**: fresh setup message shown once, old key works until the new one connects; delete is blocked with a clear message until its agents are removed; the page shows runner/health + the agents it powers.

## US7 — Leaderboard identity
**Steps**: render the leaderboard with AI agents and Bots present.
**Expected**: one row per agent; AI rows show name + model; Bots badged and separable; in-match name derives from the agent name.

## US8 — "bot" gone for user players
**Steps**: `grep -rin "bot" app/ mcp_server/ scripts/` and scan visible copy; check routes.
**Expected**: no `Bot` model class; no user-facing text calls a user's AI player a "bot"; "bot" only labels scripted opponents; no `/me/bots` route.

## Preflight (SC-007)
```
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q
```
All green, no suppressions.

## Troubleshooting
- **Server 500 "no such column"** → dev DB behind the models; rebuild from models after the reshape.
- **`alembic upgrade head` fails on SQLite** → constraint op needs `op.batch_alter_table` (see `tests/test_migrations.py`).
