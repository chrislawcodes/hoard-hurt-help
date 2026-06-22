# 019 — Play Cockpit, Phase-Led (Mobile): Spec

**Feature:** make the human play cockpit *phase-led* on a phone. Each turn happens
in two separate moments — **Talk** (write blind), then **Act** (read the reveal,
then choose). The screen shows the one thing the current moment needs, full size,
with your controls docked at the bottom and the rest one tap away. This fixes the
cramped, duplicated talk box that shipped in 018.

**Builds on / revises:** 018 (Human Play Screen — Chat Cockpit). 018 decided the
live turn's talk should *ride in the dock* next to the controls (an act-phase
"What was just said" block above the buttons). In practice that block is a
small scrolling box jammed above the action cards
([`play_panel.html:48`](../../app/templates/fragments/play_panel.html)), and it shows
the same "who said what" the feed shows below it — so the talk reads as two
things, inches apart, in two formats. **This spec moves the live talk out of the
dock and into the feed**, leaving the dock as pure controls. Everything else 018
shipped (docked input, newest-at-top feed, score chips, tap-to-target, animation
demotion, 10-player cap) stays.

**Delivery path:** **full path** (not the small-change lane). It is cross-cutting
front-end: it reshapes the player-mode render of `game.html` / `live_region.html`
and the dock. There is **no DB or model change**, **no new dependency**, and **no
change to turn mechanics, endpoints, deadline, or auto-default**.

**Design decisions resolved 2026-06-22** (design session, `ux-design` skill):
the **phase leads the main reading area**; the live turn's talk renders as a
**"this turn" block at the top of the feed**, not in the dock; the dock is
**pure controls**; the reveal is **faithful** (every message, in full, in order —
no summary/triage); the **standings collapse to a one-line always-on strip** with
the full ten behind a tap; **"resolve" is not a screen** — the resolved turn lands
at the top of the feed and the next Talk opens immediately, with a brief highlight
on the fresh entry; the animated replay stays gone during live play and returns at
game end (unchanged from 018).

---

## Why (the problem)

018 turned the play screen into a chat cockpit and that was the right move. But it
left the *talk* in a bad spot, which is the complaint that opened this session:

1. **The talk you read to act is a cramped scrolling box.** In the **act** phase
   the dock shows a "💬 What was just said" block
   ([`play_panel.html:47–63`](../../app/templates/fragments/play_panel.html)), capped to
   ~15–20% of screen height and scrolling
   ([`style.css:2405`](../../app/static/style.css), `.cockpit #play-panel .play-said`).
   With up to nine other players, most of whom talk, that box can't hold the
   reveal — you scroll a tiny window above the buttons.
2. **The same talk shows twice, in two formats.** The dock box shows *this*
   turn's talk; the feed below shows *every resolved* turn's talk woven into each
   move row ([`turn_block.html:35`](../../app/templates/fragments/turn_block.html),
   the `said` line). Same idea, two components, two layouts, a few centimetres
   apart — which is why it reads as duplicated.

**The key insight:** a turn is **two separate moments**, and you're never in both
at once. During **Talk** you write blind — you cannot see anyone else's message,
so this turn's talk does not exist yet. During **Act** the messages are revealed
all at once and *that* is what you read to choose. So the cramming and the
duplication are entirely an **act-phase** problem, and the fix is to stop showing
talk in two places: show it once, full size, in the feed.

**The one job of this screen** (unchanged from 018): *read what just happened,
decide your move, and submit it before the clock — without hunting for anything.*

---

## Scope

**In**

- **Phase-led main area.** Keep one skeleton on every turn — clock + standings
  strip on top, one scrolling feed as the main area, controls docked at the
  bottom — and let the **phase decide what leads the feed**:
  - **Talk phase:** you write blind, so the feed's top entry is the **previous,
    just-resolved turn** (your only intel). The dock is the message box.
  - **Act phase:** the reveal lands, so a **live "this turn" block** is pinned at
    the **top of the feed** showing **every** revealed message, full size,
    attributed, names tappable. The dock is the action cards.
- **Move the live talk into the feed; make the dock pure controls.** Delete the
  act-phase "What was just said" block from the dock
  ([`play_panel.html:47–63`](../../app/templates/fragments/play_panel.html)). The dock
  shows only: talk phase → message box + Send / Stay quiet; act phase → the three
  action cards + "Who?" + Lock in.
