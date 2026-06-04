# Implementation Plan: Broadcast-booth game viewer + animation speed controls

**Branch**: `feature/broadcast-booth` | **Date**: 2026-06-04 | **Spec**: [spec.md](./spec.md)

## Summary

Make the existing single-game viewer tell one synchronized story driven by a single playhead — the animation's current turn — and add speed controls so a 16-bot game is watchable. The robot-circle animation is preserved; we extend the index it already uses to drive the rail so it *also* drives a new "now-strip" headline and the feed (current-turn-first, highlighted, revealed up to the playhead). No new data or endpoints — reuse the per-turn headline and grouped-compact data already on each turn.

## Technical Context

**Language/Version**: Python 3.14, async FastAPI; server-rendered Jinja2 templates; vanilla JS (no SPA) with htmx + Server-Sent Events for live fragment swaps.
**Primary Dependencies**: none new. Reuse `_turn_headline()` and `_turn_groups()` in `app/routes/web.py`.
**Storage**: none — no schema change. The "playhead" is client-side view state.
**Testing**: ruff + mypy + pytest (preflight). Plus real-browser verification via the nested-worktree preview harness.
**Target Platform**: web, desktop + phone widths.
**Performance Goals**: SC-001 (fastest+skip-talk ≈ ⅓ the default-no-skip time), SC-004 (regions update together, no lag).
**Constraints**: server-rendered; live regions arrive as SSE-swapped HTML fragments; must work on a phone; preserve the existing animation; never show strategy prompts.
**Scale/Scope**: 16 bots, up to ~30+ turns per game.

## Constitution Check

**Status**: PASS

- **Async/no-SPA** (`CLAUDE.md`): all JS stays vanilla + htmx; no client framework added.
- **Accessibility**: honor `prefers-reduced-motion` (FR-009); deltas/labels carried in text, not color alone (existing feed).
- **Spectator boundary**: no strategy prompts on spectator surfaces (FR-014).
- **Testing**: timing + sync logic must be browser-verified in addition to preflight (noted in quickstart).
- **No suppressions / typed signatures**: any web.py touch keeps full annotations; no `# type: ignore`.

## Architecture Decisions

### Decision 1: Speed via a single `ms()` scaler, applied at every timing site

**Chosen**: A module-level `var speed = <default>` plus `function ms(n){ return Math.round(n / speed); }` in `robot_circle.html`. Every `setTimeout(...)` delay and every `element.animate(..., {duration})` / CSS-driven duration is wrapped in `ms()`.

**Critical**: `scheduleNext()` computes the auto-advance delay as `talkDurOf(turn) + buildSchedule(turn).totalDuration + <buffer>`. `talkDurOf` and `buildSchedule().totalDuration` are built from the same constants — so they must already be scaled (compute them from `ms()`-scaled constants), and the trailing buffer must be `ms(buffer)`. If any one is missed, the player cuts a turn off early or stalls. This is the single highest-risk spot.

**Default pace**: lower the baseline constants (or set default `speed` > 1) so the out-of-box pace is snappier (FR-004). Pick the default during implementation by watching a real 16-bot game.

**Alternatives**: per-constant edits (rejected — error-prone, easy to miss `scheduleNext`); CSS `animation-duration` variable only (rejected — most timing is JS `setTimeout`, not CSS).

### Decision 2: Skip-talk via `talkDurOf()` short-circuit + `renderTurn` guard

**Chosen**: a `var skipTalk` toggle; `talkDurOf()` returns 0 when set; `renderTurn()` skips the talk-phase block (the speak loop + talk caption) when set, jumping straight to scheduling actions. Messages remain in the feed regardless. Keeps the change to two well-scoped spots.

### Decision 3: Playhead is the animation's index; it broadcasts via a data-attribute + event

**Chosen**: `robot_circle.html` is the single source of truth for the current turn (its existing `idx` / `renderTurn`). On each render it (a) writes the current turn's sequence to a data attribute on the persistent `#live-region` element (e.g. `data-rc-seq`), and (b) dispatches a DOM `CustomEvent('rc:turn', {detail:{seq}})`. The feed coordinator (in `game.html`) listens for `rc:turn` to re-sync live, and re-reads `data-rc-seq` on `htmx:afterSwap` so the sync survives SSE fragment swaps.

