# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md) · Constitution: project `CLAUDE.md`

## Preflight (per constitution § Preflight Gate)
- [ ] `python3 -m ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes
- [ ] `pytest -q` passes
- [ ] No suppressions used to get green (SC-007)

## Coverage (per constitution § Testing Requirements)
- [ ] New engine/turn logic has tests (turn-resolution fan-out; bot-as-agent seating)
  - Reference: CLAUDE.md § "Always write tests for new game logic in app/engine/"
- [ ] External model/CLI calls are mocked (runner per-agent model selection) — no live Claude/Hermes calls
  - Reference: CLAUDE.md § "Mock external API calls"
- [ ] DB tests use the SQLite in-memory test DB built from models; no live Postgres
  - Reference: CLAUDE.md § "test DB is SQLite in-memory"
- [ ] `tests/test_migrations.py` passes `alembic upgrade head` on SQLite after the reshape

## Acceptance (per spec-acceptance.md)
- [ ] US1 combined create flow (SC-001)
- [ ] US2/US3 one connection / many agents / per-agent model (SC-002, SC-003)
- [ ] US4 bot has no connection, plays, labeled on leaderboard (SC-004)
- [ ] US5 strategy snapshot + active-match edit block (SC-005)
- [ ] US6 connection management (reissue overlap, delete-block)
- [ ] US7 leaderboard row = agent + model; in-match name from agent
- [ ] US8 no "bot" for a user's player; no `/me/bots`; no `Bot` class (SC-006, SC-008)
