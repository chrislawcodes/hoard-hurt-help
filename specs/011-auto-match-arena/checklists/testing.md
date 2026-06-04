# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md)  
**Constitution**: CLAUDE.md

## Preflight Gate (per CLAUDE.md — must pass before any push)

```bash
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && \
mypy app/ mcp_server/ && \
pytest -q
```

- [ ] `ruff check` passes with zero errors
- [ ] `mypy app/ mcp_server/` passes with zero errors (no type: ignore added)
- [ ] `pytest -q` passes — all tests green

## New Test Coverage (per CLAUDE.md — new engine logic must have tests)

- [ ] `tests/test_arena.py` exists and covers `ensure_practice_arena`
- [ ] `tests/test_arena.py` covers `ensure_auto_match`
- [ ] `tests/test_arena.py` covers `fill_and_start_auto_matches`
- [ ] Tests use in-memory SQLite test DB — no live Postgres required
- [ ] External calls (if any) are mocked; DB is real (per CLAUDE.md: don't mock DB in integration tests)

## Test Quality

- [ ] Each test function asserts a specific, observable outcome (state change, row count, column value)
- [ ] Idempotency tests call each function twice and assert no duplicates are created
- [ ] Edge case tests cover: no Sim presets available, existing completed Practice Arena, auto-match poller firing twice

## Migration Test

- [ ] `tests/test_migrations.py` (existing) passes — confirms migration 0019 applies and rolls back cleanly on SQLite

## Manual Verification (per quickstart.md)

- [ ] US-1: Practice Arena appears in lobby on server start; join triggers immediate game start; new arena recreated
- [ ] US-2: Auto-match appears at :00/:30 boundary; starts with Sim fill at start time; zero-human case works
- [ ] US-3: `/play` renders all three user states correctly; join button routes to game viewer after joining Practice Arena
- [ ] US-4: "Play now →" on homepage lands on `/play`
- [ ] US-5: Lobby upcoming section shows Practice Arena and auto-match without admin intervention
