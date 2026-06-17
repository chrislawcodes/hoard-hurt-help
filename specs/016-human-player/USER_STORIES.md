# Human Player — User Stories

Stories for the "let a human join and play" feature. Grouped by stage of the
journey: **discover & join → play a turn → get feedback → view & manage → leave**,
plus spectator and admin stories that must keep working.

Format: *As a … I want … so that …*, with **acceptance criteria** (AC) as
checkable conditions. **P1** = must ship in v1. **P2** = nice-to-have / later.

See `DESIGN.md` for scope and `SPEC.md` for exact behavior.

---

## A. Discover & join

### A1 — See that I can play, not just watch (P1)
As a signed-in visitor, I want to see a clear "Join as a player" option on a
scheduled match, so that I know I can play and not only spectate.
- AC: A scheduled/registering match shows a "Play this match" control in the
  lobby card and on the match viewer.
- AC: The control is visible to signed-in users; signed-out users see a
  "Sign in to play" prompt that routes through Google sign-in and back.
- AC: The control is hidden once the match is full, active and unjoinable, or
  finished.

### A2 — Join with no setup (P1)
As a human, I want to take a seat without registering an agent, connection, API
key, or strategy prompt, so that joining is one quick step.
- AC: Joining as a human does **not** route me through the "create an AI agent"
  or "connect a client" flows.
- AC: I provide only a display name (pre-filled from my handle), then confirm.
- AC: My seat name follows the same public format used for every player and is
  unique within the match.

### A3 — Pick a display name (P1)
As a human, I want to choose the name shown to other players, so that I have an
identity in the game.
- AC: Name is pre-filled with a sensible default (my handle).
- AC: If the name collides with an existing seat, the server suggests a tweaked
  one rather than erroring.

### A4 — Join from either the lobby or the viewer (P1)
As a human, I want to join from the lobby card *or* from inside the match viewer,
so that I can commit the moment I decide.
- AC: Both entry points reach the same join action and land me back on the match
  viewer, now seated.

### A5 — Be told when joining isn't allowed (P1)
As a human, I want a clear message when I can't join, so that I'm not confused.
- AC: Full match → "This match is full."
- AC: Already started and not joinable → explains why.
- AC: Already seated → takes me to the viewer instead of erroring.

### A6 — Reserve nothing I don't need (P1)
As a human, I want joining to not depend on any external client being online,
so that my seat is immediately real (unlike an AI agent waiting on a connection).
- AC: A human seat is active on join — it is never put in a "waiting for your
  client to connect" hold.

### A7 — Know what I'm signing up for (P1)
As a human, I want to see the time commitment before I join, so that I'm not
surprised by a long match.
- AC: The join screen states the match size and rough active time
  (e.g. "~N turns · expect to be active ~M min").
- AC: Notification permission is requested here, at join — never mid-turn.

---

## B. Play a turn

### B1 — Know it's my turn, on the page (P1)
As a human, I want the viewer to clearly show when it's my turn, so that I don't
miss my window.
- AC: When my turn opens, a play panel appears in the viewer's live region.
- AC: A countdown shows the seconds left in the current phase.
- AC: When it's not my turn, no play panel shows for me.

### B2 — Know it's my turn, off the page (P1)
As a human who's looking at another tab, I want an alert when my turn opens, so
that I come back in time.
- AC: A browser notification fires when my turn opens (if I've granted
  permission).
- AC: The tab title changes to flag my turn (e.g. "(Your turn!) …") as an
  always-on fallback.
- AC: An optional sound can be enabled/muted; it defaults **off**.
- AC: Alerts fire on the talk phase opening and the act phase opening, and clear
  once I submit/Pass or the turn resolves.

### B3 — Say something or Pass (talk phase) (P1)
As a human, I want to post a short public message *or* skip it in one tap, so that
talk is a choice, not a per-turn chore.
- AC: The talk panel has a one-line message box, a Submit, and a one-tap **Pass**.
- AC: Submitting records my message; Passing records no message; both count me as
  done immediately so the phase can resolve early.
- AC: Message length is capped (matching the cap agents use).
- AC: If I do nothing, an empty message is recorded at the deadline.

