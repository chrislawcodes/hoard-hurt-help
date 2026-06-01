# Implementation Quality Checklist

**Purpose**: Validate code quality during implementation
**Feature**: [tasks.md](../tasks.md)

## Code Quality (per CLAUDE.md)

- [ ] New route handlers are `async def` (Async Consistency)
  - Reference: CLAUDE.md § Python Standards → Async Consistency
- [ ] All new function signatures have type annotations
  - Reference: CLAUDE.md § Python Standards → Type Annotations
- [ ] No `# type: ignore` or `# noqa` to silence errors; root causes fixed
  - Reference: CLAUDE.md § Python Standards → No Suppressions
- [ ] No bare `except:` (specific exceptions only)
  - Reference: CLAUDE.md § Python Standards → No Bare except
- [ ] No vague filenames — new template is `agent_ludum.html`; any extracted module is domain-named (e.g. `lobby_views.py`), never `utils.py`/`helpers.py`
  - Reference: CLAUDE.md § File Structure

## Architecture (per DESIGN.md §11 — platform + game modules)

- [ ] `/` is the platform face; `/play/hoard-hurt-help` is game #1's lobby — no game rules leak into the platform shell
- [ ] Lobby is NOT routed under `/games/{game_id}` (avoids the per-match viewer collision)
- [ ] Marketing data regions reuse existing helpers (`_featured_replay`, `_top_standings`) — no duplicate query logic

## Styling (per plan Decision 4)

- [ ] Agent Ludum identity lives as an `.al` / `.al-plum` scope in `app/static/style.css` (extends the token system; no parallel stylesheet, no 15th `data-theme`)
- [ ] Move-trio (Hoard/Hurt/Help) is distinguishable without color alone (text/shape label)

## Honesty (per spec US3 / FR-009 / FR-010)

- [ ] No fabricated ELO numbers, no `@owner` handles, no "rival in ~3s" / "starts at ELO 1500" copy
- [ ] All displayed data maps to a real game; honest empty state when none exists

## Accessibility & Responsiveness (per UX constitution)

- [ ] Correct on first paint with no JavaScript; `prefers-reduced-motion` respected (reuses home.html static-first pattern)
- [ ] Readable + usable at phone width; CTAs reachable; no horizontal scroll
