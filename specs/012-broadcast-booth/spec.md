# Feature 012 — Broadcast-booth game viewer + animation speed controls

- **Feature branch**: `feature/broadcast-booth`
- **Created**: 2026-06-04
- **Status**: Spec
- **Input**: Unify the single-game viewer so the robot-circle animation, the standings rail, and the turn feed tell ONE synchronized story driven by a single playhead — keeping the existing animation — and add speed controls so a 16-bot game is watchable.

## Background

The single-game viewer has three pieces that currently feel disconnected: the animated robot-circle **stage**, the standings **rail**, and the turn **feed**. They show three different moments at once (stage cued to turn 1, rail at final standings, feed newest-first), and the feed sits ~1100px below the action. Separately, the animation is **too slow to watch** with 16 bots (a long "talk" phase plus ~1.5s per action) — the original complaint that began this work.

Frame was settled with the owner: the **spectator** is the primary user; when spectator and bot-operator goals conflict, the spectator wins. The **animation is the clock** — the rail and feed follow the animation's current turn. The story **auto-plays** on load (honoring reduced motion) and reads **current-turn-first**.

## User Scenarios & Testing

### User Story 1 — Control the pace so a 16-bot game is watchable (Priority: P1)

As a spectator, I need to speed up the animation and skip the talk phase so that watching a 16-bot game does not drag.

**Why this priority**: This is the original pain. Without watchable pacing, the synchronized "story" auto-plays too slowly to enjoy — every other story depends on it.

**Independent Test**: Open a finished 16-bot game. Raise the speed; confirm the animation visibly runs faster and each turn still resolves fully (no cut-off mid-action). Toggle "skip talk"; confirm the talk phase is skipped and turns play action-only.

**Acceptance Scenarios**:
1. **Given** a turn is playing at 1×, **When** the spectator selects 2× (or 3×), **Then** that turn and all following turns play proportionally faster and each turn still completes before the next begins.
2. **Given** auto-advance is running, **When** the speed changes, **Then** the player does not cut a turn off early or stall between turns.
3. **Given** "skip talk" is on, **When** a turn plays, **Then** the talk phase is omitted and the actions play immediately; the spoken messages remain available in the feed.
4. **Given** a fresh page load, **When** the viewer first plays, **Then** the default pace is noticeably snappier than the pre-feature pace.

### User Story 2 — Watch the game as one synchronized story (Priority: P1)

As a spectator, I need the stage, the standings, the headline, and the feed to all reflect the same turn at the same time so that I read one coherent story instead of reconciling three widgets.

**Why this priority**: This is the core of the feature — the unification. Without it the page stays disjointed.

**Independent Test**: Step the playhead (Next/Prev) to a given turn. Confirm that, together, the stage shows that turn's actions, the rail shows standings as-of that turn, a "now" strip shows that turn's one-line headline + its marquee action, and the feed highlights that same turn.

**Acceptance Scenarios**:
1. **Given** the playhead is on turn N, **When** the page is at rest, **Then** the stage, rail, now-strip, and the highlighted feed turn all correspond to turn N.
2. **Given** the playhead advances to turn N+1 (auto or manual), **When** it lands, **Then** all four regions update together to turn N+1.
3. **Given** the spectator scrubs back to an earlier turn, **When** it lands, **Then** the rail shows the standings as-of that earlier turn (it follows the playhead; it does not stay pinned to the latest).
4. **Given** the now-strip, **When** a turn is showing, **Then** it presents the turn's deterministic headline and a single marquee action chip (the turn's most dramatic action), plus the transport controls.

### User Story 3 — The story tells itself on load (Priority: P2)

As a spectator, I need the game to start unfolding the moment I open it, with the feed reading newest-at-the-top, so I don't have to press anything to follow the story.

**Why this priority**: Strong default experience, but the viewer is still usable if a spectator must press play; hence P2 not P1.

**Independent Test**: Open a finished game with default motion settings — confirm it begins auto-playing from the start and the feed shows the current turn at the top with history descending. Re-open with reduced-motion enabled — confirm it does not auto-animate and instead opens at the latest turn.

**Acceptance Scenarios**:
1. **Given** default motion settings, **When** the viewer loads, **Then** it auto-plays the story from the first turn and the regions advance together.
2. **Given** `prefers-reduced-motion: reduce`, **When** the viewer loads, **Then** it does not auto-animate and opens showing the latest turn's state.
3. **Given** the feed, **When** the playhead is on turn N, **Then** turn N's block is at the top, earlier turns descend below it, and turns later than N are not shown (the story is revealed up to the playhead).
4. **Given** a live game, **When** a new turn resolves, **Then** the new turn becomes the current turn at the top and all regions advance to it.

### User Story 4 — The full record stays available (Priority: P3)

