# Plan Summary: Broadcast-booth viewer + animation speed controls

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/templates/fragments/robot_circle.html` | modify | Phase 1: `ms()` speed scaler (1×/2×/3×) at every setTimeout/animation site **incl. `scheduleNext` via `talkDurOf`+`buildSchedule().totalDuration`** + skip-talk toggle + snappier default. Phase 2: emit `rc:turn` event + write `data-rc-seq` on `#live-region`; autoplay default; reduced-motion → start at latest; retire `railToLatest` default. |
| `app/templates/game.html` | modify | Feed coordinator JS: on `rc:turn` and `htmx:afterSwap`, show turns `seq<=playhead`, order current-first, highlight current, fill now-strip. Reconcile with existing view-switch + round-nav closures. |
| `app/templates/fragments/live_region.html` | modify | Now-strip element under the stage; feed container hooks for current-first / reveal-up-to. |
| `app/templates/fragments/turn_block.html` | modify (light) | Data hooks for seq→headline matching (`data-seq` already present; `turn.headline` already rendered). |
| `app/templates/fragments/robot_circle_standings.html` | modify (light) | Rail styling tweaks for the booth layout, if needed. |
| `app/static/style.css` | modify | Booth layout (now-strip), speed/skip-talk controls, current-first feed spacing, mobile stacking. |
| `app/routes/web.py` | modify (light) | Reuse `_turn_headline`/`_turn_groups` (already attached to turns); pass an autoplay default to the template only if needed. No new data. |

## Migration Steps

None — no schema change, no new endpoints.

## Data Model

None — reuses existing per-turn fields already on each history turn: `headline` (deterministic play-by-play), `groups` (grouped-by-action), `summary` (counts), `feed_actions`. Playhead = client-side current-turn index, not persisted.

## Key Constraints

- **Scale every timing site, including `scheduleNext`**: speed must scale all `setTimeout` delays and animation durations, and `scheduleNext`'s delay (`talkDurOf` + `buildSchedule().totalDuration` + buffer) must be built from already-scaled values. — *Why: if the auto-advance delay isn't scaled with the rest, the player cuts a turn off mid-animation or stalls between turns.*
- **Playhead handoff lives on `#live-region`** (`data-rc-seq`), not a feed-owned JS var. — *Why: SSE swaps replace `#live-region`'s innerHTML (the feed); only state on the persistent element survives, so the feed must re-read it on `htmx:afterSwap`.*
- **Animation stays the single playhead source**: feed + rail + now-strip follow the animation's `renderTurn(idx)`; nothing else owns "the current turn." — *Why: two clocks is exactly the disjoint we're removing.*
- **Preserve the existing robot animation**: extend, don't replace. — *Why: the personality of the animation is a core product asset; the mockup's circles were stand-ins only.*
- **Reuse the existing transport controls** for the now-strip. — *Why: a second Play/Prev/Next set would desync from the animation's real controls.*
- **Honor `prefers-reduced-motion`**: no autoplay, open at latest. — *Why: accessibility + the constitution; auto-animation is hostile to motion-sensitive users.*
- **Never show strategy prompts** on spectator surfaces. — *Why: spectator boundary (FR-014).*
