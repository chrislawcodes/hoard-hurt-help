# Implementation Quality Checklist

**Purpose**: Validate code quality during implementation
**Feature**: [tasks.md](../tasks.md) — references `CLAUDE.md` (project constitution)

## Code Quality (per CLAUDE.md § Python Standards)
- [ ] All new function signatures fully type-annotated; `from __future__ import annotations` where needed for forward refs
- [ ] No `# type: ignore` / `# noqa` to silence errors — root cause fixed (§ No Suppressions)
- [ ] No bare `except:`; only specific exceptions (route-boundary `except Exception` only if justified) (§ No Bare except)
- [ ] New route handlers and DB access are `async def`; no sync DB calls on async paths (§ Async Consistency)

## File Structure (per CLAUDE.md § File Structure)
- [ ] New domain logic in `app/engine/bot_activity.py` (domain-meaningful name; no `utils.py`/`helpers.py`)
- [ ] App code in `app/`; MCP code in `mcp_server/` — not mixed
- [ ] New fragment template named for its job (`bots/_status.html`)

## Feature Constraints (per plan-summary.md)
- [ ] First-connection write happens only on `NULL→now` in `require_bot`; no write/publish on later calls
- [ ] First-move publishes only on the first non-defaulted submission; covers HTTP + MCP submit paths
- [ ] `/status` and `/stream` are owner-scoped (`require_user` + `_owned_bot`); non-owner → 404
- [ ] The connection code is never re-rendered in any new path; only `key_hint` shown (FR-011)
- [ ] Reuses `app/broadcast.py` on `bot:{id}` — no new transport
- [ ] State conveyed by icon + text, not color alone; panel works at 375px (FR-012)
- [ ] First paint renders true state without any SSE event; events only re-fetch + add a one-shot flourish

## Data-Critical Migration (per CLAUDE.md global data-critical rule)
- [ ] Migration is additive (`add_column`), nullable, SQLite-safe (no `drop_constraint`/batch)
- [ ] No backfill required; `NULL` handled by play-history precedence in the resolver
- [ ] Verified post-apply: `bots` row count unchanged; column present; `downgrade` reverses it
