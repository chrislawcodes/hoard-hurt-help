# Implementation Plan: Live Connection Handshake for Bot Onboarding

**Branch**: `005-bot-onboarding-handshake` | **Date**: 2026-05-30 | **Spec**: [spec.md](./spec.md)

## Summary

Add a live status panel to the bot detail page (`GET /me/bots/{id}`) that confirms a bot connected, guides the operator to get it into a game, and celebrates its first move — all without a page reload. It rides the **existing** in-process pub/sub (`app/broadcast.py`) on a new per-bot channel, detects first connection in the single agent-auth choke point (`require_bot`), and detects first move in the submit path. One additive nullable column (`bots.first_connected_at`) is the only schema change; everything else is derived from data we already store.

## Technical Context

**Language/Version**: Python 3.11+ (async)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x (async), Jinja2, HTMX + htmx-ext-sse (already loaded in `base.html`), Alembic
**Storage**: SQLite (dev/tests via `Base.metadata.create_all`), Postgres (prod via Alembic)
**Testing**: pytest, in-memory SQLite test DB
**Target Platform**: single-instance Railway deploy (in-process pub/sub is sufficient — see Decision 2)
**Performance Goals**: connection/first-move confirmation visible within a few seconds (SC-001..003); no added latency on the agent hot path beyond one write on the *first* connect
**Constraints**: server-rendered + SSE only (no SPA); paste-once credential model must stay intact (FR-011); mobile-first; no color-only state (FR-012)
**Scale/Scope**: ~9 files touched, 1 additive migration, 1 new helper module, 2 new owner-scoped routes, 1 new fragment template

## Constitution Check

**Status**: PASS

Validated against `CLAUDE.md`:
- **Async consistency** — both new routes and the helper are `async def`; all DB access uses the async session. ✓
- **No suppressions / type annotations** — every new function fully annotated; no `# type: ignore` / `# noqa`. ✓
- **No bare except** — the only new error handling catches specific exceptions; route-level `except Exception` only if needed at a boundary. ✓
- **Testing** — new logic (state resolution, first-connection/first-move detection, owner-scoping, event emission) gets tests; game logic untouched; test DB is in-memory SQLite. ✓
- **Data-critical** — one additive, nullable, backfill-free column via Alembic; verified by row-count check; the migration uses `add_column` only (SQLite-safe, no `drop_constraint`). ✓
- **File structure** — new domain logic in `app/engine/bot_activity.py` (no `utils.py`); routes stay in `app/routes/bots_web.py`; fragment template named for its job. ✓

## Architecture Decisions

### Decision 1: Persist only `first_connected_at`; derive everything else

**Chosen**: Add a single nullable `bots.first_connected_at` column. Derive "has moved" and "in a game" from existing `Player` / `TurnSubmission` data at render time.

