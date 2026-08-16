# Board Game Design Patterns — Reference for HHH Game Design

A grounded reference for the `game-design` skill. Every game, designer, and term
is drawn from cited sources — nothing invented. Target game context: 4-player
(or up to ~20), simultaneous-reveal, repeated Prisoner's Dilemma. 7 rounds × 5
turns. Moves: HOARD (+2 self), HELP (+4 to target; mutual pact pays +6 under
today's default rule, up to +8 under the other selectable modes — see
`MutualHelpMode` in `app/games/hoard_hurt_help/rules.py`), HURT (-4 to target,
+0 to attacker). Round won by sole high score; ties split the win.

---

## Problem 1 — No scarcity / stable cooperative equilibrium (everyone ties)

**Design term:** *Positive-sum game.* When everyone can gain at once and nothing
is scarce, players converge on mutual cooperation and outcomes flatten. The fix is
to make the win prize *scarce* — only one player can hold it.

**Published solutions:**
- **Catan** — finite board positions force rivalry over placement even though
  resource income is non-zero-sum.
- **Tigris & Euphrates** (Knizia) — your score is your *lowest* of four
  categories. You can never max everything; the binding constraint stops everyone
  topping out together.
- **Diplomacy** — exactly 34 supply centers, you need 18 to win. One player's
  growth is literally another's loss.

**Lesson for HHH:** The scarce prize already exists (sole round-win). The problem
is that mutual-HELP lets two pairs *share* the ceiling — both pairs bank the same
per-turn pact payout every turn, so they finish a round exactly tied. The fix
is either (a) make the ceiling unreachable by both pairs simultaneously (scarcity
/ decay), or (b) make the solo win so valuable that breaking from the cooperating
pack is worth the risk.

---

## Problem 2 — Dead aggression action (HURT never used)

**Design terms:** *Take-that* (an action intended to harm another player's plans),
*feel-bad* (mechanically valid but emotionally punishing), *strictly dominated*
(there is always a better alternative, so rational players skip it entirely).

- **Take-that** — "intended to be harmful to another player's plans but does not
  directly eliminate them." Source: BoardGameGeek mechanic.
- **Feel-bad** — Rosewater's concept: ante in early Magic was legal but produced
  a bad experience every time. A dead button is the worst form.
- **Strictly dominated** — game theory: a strategy that is *never* the rational
  choice, regardless of what others do. If HURT pays the attacker +0 and HOARD
  pays +2, HURT is dominated and rational bots skip it.

**Published solutions:**
- **Munchkin** — take-that cards are cheap/free and deny a rival a win; the
  payoff is *indirect* (blocking their win = advancing yours).
- **Cosmic Encounter** — attacking is the path to colonies (your win condition);
  aggression *is* the scoring engine.
- **Magic: The Gathering** — Rosewater's rule: a card that is never correct is a
  design failure. An action must have a *discoverable good use*.

**Lesson for HHH:** HURT must *advance the attacker's rank*, not just hurt the
target. The Munchkin model: knocking the leader down −4 hands you the round — but
only if it's worth giving up +2 HOARD. The ratio matters: HURT needs to break
even on opportunity cost to be live.

---

## Problem 3 — Runaway leader / outcome decided early

**Design term:** *Runaway leader*, caused by a *positive feedback loop* — more
lead → more power → more lead. "A runaway leader is a player who establishes a
lead, and by virtue of having that lead, is able to continually press the
advantage to make the lead insurmountable." (Oakleaf Games)

**Counter-argument to always fix it:** Designer Matt Pioch warns that artificial
catch-up means "the game is producing the scoring results rather than the player
making decisions." Don't add rubber-banding reflexively — check if it's actually
a problem first.

**Published solutions:**
- **Power Grid** — explicit *negative feedback*: the leader buys resources last
  and at worse prices. Being ahead is taxed.
- **Cosmic Encounter** — *player-driven bash-the-leader*: the table can gang up.
  No forced mechanic, just a structural invitation.
- **Mario Kart** — *rubber-banding* (power-ups favor last place). Controversial;
  many designers consider it a last resort.

**Lesson for HHH:** The per-round reset already supplies one strong brake — a big
point lead in round 1 grants no advantage in round 2. The main risk is a *match*
runaway: if Sonnet is at 4.0 round-wins entering the final round, that feels
uncatchable even though the math allows it. The answer is structural (making late
rounds high-leverage) not rubber-banding.

---

## Problem 4 — Anticlimactic endgame (everyone hoards at the end)

**Design term:** *Anticlimax.* "An entrepreneurial player could simply fulfill the
mechanical condition as soon as was convenient and end the game with a thud of an
anticlimax." (Games Precipice)

Fix: "Games where many points score at the very end… never know who will be the
final winner… keep players involved much more." (Knizia, via Critical-Hits)

**Published solutions:**
- **Tigris & Euphrates** — scoring off the minimum of four categories means a
  single late move can swing your counted score; no coasting.
- **Diplomacy** — the win threshold is a sharp majority reached by a final push,
  not a slow accumulation.
- **Pandemic Legacy** — escalating consequences in the final act; the end is
  harder and more dramatic, not a formality.

**Lesson for HHH:** The last turn(s) of a round should be *highest-leverage*, not
lowest. If hoarding passively is the dominant final move (because the outcome is
already determined), either the outcome is being determined too early (fix the
mid-game) or the endgame payoffs need to reward bold play (escalating multipliers,
decisive tiebreak).

---

## Problem 5 — Kingmaking (trailing player decides who wins)

**Design term:** *Kingmaking.* Three conditions: the player's move decides the
winner among others; they can't improve their own standing; they know it. Result:
"not significantly different from playing for 3 hours and rolling a die to decide
the winner." (BGDF)

**Kingmaking vs. bash-the-leader tension:** These are in conflict. You want
trailing players to gang up on the leader (drama) without one player having
unilateral deciding power (kingmaking). The line: kingmaking is worst when the
player *knows* they're the kingmaker and has no self-interest left.

**Published solutions:**
- **Hidden/semi-hidden scoring** — if players don't know exact standings, no one
  knows they're kingmaking.
- **Per-player stake kept live** — even a trailing player still has a reason to
  optimize *their own* position (not just pick a king).
- **Simultaneous reveal** — removes the "I know I'm the deciding vote" moment by
  forcing everyone to commit blind.

**Lesson for HHH:** Simultaneous reveal is already your best anti-kingmaking
lever — a player can't *knowingly* give the win to someone in the act phase
because everyone commits at the same time. The dangerous case is a player who is
mathematically out of the *match* (not just a round) — they have no self-interest
left and become a pure kingmaker for later rounds. Keep every player's match
standing in play as long as possible.

---

## Problem 6 — Frozen alliances (partnerships never break)

**Design term:** *Frozen alliances* vs. *shifting alliances.* "You cannot win
without allies, and you cannot win without eventually betraying them."
(Diplomacy). In Survivor, empirically only ~26% of coalitions survive a single
vote — instability is the entertainment.

**Published solutions:**
- **Diplomacy** — *simultaneous secret orders*: you commit before anyone reveals,
  so every turn is a blind read. Betrayal is structurally invited every single
  turn.
- **Cosmic Encounter** — *fresh per-encounter alliances*: yesterday's ally is
  invited or not to today's fight. No pact freezes.
- **Survivor** — *forced periodic vote*: a cadence that schedules a betrayal
  decision; idols inject sudden information shocks.
- **The Resistance / Secret Hitler** — *fragmented hidden information*: trust must
  be re-evaluated constantly; no alliance can verify itself into permanence.

**Lesson for HHH:** You already own Diplomacy's core lever (TALK then simultaneous
ACT). The risk: if mutual-HELP (+6/+6 under today's default rule, historically
+8/+8) is too dominant, alliances freeze because breaking costs too much. The fix
is raising the expected value of betrayal (closer payoffs between cooperation and
solo play), not adding new betrayal mechanics.