As a bot operator, I need to still read the complete turn record (each bot's message and move) so that I can review how my bot did.

**Why this priority**: Secondary user; the existing Cards/Compact feed views already serve this and must not regress.

**Independent Test**: Switch the feed to Cards — confirm full messages show. Switch to Compact — confirm the grouped-by-type view shows. Confirm both still work after a live update.

**Acceptance Scenarios**:
1. **Given** the feed view switcher, **When** the spectator picks Cards, **Then** full per-bot message cards show for each revealed turn.
2. **Given** the Compact view, **When** selected, **Then** the grouped-by-action view shows.
3. **Given** a live feed update arrives, **When** the region re-renders, **Then** the chosen view and the synced playhead state are preserved.

## Edge Cases

- **No turns resolved yet** → the stage shows its empty state; the now-strip and feed show "waiting for the first move"; nothing auto-plays.
- **Single-turn game** → auto-play plays that one turn and stops; the feed shows one block; controls clamp.
- **Very long game (30+ turns)** → at the default/raised speed plus skip-talk, auto-play remains watchable; the feed reveals turns up to the playhead without unbounded layout cost.
- **Reduced motion** → no auto-animation; opens at the latest turn with rail + feed current.
- **Live game** → the playhead sits at the live edge; an arriving turn (via the SSE fragment swap) becomes current and all regions advance; the spectator's chosen feed view and any manual scrub position must survive the swap.
- **Spectator scrubs back, then a live turn arrives** → define behavior (recommended: a subtle "jump to live" affordance rather than yanking them forward).

## Requirements

### Functional Requirements

- **FR-001**: The viewer MUST provide a speed control offering at least 1×, 2×, and 3×; selecting a speed MUST scale all animation timing proportionally. Supports US1.
- **FR-002**: The auto-advance interval between turns MUST scale with the selected speed so that no turn is cut off early and no dead gap appears between turns. Supports US1.
- **FR-003**: The viewer MUST provide a "skip talk" toggle that omits the talk phase while preserving the spoken messages in the feed. Supports US1.
- **FR-004**: The default animation pace MUST be snappier than the pre-feature pace. Supports US1.
- **FR-005**: A single "playhead" (the current turn) MUST drive the stage, the rail, the now-strip, and the feed highlight together; advancing or scrubbing the playhead MUST update all four. Supports US2.
- **FR-006**: The standings rail MUST reflect standings as-of the current playhead turn (it follows the playhead; the prior "always latest" behavior is retired). Supports US2.
- **FR-007**: The now-strip MUST display the current turn's deterministic one-line headline and a single marquee action chip, alongside the transport controls. Supports US2.
- **FR-008**: On load with default motion settings, the viewer MUST auto-play from the first turn. Supports US3.
- **FR-009**: When `prefers-reduced-motion: reduce` is set, the viewer MUST NOT auto-animate and MUST open at the latest turn. Supports US3.
- **FR-010**: The feed MUST present the current turn at the top, earlier turns descending below it, and MUST NOT show turns later than the playhead. Supports US3.
- **FR-011**: The feed view selection (Story/Cards/Compact) and the synced playhead/view state MUST survive the SSE fragment swap that updates the live region. Supports US2, US4.
- **FR-012**: The existing robot-circle animation (characters, props, motion) MUST be preserved, not replaced. Supports US2.
- **FR-013**: The viewer MUST remain usable on a phone-width screen with no horizontal overflow. Supports US1–US4.
- **FR-014**: Spectator-facing surfaces MUST NOT reveal any bot's private strategy prompt.

### Success Criteria

- **SC-001**: A 16-bot turn at the fastest speed with "skip talk" on resolves in roughly one-third or less of the time it takes at the default-without-skip pace.
- **SC-002**: On opening a finished game (default motion), the story begins advancing with no spectator interaction.
- **SC-003**: From any resting playhead position, the stage, rail, now-strip, and highlighted feed turn all describe the same turn (no mismatch).
- **SC-004**: Stepping or auto-advancing the playhead updates all four regions together (no region lags a turn behind).
- **SC-005**: With reduced motion enabled, opening a game produces no auto-animation and shows the latest turn's standings and feed.
- **SC-006**: At 375px width the viewer shows the stage, rail, now-strip, and feed stacked with no horizontal scrollbar.
- **SC-007**: Switching feed views and receiving a live update never resets the chosen view or the playhead position.

## Key Entities

No new persisted data. The feature reuses existing per-turn data already computed server-side: the deterministic headline, the grouped-by-action summary, and the per-turn standings. The "playhead" is client-side view state (the current turn index), not stored.

## Assumptions

- The existing animation already advances a current-turn index that drives the ring and rail; this feature extends that same index to also drive the now-strip and feed (a coordination layer, not a rewrite).
- The deterministic headline and grouped-compact data shipped earlier this session are available on each turn and are reused as-is.
- "Marquee action" priority for the now-strip chip follows the existing highlights ordering (betrayal > new pact > hurt > help > hoard); refining this ranking is a minor follow-up, not a blocker.
- Live behavior on scrub-back (whether to auto-jump to live) defaults to a non-intrusive "jump to live" affordance; exact treatment can be finalized in planning.

## Out of Scope

- Shared-selection / focus-a-bot (highlight one bot across all regions).
- Chapters / recap-reel mode.
- Story-timeline drama-map scrubber.

## Constitution Check

PASS. Aligns with the project constitution: async/server-rendered (no SPA); accessibility honored via reduced-motion; action meaning not carried by color alone (the feed states deltas and labels in text); spectator boundary preserved (FR-014, no strategy prompts shown). Testing requirement noted: timing logic and the playhead↔feed sync require real-browser verification in addition to the preflight gate (ruff + mypy + pytest).
