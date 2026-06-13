# Spec — BYO Terminal: Mode A (interactive MCP play) v1

**Feature branch:** `feat/byo-terminal-mode-a`
**Created:** 2026-06-13
**Status:** Draft (spec stage)
**Run:** `docs/workflow/feature-runs/byo-terminal-mode-a/`

## Summary

Players can already play by pointing any MCP client at our existing MCP server
(`mcp_server/server.py`, mounted at `/mcp`) with their `sk_conn_` connection key.
This feature turns that into a first-class, **watchable, trust-first** way to
play — "Mode A" — that coexists with the always-on connector. The user runs
their own AI in their own client, watches it play, and installs nothing.

The MCP path already works. This feature is the **plumbing around it**: make idle
waiting stop burning tokens, make usage visible, and make connecting easy on any
client. It is explicitly *not* a front-door redesign — that's a later pass.

Design context lives in `docs/platform/AGENT_LUDUM_DESIGN.md` §13 and
`AGENT_LUDUM_ARCHITECTURE.md` (§9, Flow A, change-index).

## User Scenarios & Testing

### User Story 1 — Watch my own AI play, nothing installed (Priority: P1)

As a trust-wary player, I want to point the AI I already have at the game and
watch it play in my own terminal/app, so I never have to install a background
service that reads my credentials.

**Why this priority:** This is the whole reason the feature exists — the alpha
user was blocked by both corporate DNS and their own AI refusing the connector
install. Without this, the trust wall stands.

**Independent test:** Connect a client with a `sk_conn_` key, paste the play
prompt, confirm the AI calls `get_next_turn`, plays a real turn, and the move
appears in the web viewer — with nothing installed beyond the MCP connection.

**Acceptance Scenarios:**
1. **Given** a valid `sk_conn_` key configured as the MCP header, **When** the user tells their AI to play, **Then** the AI plays turns through the existing MCP tools and the moves resolve in the match.
2. **Given** the user closes their client, **When** their next turn comes up, **Then** the absent player defaults to Hoard (existing rule) and the match continues; **When** the user reconnects, **Then** they resume taking their own turns. (No new engine code — reuse.)

### User Story 2 — Idle waiting doesn't drain my tokens (Priority: P1)

As a player who leaves their AI running between turns, I want the "is it my turn
yet?" waiting to be cheap, so a slow match doesn't quietly burn my model quota.

**Why this priority:** In Mode A the AI itself runs the loop, so every poll is a
paid model call. At a 5-second cadence this is the dominant cost and makes Mode A
impractical to leave running.

**Independent test:** Simulate a connection waiting with no open turn; confirm the
server holds the request and the client is not prompted to re-poll every few
seconds; confirm a turn that opens mid-hold is returned promptly.

**Acceptance Scenarios:**
1. **Given** no open turn, **When** the client calls `next-turn`, **Then** the server holds the request open up to a bounded window (~25–30s) and returns `waiting` only when the window expires — not immediately.
2. **Given** a turn opens 3 seconds into the hold, **When** the hold is active, **Then** the endpoint returns the turn promptly (well under the full window).
3. **Given** the long-poll window expires, **Then** the response carries a raised `next_poll_after_seconds` (≈30s) as the fallback cadence.
4. **Given** the connector (non-interactive) polls the same endpoint, **Then** its behavior is unchanged or improved — no regression.

### User Story 3 — See my usage on the dashboard (Priority: P2)

As a player, I want to see how much my agent has been calling the server, so I
have a sense of activity and (later) cost.

**Why this priority:** Anti-surprise. Important, but the game is playable without
it, and the token-estimate layer depends on offline calibration we don't have yet.

**Independent test:** After a connection makes N calls, the dashboard shows a
per-connection figure reflecting those calls/turns.

**Acceptance Scenarios:**
1. **Given** a connection has served turns, **When** the owner views their dashboard, **Then** it shows that connection's exact turns-played count (and an approximate call count if shipped, labeled approximate).
2. **Given** many rapid calls, **When** the counter updates, **Then** it does not add an unthrottled extra DB write per call (no write-amplification regression).

