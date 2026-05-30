# Quickstart: Live Connection Handshake for Bot Onboarding

## Prerequisites

- [ ] Dev server running on `http://localhost:8766` (`preview_start hoard-hurt-help`, or `.venv/bin/uvicorn app.main:app --port 8766 --reload`)
- [ ] A fresh dev DB built from the models (tests/dev use `create_all`; if migrating Postgres, `alembic upgrade head`)
- [ ] Signed in with Google (the bot pages are owner-only)
- [ ] A way to simulate an authenticated agent call (the runner, an MCP client, or `curl` with `X-Agent-Key`)

## Testing User Story 1: Confirm the bot connected, live

**Goal**: The detail page flips from "Waiting…" to "✓ Connected" with no reload.

**Steps**:
1. Create a bot at `/me/bots` → land on `/me/bots/{id}` with the fresh-key setup message.
2. Leave the page open. Confirm the status panel reads "Waiting for your bot to connect…".
3. Make one authenticated agent call with the bot's key (e.g. `GET /api/agent/next-turn` with `X-Agent-Key: <key>`, or run the runner).

**Expected**:
- Within a few seconds the panel updates in place to "✓ Your bot connected. Last step: get it into a game." + "Join a game →".
- Reload the page → it still shows the connected state (durable).

## Testing User Story 2: Guided from connected to playing

**Goal**: A connected, gameless bot is never a dead end.

**Steps**:
1. With the connected bot (no games), view the panel and the Games section.

**Expected**:
- Panel shows the "last step: get it into a game" message with a primary "Join a game →".
- Games empty state reads "Connected but not in a game yet — that's the last step. Join a game →".
- Following the action lands on the join path for an open game.

## Testing User Story 3: See the first move

**Goal**: The first move ends onboarding on a clear win.

**Steps**:
1. Join the bot into an open game (via the lobby/join path).
2. With the detail page open, let the bot take (or simulate) its first submitted action.

**Expected**:
- Before the move: "✓ In '[game]'. Waiting for its first move…".
- After the move: panel updates in place to "✓ [bot] just made its first move. Watch it live →" linking to the game viewer.
- Reload after the fact → shows the calm "playing in '[game]' — Watch live" state, not a re-run celebration.

## Testing User Story 5: Don't lose the key

**Steps**:
1. On the fresh-key view, find the quiet "the code won't show again — lost it? reissue" line.
2. Reissue; confirm a new setup message appears and the old code stops working.

## Testing User Story 6: Returning operator

**Steps**:
1. Open the detail page of a bot that has already connected and moved.

**Expected**:
- No large waiting/celebration block — just a quiet status line.

## Troubleshooting

**Issue**: Page shows blank / server won't start with "no such column: bots.first_connected_at".
**Fix**: The dev DB predates the model. Rebuild it from the models (`Base.metadata.create_all` after backing up the old `.db`); the broken SQLite migration chain is a separate pre-existing issue.

**Issue**: Panel never leaves "Waiting…" after connecting.
**Fix**: Confirm the agent call actually authenticated (200, not 401). A bad/stale key resolves to no bot and is the passive-nudge case by design — reissue and paste again.
