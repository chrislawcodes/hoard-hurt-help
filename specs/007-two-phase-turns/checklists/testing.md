# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md) — references CLAUDE.md (Testing Requirements + Preflight Gate)

## Preflight (per CLAUDE.md — must pass before any push)
- [ ] `python3 -m ruff check .`
- [ ] `mypy app/ mcp_server/`
- [ ] `pytest -q`

## Required Coverage (maps to success criteria)
- [ ] **SC-002** leak sweep: every agent endpoint returns zero thinking, for a game seeded with known thinking strings (T025) ⚠️
- [ ] **SC-005** payoff parity: identical actions → identical per-player deltas vs legacy resolution (T019)
- [ ] Per-phase resolve-early + deadline defaulting: empty talk message on miss, HOARD on missed act (T019)
- [ ] Resume tri-state: restart in talk / in act / after act — no double reveal or double-count (T020) ⚠️
- [ ] **SC-004** same-turn mutual-help pair (+8 each) in an integration game (T019)
- [ ] **SC-003** spectator API exposes thinking for both phases (T026)
- [ ] Migration upgrade-head on SQLite (T005); `tests/test_migrations.py` green
- [ ] Legacy single-phase game still renders in the viewer (T030)

## Test Conventions (per CLAUDE.md)
- [ ] Test DB is in-memory SQLite; no live Postgres required
- [ ] External model calls (Claude/Codex/Gemini/Hermes) mocked; DB not mocked in integration tests
- [ ] New game logic in `app/engine/` has tests
