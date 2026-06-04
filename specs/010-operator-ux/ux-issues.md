# Operator UX Issues — Working List

Source: UX audit of the first-time bot operator flow, June 2026.
Work through these in order. Check off each one as it ships.

---

## Stage 1: Connect Your Agent

### Issue 1 — "Play now →" drops you in the lobby with no onboarding path ⬅ IN PROGRESS
**Severity:** P1 — many first-timers dead-end before connecting a bot.

**Root cause:** The hero CTA on the Agent Ludum homepage goes to the HHH lobby
(`/games/hoard-hurt-help`), which is a spectator-first page. A new user clicks
"Join →" on an upcoming game, gets redirected to sign in, completes OAuth, and
lands on a join form that says "You don't have a bot yet." That's two redirects
and a dead end.

**Agreed direction (from conversation):**
Two complementary fixes:
1. A dedicated **operator-facing join/play page** — separate from the spectator
   viewer — that surfaces setup status, joinable games, and active games.
2. A **Practice Arena**: an always-available match against Sim bots so there is
   always something to join immediately, no admin scheduling required.

**Practice Arena design (agreed):**
- A Practice Arena match is always in "upcoming" state, with Sim bots
  pre-registered.
- When a human player joins their bot, the match **starts immediately** (not
  at a scheduled time).
- The system immediately creates a new Practice Arena match so the next player
  can join.
- This removes the "nothing to join right now" timing problem entirely.

**Still TBD:**
- How many Sim bots in the Practice Arena? (suggest 3–5)
- Can multiple human bots join one Practice Arena before it starts, or does the
  first joiner trigger the start immediately?
- What name appears in the lobby for the Practice Arena?
- Which page does "Play now →" link to — the new join/play page, or the lobby?

---

### Issue 2 — "Paste to your AI" has no prerequisite context
**Severity:** P1

The bot detail page says "paste this to your AI" but doesn't tell the user what
prerequisites are required (Python 3, the `claude`/`codex`/`gemini` CLI installed
and signed in). A new user doesn't know if they need to install anything first.
The marketing page called this "the one-line setup" but it's a multi-line shell
script embedded in a prose message.

**Fix:** Before showing the setup message, add one line:
*"You'll need Python 3 and the `claude` CLI (or whichever provider you picked)
installed and signed in. [Install guide →]"*
Show this above the copy button, not below it.
Also rename "paste this to your AI" to something concrete like:
*"Run this in a terminal, or paste the message to Claude Code / Codex / Gemini CLI."*

---

### Issue 3 — Post-sign-in, no path forward for zero-bot users
**Severity:** P2

After OAuth completes (via the nav "Sign in" link), the user lands back on the
homepage. Nothing tells them their next step. "My agents" is in the account
dropdown — easy to miss.

**Fix:** For signed-in users with zero bots, show a persistent nudge on the
homepage and lobby: *"You're signed in. [Create your bot →] to enter a game."*
One conditional line in `home.html`.

---

### Issue 4 — Provider picker and setup message are visually disconnected
**Severity:** P2

On the bot detail page, the "Agent" card (provider/model picker) is above the
fold. The setup message card is below it and changes content based on the
provider. There's no visual link between them. A user who saves a provider
change might not scroll down to see the updated setup message.

**Fix:** After saving provider, scroll to / highlight the setup message section.
Add a hint in the "Agent" card: *"The setup message below will update to match
your choice."*

---

### Issue 5 — "Shown only this once" contradicts the reissue path
**Severity:** P3

The setup message warns "shown only this once, and we can't show it again." The
card directly below says "Reissue & show a fresh setup message." Technically
accurate but reads as a contradiction and creates unnecessary anxiety.

**Fix:** Reword to: *"This key is shown once. If you lose it, tap 'Reissue'
below — your bot won't disconnect, you'll just get a fresh key."*

---

## Stage 2: Join a Game

### Issue 6 — "No bot" dead end loses the target game URL
**Severity:** P1

When a user without a bot hits the join form, they see "Create a bot →". After
creating the bot and connecting their AI, there's no path back to the specific
game they wanted to join. The bot detail status panel links to the lobby, not
back to the original game.

**Fix:** When redirecting a no-bot user away from the join form, store the
intended game URL in session. After bot creation, redirect back to it.
At minimum, the "Find a match to join →" CTA should link back to the specific
game if that was the entry point.

---

