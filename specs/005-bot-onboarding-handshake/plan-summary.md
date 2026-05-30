# Plan Summary: Live Connection Handshake for Bot Onboarding

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/bot.py` | modify | + `first_connected_at: Mapped[datetime \| None]` (nullable) |
| `migrations/versions/0005_add_bot_first_connected_at.py` | create | additive `add_column` only; SQLite-safe; no backfill |
| `app/deps.py` | modify | `require_bot`: on `first_connected_at IS NULL`, set it + commit + publish `connected` to `bot:{id}` |
| `app/engine/bot_activity.py` | create | `mark_first_move(db, bot_id)`, `compute_onboarding_state(db, bot)`, `OnboardingState` enum |
| `app/routes/agent_api.py` | modify | `agent_submit`: call `mark_first_move` after `record_submission` |
| `app/routes/bots_web.py` | modify | + `GET /{id}/status` (owner fragment), + `GET /{id}/stream` (owner SSE) |
| `app/templates/bots/detail.html` | modify | status panel + SSE wiring; key-safety line; empty-Games copy |
| `app/templates/bots/_status.html` | create | status fragment, one block per state |
| `app/static/style.css` | modify | status-panel styles; reuse lobby live badge/dot |
| `tests/test_bot_onboarding.py` | create | state machine, first-connect/first-move detection, owner-scoping, event emission, first-paint |
| `mcp_server/*` (submit path) | verify/modify | ensure first move routes through `mark_first_move`; add call if separate |

## Migration Steps

1. Generate revision `0005` that runs only:
   `op.add_column("bots", sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True))`
2. `downgrade()` drops the column.
3. Prod (Postgres): `alembic upgrade head`. Dev/tests: schema via `create_all` (no migration run needed).
4. Verify: `bots` row count unchanged; column exists.

## Data Model

**Bot** (modified): `bots` — adds nullable `first_connected_at` (write-once, set in `require_bot`).
**Onboarding state** (derived, not stored): from `first_connected_at` + bot's `Player`/`Game.state` + existence of a non-defaulted `TurnSubmission`.
**Live event** (not stored): channel `bot:{id}`, events `connected` / `moved`, empty payload (re-fetch triggers), no secret.

## Key Constraints

- **First-connection hook in `require_bot`** — set `first_connected_at` + publish only on the `NULL→now` transition. *Why: it's the one auth choke point every agent path crosses, so one guarded write covers all connection methods with zero hot-path cost after first connect.*
- **Reuse `broadcast.py` on `bot:{id}`** — no new transport. *Why: matches spectator SSE; single-instance deploy makes in-process pub/sub sufficient.*
- **Owner-scope `/status` and `/stream`** — `require_user` + `_owned_bot`. *Why: connection status is private; FR-010 forbids any public leak.*
- **Paste-once preserved** — never re-render the key in any new path. *Why: FR-011 / security model; only a `key_hint` is ever shown.*
- **Additive, backfill-free migration** — nullable column, `NULL` = never connected. *Why: data-critical rule; play-history precedence makes existing bots render correctly without a backfill.*
- **First-paint truth** — `/status` renders the real state without any event; events only re-fetch + add a one-shot flourish. *Why: FR-004 + US3.3 (don't re-run the celebration on reload).*
- **Bad key is passive** — no live "invalid code" event. *Why: a wrong key resolves to no bot and can't be attributed; honesty over a fake signal (Decision 5).*
- **Async + typed + no suppressions** — all new code. *Why: CLAUDE.md hard rules.*
