# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md) — references CLAUDE.md (project constitution)

## Security Invariant (highest priority)
- [ ] No `thinking` field exists on ANY schema in `app/schemas/agent.py`
- [ ] No agent endpoint response (`/turn`, `/next-turn`, history, chat, opponent-history) contains thinking text for any player — incl. the requester's own
- [ ] Spectator history uses its OWN types, not the agent `HistoryTurn`
  - Reference: CLAUDE.md "Never Do" (don't leak privileged data); spectator API "never returns strategy prompts"

## Code Quality (per CLAUDE.md)
- [ ] All route handlers and DB calls are `async def`; no sync DB in async paths
- [ ] Full type annotations on every new/changed signature; `from __future__ import annotations` where needed
- [ ] No `# type: ignore` / `# noqa` to silence errors — fix root cause
- [ ] Specific exception types (no bare `except:`); `except Exception` only at route/task top
- [ ] New files are domain-named (no `utils.py`/`helpers.py`); app code in `app/`, runners in `scripts/`

## Migration (per CLAUDE.md / sqlite-migration-batch-mode)
- [ ] Migration is adds-only (columns + new table); if any constraint op sneaks in, wrap in `op.batch_alter_table`
- [ ] `server_default` set on new NOT NULL columns so existing rows upgrade cleanly
- [ ] `alembic upgrade head` succeeds on a SQLite dev DB

## Correctness
- [ ] Payoff math in `resolver.py` unchanged (parity)
- [ ] Turn-loop resume is idempotent across both phases (no double reveal/resolve)
- [ ] Legacy completed games still render (fallback to `turn_submissions.message`)
- [ ] All five runners + the served runner files branch on phase and carry thinking
