# 018 — Human Play Screen (Chat Cockpit): Spec

**Feature:** redesign the screen a signed-in human uses to *play* a live
Hoard-Hurt-Help match by hand, so the input is always in reach and the table-talk
is the main thing you read. It turns today's spectator-style replay page into a
chat-style cockpit for the seated player.

**Builds on:** 016 (Human Player) and 017 (Human + Agent Join), which already
shipped the ability to join and play by hand. This spec changes **layout and
presentation only** — the turn engine, submit endpoints, deadline, and
auto-default already exist and are not touched.

**Delivery path:** recommend the **full path** (not the small-change lane). It is
cross-cutting: it reshapes `game.html`, which spectators also use, and it changes
the feed order for the player view. There is **no DB or model change** and **no
new dependency**.

**Design decisions resolved 2026-06-20** (design session, `ux-design` skill):
the input is **docked at the bottom** on every device (always in reach); the feed
stays **newest-at-top**, like the rest of the site, with the live turn's talk
carried in the dock next to your controls; a collapsible standings panel
(**10 players max**) plus a tiny always-on score chip on every name; tap a name to
set your target; the animated replay steps aside during live play and returns when
the game ends.

---

## Why (the problem)

Human play works today, but the screen is a spectator's replay with the player's
controls bolted on underneath. Two complaints, both reproduced in the shipped
code:

1. **The input is below the fold.** The play panel renders *inside* `#live-region`
   ([`live_region.html:13`](../../app/templates/fragments/live_region.html)),
   which sits *below* the page title, the coach panel, and the full autoplaying
   robot-circle animation ([`game.html:28`](../../app/templates/game.html) vs the
   region at `game.html:57`). Returning mid-game, you must scroll past a movie of
   your own match before you can act.
2. **You can't read who's talking.** The full transcript is the feed, which is
   *below* the play panel. The only talk shown near the input is an act-phase-only
   "What was just said" preview ([`play_panel.html:42`](../../app/templates/fragments/play_panel.html)).
   During the talk phase, and for any history, there is no running, attributed
   chat where you act.

**The one job of this screen:** *read what just happened, decide your move, and
submit it before the clock — without hunting for anything.*

---

## Scope

**In**

- A **player mode** of `game.html`: when the viewer holds a seat in a **live**
  match (`viewer_is_human` and the game is `active`), render the chat cockpit
  described below instead of the spectator replay layout.
- **Input docked at the bottom** on phone and desktop, phase-aware (talk box →
  action cards), reusing the existing `play_panel.html` controls.
- **Feed** stays **newest-at-top** (today's order, for everyone — no reversal).
  In the player view, names are **attributed and tappable** and each carries a
  **score chip**; the live turn's talk is shown in the dock next to your controls
  so you read-and-act together.
- **Tap-to-target:** tapping a name fills the act-phase "Who?" target.
- **Integrated standings:** desktop = an always-open side panel (all 10); phone =
  a collapsed summary that expands to all 10 on one screen. Shows this-round
  points and round-wins; your row highlighted.
- **Animation demotion:** during live play for the seated player, the
  robot-circle stage collapses to the standings; the **full replay returns when
  the game is `completed`**.
- A **visible deadline clock** that warns under 10 seconds.
- All **player states** below, with microcopy.

**Out (this spec)**

- The **spectator** and **completed-game** views keep today's behavior (full
  animation + newest-first feed). We do not redesign watching.
- No change to turn mechanics, submit endpoints, deadline length, auto-default,
  or autopilot — all shipped in 016/017.
- No DB, model, or migration change.
- Human-vs-human matchmaking, clock pause/extend, strategy editing — unchanged.

---

## The design

### Layout — phone (primary)

Top to bottom, the screen is a fixed-height "app" view (it fills the viewport; it
is not a long scrolling document):