**Rationale**:
- "Has this bot ever connected?" is **not** derivable — polling `get_next_turn` persists nothing today, so without a marker a reload can't show "connected." This one fact must be stored.
- "Has it moved?" and "is it in a game?" **are** derivable (a non-defaulted `TurnSubmission` for any of the bot's players; an active/pre-game `Player`). Adding columns for those would duplicate state and risk drift.
- Backfill-free: existing bots get `NULL`. That reads as "never connected," which is harmless because state resolution gives **derived play history precedence** (a bot that has moved shows the established state regardless of the NULL). So no production backfill is needed (FR-013).

**Alternatives considered**:
- `last_seen_at` updated on every poll — rejected: a write per poll per bot is needless amplification for this feature; revisit if we later want "active N seconds ago."
- `first_move_at` column — rejected: derivable from `TurnSubmission`; no need to persist.

**Tradeoffs**: Pro — minimal schema, no drift, no backfill. Con — "has moved" costs one small query per render (a bounded `EXISTS`/count; acceptable on an owner-only page).

### Decision 2: Reuse `broadcast.py` on a per-bot channel

**Chosen**: Publish to channel key `f"bot:{bot_id}"` using the existing `publish`/`subscribe`. Add an owner-scoped SSE route that subscribes to that key.

**Rationale**: `broadcast.py` already keys by an arbitrary string; spectator streams use `game_id`. A `bot:{id}` key is the same pattern, no new transport (matches the "ride the existing mechanism" constraint). Single-instance deploy makes in-process pub/sub sufficient (documented limitation already in `broadcast.py`).

**Tradeoffs**: Pro — zero new infrastructure, consistent with spectating. Con — like spectating, it won't fan out across multiple workers; that's an accepted, pre-existing platform limitation.

### Decision 3: Detect first connection in `require_bot` (the auth choke point)

**Chosen**: In `app/deps.py::require_bot`, after a bot is resolved, if `first_connected_at IS NULL`, set it to now, commit, and publish a `connected` event to `bot:{id}`. The `IS NULL` guard makes this a one-time write; every later call short-circuits with no write and no publish.

**Rationale**: Every agent path (runner, MCP, direct API, the global `get_next_turn`) resolves the bot through `require_bot` (directly or via `require_bot_player`). Hooking here covers **all** connection methods with one edit. The cost is one extra `UPDATE` on the very first authenticated call only.

**Tradeoffs**: Pro — universal, minimal. Con — a commit inside a dependency; safe here because it runs first and is idempotent, but documented so it isn't mistaken for general practice.

### Decision 4: Detect first move in the submission path via a shared helper

**Chosen**: A helper `app/engine/bot_activity.py::mark_first_move(db, bot_id)` checks whether this bot had any prior non-defaulted submission; if not, it publishes a `moved` event to `bot:{id}`. Call it from `agent_api.py::agent_submit` after `record_submission`, and from the MCP submit path if that is separate (verify during implementation; factor so both call the one helper).

**Rationale**: Keeps "first move" logic in one domain module rather than duplicated across HTTP and MCP submit paths.

**Tradeoffs**: Pro — single source of truth. Con — must confirm all submit paths route through it (an implementation task).

### Decision 5: Bad-key path is passive, not a falsely-promised live event

**Chosen**: Do **not** claim a live "invalid code" event. A wrong/stale code resolves to **no** bot in `require_bot` (the old `key_lookup` is overwritten on reissue), so it cannot be attributed to this bot's channel. Instead: the panel surfaces a gentle "Taking longer than expected? The code may be wrong — reissue and paste again." nudge after a wait, and the setup message already tells the AI to report `invalid key`.

**Rationale**: Honesty over a feature we can't faithfully deliver. Matches the spec's best-effort assumption (FR-004's correct-on-reload guarantee plus a timed nudge covers the recovery need without fabricating attribution).

**Tradeoffs**: Pro — no misleading UI, no new key-history tracking/security surface. Con — US4 is delivered as a passive nudge + AI-reported error rather than a positively-detected state; noted as a scope refinement.

## Onboarding State Machine (computed for first paint; re-fetched on events)

Resolved per bot, owner-only, in `compute_onboarding_state`:

| State | Condition (precedence top→bottom) | Panel shows |
|-------|-----------------------------------|-------------|
| `playing` (established / US6) | bot has any non-defaulted submission | Quiet line: "Playing in '[game]'. Watch live →" — no big block |
| `in_game_no_move` | connected, has an ACTIVE-game player, no submission yet | "✓ In '[game]'. Waiting for its first move…" |
| `connected_pregame` | connected, only pre-game (SCHEDULED/REGISTERING) players | "✓ Connected. '[game]' hasn't started yet — it'll play automatically." |
| `connected_no_game` | connected, no players | "✓ Your bot connected. Last step: get it into a game." + **Join a game →** |
| `waiting_in_game` | not connected, but already entered in a game | "In '[game]' — waiting for your bot to connect. Keep your AI running." |
| `waiting` | not connected, no players | "Waiting for your bot to connect… keep your AI running. This can take a minute." (+ timed reissue nudge) |

`connected` ⇔ `first_connected_at IS NOT NULL`. The live `connected` / `moved` SSE events trigger a re-fetch of this fragment; the events additionally add a one-shot CSS flourish class that plain reloads don't (so the "just happened" delight fires live but isn't re-run on reload — satisfies US3.3).

## SSE Channel & Endpoints

- **Channel**: `f"bot:{bot_id}"` via `app/broadcast.py` (existing).
- **`GET /me/bots/{bot_id}/stream`** (new, owner-scoped via `require_user` + `_owned_bot`): `StreamingResponse` of `subscribe(f"bot:{bot_id}")` — mirrors `app/routes/sse.py::game_stream`, but ownership-checked so status never leaks (FR-010).
- **`GET /me/bots/{bot_id}/status`** (new, owner-scoped): renders the `bots/_status.html` fragment for the current computed state. The detail page renders this inline for correct first paint (FR-004) and re-fetches it on `sse:connected` / `sse:moved`.

## Project Structure

```
app/
├── models/bot.py            - + first_connected_at (nullable datetime)
├── deps.py                  - require_bot: set first_connected_at + publish "connected" on first connect
├── engine/bot_activity.py   - NEW: mark_connected, mark_first_move, compute_onboarding_state
├── routes/
│   ├── bots_web.py          - + GET /{id}/status (fragment), + GET /{id}/stream (SSE)
│   └── agent_api.py         - agent_submit: call mark_first_move after record_submission
├── templates/bots/
│   ├── detail.html          - status panel + SSE wiring; key-safety line; empty-Games copy
│   └── _status.html         - NEW: the status fragment (one block per state)
└── static/style.css         - status-panel styles (reuse lobby badge/dot)

migrations/versions/
└── 0005_*.py                - NEW: additive add_column bots.first_connected_at

tests/
└── test_bot_onboarding.py   - NEW: state machine, first-connect/first-move detection, owner-scoping, event emission
```

**Structure Decision**: Platform-only change (no game-module edits). New domain logic isolated in `app/engine/bot_activity.py`; MCP submit path checked to route through the same helper.

## Migration Approach (data-critical)

- Single op: `op.add_column("bots", sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True))`; downgrade drops it.
- **SQLite-safe**: `add_column` of a nullable column needs no batch mode and no `drop_constraint` — it won't trip the known broken-chain issue. (Local dev/tests build schema via `create_all`, which picks up the model field automatically; the migration is for Postgres prod.)
- **Backfill**: none. `NULL` = never connected; state resolution handles existing bots via play-history precedence.
- **Verification**: dry-run/inspect the generated SQL; after apply, confirm `bots` row count unchanged and the column exists.

## Testing Strategy

- **State machine** (`compute_onboarding_state`): table-driven over the six states using fixtures (bot ± `first_connected_at`, ± players in each game state, ± submissions). Pure-ish DB reads against in-memory SQLite.
- **First-connection detection**: a first authenticated agent call sets `first_connected_at` once and publishes one `connected` event; a second call writes/publishes nothing. Assert via a `broadcast` capture.
- **First-move detection**: first non-defaulted submission publishes one `moved` event; later submissions don't.
- **Owner-scoping / security**: `/status` and `/stream` reject non-owners (404/401) and never expose the key; status absent on public pages.
- **First paint correctness** (FR-004): the rendered fragment matches the bot's true state without any event.
- Mock external model/HTTP calls; never mock the DB (use the test DB), per CLAUDE.md.

## Endpoints (internal; no public API contract change)

| Method | Path | Auth | Returns |
|--------|------|------|---------|
| GET | `/me/bots/{id}/status` | owner | HTML fragment (status panel) |
| GET | `/me/bots/{id}/stream` | owner | `text/event-stream` (`connected`, `moved`) |

No change to the public agent API surface, so no `contracts/` artifact is generated.
