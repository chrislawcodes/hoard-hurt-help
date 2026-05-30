# Implementation Quality Checklist

**Purpose**: Validate code quality during implementation
**Feature**: [tasks.md](../tasks.md)

## Code Quality (per constitution — `CLAUDE.md` → Python Standards)

- [ ] No `# type: ignore` or `# noqa` used to silence errors; root causes fixed.
  - Reference: Constitution § Python Standards → No Suppressions
- [ ] All new function signatures fully type-annotated; `from __future__ import annotations` where forward refs are needed.
  - Reference: Constitution § Type Annotations
- [ ] No bare `except:`; specific exception types (argon2 verify, httpx, SQLAlchemy).
  - Reference: Constitution § No Bare except
- [ ] All new routes and DB calls are `async def`; no sync DB in async paths.
  - Reference: Constitution § Async Consistency

## Security (per constitution — Never Do / Standards)

- [ ] Bot key stored only as `key_lookup` (sha256) + `key_hint`; plaintext shown once, never logged.
  - Reference: Constitution § Never Do (no secrets), spec FR-002/FR-024
- [ ] Auth uses indexed lookup + `hmac.compare_digest` (no full-table scan).
  - Reference: spec SC-004, research.md Q1

## File Structure (per constitution — File Structure)

- [ ] New code placed by responsibility under `app/`; MCP wrapper stays thin in `mcp_server/`.
- [ ] No vague filenames (`utils.py`/`helpers.py`); domain-meaningful names (`bot.py`, `next_turn.py`, `caps.py`).

## Data-Critical Migration (global data-critical-waves rule)

- [ ] `0003` migration carries the ⚠️ data-affecting header; reviewer confirms the throwaway-data clear before any prod apply.
- [ ] Migration verified to run on a fresh SQLite DB; test DB schema build path confirmed unaffected (T002 finding).
- [ ] Downgrade path present (does not restore cleared game rows — documented).

## Behavior Preservation

- [ ] Existing turn/submit/history/chat/standings response shapes unchanged after the auth-source swap.
- [ ] Rate limiting still enforced (now per bot.id).