1. **Title bar** — match name, a Leave control, the deadline clock.
2. **Standings summary (collapsed)** — one line: your spot + the leader +
   "▾ all N". Tap to expand to the full 10 on one screen; tap again to collapse.
3. **Chat feed** — the scrolling middle. Newest at the bottom. Each turn shows a
   round/turn divider, then each player's talk (name + message) and the resolved
   moves. Names carry score chips and are tappable in the act phase.
4. **Docked input** — pinned to the bottom. Talk phase: the message box +
   Send / Stay quiet. Act phase: the three action cards + "Who?" + Lock in.

### Layout — desktop

Same chat column, but the standings move out of the way into a **persistent
right-hand panel** (all 10 always visible, since there's room). The input stays
docked at the bottom of the chat column. The title bar + clock sit on top.

### The feed and the dock

- **Why the input is docked at the bottom:** so it's always in reach (thumb-
  friendly on a phone), never below the fold. This is separate from feed order.
- **Feed order: newest at the top, flowing down** — the same as today's feed and
  the rest of the site, so it reads naturally and nothing jumps. A newly resolved
  turn appears at the top of the feed.
- **The live moment lives in the dock, not the feed.** During the **act** phase
  the dock shows **this turn's revealed talk** right above your controls (the
  shipped "What was just said" block), so the thing you act on is next to your
  buttons — you never scroll to the top to read it. During the **talk** phase you
  write blind, so the dock is just your message box; the feed's top entry is the
  **previous** turn, which is what you base your message on.
- **Attribution:** every message is `Name: "message"`, reusing `turn_block.html`'s
  existing `who-name` + `said` rendering.
- **Catch-up:** if a new turn resolves while you're reading history lower down, a
  small **"↑ new turn"** marker appears. The dock's "your turn" prompt is the real
  signal that you need to act, so the feed never has to yank your view.

### The docked input (reuse `play_panel.html`)

- **Talk phase:** title "Your turn — say something (optional)", the message box
  (placeholder "Bluff, promise, or threaten…"), a character counter, **Send** and
  **Stay quiet**. (All already built.)
- **Act phase:** the three action cards — **Hoard** (+2 you), **Help** (+4 them,
  +8 mutual), **Hurt** (−4 them) — a **"Who?"** target shown only for Help/Hurt,
  and **Lock in my move**. (All already built.)
- The dock never changes size or place between phases beyond its inner controls,
  so the player never loses it.

### Tap-to-target

In the act phase, every name in the feed is tappable. Tapping a name:
1. selects **Help** if no action is chosen yet (a sensible default; the player can
   switch to Hurt), and
2. fills the "Who?" field with that name (the blue `Name ✕` pill).

Typing into "Who?" still works (the existing datalist); tapping is the fast path
when reacting to something someone said. The ✕ clears it.

### Score chips on names

Every name in the feed and the target picker carries a small, muted score chip —
the player's **current in-round score**. Always shown, in both phases. Only
**you** and the **leader** are emphasized (bold); everyone else is muted, to keep
a 10-name screen calm. This is what lets the standings stay collapsed — the score
of whoever just talked is already on their name, right where you act.

### Standings (collapsed / expanded)

The game is won by **round-wins** (most rounds won; tiebreak total score), but
minute-to-minute you race on **this round's score** (it resets each round). The
panel shows both.

- **Collapsed (phone default):** `🏆 You 12 · 2nd · Ada leads 14 · ▾ all N`.
- **Expanded (one tap, all 10 fit one screen, no scroll):** a ranked list — rank,
  name, this-round points, round-wins. **Your row is highlighted.** Footer legend:
  "● = round wins (decides the game) · number = points this round (resets next
  round)."
- **Desktop:** the expanded list is the always-open right panel; it can collapse
  to a thin rail to give the chat more width.
- Data reuses today's `scoreboard` context and the round-win tally already
  computed for the standings rail.

### Animation demotion