**Rationale**: the animation script runs once and persists (it's outside `#live-region`, which is the only thing SSE swaps). The feed is *inside* `#live-region` and is replaced on each swap — so the durable handoff is a data attribute on the persistent element, not a JS variable the feed owns.

**Alternatives**: a global JS pub/sub object (works, but a DOM event + data attribute is simpler and already how the page is wired); server-driven sync (rejected — the playhead is pure client state).

### Decision 4: Now-strip = headline (from feed data) + marquee chip (from animation) + existing transport

**Chosen**: a now-strip element directly beneath the stage. Its headline is sourced from the matching turn's server-rendered headline (`turn.headline`, already present); the marquee chip reuses the animation's existing per-turn "marquee"/badge computation; the transport controls reuse the animation's existing Play/Prev/Next (do not duplicate). The coordinator fills the now-strip on each `rc:turn`.

**Rationale**: avoids re-deriving the headline in JS (it's deterministic and already rendered); avoids a second set of controls.

### Decision 5: Feed follows the playhead — current-first, reveal-up-to, in JS

**Chosen**: keep `live_region.html` rendering all resolved turns (as today), and let the feed coordinator JS, on each `rc:turn` / `afterSwap`, (a) show only turn-blocks with `seq <= playhead`, (b) order the current turn first with history descending, and (c) highlight the current block. This mirrors the validated mockup behavior and keeps it a view concern (no re-fetch per turn).

### Decision 6: Rail follows the playhead; reduced-motion opens at latest

**Chosen**: retire #121's "rail always shows latest" default. The rail already updates from `renderTurn`; let it follow the playhead. On load: auto-play from turn 1 (rail follows). If `prefers-reduced-motion: reduce`, do not auto-animate — render the latest turn (rail + feed current) and stay paused.

## Project Structure

Monolithic FastAPI app. Files this feature touches:

```
app/
├── routes/web.py                              - (light) reuse _turn_headline/_turn_groups; pass autoplay default to template if needed
├── templates/
│   ├── game.html                              - feed coordinator JS: listen rc:turn + afterSwap; reorder/hide/highlight feed; fill now-strip; reconcile with existing view-switch + round-nav JS
│   ├── fragments/robot_circle.html            - Phase 1: speed (ms() scaler incl. scheduleNext) + skip-talk; Phase 2: emit rc:turn + write data-rc-seq; autoplay default; reduced-motion start-at-latest; retire railToLatest default
│   ├── fragments/robot_circle_standings.html  - (light) rail styling tweaks if needed for booth layout
│   ├── fragments/live_region.html             - now-strip element placement; feed container hooks for current-first/reveal-up-to
│   └── fragments/turn_block.html              - (light) data hooks for matching seq → headline (data-seq already present)
└── static/style.css                           - booth layout (now-strip), speed/skip-talk controls, mobile stacking, feed current-first spacing
```

**Structure Decision**: Phase 1 is isolated to `robot_circle.html` (speed + skip-talk) and ships/verifies independently. Phase 2 adds the now-strip + the coordinator JS in `game.html` and the layout in CSS, plus the broadcast hooks in `robot_circle.html`. `web.py` changes are minimal (data is already computed).

## Risks

- **`scheduleNext` timing dependency** (Decision 1) — the one spot that silently breaks auto-advance at speed. Mitigated by the single `ms()` scaler + an explicit verification step at 2× and 3×.
- **SSE-swap sync** — the feed is replaced on every live update; the coordinator must re-apply from `data-rc-seq` on `afterSwap` (Decision 3/5). Verify with a simulated swap.
- **Auto-play pacing on long games** — even scaled, a 30-turn auto-play is long; skip-talk + the snappier default are the levers. Verify on a long game.
- **Reduced-motion path** — easy to forget; explicit acceptance scenario + verification.
