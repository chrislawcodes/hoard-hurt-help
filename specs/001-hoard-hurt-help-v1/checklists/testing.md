# Testing Quality Checklist

**Purpose**: validate test coverage and quality before declaring a phase done.
**Feature**: [tasks.md](../tasks.md)

## Pre-commit / per-phase gate

- [ ] `pytest` runs end-to-end with no failures.
- [ ] `ruff check app/ tests/ mcp_server/` clean.
- [ ] `black --check app/ tests/ mcp_server/` clean.
- [ ] `mypy app/ mcp_server/` clean (strict on `app/engine/`; pragmatic elsewhere).
- [ ] `alembic upgrade head` works against a fresh DB.

## Coverage targets (no constitution — project defaults)

- [ ] `app/engine/resolver.py` ≥ 95% line coverage. This is the math.
- [ ] `app/engine/state_machine.py` ≥ 95% line coverage. Every transition + every illegal transition.
- [ ] `app/engine/scheduler.py` ≥ 80% (some asyncio paths hard to hit deterministically).
- [ ] `app/routes/*.py` ≥ 80% — every status code from spec §10 covered.
- [ ] `app/auth/*.py` ≥ 80% — OAuth round-trip covered with a mocked Google.
- [ ] `mcp_server/server.py` ≥ 80% — each tool tested for header propagation + error pass-through.

## Test structure

- [ ] One test file per logical area (`test_resolver.py`, `test_state_machine.py`, `test_agent_api.py`, ...).
- [ ] Async tests use `pytest-asyncio` with `asyncio_mode = "auto"`.
- [ ] Each test uses a fresh in-memory SQLite engine; no test pollution across tests.
- [ ] Use `httpx.AsyncClient(app=app, base_url="http://test")` for HTTP-flavored tests.

## Engine math (critical — Phase 3)

- [ ] Single Hoard: +2 to self.
- [ ] Single Help: +4 to target, 0 to source.
- [ ] Single Hurt: −4 to target, 0 to source.
- [ ] Help stacking: 5 helps on one target = +20 to that target.
- [ ] Hurt stacking: 5 hurts on one target = −20 raw delta, clipped to floor.
- [ ] Mutual help (A↔B): each ends +8.
- [ ] Mutual bonus does NOT double when a third party also Helps A.
- [ ] Score floor at 0 applied to final delta, not per-incoming-Hurt — explicit test for `(3, [-4, -4, +4]) → 0`.
- [ ] HURT against 0-score target: target unchanged, attacker's score unchanged (no +2).
- [ ] Missed submission: `was_defaulted=true`, action `HOARD`, message exactly `"I did not submit a turn."`, +2 applied.
- [ ] Round tie: 3-way tie at top scores each tied player 1/3 round-win.
- [ ] Game tiebreaker: equal round-wins resolved by `total_round_score`.

## API contract (Phase 4)

- [ ] Every endpoint in `contracts/api.yaml` has at least one happy-path test.
- [ ] Every error code in spec §10 has at least one test that triggers it.
- [ ] Idempotent submit: a second submit with the same `(turn_token, player_id)` returns the same 202 response, not 409.
- [ ] Polling faster than 1 Hz returns 429 without consuming a submission slot.

## Auth (Phase 2)

- [ ] OAuth callback with a mocked Google userinfo creates a new `User` row on first sign-in.
- [ ] OAuth callback with same `google_sub` returns the existing `User` (no duplicate row).
- [ ] Admin email lookup is case-insensitive.
- [ ] Non-admin email on `/admin/*` returns 403 (not redirect).

## Integration (Phase 10)

- [ ] A 5-player game runs end-to-end through real HTTP (not direct ORM calls): join, poll, submit, resolve, final scoreboard correct.
- [ ] SSE stream emits at least one `turn_resolved` event during the integration run.

## Manual smoke tests (post-deploy)

- [ ] Sign in with Google works on the deployed Railway URL.
- [ ] MCP setup command from the dashboard works in Claude Code; tools appear; one real turn lands.
- [ ] ChatGPT Custom GPT can be added; actions work; one real turn lands.
- [ ] CSV export downloads with the expected column list.