### B4 — Make my move with the stakes in front of me (P1)
As a human, I want to choose Hoard / Help / Hurt with the payoffs shown and a
safe default, so that I can act fast and never get punished for hesitating.
- AC: **Hoard is pre-selected by default** when the act phase opens.
- AC: Each action card shows its payoff in text (e.g. "Hoard +2 you", "Help +4
  them", "Hurt −4 them") — distinguishable **without relying on color alone**.
- AC: Help and Hurt require a target via a **type-ahead / search picker** (usable
  even in large matches); Hoard takes no target and hides the picker.
- AC: I cannot target myself.

### B5 — Coast or commit; the clock is never a trap (P1)
As a human, I want a safe, predictable result whether I act or not, so that
running low on time isn't a punishment.
- AC: When the clock ends, the server records my **current selection** (Hoard if I
  never touched it).
- AC: If I'd changed the selection (e.g. Help → Bob) but didn't click Submit, that
  selection is recorded — not a fallback.
- AC: A coasted Hoard appears in the feed as a normal Hoard, with no scolding
  "you missed a turn" copy.

### B6 — Change my mind before the phase closes (P1)
As a human, I want to re-pick before the phase resolves, so that a fat-finger
isn't permanent.
- AC: Re-selecting in the same open phase replaces my pending choice; the clock
  submits my latest selection.
- AC: Once the phase resolves, my choice is locked and re-submit is refused with
  a clear "that turn already resolved" message.

### B9 — See that the game is alive while I (or others) think (P1)
As a human or spectator, I want to see when a phase is waiting on players, so that
a slow turn doesn't look frozen.
- AC: During an open phase the viewer shows a neutral "waiting on N players…"
  (no names, no choices).
- AC: Submit/Pass shrinks N; the phase resolves when N reaches zero or the clock
  ends.

### B10 — Play on my phone (P1)
As a human who watches on my phone, I want to play on my phone too, so that the
"your turn" alert I get there is actionable.
- AC: In a normal-size match, the three actions are large tap targets, the panel is
  thumb-reachable, and the target picker works on a small screen.
- AC: Huge (up to 100-player) matches need not be phone-tuned in v1.

### B7 — Play even with agents and bots in the match (P1)
As a human, I want to play in a match that also has AI agents and scripted bots,
so that I get the real, mixed matchup.
- AC: A match can contain humans, agents, and bots at once.
- AC: The turn resolves only when all active players (any kind) have acted or the
  clock expires.

### B8 — Have a fair, fast panel (P1)
As a human under a 60s clock, I want the panel ready instantly, so that I don't
waste seconds.
- AC: The play panel renders with the action picker reachable in one glance; no
  extra navigation.
- AC: The message box is focused/ready so I can type immediately.

---

## C. Feedback

### C1 — Confirm my submission (P1)
As a human, I want clear confirmation when I've submitted, so that I trust it
worked.
- AC: After submit while the phase is still open, the panel shows "Submitted — you
  can still change this until the clock ends" and shows my current choice.
- AC: "Locked" wording is used only after the turn resolves.
- AC: The countdown keeps running with "waiting on N players…" (others may still
  be acting).

### C2 — See the turn resolve in the story (P1)
As a human, I want my move to appear in the feed like everyone else's, so that I
feel part of the narrative.
- AC: When the turn resolves, the feed shows my action, my message, my points
  delta, and the round outcome — through the same feed rendering all players use.
- AC: The scoreboard updates with my new score.

### C3 — See an error I can recover from (P1)
As a human, I want submit errors explained, so that I know what to do.
- AC: Submitting after the deadline → "That turn already resolved" (not a raw
  error).
- AC: Submitting an illegal move (e.g. Help with no target) → an inline message
  telling me what to fix, with nothing recorded.

---

## D. View & manage

### D1 — See it in My Matches (P1)
As a human, I want my joined matches listed in "My matches," so that I can find
my way back.
- AC: A match I've joined as a human appears in `/me/matches` with my seat name.
- AC: I can click through to the viewer to watch or play.

### D2 — Return on any device (P1)
As a human, I want to rejoin from any device by signing in, so that I'm not tied
to one browser.
- AC: Signing in with the seat-owning account on another device shows the play
  panel on my turn.
- AC: Signing in with a different account never shows my play panel.

### D3 — Watch when it's not my turn (P1)
As a human, I want the full spectator viewer between my turns, so that I can read
the room.
- AC: Between turns I see the same live feed and scoreboard spectators see.

---

## E. Leave

### E1 — Leave before the match starts (P1)
As a human, I want to drop my seat before kickoff, so that I'm not committed.
- AC: A "Leave match" control is available pre-start.
- AC: After leaving, my seat is removed from the match before it starts (or, if it
  has started, see E2).

### E2 — Leave during the match (P1)
As a human, I want to quit a match in progress, so that I'm not stuck playing.
- AC: A "Leave match" control is available during the match, with clear copy that
  my seat will Hoard for the rest of the match.
- AC: After leaving, my seat **auto-Hoards every remaining turn**, submitted
  immediately so it never makes the table wait on the clock.
- AC: My seat stays in the standings with a "left" marker; my already-played turns
  remain in the record.

---

## F. Spectator (must-not-regress)

### F1 — Watch unchanged (P1)
As a spectator, I want the viewer to stay clean and familiar, so that the watch
experience doesn't regress.
- AC: A spectator (not the seat owner) never sees a play panel.
- AC: The feed, scoreboard, live updates, and replay timeline are unchanged,
  except for two additive, calm elements visible to everyone: the "Play this
  match" CTA and the "waiting on N players…" phase indicator.

### F2 — Strategy boundary holds (P1)
As a spectator, I must never see private strategy. (Humans have none, but the
boundary stays.)
- AC: No private per-player intent ("thinking") is exposed to spectators.

---

## G. Admin (supporting)

### G1 — Run matches humans can join (P1)
As an admin, I want the matches I create to be joinable by humans by default, so
that no extra setup is needed.
- AC: A standard scheduled match accepts human seats with no special flag in v1
  (or, if a flag is chosen in the spec, the admin create form exposes it clearly).

### G2 — See humans in a match (P2)
As an admin, I want to tell which seats are humans, so that I can reason about a
match.
- AC: Admin match views indicate which players are human. (P2 — public views stay
  uniform.)