### User Story 4 — Connect any common client in one step (Priority: P2)

As a player on any MCP client, I want a single play-prompt plus a one-time
connect snippet for my client, so setup is obvious.

**Why this priority:** Onboarding clarity. The play prompt is universal; only the
one-time connect step differs per client.

**Independent test:** Following the docs for a given client, a user connects and
plays without further help.

**Acceptance Scenarios:**
1. **Given** the setup docs, **When** a user follows the connect snippet for their client (Claude Code / Desktop / Codex / Gemini / Cursor), **Then** the MCP server is added and the universal play-prompt starts play.
2. **Given** the docs, **Then** they use the correct header/key (`X-Connection-Key` / `sk_conn_`), not the stale `X-Agent-Key` / `sk_bot_`.

## Edge Cases

- **Client request timeout < hold window** → the hold must stay under typical client timeouts (~25s default); if a client still times out, it retries and the raised poll-interval hint applies. (Per-client tolerance is measured in the verification matrix.)
- **Deploy / restart mid-hold** → the held request drops; the client retries (same as today's transient-error guidance). No stuck state.
- **DB connection exhaustion** → the hold must NOT keep a DB session checked out across the wait; on a single instance, many concurrent holds that each pinned a connection would exhaust the pool.
- **Sticky-pin race during a hold** → two connections waiting must not double-claim a turn; the pin is claimed only at the moment a real turn is returned, via the existing atomic conditional UPDATE.
- **Key revoked mid-session (401)** → the client stops and the user reissues the key and reconnects.
- **Counter under concurrency** → increments must not be lost or cause contention hot-spotting on the connection row.

## Requirements

### Functional Requirements

- **FR-001:** The `next-turn` endpoint MUST support a bounded long-poll: when no turn is open it holds the request up to a configurable window (default ~25s) and returns the moment a servable turn opens, otherwise returns `waiting` at expiry. The hold MUST be an **internal periodic re-check loop** using `await asyncio.sleep` between checks — not a blocking wait, and NOT dependent on an external event bus. (An event-driven wakeup via the existing in-process `app/broadcast.py` pub/sub is a permissible *later* optimization, explicitly out of scope for v1.) (US2) [revised from spec review: mechanism made explicit]
- **FR-002:** While holding, the endpoint MUST NOT keep a DB session/connection checked out across the wait — it acquires a session per check and releases it before each `asyncio.sleep`. Because the wait is `await`-based (not a blocking thread), a held request MUST NOT tie up a FastAPI worker. (US2, edge: pool exhaustion / worker starvation) [revised from spec review]
- **FR-003:** The long-poll MUST claim the sticky pin only when returning a real turn, preserving the existing atomic no-double-serve guarantee. (US2, edge: pin race)
- **FR-004:** The idle waiting poll-interval hint MUST be raised (waiting cadence ≈30s) without regressing active-turn responsiveness or the connector. (US2)
- **FR-005:** The long-poll hold duration MUST be a small constant/configurable value chosen to stay under typical MCP client request timeouts, with the poll-interval hint as the fallback. (US2, edge: client timeout)
- **FR-006:** The system MUST count, per connection, the number of **turns played**, incremented exactly once at the **act-submission** point (where a `submit_action` is persisted) — NOT at `mark_seen`. Counting at `mark_seen` would count polls and both two-phase sub-steps, not turns. (US3) [revised from spec review: HIGH — turns ≠ calls]
- **FR-006a:** The system MAY also surface an **approximate** authenticated-call count. Any call counter at `mark_seen` (which runs on every call) MUST reuse the existing heartbeat throttle (fold into the same `last_seen_at` write) or be batched — it MUST NOT add an unthrottled per-call write. An approximate call count is acceptable; if exactness would require a per-call write, the call count is deferred and only turns-played ships. (US3, edge: write-amplification) [from spec review]
- **FR-007:** Counter storage MUST be persisted via a schema migration following the repo's SQLite batch pattern, on column(s)/table chosen to avoid hot-row contention under polling load. (US3)
- **FR-008:** The player dashboard MUST display, per connection, the **turns-played** count (exact); a call count, if shipped, MUST be labeled approximate. (US3)
- **FR-009:** Setup docs MUST present one universal play-prompt plus one-time connect snippets for Claude Code, Claude Desktop, Codex, Gemini, and Cursor, using `X-Connection-Key` / `sk_conn_`. (US4)
- **FR-010:** The feature MUST NOT regress the connector path, which shares the `next-turn` endpoint. (US1, US2)
- **FR-011:** A player who disconnects mid-match MUST default to Hoard (existing rule) and be able to rejoin — with NO new engine code. (US1)
- **FR-012:** The universal play-prompt MUST teach the full loop the server actually enforces: (a) handle BOTH phases — when `current.phase == "talk"` call `submit_talk`, when `"act"` call `submit_action`; (b) save the top-level `agent_turn_token` from `get_next_turn` and resend it on every `submit_talk`/`submit_action` (the write endpoints reject a missing/stale token) alongside the per-turn `turn_token`. A prompt that only teaches "poll then submit_action" stalls on talk phase or fails the first write with `WRONG_PHASE`/stale-token. (US1, US4) [from spec review: 2× MEDIUM]
- **FR-013:** The fallback poll-interval hint returned at long-poll expiry SHOULD carry a small random jitter so many waiting clients don't resynchronize into request spikes. (US2) [from spec review: LOW]

### Non-Functional / Constraints

- Async-only: route and DB calls stay `async def`; no sync DB in async paths.
- No suppressions (`# type: ignore` / `# noqa`); Preflight Gate (ruff + mypy + pytest) green.
- Migration MUST follow the repo's SQLite batch pattern so `alembic upgrade head` works on the dev DB.

## Success Criteria

- **SC-001:** Idle model calls while waiting drop by an order of magnitude vs the current 5s cadence (≈12/min → ≈2/min worst case, near-zero effective with the hold).
- **SC-002:** A turn that opens during a hold is returned within ~1s of opening (not at window expiry).
- **SC-003:** The dashboard shows the correct exact turns-played count per connection after a played match (call count, if shown, labeled approximate).
- **SC-004:** Mode A is verified end-to-end (connect + play + watch) on all five tier-1 clients (operator-run matrix), each capturing the client's long-poll tolerance and one `/cost` reading.
- **SC-005:** No connector regression; Preflight Gate green; no new unthrottled per-call DB write.

## Key Entities

- **Connection** (`app/models/`) — gains a per-connection **turns-played** count (incremented at act-submission) and optionally an approximate call count (throttled at `mark_seen`); new column(s), new migration, no hot-row contention.
- **next-turn endpoint** (`app/routes/agent_next_turn.py`) — gains the bounded long-poll (internal async re-check loop); shared by connector and Mode A.
- **submit_action path** (`app/routes/agent_api.py` / `agent_next_turn.py`) — where **turns-played** is incremented (exact, low-frequency).
- **mark_seen** (`app/engine/connection_activity.py`) — choke point for the *approximate call* count only, reusing the existing heartbeat throttle; NOT where turns-played is counted.

## Scope

**In:** bounded long-poll + raised poll interval; per-connection counter + dashboard number; universal play-prompt + per-client connect snippets; fix stale `setup-mcp.md`.

**Out (later passes):** front-door/UI positioning reshuffle; in-terminal cost relay; pre-play cost heads-up; silent-agent detection; strategy-input box fix; no-MCP raw-HTTP fallback; replacing the connector; the token *estimate* layer (needs offline calibration).

## Assumptions

- The existing MCP server + agent API + turn-routing work and are reused as-is.
- Per-client request timeouts vary and are unknown until the operator verification matrix measures them; the ~25s default is a safe starting point.
- Token cost is the player's; the server cannot see real token counts in Mode A, so the dashboard shows raw call/turn counts now and a calibrated estimate later. The user's own `/cost` is ground truth.
- Calibration is a one-time internal lab step, never asked of users.
