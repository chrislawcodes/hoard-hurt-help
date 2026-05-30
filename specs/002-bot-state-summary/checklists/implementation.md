# Implementation Quality Checklist

**Purpose**: Validate code quality during implementation
**Feature**: [tasks.md](../tasks.md) · Constitution: `CLAUDE.md`

## Async consistency (per CLAUDE.md § Python Standards)
- [ ] All new route handlers and DB calls are `async def`
- [ ] No sync DB calls mixed into async paths

## Type annotations (per CLAUDE.md § Type Annotations)
- [ ] Every new function signature is fully annotated
- [ ] `from __future__ import annotations` where forward refs need it

## No suppressions (per CLAUDE.md § No Suppressions)
- [ ] No `# type: ignore`, no `# noqa`, no swallowed exceptions
- [ ] No bare `except:`; `except Exception` only at route/task tops
- [ ] Root causes fixed, not silenced

## File structure (per CLAUDE.md § File Structure)
- [ ] New engine logic in domain-named modules (`opponent_stats.py`, `board_signals.py`, `turn_summary.py`) — no `utils.py`/`helpers.py`
- [ ] App (`app/`) and MCP (`mcp_server/`) code stay separated; MCP tools call the HTTP API
- [ ] Files stay focused (one responsibility each)

## Feature-specific invariants
- [ ] Computed signals use action data only — **no message text parsing** (v1)
- [ ] Server returns facts only — no "trust score"/judgment fields
- [ ] `history` removed from the push payload; available only via the pull endpoint
- [ ] Summary size bounded by short-list cap; independent of turn count
- [ ] Heuristics deterministic (explicit tiebreaks/thresholds)
- [ ] SQL `GROUP BY` aggregation; full detail built only for the short-list
- [ ] Error envelopes match the existing `{ error: { code, message, details } }` shape
- [ ] No secrets committed
