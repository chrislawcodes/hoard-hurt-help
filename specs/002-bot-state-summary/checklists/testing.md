# Testing Quality Checklist

**Purpose**: Validate test coverage and quality
**Feature**: [tasks.md](../tasks.md) · Constitution: `CLAUDE.md`

## Preflight gate (per CLAUDE.md § Preflight Gate) — before any push
- [ ] `ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes
- [ ] `pytest -q` passes
- [ ] No suppressions used to make checks pass

## Test data & isolation (per CLAUDE.md § Testing Requirements)
- [ ] Test DB is SQLite in-memory — no live Postgres required
- [ ] External APIs (Claude / Hermes) mocked; DB not mocked in integration tests
- [ ] New game logic in `app/engine/` has unit tests (mandatory)

## Engine coverage (pure functions, no DB)
- [ ] `opponent_stats`: tallies, reciprocity (both directions), short-list cap + selection reasons, aggregate completeness, ties
- [ ] `board_signals`: temperature (cooperative/hostile/mixed/empty), alliances, surging, pattern-break, new_alliance, caps
- [ ] `turn_summary`: assembled shape + edge cases

## Edge cases (per spec.md § Edge Cases)
- [ ] Turn 1 / empty history → empty delta, zeroed stats, no flags, no error
- [ ] Tiny 3-bot game → short-list is everyone; aggregate empty/omitted
- [ ] Large game → aggregate covers the long tail; payload bounded
- [ ] Player with `left_at` excluded from counts/standings
- [ ] Defaulted HOARD turn + default message handled (not a directed message)
- [ ] Pull: unknown opponent/turn → error envelope; over-rate → RATE_LIMITED

## API/MCP coverage
- [ ] `get_turn` returns new `summary` shape (no `history`)
- [ ] 4 pull endpoints + 4 MCP pull tools return correct data
- [ ] Updated `test_agent_api.py` / `test_mcp.py` no longer rely on removed fields

## Success criteria verification (per spec.md § Success Criteria)
- [ ] SC-001 payload bounded / flat with turns (local check)
- [ ] SC-002 legal move from summary alone
- [ ] SC-003 directed messages appear next turn
- [ ] SC-005 all 5 setup blocks + docs updated