---

## Problem 7 — Player count scaling (4 vs. 15–20 players)

**Design term:** *Player count scaling.* Games that work at one count often break
at another — usually for one of three reasons: (a) *interaction density* changes
(more players = fewer meaningful interactions per person per turn); (b) *alliance
math* changes (with N players you can't pair everyone into mutual-help pairs once
N > 2×pairs, which introduces natural scarcity); (c) *kingmaking probability*
rises (more spectators per deciding vote).

**Published solutions:**
- **7 Wonders** — scales 2–7 via a *simultaneous draft*; no per-player wait time.
  Works at high count because everyone acts at once.
- **The Resistance / Secret Hitler** — scales 5–10 by adjusting the ratio of
  roles (Spies/Liberals); the core tension scales with the uncertainty, not the
  player count.
- **Cosmic Encounter** — works 3–5 well; explicitly recommends against very high
  counts because per-encounter negotiation takes too long.
- **Diplomacy** — exactly 7 players by design; each player controls a *different
  nation* with different starting positions, so the count IS the game.

**For HHH specifically:**
- At **4 players**: exactly 2 mutual-HELP pairs fit. Both pairs reach the same
  per-round ceiling every round. Result: ceiling ties. Scarcity of the win is the
  whole problem.
- At **8–10 players**: 4–5 pairs could form, but targeting is single-player, so
  HELP pairs still dominate AND there are more targets for HURT (more interesting).
  HURT becomes relevant because knocking down one co-leader might let you solo.
- At **15–20 players**: you can't pair everyone. Bots compete for HELP partners;
  solo HOARD becomes relatively more valuable; HURT on a runaway leader becomes
  worth it because the field is wide. *Natural scarcity emerges from player count.*

**Lesson:** A significant drama problem at 4 players can *solve itself* at 10–15,
because the pair-to-player ratio forces competition. Always test proposed rule
fixes at multiple player counts — a fix that works at 4 may be overkill at 15.

---

## Vocabulary — terms a designer/reviewer uses

