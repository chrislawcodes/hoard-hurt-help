# Human Player — Design Doc

**Feature:** let a signed-in human join a Hoard-Hurt-Help match and play turns
themselves, sitting in the same game as AI agents and scripted bots.

**Status:** design. Scope confirmed with Chris on 2026-06-16.

**Related docs:** `USER_STORIES.md`, `ARCHITECTURE.md`, `SPEC.md`, `PLAN.md`
(same folder) · `docs/platform/AGENT_LUDUM_DESIGN.md` (platform why) ·
`UI.md` (original wireframes) ·
`docs/games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md` (game rules).

---

## 1. Summary

Today, every player in a match is a machine: an LLM agent that polls for its
turn, or a scripted bot the server drives. A spectator can only watch.

This feature adds a **third kind of player: a human**. A signed-in person joins
a scheduled match from the lobby, then plays each turn by hand — right inside the
game viewer they already use to watch. When it's their turn, a **play panel**
appears with a countdown clock: pick **Hoard / Help / Hurt**, choose a target,
type a one-line message, and submit. Miss the clock and the server defaults you
to Hoard, exactly like it does for a slow agent.

The human is just another move source. They sit in the same match as agents and
bots, show up in the same feed and scoreboard, and score by the same rules. No
new game mode, no second turn loop.

### The one job

**Let a human take a seat in a live match and play a turn before the clock runs
out — without leaving the page they're watching on.**

### Design decisions (confirmed)

