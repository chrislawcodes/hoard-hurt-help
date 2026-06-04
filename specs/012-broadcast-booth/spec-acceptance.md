# Acceptance Criteria: Broadcast-booth viewer + animation speed controls

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Control the pace so a 16-bot game is watchable | P1 |
| US-2 | Watch the game as one synchronized story | P1 |
| US-3 | The story tells itself on load | P2 |
| US-4 | The full record stays available | P3 |

## Acceptance Scenarios

### US-1: Control the pace (speed + skip-talk)
- Given a turn is playing at 1×, When the spectator selects 2× (or 3×), Then that turn and all following turns play proportionally faster and each turn still completes before the next begins.
- Given auto-advance is running, When the speed changes, Then the player does not cut a turn off early or stall between turns.
- Given "skip talk" is on, When a turn plays, Then the talk phase is omitted and the actions play immediately; the spoken messages remain available in the feed.
- Given a fresh page load, When the viewer first plays, Then the default pace is noticeably snappier than the pre-feature pace.

### US-2: One synchronized story
- Given the playhead is on turn N, When the page is at rest, Then the stage, rail, now-strip, and the highlighted feed turn all correspond to turn N.
- Given the playhead advances to turn N+1 (auto or manual), When it lands, Then all four regions update together to turn N+1.
- Given the spectator scrubs back to an earlier turn, When it lands, Then the rail shows the standings as-of that earlier turn (it follows the playhead; not pinned to latest).
- Given the now-strip, When a turn is showing, Then it presents the turn's deterministic headline and a single marquee action chip, plus the transport controls.

### US-3: Story tells itself on load
- Given default motion settings, When the viewer loads, Then it auto-plays from the first turn and the regions advance together.
- Given prefers-reduced-motion: reduce, When the viewer loads, Then it does not auto-animate and opens showing the latest turn's state.
- Given the feed, When the playhead is on turn N, Then turn N's block is at the top, earlier turns descend below it, and turns later than N are not shown.
- Given a live game, When a new turn resolves, Then the new turn becomes the current turn at the top and all regions advance to it.

### US-4: Full record stays available
- Given the feed view switcher, When the spectator picks Cards, Then full per-bot message cards show for each revealed turn.
- Given the Compact view, When selected, Then the grouped-by-action view shows.
- Given a live feed update arrives, When the region re-renders, Then the chosen view and the synced playhead state are preserved.

## Success Criteria
- SC-001: A 16-bot turn at the fastest speed with skip-talk on resolves in roughly one-third or less of the default-no-skip time.
- SC-002: On opening a finished game (default motion), the story begins advancing with no interaction.
- SC-003: From any resting playhead position, the stage, rail, now-strip, and highlighted feed turn all describe the same turn.
- SC-004: Stepping or auto-advancing the playhead updates all four regions together (no region a turn behind).
- SC-005: With reduced motion, opening a game produces no auto-animation and shows the latest turn's standings + feed.
- SC-006: At 375px width the stage, rail, now-strip, and feed stack with no horizontal scrollbar.
- SC-007: Switching feed views and receiving a live update never resets the chosen view or the playhead position.

## Key Constraints
- Scale every animation timing site **including `scheduleNext`'s auto-advance delay** — *Why: unscaled, the player cuts a turn off mid-animation or stalls between turns.*
- Sync state (playhead seq, feed view) persists on `#live-region` — *Why: SSE swaps replace the feed; only state on the persistent element survives.*
- The animation is the single playhead source; rail + feed + now-strip follow it — *Why: two clocks is the disjoint being removed.*
- Preserve the existing robot animation; reuse its transport controls — *Why: core product asset; a second control set would desync.*
- Honor prefers-reduced-motion (no autoplay, open at latest) — *Why: accessibility + constitution.*
- No strategy prompts on spectator surfaces — *Why: spectator boundary.*