| Term | Definition | Source |
|---|---|---|
| **Interesting decisions** | "A game is a series of interesting decisions" — a choice must have meaningful, non-dominated alternatives. | Sid Meier, GDC 2012 |
| **Tension curve / arc** | The rise and fall of dramatic tension over a session, building to a climax. | Fullerton, *Game Design Workshop* |
| **Player interaction** | How much players actively engage *each other* vs. solo-optimize. | Shut Up & Sit Down |
| **Dominant strategy** | A single approach that is *always* the rational choice; kills interesting decisions. | Game theory |
| **Swinginess** | A result that swings wildly based on a single event; can create excitement or feel unfair. | Design discourse |
| **Runaway leader** | A player whose lead compounds until it's insurmountable. | Oakleaf Games |
| **Kingmaking** | A player who can't win decides who does. | BGDF |
| **Rubber-banding / catch-up** | Mechanisms helping the trailer; the heavy form (Mario Kart) can override skill. | Oakleaf / Thoughtful Gamer |
| **Push-your-luck** | Settle for existing gains or risk them for more; player chooses when to stop. | BGG |
| **Downtime** | Time a player waits with nothing to do. | Design writing |
| **Feel-bad** | Mechanically valid but emotionally punishing. | Mark Rosewater, Making Magic |
| **Analysis paralysis (AP)** | Downtime caused by overwhelming choices. | BGDF |
| **Take-that** | An action intended to harm another player's plans without eliminating them. | BGG |
| **MDA framework** | Mechanics → Dynamics → Aesthetics. Designers build M→D→A; players experience A→D→M. | Hunicke, LeBlanc, Zubek |
| **Strictly dominated** | A strategy that is never rational because another always beats it. | Game theory |
| **Positive feedback loop** | Success breeds more success; leads to runaway leader. | Game theory |
| **Negative feedback loop** | Being ahead gets harder; produces catch-up / rubber-banding. | Game theory |
| **Nash equilibrium** | A state where no player can improve by changing strategy unilaterally. Mutual-HELP pair in HHH is one. | Game theory |
| **Pareto efficiency** | An outcome where no one can improve without making someone worse. The 80/80/80/80 tie is Pareto-efficient but undramatic. | Economics |

---

## Closest neighbors — alliance + betrayal + negotiation games

| Game | Core drama lever | Warning to learn from |
|---|---|---|
| **Diplomacy** | Simultaneous secret orders — every turn is a blind read of whether your ally keeps their word. "Cannot win without allies, and cannot win without betraying them." | Notorious for ruined friendships; high stakes = high engagement but also high drop-out. |
| **Cosmic Encounter** | No elimination + fresh per-encounter alliances; beaten-down players still bargain. | Works at 3–5; falls apart at high counts due to negotiation time. |
| **The Resistance / Secret Hitler** | Fragmented hidden information forces constant trust re-evaluation; no alliance can verify itself. | Role-assignment games are a different genre — but the information-fragmentation lever is universal. |
| **Survivor** | Forced vote cadence schedules betrayal; ~26% of coalitions survive a vote. Idols = information shocks that shatter stable blocs. | Reality TV structure; the "tribal council" cadence is the single most powerful forced-drama mechanism in the genre. |
| **Pandemic Legacy** | Escalating stakes as the season progresses; the final act is the hardest, not a coast. | Co-op, not competitive — but the "raise the stakes over time" arc is the template for dramatic endgames. |

---

## Sources

- Sid Meier, "Interesting Decisions," GDC 2012: https://www.gamedeveloper.com/design/gdc-2012-sid-meier-on-how-to-see-games-as-sets-of-interesting-decisions
- Hunicke, LeBlanc, Zubek, "MDA": https://users.cs.northwestern.edu/~hunicke/MDA.pdf
- Oakleaf Games, "Runaway Leader, Rubber Banding": https://oakleafgames.wordpress.com/2014/02/13/game-theory-runaway-leader-rubber-banding-and-feedback/
- Matt Pioch, "The Runaway Leader Problem": https://insideupgames.com/board-game-reviews/the-runaway-leader-problem/
- BGDF, "Kingmaking": https://www.bgdf.com/forum/archive/archive-game-creation/topics-game-design/tigd-kingmaking-common-problem-2
- BGDF, "Analysis Paralysis": https://www.bgdf.com/forum/archive/archive-game-creation/topics-game-design/tigd-analysis-paralysis-common-problem-1
- Mark Rosewater, "When Cards Go Bad Revisited": https://magic.wizards.com/en/news/making-magic/when-cards-go-bad-revisited-2012-10-22
- BGG, "Take That" mechanic: https://boardgamegeek.com/boardgamemechanic/2686/take-that
- BGG, "Push Your Luck": https://boardgamegeek.com/boardgamemechanic/2661/push-your-luck
- Games Precipice, "Late Game Structures": https://www.gamesprecipice.com/endings/
- Reiner Knizia, design philosophy: https://critical-hits.com/blog/2008/07/03/reiner-knizia-creation-of-a-successful-game/
- Diplomacy (Wikipedia): https://en.wikipedia.org/wiki/Diplomacy_(game)
- Survivor alliance stability: https://www.fsb.miamioh.edu/lij14/Thesis_BraggJulia.pdf
- Shut Up & Sit Down (player interaction): https://www.shutupandsitdown.com/category/reviews/
