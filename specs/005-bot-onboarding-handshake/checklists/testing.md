# Testing Quality Checklist

**Purpose**: Validate test coverage and quality
**Feature**: [tasks.md](../tasks.md) — references `CLAUDE.md`

## Pre-Commit / Preflight (per CLAUDE.md § Preflight Gate)
- [ ] `python3 -m ruff check .` passes
- [ ] `mypy app/ mcp_server/` passes
- [ ] `pytest -q` passes
- [ ] No suppressions added to make checks pass

## Coverage of New Logic (per CLAUDE.md § Testing Requirements)
- [ ] `compute_onboarding_state` tested across all six states (table-driven)
- [ ] First-connection detection: first authed call sets `first_connected_at` + one `connected` event; later calls neither
- [ ] First-move detection: first non-defaulted submission emits one `moved`; later submissions none; covered for HTTP and MCP submit paths
- [ ] First-paint correctness: `/status` renders true state with no event (FR-004)
- [ ] Owner-scoping: `/status` and `/stream` reject non-owners; no key in any response

## Test Hygiene (per CLAUDE.md § Testing Requirements)
- [ ] External calls (model/HTTP) mocked; the DB is NOT mocked — uses the in-memory SQLite test DB
- [ ] New game logic, if any, lives under `app/engine/` and is tested there (here: `bot_activity.py`)
- [ ] Edge cases tested: connected-but-no-game, in-pre-game, paused bot, reissue-not-mistaken-for-disconnect

## Manual Verification (per quickstart.md)
- [ ] Walked waiting → connected → join → first-move in the live preview at :8766
- [ ] Checked phone width (375px) and that status never appears on public pages