For a seated player in a **live** game, do **not** render the full
`rc-layout` robot-circle stage ([`game.html:28`](../../app/templates/game.html)).
Its standings rail folds into the standings panel above. When the game is
`completed`, the player becomes a spectator of their own match and the **full
replay returns** as the lead. Spectators are unaffected at all times.

### The clock

- The deadline clock is visible in the title bar (and/or the dock).
- Under **10 seconds** it turns red and the dock pulses, because missing the
  deadline **auto-Hoards** you. (The near-deadline auto-submit of the current
  selection already exists in `game.html`.)

---

## States & microcopy (the build contract)

Each state names what shows and the exact words.

| State | What shows | Microcopy |
|---|---|---|
| **Your turn — talk** | Dock = message box + Send/Stay quiet; clock running; feed shows last turn | Title "Your turn — say something *(optional)*" · placeholder "Bluff, promise, or threaten…" · buttons "Send" / "Stay quiet" |
| **Your turn — act** | Dock = action cards + Who? + Lock in; feed shows this turn's talk, names tappable | Title "Your turn — make your move" · "Who?" · "Lock in my move" · hint "Tap a name to Help or Hurt them" |
| **Submitted (either phase)** | Dock stays, choice shown as set, can still change | "Submitted — you can still change this until the clock ends." |
| **Waiting on others** | Dock shows a calm waiting state; feed live | "Waiting on N player(s)…" (reuse existing indicator) |
| **You missed a turn** | The defaulted turn appears in the feed, flagged | "You missed the deadline → auto-Hoard +2" (reuse `was_defaulted` "missed turn" tag) |
| **You came back / it's your move** | On load, the feed is at the top (latest); the dock shows your turn; a one-line catch-up if turns passed | "↩ While you were away: N turns played. You're caught up — it's your move." |
| **Round boundary** | A divider in the feed; scores reset note | "— Round X · scores reset —" |
| **On autopilot (you left)** | Dock replaced by a notice; feed keeps streaming | "You left this match. Your seat plays **Hoard** for the rest — you can keep watching." (existing copy) |
| **Game over** | Full robot-circle replay returns; dock gone; result shown | "Game over. Find your next match →" (existing) |
| **Spectator (no seat)** | Today's layout, unchanged | — |
| **Pre-start (registering)** | Roster of who's in; no dock yet | "✓ You're in as **{name}**." + "Registered" roster (existing) |

Empty / edge:
- **No turns resolved yet (start of match):** feed shows "Waiting for the first
  move…" and the dock shows your first turn when it opens.
- **Submit blocked (past deadline / not your turn):** inline error in the dock
  (`play-error`), e.g. "That turn just closed — hang on for the next one."

---

## Where to build it (file map)

Grounded in the current code. All front-end unless noted.

| File | Change |
|---|---|
| [`app/templates/game.html`](../../app/templates/game.html) | Add a **player-mode** branch: when `viewer_is_human` and game `active`, render the chat cockpit and **skip** the `rc-layout` stage; keep the full stage for spectators and completed games. Extend the existing panel JS for auto-scroll, "↓ new", tap-to-target fill, and standings collapse/expand. |
| [`app/templates/fragments/live_region.html`](../../app/templates/fragments/live_region.html) | Reorder for player mode: standings summary + chat feed + the **docked** `play_panel` at the bottom. Spectator path unchanged. |
| [`app/templates/fragments/play_panel.html`](../../app/templates/fragments/play_panel.html) | Mostly reused as-is. Make it render as the bottom dock; the act-phase "What was just said" block becomes redundant once the feed shows revealed talk inline — drop or keep minimal. |
| [`app/templates/fragments/pd_live_region.html`](../../app/templates/fragments/pd_live_region.html) + [`turn_block.html`](../../app/templates/fragments/turn_block.html) | Keep today's **newest-first** order (no reversal). For the player (`viewer_is_human`), add **score chips** on `who-name` and make names **tappable** in the act phase. |
| **New:** `app/templates/fragments/play_standings.html` | The collapsible standings (summary + expanded list). Desktop = side panel; phone = collapse/expand. Reuses `scoreboard` + round-win data. |
| [`app/static/style.css`](../../app/static/style.css) | New: chat-column layout (fixed-height app view), bottom dock, standings panel/collapse, name chips, "↓ new" button, clock-low state. Extend the existing `.play-panel` / `.rc-layout` rules; do **not** fork the type scale or color vars. |
| [`app/routes/web_viewer.py`](../../app/routes/web_viewer.py) | Context: add a **name → in-round score** map and **ranks** for the chips and the standings; a flag to suppress the replay stage in player mode. Reuse `scoreboard`, `play_targets`, `play_*` already built. |
| [`app/routes/matches_user.py`](../../app/routes/matches_user.py) + [`game_admin_web.py`](../../app/routes/game_admin_web.py) | **Cap matches at 10:** lower the create default/limit from 20 to 10 (game hard cap stays 100). Add a validation guard + test. |

