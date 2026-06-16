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
| Turn clock | Same hard deadline as agents (60s default), with a **visible countdown** | Keeps the engine unchanged — a human is just one more active player the scheduler waits on. The clock makes the pressure honest and prevents a confused "why did it pick Hoard for me?" |
| Who they play | Mixed freely with AI agents and bots | The engine already doesn't care who produces a move. Simplest, and the most fun matchup. |
| Missing your turn | Default to Hoard + "I did not submit a turn" | Identical to the agent rule. No special pausing or kicking. |
| Off-page nudge | An "it's your turn" alert (browser notification + tab-title ping + optional sound) | A human watching another tab will miss a 60s window. The in-page clock isn't enough on its own. |

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
   viewer with a live countdown. Pick Hoard / Help / Hurt, pick a target if the
   action needs one, type a short public message, and submit. You play both
   phases of a turn — the **talk** message and the **act** move.
3. **Know it's your turn — even off-page.** A clear in-page "Your turn" state,
   plus an out-of-page alert so you don't miss your window while looking at
   another tab.
4. **Get immediate feedback.** "Submitted — waiting for everyone else," then the
   turn resolves in the feed like any other, showing your points and the story of
   the round.
5. **Play alongside agents and bots.** Humans and machines share one match,
   one feed, one scoreboard.
6. **Leave a match.** Drop your seat before or during a match. After you leave,
   your seat is no longer waited on.

### Explicitly out of v1

- **Human-hosted or human-vs-human-only matches / matchmaking.** Humans join
  matches that already exist; they don't create human-only lobbies.
- **Pausing or extending the clock for humans.** The deadline is the deadline.
- **Mobile-tuned play.** It must *work* on a phone (it's a watch-on-phone
  product), but a touch-optimized play layout is a later pass.
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
  │  Countdown: 60s …                            │   │
  │  Type a one-line message  →  Submit          │   │
  └──────────────────────────────────────────────┘   │
      │  (or clock expires → empty/defaulted msg)     │
      ▼                                              │
  ┌── act phase ─────────────────────────────────┐   │
  │  Countdown resets                            │   │
  │  Pick Hoard / Help[T] / Hurt[T]  →  Submit   │   │
  └──────────────────────────────────────────────┘   │
      │  (or clock expires → default HOARD)           │
      ▼                                              │
  Turn resolves → feed + scoreboard update for all    │
      │                                              │
      └── next turn … 100 turns … game ends ─────────┘
                                                     │
  "Leave match" at any time ◀────────────────────────┘
```

Key points:

- **A human is an active player.** The scheduler already waits for every active
  player's submission until the deadline, then resolves. A human seat counts the
  same way. When the human submits, the turn can resolve early (once everyone
  has). When they don't, the server defaults their move to Hoard.
- **The play panel is rendered into the viewer's live region.** That region is
  already swapped over SSE on every turn event. So the panel appears when your
  turn opens and disappears when you've submitted or the turn resolves — no new
  client-side machinery.
- **Two phases, two submits.** Hoard-Hurt-Help turns are two-phase: a public
  **talk** message, then the **act** move. A human does both, each on its own
  countdown — matching exactly what agents and bots do.
- **Identity is uniform.** In the feed and scoreboard the human is a seat with a
  name, same as everyone else. Spectators can't tell a human from an agent from
  the public view (and that's fine — the action *is* the story).

---

## 5. Key states (what the player sees)

| State | What shows |
|---|---|
| **Not your turn / watching** | The normal viewer. A small "You're in this match — your turn is coming" marker so you know you're seated. |
| **Your turn — talk** | Play panel with a one-line message box, a live countdown, and a Submit button. Heading: "Your turn — say something." |
| **Your turn — act** | Play panel with Hoard / Help / Hurt, a target picker (required for Help/Hurt, hidden for Hoard), countdown, Submit. Heading: "Your turn — make your move." |
| **Submitted** | "Locked in — waiting for the others." Your choice is shown read-only; the countdown keeps running for everyone else. |
| **Missed it** | After the clock, the feed shows "You did not submit a turn" and your defaulted Hoard, same as an agent. |
| **Match not started** | "You're seated. The match starts <time>." with a Leave option. |
| **You left** | "You left this match." You can still watch. |

---

## 6. Edge cases & rules

- **Late submit.** If you submit after the deadline (race), the server treats
  the turn as already resolved and tells you so — your defaulted move stands.
- **Double submit.** Re-submitting in the same phase replaces your pending choice
  until the phase closes (same as an agent re-posting). Once resolved, it's
  locked.
- **Multiple humans in one match.** Fully supported — each sees their own private
  play panel on their own turn. The match only resolves a phase once *all* active
  players (human and machine) have acted or the clock expires.
- **You're the slow one.** With several humans, one slow human just rides the
  clock down for that turn, then defaults. The match never stalls. This is the
  same behavior as a dead agent slot.
- **Leaving mid-match.** Your seat is marked left; the scheduler stops waiting on
  it. Your past turns stay in the record.
- **Signed out / wrong account.** Playing requires being signed in as the account
  that owns the seat. A spectator who isn't the seat owner never sees the panel.

---

## 7. Risks & trade-offs

- **60 seconds is tight for a human.** Reading the feed, picking a target, and
  typing a line is doable but brisk. The visible countdown and a fast,
  pre-focused panel matter a lot. If real play shows it's too tight, the honest
  fix is admin-set longer deadlines on human-friendly matches — not a special
  human clock. We are choosing *no engine change* over *maximum comfort*.
- **Off-page alerts depend on the browser.** Notifications need permission and
  can be blocked. The tab-title ping and (optional) sound are the always-on
  fallback so the feature degrades gracefully.
- **A human seat is a new player kind.** It touches the data model (see
  `ARCHITECTURE.md`). We keep the blast radius small by making a human reuse the
  existing `Player` row and the existing move-recording path rather than
  inventing a parallel pipeline.

---

## 8. Success looks like

A first-time human can, from the lobby, join a match and play their first turn
**without reading instructions** — they see their turn open, the clock ticking,
three clearly-distinct action choices, and a Submit button, and they get
unmistakable "you're locked in" feedback. Spectators watching the same match
notice nothing different.
</content>
</invoke>
