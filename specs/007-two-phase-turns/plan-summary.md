# Plan Summary: Two-Phase Turns with Private Bot Reasoning

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/models/turn.py` | modify | `Turn.phase`, `Turn.talk_resolved_at`; `TurnSubmission.thinking`; new `TurnMessage` model |
| `migrations/versions/00NN_two_phase_turns.py` | create | add columns + `turn_messages` table; no batch needed (adds only) |
| `app/engine/scheduler.py` | modify | two-phase `_run_game`; per-phase wait/resolve-early; tri-state resume; `_all_messaged` helper |
| `app/engine/resolver.py` | modify | per-phase defaulting (empty msg for talk, HOARD for act); payoff math unchanged |
| `app/engine/rules.py` | modify | `RULES_TEXT` describes talk→act; bump `RULES_VERSION` to `v2` |
| `app/games/base.py` | modify | add `record_message` to the module contract (optional hook) |
| `app/games/hoard_hurt_help/game.py` | modify | implement `record_message`; `record_submission` stays action-only |
| `app/routes/agent_api.py` | modify | phase-aware `/turn` + `/submit`; NEW `POST /message`; per-phase idempotency/default |
| `app/routes/agent_next_turn.py` | modify | add `phase` + current-turn `talk_messages` to payload (NO thinking) |
| `app/routes/spectator_api.py` | modify | build rich history: messages+thinking, actions+thinking |
| `app/routes/web.py` + templates | modify | live/watch/analysis render talk round + act round + collapsed-by-default thinking |
| `app/schemas/agent.py` | modify | `CurrentTurn.phase` + `talk_messages`; `HistoryTurn.messages`; `SubmitRequest.thinking`; NEW `MessageRequest`. **No thinking field anywhere here.** |
| `app/schemas/spectator.py` | modify | two-phase history shape (messages + actions), **NO thinking** — spectator JSON is public + proxied by MCP `get_game_state`, so thinking must not be here |
| `app/routes/web.py` + templates | modify | the ONLY place thinking is exposed — read from DB, render into viewer/analysis HTML (collapsed per bot) |
| `app/routes/sse.py` + live template | modify | new `turn_talked` event at talk-resolution so live viewer reveals talk on time |
| `app/engine/{turn_summary,opponent_stats,game_insights,board_signals}.py` | audit/modify | any reader of `turn_submissions.message` → read `turn_messages` (legacy fallback) |
| `scripts/agentludum_agent.py` | modify | branch on phase; talk→{message,thinking}, act→{action,target,thinking} |
| `scripts/agentludum_agent_codex.py` | modify | same |
| `scripts/agentludum_agent_gemini.py` | modify | same |
| `scripts/agentludum_bot.py` | modify | stateless runner: same phase branching |
| `scripts/bot.py` | modify | random test bot: talk→canned message, act→random action |
| `tests/...` | create/modify | SC-002 leak sweep, SC-005 parity, per-phase resolve/default, resume tri-state, SC-004 mutual-help, migration, legacy render |

## Migration Steps

1. `op.add_column("turns", phase VARCHAR(8) NOT NULL server_default 'talk')`
2. `op.add_column("turns", talk_resolved_at DATETIME NULL)`
3. `op.add_column("turn_submissions", thinking TEXT NOT NULL server_default '')`
4. `op.create_table("turn_messages", ...)` with `UNIQUE(turn_id, player_id)` + indexes on turn_id, player_id
5. Verify `pytest tests/test_migrations.py` (upgrade head on SQLite) passes — adds only, no batch_alter_table required.

## Data Model

- **Turn**: `turns` — adds `phase` (talk|act), `talk_resolved_at`; `turn_token`/`deadline_at` describe the current phase; `resolved_at` = act/turn done.
- **TurnMessage**: `turn_messages` — `(turn_id, player_id)` unique; `text` (public), `thinking` (private), `was_defaulted`, `submitted_at`.
- **TurnSubmission**: `turn_submissions` — adds `thinking` (private); keeps legacy `message` for old games.

## Key Constraints

- **Segregation by construction**: NO JSON schema carries `thinking` — not agent schemas AND not spectator schemas. Thinking is HTML-only (rendered in `web.py` templates) — *Why: the spectator JSON is public and MCP `get_game_state` proxies it to bots, so any thinking on a JSON shape leaks; keeping it off every JSON channel makes a leak impossible by construction.*
- **Leak test covers all 3 channel types**: agent HTTP API + every MCP tool + spectator JSON — *Why: the review found the original test only checked HTTP endpoints, missing the MCP proxy.*
- **No v1→v2 in-flight migration**: deploys happen with no ACTIVE games — *Why: owner decision; removes the cross-version resume freeze the review flagged.*
- **Log redaction**: `/message` + `/submit` bodies carry thinking; don't log them — *Why: response schemas being clean doesn't stop a request-body access log from leaking it.*
- **Phase tri-state resume**: `resolved_at` → done; `talk_resolved_at` → resume act; else resume talk — *Why: a mid-turn restart must not re-reveal messages or double-apply payoffs (mid-deploy-game-freeze).*
- **Payoff parity**: resolver math untouched; defaulting only moves per-phase — *Why: prove the structure change didn't alter balance (SC-005).*
- **Legacy fallback**: viewer/spectator/analysis read `turn_messages` for new games, fall back to `turn_submissions.message` for old ones — *Why: completed single-phase games have no turn_messages.*
- **Per-phase one-submission**: one talk message + one action per player per turn, each idempotent on `(turn_id, player_id)` — *Why: fair blind simultaneity within each phase.*
- **No batch migration**: only column-adds + a new table — *Why: avoids the SQLite ALTER-constraint pitfall; keeps test_migrations green.*