---

## Responsive

- **Phone is the design target.** Fixed-height app view; dock pinned to the
  bottom (thumb reach); standings collapsed by default.
- **Desktop** adds the right-hand standings panel and more chat width; dock stays
  at the bottom of the chat column.
- Breakpoint reuses the existing `rc-layout` phone breakpoint (~620px) rather than
  inventing a new one.

## Accessibility

- **Action is never color alone.** Hoard/Help/Hurt always carry their text label
  and payoff, so they're distinguishable without color.
- Clock uses `aria-live="polite"` (already present); the "your turn" change is
  announced.
- Name chips: the score is real text, not a color.
- Tap targets ≥ 44px; the action cards already meet this.
- Contrast on muted chips must pass against both light and dark surfaces.

## Acceptance criteria (testable)

1. **Input is never below the fold.** As a seated player in a live game, on first
   paint at phone width, the docked input is visible without scrolling — in both
   talk and act phases. *(Fixes complaint 1.)*
2. **Transcript is visible and attributed.** The chat feed showing other players'
   names + messages is on screen with the input, in both phases. *(Fixes
   complaint 2.)*
3. **Newest-at-top feed.** A newly resolved turn appears at the top of the feed;
   the dock shows your turn so you never rely on scrolling to know it's your move;
   a "↑ new turn" marker appears if you're reading history lower down.
4. **Tap-to-target.** Tapping a name in the act phase fills "Who?" with that name.
5. **Score chips.** Every name in the feed shows the player's current in-round
   score; you and the leader are emphasized.
6. **Standings.** Phone collapses to a summary and expands to all 10; desktop
   shows the panel; your row is highlighted; round-wins shown.
7. **Animation demoted live, restored at end.** No full replay stage for a seated
   player mid-game; the full replay shows when the game is completed.
8. **Spectator unchanged.** A viewer with no seat sees today's layout.
9. **Preflight green:** `ruff`, `mypy app/ mcp_server/`, `pytest -q` all pass; the
   existing `tests/test_human_play_routes.py` / `test_human_player.py` still pass,
   plus new template/context tests for the player-mode branch and chip/rank data.

## Resolved decisions (2026-06-20)

1. **Cap matches at 10** — yes. Lower the create default from 20 to 10 as part of
   this work (in scope above; game hard cap stays 100).
2. **Feed order — newest at the top, for everyone.** No reversal; today's order
   stays. The input is docked at the bottom for reach, and the live turn's talk
   rides in the dock so it sits next to your controls.
3. **Standings on phone — at the top** (collapsed summary), with per-player
   numbers carried on the name chips down where you act.
4. **Clock warning — red + pulse under 10 seconds.** No sound.

## Out of scope / future

- Spectator redesign, replay-while-playing, human-vs-human matchmaking.
- Sound, richer notifications beyond the existing tab/title alerts.
- Strategy editing for humans.