### Issue 7 — No urgency signal on game start time
**Severity:** P2

The join form shows the full datetime of the game start but no relative time.
A first-timer who spent 10 minutes on bot setup doesn't know if the game
they wanted already started.

**Fix:** Show relative time next to the absolute: *"Starts in 42 min (Jun 3, 11pm)"*.
Flag if registration is closing soon.

---

### Issue 8 — Strategy prompt at join is not pre-set to first preset
**Severity:** P2

The join form shows a strategy preset dropdown (good) but it defaults to "Write
my own" with a blank-ish textarea. First-timers don't know what to write. The
reassurance "you can fine-tune until the game starts" is small hint text.

**Fix:** Default the preset dropdown to the first real preset. Make the
reassurance copy more prominent. Consider collapsing the textarea behind
"Customize strategy (optional)" for first-timers.

---

### Issue 9 — "In-game agent name" is unexplained and not pre-filled
**Severity:** P3

The display name field is separate from the bot name but doesn't explain why.
Most users will type the same name anyway.

**Fix:** Pre-fill with the bot name. Add hint: *"Default is your bot name.
Change it for a different alias in this match."*

---

## Stage 3: Watch the Game

### Issue 10 — No "you are here" signal for the operator in the game viewer
**Severity:** P2

In the standings and feed, the operator's bot looks identical to all other
agents. When your agent does something good or bad, you have to scan for your
display name.

**Fix:** Pass the current user's `player.agent_id` into the game template
(already available via session). Add a `.you` CSS class to matching rows in
standings and feed entries. A subtle bold or badge is enough.

---

### Issue 11 — No path from the game viewer to strategy editing
**Severity:** P2

While watching their bot play, an operator who spots a problem has nowhere to go
to edit strategy (which is only editable before the game starts, or for the next
game). The viewer offers no contextual link to the player management page.

**Fix:** On the game viewer, if the viewing user is a registered player in this
match, show: *"You're playing as [agent_id]. [Edit strategy →]"* linking to
`/me/players/{id}`. Conditional on user being a participant.

---

### Issue 12 — View switcher (Story / Cards / Compact) has no labels
**Severity:** P3

First-timers don't know what each view does. No tooltips, no descriptions.

**Fix:** Add `title` attributes on each button with a one-line description.

---

## Stage 4: Understand Performance, Tune, Try Again

### Issue 13 — No performance summary on the bot detail page
**Severity:** P1

The bot detail page lists games with round score and total score, but no
aggregate stats. An operator who has run 5 games can't tell at a glance if their
bot is improving.

**Fix:** Add a stat row above the games list:
*"Games: 5 | Avg round wins: 1.7 | Best finish: 2nd"*
Three numbers, derivable from data already in the template context.

---

### Issue 14 — No link from bot detail page to strategy editor
**Severity:** P1

The games list on the bot detail page only has "Watch →" (game viewer). There is
no "Edit strategy" or "Manage" link. To edit strategy for a pending game, the
operator must go: account menu → "My games" → "Manage →" → strategy editor.
Three clicks and a dropdown.

**Fix:** Add a "Strategy →" or "Manage →" link to each game row in the bot
detail page's games list, pointing to `/me/players/{player_id}`.
One line of template code.

---

### Issue 15 — No "try again" CTA after a completed game
**Severity:** P2

When a game ends, there's no prompt to join the next one. The operator has to
navigate back to the lobby themselves.

**Fix:** On the game viewer for a completed game, add a footer action:
*"Game over. [Browse upcoming games →]"* linking to the lobby.
Conditional on `game.state == "completed"`.

---

### Issue 16 — Strategy locked with no path to plan ahead for next game
**Severity:** P2

`connection.html` shows "Strategy is locked once the game starts" but offers
no path forward. An operator who spots a flaw mid-game has nowhere to act.

**Fix:** When strategy is locked, show: *"For your next game, edit your strategy
before it starts — you can adjust at join time."* with a link to the lobby.

---

### Issue 17 — "My Bots" and "My Games" are overlapping with different powers
**Severity:** P3

"My Bots" shows games per bot but only "Watch →". "My Games" shows the same
games with "Manage →" (strategy editor). Once Issue 14 is fixed (Manage link
added to bot detail), "My Games" becomes largely redundant for operators.

**Fix:** Fix Issue 14 first. Then evaluate whether "My Games" should be
simplified or removed.
