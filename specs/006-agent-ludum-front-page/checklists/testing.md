# Testing Quality Checklist

**Purpose**: Validate test coverage and quality
**Feature**: [tasks.md](../tasks.md)

## Preflight Gate (per CLAUDE.md § Preflight Gate)

- [ ] `python3 -m ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes
- [ ] `pytest -q` passes
- [ ] No suppressions used to pass the gate

## Test Coverage (per CLAUDE.md § Testing Requirements)

- [ ] Route move covered: `GET /` serves marketing (asserts AL marker + CTA `href="/play/hoard-hurt-help"`); `GET /play/hoard-hurt-help` serves the lobby; `GET /games/{id}` still 200
- [ ] Honest empty-data state covered: zero games → `GET /` is 200, empty regions shown, no fabricated rows (no `ELO`/`@` leak)
- [ ] Real-data wiring covered: one seeded finished game → its real agent name appears in the rendered standings
- [ ] No-404 link sweep: repointed lobby links resolve; no `href="/"`-means-lobby leftovers
- [ ] Tests use the in-memory SQLite test DB (no live Postgres); external APIs not involved here
- [ ] Existing engine tests (`app/engine/*`) remain untouched and green (guard that the route move preserved behavior)

## Test Quality

- [ ] New tests live in `tests/` with focused, domain-named files (`test_agent_ludum_routing.py`, `test_agent_ludum_data.py`)
- [ ] Assertions are specific (marker strings / hrefs / agent names), not just status codes
