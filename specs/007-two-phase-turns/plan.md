# Implementation Plan: Two-Phase Turns with Private Bot Reasoning

**Branch**: `feat/two-phase-negotiation` | **Date**: 2026-06-01 | **Spec**: [spec.md](spec.md)

## Summary

Split each turn into a **talk** phase (public message + private thinking, no action) and an **act** phase (action + target + private thinking, taken after every talk message is revealed). The scheduler turn loop runs the two phases in sequence with per-phase resolve-early; messages live in a new `turn_messages` table and thinking lives in private columns the spectator surface reads but no agent surface ever does. Payoff math is untouched.

## Technical Context

- **Language/Version**: Python 3.12, async throughout.
- **Framework**: FastAPI + async SQLAlchemy 2.0 (`AsyncSession`), Alembic migrations, Jinja2 templates, SSE broadcast.
- **Storage**: SQLite (dev/test, in-memory for pytest), Postgres (prod). One connection string difference.
- **Testing**: pytest; test DB is in-memory SQLite; external model calls mocked.
- **Performance/Cost**: ~2 model calls per bot per turn (one per phase). Keep payloads lean (no pre-digesting).
- **Scope**: PD is the only game module; the platform turn loop is game-agnostic and is what changes.

## Constitution Check (CLAUDE.md)

**Status: PASS** (no FAIL items).

- **Data segregation (security-critical)** — thinking must never reach an agent. Enforced *by construction*: agent schemas gain no `thinking` field; spectator gets its own history type (see Decision 1). Covered by SC-002 leak tests.
- **Async consistency** — all new route handlers, the new `/message` endpoint, the scheduler phase steps, and DB reads/writes are `async`.
- **No suppressions** — no `# type: ignore` / `# noqa`; specific exception types only.
- **Type annotations** — full signatures; `from __future__ import annotations` where needed.
- **Testing** — new engine logic (per-phase resolve/default, resume) and the segregation guarantee are tested; migration must pass `tests/test_migrations.py`.
- **File focus** — no `utils.py`; new code lands in domain-named modules (see Structure).

## Architecture Decisions

### Decision 1 — Thinking is HTML-only; NO JSON schema carries it (revised after review)

**Chosen**: No JSON schema carries `thinking` — not the agent schemas *and not the spectator JSON schema*. Both the agent payloads and the spectator JSON API expose the two-phase shape (messages + actions) **without** thinking. Thinking is read straight from the DB in the web routes (`app/routes/web.py`) and rendered into the viewer/analysis **HTML templates** only.

**Rationale**: The original "separate spectator JSON type that carries thinking" idea was wrong. The spectator JSON API is public and the MCP `get_game_state` tool *proxies it*, so any thinking on the spectator JSON would flow straight to a bot. Keeping thinking out of *every* JSON schema means no API and no MCP tool can leak it, by construction — the only surface that carries it is server-rendered HTML, which a bot would have to scrape (accepted, deferred risk per the owner). Safer *and* simpler (no schema duplication; spectator JSON keeps mirroring the agent two-phase shape).

**Alternatives considered**:
- *Thinking on a separate spectator JSON type*: rejected — `get_game_state` proxies spectator JSON to bots, so it leaks.
- *Human-auth-gate / reveal-after-completion*: deferred — owner chose the lighter "HTML-only, accept scraping for now" model.

**Tradeoffs**: Pro: zero JSON leak surface; simpler schemas. Con: thinking isn't available for JS/live-merge — the viewer renders it server-side (fine; reasoning is collapsed-by-default per-turn HTML).

### Decision 2 — Turn phase as a tri-state on the existing `turns` row

**Chosen**: One `turns` row per (round, turn), with `phase` (`talk`|`act`), `talk_resolved_at` (nullable), and the existing `resolved_at` meaning *act/turn fully resolved*. `turn_token` and `deadline_at` always describe the **current** phase and are regenerated/reset at the talk→act transition.

**Resume tri-state** (FR-015): `resolved_at` set → turn done; else `talk_resolved_at` set → resume in **act**; else → resume in **talk**. This mirrors the idempotent get-or-create already in `_open_turn` and the lesson from [[mid-deploy-game-freeze]] (a blind re-INSERT/re-resolve froze a game).

**Alternatives considered**:
- *Two physical turn rows (talk-turn, act-turn)*: rejected — explodes the `(game,round,turn)` uniqueness and the viewer/history grouping.
- *Phase tracked only on the Game*: rejected — the turn row is the natural owner and is what resume reads.

**Tradeoffs**: Pro: minimal new columns, resume is a clean tri-state. Con: the talk-phase token/deadline are overwritten at transition (acceptable — resolve-early almost always fires first; the talk record is preserved in `turn_messages`).

### Decision 3 — Messages in a new `turn_messages` table; thinking as private columns

**Chosen**: New `turn_messages(turn_id, player_id, text, thinking, was_defaulted, submitted_at)` for the talk phase; add `thinking` to `turn_submissions` for the act phase. Keep (do not drop) `turn_submissions.message` for backward-compat with already-completed single-phase games.

**Rationale**: Each phase has exactly one row per player (clean `UNIQUE(turn_id, player_id)` per table, reusing the existing idempotency pattern). Keeping the legacy `message` column lets the viewer render historical games unchanged (fallback when a turn has no `turn_messages`). All adds are column-adds / a new table — **no constraint drop/alter, so no `op.batch_alter_table` needed**; still validated by `tests/test_migrations.py`.

### Decision 4 — Two distinct submit operations; phase advertised in the payload