- **Faithful reveal.** The "this turn" block shows every other player's message in
  full, in the order spoken; players who stayed quiet collapse to one
  "+N stayed quiet" line. No summarising, ranking, or triage.
- **Standings = always-on strip + drawer.** A single line stays visible on top
  (round·turn · your score & rank · the leader · "10 players ▾"). Tapping it opens
  the full ten-row drawer (round-points, round-wins, your row highlighted), which
  dismisses. Reuses 018's `play_standings.html` data; changes its phone shape to a
  strip+drawer.
- **Resolve as a moment, not a screen.** On `turn_resolved` the just-resolved turn
  appears at the top of the feed (now with its outcomes) and the next Talk phase
  opens immediately; a **brief client-side highlight** (fades after ~3s) marks the
  fresh top entry as just-happened. No countdown, no held state.
- **Score chips, tap-to-target, animation demotion, clock** — all kept from 018.

**Out (this spec)**

- **Spectator and completed-game views** keep today's behaviour (full animation +
  newest-first feed). We do not redesign watching.
- **No change to turn mechanics, submit endpoints, deadline length, auto-default,
  or autopilot** — all shipped in 016/017.
- **No DB, model, or migration change.**
- **No clock / pacing change.** The act phase already gives a **fresh 60 seconds**
  per phase that **waits for you** (a phase only ends early when *every* active
  player has moved, and you are one of them — fast bots cannot cut your time
  short; [`scheduler_turn_loop.py:325`](../../app/engine/scheduler_turn_loop.py),
  `_wait_for_turn`). Reading ~8 short messages in that window is comfortable, so no
  clock scaling is in scope.
- **Desktop** is a follow-up (see Responsive). The design target here is the phone.

---

## The design (phone — the target)

The screen is a fixed-height "app" view that fills the viewport — not a long
scrolling document. Top to bottom it is the same on every turn:

1. **Top bar** — match name and the deadline clock.
2. **Standings strip** — one line: `R3·T4 · You 31·#4 · Bolt 42 · 10 players ▾`.
   Always visible; tap to open the standings drawer.
