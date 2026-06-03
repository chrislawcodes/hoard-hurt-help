# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md) · validated against `CLAUDE.md`

## Preflight (per CLAUDE.md § Preflight Gate)
- [ ] `python3 -m ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes
- [ ] `pytest -q` passes
- [ ] No suppressions added to make checks pass

## Migration Tests (per CLAUDE.md § Testing — test DB is SQLite in-memory)
- [ ] `tests/test_migrations.py` builds a prod-shaped DB at 0017 and asserts `upgrade head` succeeds on SQLite
- [ ] Row counts preserved for matches/players/turns/turn_submissions/request_incidents
- [ ] Zero orphaned FKs after migration; zero rows `LIKE 'G\_%'`
- [ ] Dry-run preview counts equal applied counts (SC-005)

## Behavioral Tests
- [ ] URL/redirect tests: catalog, nested match URL, `/play`→301, `/games/G_xxxx`→301
- [ ] Alias tests: old vs new REST path parity; `game_id` + `match_id` request bodies; dual-key responses
- [ ] MCP tests: old + new arg names accepted
- [ ] Leak tests (agent API + MCP + spectator JSON) still pass — visibility unchanged

## Coverage Targets (per CLAUDE.md § Testing Requirements)
- [ ] New game logic? None added — but existing `app/engine/` tests still pass
- [ ] External API calls (Claude/Hermes) remain mocked; DB tests use the test DB, not mocks
