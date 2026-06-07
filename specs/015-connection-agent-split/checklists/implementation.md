# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md) · Constitution: project `CLAUDE.md`

## Code Quality (per constitution)
- [ ] All new function signatures have type annotations; `from __future__ import annotations` where needed
  - Reference: CLAUDE.md § Python Standards → Type Annotations
- [ ] No `# type: ignore` / `# noqa` / swallowed exceptions to pass checks
  - Reference: CLAUDE.md § No Suppressions
- [ ] No bare `except:`; `except Exception` only at route/task top level
  - Reference: CLAUDE.md § No Bare except
- [ ] Route handlers and DB calls are `async`; no sync DB in async paths
  - Reference: CLAUDE.md § Async Consistency
- [ ] Files stay focused; split by responsibility with domain-meaningful names (connections_* vs agents_*); no `utils.py`/`helpers.py`
  - Reference: CLAUDE.md § File Structure

## Architecture (per plan)
- [ ] Invariant enforced: `kind=ai ⇒ connection_id + model NOT NULL`; `kind=bot ⇒ connection_id NULL`
- [ ] Provider on Connection, model on Agent (model constrained to the connection's provider)
- [ ] next-turn payload identifies the agent (`agent_id`/`agent_name`/`model`) — Slice 1
- [ ] Delete-connection blocked while it powers agents
- [ ] MCP-direct "Advanced" connect path fully removed
- [ ] No `Bot` model class; "bot" only as `AgentKind`/scripted-opponent label; no `/me/bots` route

## High-Care Area
- [ ] Turn-resolution (Slice 1) landed serially, not split across parallel agents
- [ ] Heaviest test coverage on next-turn fan-out (past mid-deploy freeze risk)
