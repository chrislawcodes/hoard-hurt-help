# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md)

## Code Quality (per `CLAUDE.md` → Python Standards)
- [ ] No `# type: ignore` / `# noqa`; root causes fixed.
- [ ] Full type annotations on the `Game` Protocol, `GameConfig`, registry, adapter, stub.
- [ ] No bare `except`; async DB calls in async paths.

## Architecture (per plan)
- [ ] `app/engine/*` is **unchanged** (regression gate) — the PD module only *calls* it.
- [ ] Platform code (scheduler, agent API, viewer, lobby) references the `Game` contract via the registry — never a specific game.
- [ ] `get(game_type)` raises generically; the poller skips an unregistered game rather than crashing (SC-004).
- [ ] `validate_move` failure surfaces as the standard `400 INVALID_MOVE` envelope.

## File structure (per `CLAUDE.md` → File Structure)
- [ ] Game code under `app/games/` (domain-named); no `utils`/`helpers`.
- [ ] PD module isolated under `app/games/hoard_hurt_help/`.

## Data-critical migration (global data-critical-waves rule)
- [ ] `0004` carries the data-affecting header; reviewer confirms before prod (benign: column add + backfill).
- [ ] Storage generalization (move JSON) NOT done here — TurnSubmission/Player columns untouched.

## Behavior preservation
- [ ] PD request/response shapes (turn/next-turn/submit) unchanged.
- [ ] PD scoring (HOARD/HELP/HURT/mutual bonus/floor/round wins/finalize) byte-identical.
