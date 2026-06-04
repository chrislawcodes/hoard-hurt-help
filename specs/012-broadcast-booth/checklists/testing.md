# Testing Quality Checklist

**Feature**: [tasks.md](../tasks.md)

## Preflight (per `CLAUDE.md` § Preflight Gate)
- [ ] `python3 -m ruff check .` passes
- [ ] `mypy app/ mcp_server/` — no NEW errors (pre-existing `app/models/enum_types.py:68` excepted, documented)
- [ ] `pytest -q` passes

## Browser verification (required — preflight can't prove timing/sync)
- [ ] US1: 1×/2×/3× across ≥3 turns, no cut-off/stall; skip-talk on/off; snappier default (SC-001)
- [ ] US2: all four regions match + advance together; rail follows scrub-back (SC-003/SC-004)
- [ ] US3: autoplay on load + current-first/reveal-up-to feed; reduced-motion opens at latest paused (SC-002/SC-005); live game advances
- [ ] US4: Story/Cards/Compact all render; view + playhead survive a simulated SSE swap (SC-007)
- [ ] Mobile 375px: stacked, no horizontal overflow (SC-006)
- [ ] Empty game: empty state, no autoplay

## States covered
- [ ] Live, finished/replay, empty, mobile, reduced-motion
- [ ] Screenshots captured for the PR Validation section
