# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md)

## Code Quality (per `CLAUDE.md`)
- [ ] No suppressions to pass checks — no `# type: ignore`, no `# noqa`, no swallowed exceptions (§ Python Standards)
- [ ] Any `app/routes/web.py` touch keeps full type annotations on signatures
- [ ] Async consistency preserved (no sync DB in async paths) — feature adds no DB calls
- [ ] Files stay focused; no vague `utils.py`/`helpers.py` added
- [ ] All styling in `app/static/style.css`, extending existing variables/scale (no parallel system)

## Feature-specific correctness
- [ ] `ms()` applied at EVERY setTimeout + animation-duration site, including `scheduleNext` (`talkDurOf` + `buildSchedule().totalDuration` + buffer) — the cut-off/stall risk
- [ ] Playhead handoff lives on `#live-region` (`data-rc-seq`); feed re-applies on `htmx:afterSwap`
- [ ] Animation remains the single playhead source; rail + feed + now-strip follow it
- [ ] Existing robot animation preserved (not replaced); existing transport controls reused for the now-strip
- [ ] `prefers-reduced-motion: reduce` honored (no autoplay, open at latest)
- [ ] No strategy prompts surfaced on spectator views (FR-014)
- [ ] Fragments make sense on first paint AND after every SSE swap

## Process (per `CLAUDE.md`)
- [ ] Work done in the `_wt_booth` worktree; one feature → one branch
- [ ] Preflight gate green before push (ruff + mypy + pytest); pre-existing `enum_types.py:68` documented, not mine