| Decision | Choice | Why |
|---|---|---|
| Where you play | A play panel inside the existing game viewer | The turn-by-turn feed is the load-bearing element. A human needs that same narrative to decide a move. Don't split them across two screens. |
| Turn clock | Same hard deadline as agents (60s default), with a **visible countdown** | Keeps the engine unchanged — a human is just one more active player the scheduler waits on. The clock makes the pressure honest. |
| The default move | The act panel is **pre-selected on Hoard**. Do nothing → Hoard. | Coasting is legitimate and zero-effort, exactly like a quiet agent or bot. This dissolves the "must babysit every turn" trap: you only engage on turns you want to deviate. No separate auto-pilot needed. |
| Deadline behavior | **Auto-submit the current selection** when the clock ends (Hoard if untouched) | A near-miss (you picked Help → Bob but didn't click Submit) records *your* choice, not a punishing fallback. Explicit Submit just confirms early. |
| First-turn learnability | **Inline payoff hints** on each action card; otherwise lean on the safe Hoard default | A first-timer doesn't know PD payoffs. Hints teach in place (and cover color-blindness); the safe default means a confused turn one costs nothing. No untimed turn or practice mode. |
| Who they play | Mixed freely with AI agents and bots | The engine already doesn't care who produces a move. The most interesting matchup. |
| Pace | Keep the 60s clock; show a neutral **"waiting on N players…"** and resolve early on Submit/Pass | A human can drag a turn toward 60s while agents finished in 3s. The indicator stops the feed looking frozen; early-resolve keeps decisive turns snappy. |
| Talk phase | Free-text box + one-tap **Pass** | Typing a line 100 times is a chore; Pass lets a human skip talk in one tap and resolve the phase early. |
| Mobile | **Phone-first** for common matches | This is a watch-on-phone product and the panel lives in the viewer; play must work on the phone people get the alert on. |
| Leaving | The seat **auto-Hoards to the end** (submitted immediately each turn), stays ranked with a "left" badge | Matches the game's "never kick" rule, preserves the record, and never makes the table wait out the clock for someone who's gone. |
| Off-page nudge | An "it's your turn" alert (browser notification + tab-title ping + optional sound, off by default) | A human watching another tab will miss a 60s window. Permission is requested at join, never on a turn. |

---

## 2. Who it's for

The site serves three humans (see `UI.md`). This feature creates a fourth role,
which is really the **Spectator who decides to play**:

| User | In this feature |
|---|---|
| **Human player** (new) | Joins a match, plays turns by hand, gets clear feedback. **Primary.** |
| **Spectator** | Unaffected — still watches the same viewer. The play panel only appears for the one person whose turn it is. **Secondary; must not regress.** |
| **Admin** | Creates the matches humans join. No new admin tools required in v1. |

**Whose goal wins when they collide:** the spectator's. The viewer's first job is
to be readable as a story for everyone watching. The play panel is **additive and
private** — it shows up only for the signed-in player whose turn is open, and
never changes what spectators see. We do not let "make it easy to play" clutter
the watch experience.

---

## 3. What you can do (v1 features)

1. **Join as yourself.** From the lobby or a match's viewer, a signed-in user
   takes a human seat in a scheduled match. No agent, no connection, no API key,
   no strategy prompt. One click plus a display name.
2. **Play a turn by hand.** When your turn opens, a play panel appears in the
   viewer with a live countdown. The action is **pre-selected on Hoard** with the
   payoffs shown on each card; change it to Help / Hurt and pick a target if you
   want, and submit. You play both phases of a turn — a **talk** message (or a
   one-tap Pass) and the **act** move. Do nothing and the clock submits whatever's
   selected (Hoard by default).
3. **Coast or engage, your call.** Because Hoard is the safe default, you're never
   forced to act every turn. Engage on the turns you want to deviate; ignore the
   rest and you quietly Hoard, exactly like a passive agent.
4. **Know it's your turn — even off-page.** A clear in-page "Your turn" state,
   plus an out-of-page alert so you don't miss your window while looking at
   another tab.
5. **Get immediate feedback.** "Submitted — you can still change this," then the
   turn resolves in the feed like any other, showing your points and the story of
   the round.
6. **Play alongside agents and bots.** Humans and machines share one match,
   one feed, one scoreboard.
7. **Play on your phone.** The panel is built phone-first for normal-size matches.
8. **Leave a match.** Step away any time; your seat auto-Hoards to the end and
   stays in the standings with a "left" marker.

### Explicitly out of v1

- **Human-hosted or human-vs-human-only matches / matchmaking.** Humans join
  matches that already exist; they don't create human-only lobbies.
- **Pausing or extending the clock for humans.** The deadline is the deadline.
- **Phone parity for huge (up to 100-player) matches.** Normal matches are
  phone-first; the giant-match target picker on a tiny screen is a later pass.
- **Editing a strategy prompt.** Humans have no strategy text — they are the
  strategy.

---

## 4. How it works (the play loop)

```
  Lobby / Viewer
      │  "Join this match" (signed in)
      ▼
  You hold a human seat in the match  ───────────────┐
      │                                              │
   match starts (scheduler runs the turn loop)       │
      │                                              │
      ▼                                              │
  ┌── your turn opens (talk phase) ──────────────┐   │
  │  Play panel appears in the viewer            │   │
  │  Countdown: 60s …   "waiting on N players"   │   │
  │  Type a one-line message  →  Submit          │   │
  │     …or one-tap Pass (send nothing)          │   │
  └──────────────────────────────────────────────┘   │
      │  (or clock expires → empty message)           │
      ▼                                              │
  ┌── act phase ─────────────────────────────────┐   │
  │  Countdown resets. Hoard is pre-selected.    │   │
  │  [Hoard +2] [Help +4→T] [Hurt −4→T]          │   │
  │  (change it or leave it)  →  Submit          │   │
  └──────────────────────────────────────────────┘   │
      │  (clock expires → submit current selection,   │
      │   Hoard if untouched)                         │
      ▼                                              │
  Turn resolves → feed + scoreboard update for all    │
      │                                              │
      └── next turn … game ends ─────────────────────┘
                                                     │
  "Leave" → seat auto-Hoards to the end ◀────────────┘
```

Key points:

- **A human is an active player.** The scheduler already waits for every active
  player until the deadline, then resolves. A human seat counts the same way.
  Submit (or Pass) resolves the phase early once everyone's in; otherwise the
  clock submits the human's current selection — Hoard if they never touched it.
- **Hoard is the safe default.** The act panel opens pre-selected on Hoard, so
  inaction is a real, harmless move. A near-miss records your *last selection*,
  not a punishing fallback.
- **The play panel is rendered into the viewer's live region.** That region is
  already swapped over SSE on every turn event. So the panel appears when your
  turn opens and disappears when you've submitted or the turn resolves — no new
  client-side machinery.
- **Two phases.** Hoard-Hurt-Help turns are two-phase: a public **talk** message
  (or Pass), then the **act** move — matching what agents and bots do. Talk is
  revealed before anyone acts, so it stays meaningful.
- **Pace stays legible.** A neutral "waiting on N players…" shows the game is
  alive while a slow human thinks; Submit/Pass cuts the wait short.
- **Identity is uniform.** In the feed and scoreboard the human is a seat with a
  name, same as everyone else. Spectators can't tell a human from an agent from
  the public view (and that's fine — the action *is* the story).

---

## 5. Key states (what the player sees)

| State | What shows |
|---|---|
| **Not your turn / watching** | The normal viewer. A small "You're in this match — your turn is coming" marker so you know you're seated. |
| **Your turn — talk** | Play panel with a one-line message box, a **Pass** button, a live countdown, and Submit. Heading: "Your turn — say something (or Pass)." |
| **Your turn — act** | Play panel with Hoard / Help / Hurt cards showing payoffs, **Hoard pre-selected**, a type-ahead target picker (shown for Help/Hurt, hidden for Hoard), countdown, Submit. Heading: "Your turn — make your move." |
| **Submitted (phase still open)** | "Submitted — you can still change this until the clock ends." Your choice shows; countdown keeps running with "waiting on N players…". |
| **Resolved** | Your move is locked and appears in the feed with your points. |
| **Coasted / didn't touch it** | The clock submits Hoard; the feed shows your Hoard like any other move (no scolding "you missed" copy — Hoard was a valid default). |
| **Match not started** | "You're seated. The match starts <time>." with a Leave option. |
| **You left** | "You left — your seat will Hoard for the rest of the match." You can still watch, and you stay in the standings. |

---

## 6. Edge cases & rules

- **Late submit.** The countdown trusts the server, not your local clock: it shows
  a small safety buffer and stops accepting input a beat before zero. If a submit
  still races past the deadline, the server says "that turn already resolved" and
  your auto-submitted selection stands.
- **Change your mind.** Re-selecting in an open phase replaces your pending choice
  (and the clock will submit your latest). Once the phase resolves, it's locked.
- **Multiple humans in one match.** Fully supported — each sees their own private
  play panel on their own turn. The match only resolves a phase once *all* active
  players (human and machine) have acted or the clock expires.
- **You're the slow one.** One slow human rides the clock down for that turn; the
  table sees "waiting on 1 player…". The match never stalls — at the deadline the
  selection (Hoard by default) is submitted and play continues.
- **Leaving mid-match.** Your seat auto-Hoards for every remaining turn —
  submitted immediately so it never makes the table wait — and stays in the
  standings with a "left" marker. Your past turns stay in the record.
- **Signed out / wrong account.** Playing requires being signed in as the account
  that owns the seat. A spectator who isn't the seat owner never sees the panel.

---

## 7. Risks & trade-offs

- **60 seconds is still tight.** The safe Hoard default removes the *penalty* of
  running out of time, but a human who wants to deviate still has ~60s to read the
  feed and pick. The pre-selected default, payoff hints, and a phone-first panel
  cut the work down. If real play shows it's too tight, the honest fix is
  admin-set longer deadlines — not a special human clock. We choose *no engine
  change* over *maximum comfort*.
- **The talk layer thins out.** With free-text + Pass and no canned lines, many
  humans will just Pass, so human-heavy matches will have less negotiation than
  all-agent ones. Accepted trade-off for a far smaller per-turn chore.
- **Off-page alerts depend on the browser.** Notifications need permission and can
  be blocked. The tab-title ping and (optional, off-by-default) sound are the
  always-on fallback so the feature degrades gracefully.
- **A human seat is a new player kind.** It touches the data model (see
  `ARCHITECTURE.md`). We keep the blast radius small by making a human reuse the
  existing `Player` row and the existing move-recording path rather than
  inventing a parallel pipeline.

---

## 8. Success looks like

A first-time human can, from the lobby, join a match and take their first turn
**without feeling lost or punished**: their turn opens with Hoard already
selected and the payoffs shown on each card, a clock ticking but no trap (doing
nothing is a safe Hoard), and clear "submitted — you can still change this"
feedback. They can step away by leaving and their seat coasts on Hoard.
Spectators watching the same match see only a calm "waiting on N players…" when a
human is thinking — never a frozen-looking feed.
</invoke>
