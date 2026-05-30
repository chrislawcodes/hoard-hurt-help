# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md)

## Preflight Gate (per `CLAUDE.md` → Preflight Gate)
- [ ] `ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes (no new ignores)
- [ ] `pytest -q` passes
- [ ] No suppressions added to pass checks.

## The regression gate (SC-001 — the most important test)
- [ ] `test_resolver`, `test_end_to_end`, `test_board_signals`, `test_opponent_stats`, `test_turn_summary` pass with **ZERO edits** to those files.
- [ ] A full PD game scores identically to pre-refactor.

## New coverage (per `CLAUDE.md` → Testing: new game logic gets tests)
- [ ] `test_game_registry`: register/get, unknown type → `GameError`.
- [ ] `test_stub_game`: a stub module can be created, played by a bot, resolved, scored — touching only the module (SC-002).
- [ ] `test_game_type`: created games carry `game_type`.
- [ ] External APIs mocked; DB tests use SQLite in-memory (metadata build).

## Coexistence (SC-003/SC-004)
- [ ] PD + stub games run side by side through the same platform machinery.
- [ ] An unregistered `game_type` doesn't crash the poller or other games.