**Chosen**: `POST /api/games/{id}/message` (talk: `{turn_token, message, thinking}`) and the existing `POST /api/games/{id}/submit` (act: `{turn_token, action, target_id, thinking}`). The `/turn` and `/next-turn` payloads add `phase` to the `current` block and, in the **act** phase, the revealed talk messages of the current turn (public text only, no thinking).

**Rationale**: A phase-specific endpoint + token makes "wrong-phase" submissions a clean 409 and keeps idempotency per phase. The runner branches on `current.phase`.

### Decision 5 — Rules text + version bump

**Chosen**: Rewrite `RULES_TEXT_V1`'s public-chat section (which currently says *"there is no separate negotiation phase"*) to describe talk→act, and bump `RULES_VERSION` to `v2`. The submission-contract section documents both endpoints.

**Rationale**: Bots are handed the rules every turn; they must be told the turn is two-phase. The version bump records which games ran under which rules.

## Highest-Risk Areas (updated after senior-TL review)

1. **Thinking segregation (security).** Thinking is HTML-only and absent from EVERY JSON schema (agent + spectator) — Decision 1. The SC-002 leak test sweeps all three programmatic channel types — agent HTTP API, **every MCP tool** (esp. `get_game_state`, which proxies spectator JSON), and the spectator JSON API — asserting no thinking, and asserts the rendered HTML *does* contain it. Plus FR-017/SC-006 log redaction. Residual HTML-scrape risk is accepted/deferred.
2. **Turn-loop resume WITHIN a two-phase game.** A restart mid-turn must resume in the exact phase with no double-reveal/double-resolve (Decision 2 tri-state + targeted tests). Cross-version v1→v2 resume is **out of scope** — deploys happen with no ACTIVE games (spec Assumptions) — which removes the freeze vector the review flagged.
3. **Live reveal timing.** Talk must reach live spectators at talk-resolution via a new `turn_talked` SSE event (FR-004); the live page must subscribe. Test that talk appears at talk-resolution, not act-resolution.
4. **Player leaves between phases** (FR-016): one consistent rule — excluded from quorum + defaulted for the rest of the turn, removed from later turns.
5. **Backward-compat for finished games.** Old games have no `turn_messages`; viewer falls back to `turn_submissions.message`. Regression test.
6. **Analysis modules reading `turn_submissions.message`.** Audit `turn_summary/opponent_stats/game_insights/board_signals`; point at `turn_messages` (legacy fallback).
7. **Log redaction** (FR-017): `/message` and `/submit` bodies carry thinking; ensure no access/debug log or error envelope echoes the body.

## Project Structure (files this feature touches)

```
app/
├── models/turn.py            - MODIFY: Turn.phase, Turn.talk_resolved_at; TurnSubmission.thinking; new TurnMessage
├── engine/
│   ├── scheduler.py          - MODIFY: two-phase _run_game; per-phase wait/default; tri-state resume; _all_messaged
│   ├── resolver.py           - MODIFY: per-phase defaulting (empty msg for talk, HOARD for act); reads actions as today
│   └── rules.py              - MODIFY: RULES_TEXT (talk→act), RULES_VERSION v2
├── games/
│   ├── base.py               - MODIFY (maybe): record_message hook on the module contract
│   └── hoard_hurt_help/game.py - MODIFY: record_message; record_submission keeps action only
├── routes/
│   ├── agent_api.py          - MODIFY: phase-aware /turn + /submit; NEW /message; per-phase idempotency/default
│   ├── agent_next_turn.py    - MODIFY: phase + current-turn talk messages in payload (no thinking)
│   ├── spectator_api.py      - MODIFY: build rich history (messages+thinking, actions+thinking)
│   └── web.py + templates    - MODIFY: live/watch/analysis render talk round + act round + collapsed thinking
├── schemas/
│   ├── agent.py              - MODIFY: CurrentTurn.phase + talk_messages; HistoryTurn.messages; SubmitRequest.thinking; NEW MessageRequest. NO thinking anywhere here.
│   └── spectator.py          - MODIFY: SpectatorMessage/SpectatorAction/SpectatorTurn (with thinking); SpectatorState.history -> list[SpectatorTurn]
migrations/versions/00NN_two_phase_turns.py - CREATE
scripts/
├── agentludum_agent.py, agentludum_agent_codex.py, agentludum_agent_gemini.py, agentludum_bot.py, bot.py - MODIFY: branch on phase; submit message vs action; thinking field
tests/                        - NEW/MODIFY: phase resolve/default, resume tri-state, SC-002 leak sweep, payoff parity, same-turn mutual-help, migration, legacy-game render
```

**Structure decision**: the change is concentrated in the **platform** (turn loop + agent contract + spectator), not the PD payoff rules. The resolver math is unchanged; only the submission/defaulting plumbing and the read surfaces move.

## Testing Strategy

- **SC-002 (segregation)**: integration test hits `/turn`, `/next-turn`, history, chat, opponent-history for a game with non-empty thinking; asserts the serialized response contains no thinking text and the schemas expose no such field.
- **SC-005 (payoff parity)**: feed a fixed action set through the act phase; assert per-player deltas equal the legacy resolver's.
- **Per-phase loop**: resolve-early on all-talked / all-acted; default empty message on missed talk; default HOARD on missed act.
- **Resume tri-state**: simulate a restart in each phase; assert correct continuation, no double reveal/resolve.
- **SC-004**: integration game where two bots HELP each other in the same act phase → a real mutual-help pair (+8 each).
- **Migration**: `tests/test_migrations.py` upgrades head on SQLite; legacy game still renders.