3. **Feed** — the scrolling middle, the main reading area, **newest at the top**
   (today's order, unchanged). What sits at the top depends on the phase (below).
4. **Dock** — pinned to the bottom, **controls only**, phase-aware.

### The feed leads with the phase

- **Talk phase (write blind).** The feed's top entry is the **previous turn**,
  freshly resolved — its moves, deltas, and messages. That is your only intel for
  what to say, so it gets the screen. The dock below is just your message box.
- **Act phase (read + choose).** A **live "this turn" block** is pinned at the top
  of the feed: a `Round X · Turn Y` divider marked live, then **every** revealed
  message as `Name (score): "message"`, in spoken order, with the
  `+N stayed quiet` tail. This block has **talk but no outcomes yet** (the turn
  isn't resolved). Below it, the resolved history continues as today. Names in the
  live block are **tappable** to set your target.

This is the whole fix: the talk you act on is the **main reading area**, full
width, not a 15%-tall box. It appears **once** — live at the top during the act
phase, then as a normal resolved turn afterwards — never in two formats at once.

### The dock (controls only — reuse `play_panel.html` minus the talk box)

- **Talk phase:** title "Your turn — say something *(optional)*", the message box
  (placeholder "Bluff, promise, or threaten…"), a character counter, **Send** and
  **Stay quiet**. (All already built.)
- **Act phase:** the three action cards — **Hoard** (+2 you), **Help** (+4 them,
  +8 mutual), **Hurt** (−4 them) — a **"Who?"** target shown only for Help/Hurt
  (with a "(or tap a name)" hint), and **Lock in my move**. (All already built.)
- The act-phase "What was just said" block is **removed** — its content now lives
  in the feed's live "this turn" block. The dock never holds talk.
- The dock keeps its place and size between phases, so the player never loses it.

### Faithful reveal

The live "this turn" block shows **every** other player's message, in full, in the
order spoken. No truncation, no "top speakers", no summary line that interprets the
chatter. Players who stayed quiet collapse to a single `+N stayed quiet` line so
silence doesn't take a row each. With up to nine speakers this block is tall and
**scrolls within the feed** — a comfortable full-width scroll, which is the point
of giving it the whole reading area.

### Resolve is a moment, not a screen

There is no pause between turns ([`scheduler_turn_loop.py:124–129`](../../app/engine/scheduler_turn_loop.py)):
`turn_resolved` fires and the next turn's talk phase opens immediately. So:

- The just-resolved turn appears at the **top of the feed** with its outcomes
  (deltas, betrayals, headline) and the **next Talk phase opens**, with that turn
  as the "what just happened" the player reads to write their next message.
- A **brief client-side highlight** (a left-edge accent or a soft background that
  fades after ~3s) marks the fresh top entry as just-resolved, so the eye catches
  the payoff. Driven purely by the existing SSE swap — **no countdown** (there is
  nothing to count down to) and **no held screen**.

### Waiting on others is a state, not a screen

After you submit in the act phase you may wait for the slower players. The act
screen stays put: the live "this turn" block is still on top, the dock shows a calm
`✓ Locked in: <your move> — waiting on N` and you can still change your choice until
the clock ends (the re-submit path already exists). When the turn resolves the
above takes over.

### Standings (strip + drawer)

The game is won on **round-wins** (most rounds won; tiebreak total score), but
minute-to-minute you race on **this round's score** (it resets each round). The
strip shows the headline; the drawer shows both.

- **Strip (always on):** `R3·T4 · You 31·#4 · Bolt 42 · 10 players ▾` — round·turn,
  your round-score and rank, the leader's round-score, and a tap target. One row,
  so the trend is glanceable without opening anything.
- **Drawer (one tap, all ten, slides up and dismisses):** a ranked list — rank,
  name, this-round points, round-wins; **your row highlighted**. Footer legend:
  "● = round wins (decides the game) · number = points this round (resets next
  round)." Reuses 018's `scoreboard` + round-win data.

### Score chips, tap-to-target, animation, clock (kept from 018)

- **Score chips** on every name in the feed (and the live block); you and the
  leader emphasised, everyone else muted — what lets the standings stay collapsed.
- **Tap-to-target:** tapping a name in the act phase selects **Help** if no action
  is chosen yet and fills "Who?" with that name; typing still works; ✕ clears it.
- **Animation demotion:** no full robot-circle stage for a seated player in a
  **live** game; the **full replay returns when the game is `completed`**.
  Spectators are unaffected. (Already shipped; this spec does not change it.)
- **Clock:** visible in the top bar; under **10 seconds** it turns red and the
  dock pulses (missing the deadline auto-Hoards you). The mockup clocks should
  read a realistic ~`0:45`, not seconds — the phase clock is 60s.

---

## States & microcopy (the build contract)

| State | What shows | Microcopy |
|---|---|---|
| **Your turn — talk** | Feed top = previous resolved turn; dock = message box + Send/Stay quiet; clock running | Title "Your turn — say something *(optional)*" · placeholder "Bluff, promise, or threaten…" · "Send" / "Stay quiet" |
| **Your turn — act** | Feed top = live "this turn" block (every message, names tappable); dock = action cards + Who? + Lock in | Title "Your turn — make your move" · "Who? (or tap a name)" · "Lock in my move" |
| **Reveal — nobody spoke** | Live block shows the quiet line only | "No one spoke this turn." |
| **Reveal — some quiet** | Speakers in full, then a tail line | "+N stayed quiet" |
| **Submitted (either phase)** | Dock stays; choice shown as set; can still change | "Submitted — you can still change this until the clock ends." |
| **Locked in, waiting on others (act)** | Live block stays on top; dock shows locked move | "✓ Locked in: {move} — waiting on N player(s)…" |
| **Turn just resolved** | Resolved turn at feed top with a brief highlight; next Talk opening | (no banner copy — the turn block carries its own headline/deltas) |
| **You missed a turn** | The defaulted turn appears in the feed, flagged | "You missed the deadline → auto-Hoard +2" (existing `was_defaulted` tag) |
| **You came back / it's your move** | On load the feed is at the top (latest); dock shows your turn; one-line catch-up if turns passed | "↩ While you were away: N turns played. You're caught up — it's your move." |
| **Round boundary** | A divider in the feed; scores reset note | "— Round X · scores reset —" |
| **On autopilot (you left)** | Dock replaced by a notice; feed keeps streaming | "You left this match. Your seat plays **Hoard** for the rest — you can keep watching." (existing) |
| **Game over** | Full robot-circle replay returns; dock gone; result shown | "Game over. Find your next match →" (existing) |
| **Spectator (no seat)** | Today's layout, unchanged | — |
| **Pre-start (registering)** | Roster of who's in; no dock yet | "✓ You're in as **{name}**." + "Registered" roster (existing) |

Empty / edge:
- **No turns resolved yet (start of match):** during the first talk phase the feed
  shows "Waiting for the first move…"; the dock shows your first turn.
- **Submit blocked (past deadline / not your turn):** inline error in the dock
  (`play-error`), e.g. "That turn just closed — hang on for the next one."

---

## Where to build it (file map)

Grounded in the current code. All front-end unless noted.

| File | Change |
|---|---|
| **New:** [`app/templates/fragments/play_reveal.html`](../../app/templates/fragments/play_reveal.html) | The live "this turn" entry, rendered as the **top card of the one feed** (a `turn-block`, same look as every other turn, with a `turn-live` accent) — **not a separate box**. Built from the existing talk data (`play_talk` / [`_build_turn_talk`](../../app/routes/web_viewer.py)): attributed, tappable names with score chips, plus the `+N stayed quiet` tail. Self-gated to a seated human in the act phase. |
| [`app/templates/fragments/pd_live_region.html`](../../app/templates/fragments/pd_live_region.html) | Include `play_reveal.html` as the first child of `#feed`, so the live turn leads the one feed. Spectator path renders nothing (the fragment self-gates). |
| [`app/templates/fragments/play_panel.html`](../../app/templates/fragments/play_panel.html) | **Remove** both in-dock context boxes — the act-phase "What was just said" block **and** the talk-phase "What just happened" recap — so the dock is controls-only in both phases. The handle becomes a static header (no collapse caret). Submitted copy gains the "waiting on N" count. |
| [`app/templates/fragments/live_region.html`](../../app/templates/fragments/live_region.html) | Comment only — order is unchanged (standings + feed + dock); the live card now rides inside the feed via `pd_live_region`. |
| [`app/templates/game.html`](../../app/templates/game.html) | **Remove** the dock collapse/toggle JS (`dockOpen`, `applyDock`, the `[data-play-toggle]` handler) — the dock no longer collapses. Keep countdown, target show/hide, Pass, tap-to-target, near-deadline auto-submit. |
| [`app/static/style.css`](../../app/static/style.css) | Add `turn-live` accent for the live feed card; make the dock controls-only + always visible (drop the mobile collapse + caret); reserve feed bottom padding (20rem) to clear the always-open dock; remove the now-dead `.play-said` / `.play-recap` / `.play-act` rules. |
| [`tests/test_human_play_panel.py`](../../tests/test_human_play_panel.py) | Update the three assertions that pinned the in-dock boxes: the reveal is now a `turn-live` feed card ("This turn — what everyone said"); the talk phase shows the result in the feed (no dock recap); submitted copy. |

Not needed (the spec guessed these; the build didn't require them): `play_standings.html` already ships an 018 strip-that-expands, so no change; `web_viewer.py` already puts `play_talk` in context, so no change.

**Built 2026-06-22 (Direct Path).** Preflight green (`ruff`, `mypy`, `pytest -q` — 1225 passed); verified at 375px with real rendered snapshots. A live design session then drove the cockpit much more minimal than the spec above. The **final shipped shape**:

- **Top bar** — one strip: round·turn + **Standings ▾** (opens the 10-row drawer) + the countdown **timer**. No match title. No ⋯ menu — **Leave match + Analysis moved into the standings drawer**. The strip flashes red under 10s.
- **One feed.** The current turn is the **top card** of the feed (orange "live" accent, no header — round/turn already shows in the top bar). Each message is **one inline chat line**: tappable name + score chip + message. Messages are **ordered by round score** (highest first; silent last).
- **Dock = controls only** — the three action cards + target (Help/Hurt) + Lock in. No title, no clock (the timer lives in the top bar).
- **Compacted throughout** (slim nav, tight cards, no chrome) so ~8 messages fit above the dock on a phone.
- **Dropped from the spec:** the just-resolved highlight and any result banner — there is no between-turn pause, so the resolved turn just lands at the top of the feed.

This supersedes the "live block" / "standings strip" details written above; those were the starting design, not the shipped one.

---

## Responsive

- **Phone is the design target** of this spec. Fixed-height app view; controls
  docked at the bottom; standings as a one-line strip + drawer.
- **Desktop** is a **follow-up**. For now it keeps 018's behaviour (persistent
  right-hand standings panel, docked input). The phase-led feed change (live talk
  block in the feed, dock controls-only) applies on all widths, since it is a
  content/structure change, not a phone-only CSS tweak — but the detailed desktop
  layout (strip vs side panel, two-column reading) is out of scope here because
  this session designed the phone only. Flag a `020` if we want to finish desktop.
- Breakpoint reuses the existing cockpit phone breakpoint rather than inventing a
  new one.

## Accessibility

- **Action is never colour alone.** Hoard/Help/Hurt always carry their text label
  and payoff, so they are distinguishable without colour.
- The live "this turn" block is real attributed text (`Name: "message"`), not a
  colour-coded graphic; tappable names are keyboard-focusable (the existing
  `data-target-name` handler already binds Enter/Space).
- Clock uses `aria-live="polite"` (already present); "your turn" is announced.
- The just-resolved highlight is decorative (a fade); it carries no meaning that
  isn't already in the turn block's text.
- Tap targets ≥ 44px; the action cards already meet this.
- Muted score chips must pass contrast against both light and dark surfaces.

## Acceptance criteria (testable)

1. **Talk shows in one place.** In the act phase, the revealed talk renders in the
   feed's live "this turn" block and **not** in the dock; the old "What was just
   said" dock block is gone. *(Fixes the duplication.)*
2. **Faithful reveal.** The live block shows **every** speaker's message in full,
   in spoken order, with a single `+N stayed quiet` tail — no truncation or
   summary. With nine other players it scrolls within the feed.
3. **Dock is controls-only.** In both phases the dock contains only the
   phase's controls (talk box, or action cards + Who? + Lock in) — no talk.
4. **Phase leads the feed.** Talk phase: the feed's top entry is the previous
   resolved turn. Act phase: the live "this turn" block is on top.
5. **Resolve is a moment.** On `turn_resolved`, the resolved turn is at the top of
   the feed and the next Talk phase is open; the fresh entry shows a brief
   highlight that fades; there is no separate resolve screen and no countdown.
6. **Standings strip + drawer.** A one-line strip is always visible; tapping it
   opens all ten with your row highlighted and round-wins shown; it dismisses.
7. **Tap-to-target still works** from names in the live block and the feed.
8. **Animation demoted live, restored at end; spectator unchanged.** No full
   replay stage for a seated player mid-game; full replay on `completed`; a viewer
   with no seat sees today's layout.
9. **Preflight green:** `ruff`, `mypy app/ mcp_server/`, `pytest -q` all pass; the
   existing `tests/test_human_play_routes.py` / `test_human_player.py` still pass,
   plus new template/context tests for the act-phase live block, the controls-only
   dock, and the standings strip+drawer.

## Resolved decisions (2026-06-22)

1. **Faithful reveal — yes.** Show every message in full, in order. We do not
   summarise or triage the chatter, even under the clock; reading the bots bluff
   in their own words is part of the game.
2. **Live talk in the feed, not the dock.** The act-phase reveal is the feed's top
   block; the dock is controls-only. This removes both the cramped box and the
   duplicate.
3. **Resolve is a moment, not a screen.** No pause exists between turns, so there
   is no countdown banner; the resolved turn sits at the top of the feed and the
   next Talk opens, with a brief highlight on the fresh entry.
4. **Standings — always-on one-line strip + drawer** (not the 018 collapse).
5. **No clock change.** The 60s-per-phase clock that waits for you is enough; the
   earlier "scale the clock" idea was based on a fabricated number and is dropped.

## Out of scope / future

- **Desktop** finish (strip vs persistent panel, two-column reading) — possible
  `020`.
- Spectator redesign, replay-while-playing, human-vs-human matchmaking.
- Sound, richer notifications beyond the existing tab/title alerts.
- Strategy editing for humans.
