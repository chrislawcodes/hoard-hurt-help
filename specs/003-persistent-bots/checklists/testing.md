# Testing Quality Checklist

**Purpose**: Validate test coverage and quality
**Feature**: [tasks.md](../tasks.md)

## Preflight Gate (per constitution — `CLAUDE.md` → Preflight Gate)

Run from repo root before any push/PR:
- [ ] `ruff check .` passes
  - Reference: Constitution § Preflight Gate
- [ ] `mypy app/ mcp_server/` passes (no new ignores)
  - Reference: Constitution § Preflight Gate
- [ ] `pytest -q` passes
  - Reference: Constitution § Preflight Gate
- [ ] No suppressions added to make checks pass; root causes fixed.

## Test Coverage (per constitution — Testing Requirements)

- [ ] New game/engine logic has tests: `next_turn.py` selector (urgency, tie-break, already-submitted), `caps.py`, stall detection.
  - Reference: Constitution § Testing → "Always write tests for new game logic in `app/engine/`"
- [ ] Data transformations tested: bot-key lookup/match, (bot,game_id)→player resolution.
- [ ] External APIs mocked (Claude/Hermes); MCP layer not hit by app tests.
  - Reference: Constitution § Testing → mock external API calls
- [ ] DB tests use the test DB (SQLite in-memory); no live Postgres required.
  - Reference: Constitution § Testing → "test DB is SQLite in-memory"

## Acceptance Coverage (from spec-acceptance.md)

- [ ] US1: key shown once, hidden on reload, reissue invalidates old key, second bot independent.
- [ ] US2: nearest-deadline selection across games, waiting when idle, submit resolves player, paused → bot_paused.
- [ ] US3: entry creates player without a key, duplicate blocked, name collision, two bots independent.
- [ ] US4: profile CRUD, single default, seed-at-entry copy, post-edit isolation.
- [ ] US5: paused served no turns, resume restores, delete guard, panel status fields.
- [ ] US6: per-bot cap refusal names cap, platform cap refusal, GAME_FULL preserved.
- [ ] US7: threshold detection, auto-pause + reason, below-threshold no-op.

## Edge Cases (from spec.md)

- [ ] Reissue mid-game breaks connection until re-paste (warned).
- [ ] Bot in zero games → waiting, not error.
- [ ] Delete bot in active game blocked.
