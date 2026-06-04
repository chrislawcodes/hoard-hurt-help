# Quickstart: Broadcast-booth viewer + animation speed controls

Verification is browser-first — passing preflight does NOT prove the timing or the playhead↔feed sync. Use the nested-worktree preview harness.

## Prerequisites

- [ ] Work in the worktree `/Users/chrislaw/hoard-hurt-help/_wt_booth` (branch `feature/broadcast-booth`), with `.venv` symlinked.
- [ ] Seed a 16-bot game into the worktree DB: `DATABASE_URL=sqlite+aiosqlite:///<worktree>/hoardhurthelp.db .venv/bin/python /tmp/seed_viewer.py` (creates `G_9001` completed + `G_9002` active).
- [ ] Preview via the launch config pointed at the worktree (`cwd: _wt_booth`), open `/games/hoard-hurt-help/matches/G_9001`.
- [ ] See `preview-worktree-gotcha` memory: preview serves the main repo unless the worktree is nested + launch.json `cwd` points at it.

## US-1 — Pace control (speed + skip-talk)

**Steps**: Open G_9001. Note the per-turn pace at default. Select 2×, then 3×. Toggle "skip talk".

**Expected**:
- At 2×/3× each turn visibly plays faster AND fully resolves before the next starts (no cut-off, no stall) — watch the auto-advance across at least 3 turns at 3×.
- With skip-talk on, turns play action-only; the spoken messages still appear in the feed (Cards view).
- Default pace is snappier than before.
- **SC-001 check**: time a turn at fastest+skip-talk vs default-no-skip — roughly ⅓ or less.

## US-2 — One synchronized story

**Steps**: Pause. Step Prev/Next to a turn (e.g. the betrayal turn). Read all four regions.

**Expected**:
- Stage actions, rail standings (as-of that turn), now-strip headline + marquee chip, and the highlighted feed turn ALL describe the same turn (SC-003).
- Advancing one turn moves all four together, none lagging (SC-004).
- Scrubbing back shows earlier standings on the rail (it follows the playhead, not pinned latest).

## US-3 — Story tells itself on load

**Steps**: Reload G_9001 with default motion. Then reload with reduced motion forced (`preview_eval` an override or OS setting / emulate).

**Expected**:
- Default: auto-plays from turn 1; feed shows the current turn at top, history descending, future turns hidden.
- Reduced motion: no auto-animation; opens at the latest turn with rail + feed current (SC-005).
- Live game (G_9002): a new resolved turn becomes current at the top; all regions advance.

## US-4 — Full record stays available

**Steps**: Switch feed to Cards, then Compact. Trigger a simulated SSE swap (`htmx.ajax` GET `.../live` into `#live-region`).

**Expected**:
- Cards shows full messages; Compact shows grouped-by-action.
- After the swap, the chosen view AND the current playhead highlight/order are preserved (SC-007).

## Cross-cutting

- **Mobile (375px)**: stage, rail, now-strip, feed stack; no horizontal scrollbar (SC-006).
- **Empty game**: no turns → stage empty state + "waiting for the first move"; nothing auto-plays.
- **Preflight**: from the worktree, `ruff check . && mypy app/ mcp_server/ && pytest -q`. Note: `app/models/enum_types.py:68` mypy error is pre-existing on `origin/main` (mypy 2.1.0), not from this feature.

## Troubleshooting

- **Player stalls or cuts a turn short at 2×/3×** → `scheduleNext`'s delay isn't scaled; ensure `talkDurOf` + `buildSchedule().totalDuration` + buffer are all `ms()`-scaled.
- **Feed loses sync after a live update** → the coordinator isn't re-reading `data-rc-seq` on `htmx:afterSwap`.
- **Page blank after edit** → check `preview_logs`; a Jinja `dict.items`-style gotcha or a JS syntax error 500s/blanks the page.
