---
name: game-design
description: >-
  Design and improve how the Hoard Hurt Help game *plays* — the rules, the three
  actions (Hoard / Help / Hurt) and their payoffs, scoring, round/turn/endgame
  structure, player count scaling (4 vs 15–20), and the game-theory dynamics
  that decide whether a match is tense or a foregone conclusion. Grounded in how
  real published board games (Diplomacy, Cosmic Encounter, Tigris & Euphrates,
  Survivor, etc.) solve the same problems. Use this whenever the work is about
  gameplay mechanics: analyzing a played game for design problems, making a game
  "more dramatic" or "more fun to watch", a balance issue ("HURT is never used",
  "the games keep tying", "one strategy always wins"), whether comebacks are
  possible, how the game scales from 4 to 15–20 players, bad game states
  (everyone ties, no one can win, outcome obviously decided), rebalancing payoffs
  or structure (rounds, turns, player count), tie-break or endgame design,
  alliance or equilibrium analysis. Reach for it even when the user never says
  "game design" — if they are shaping the rules or the felt experience of
  *playing*, this skill applies. Does NOT cover art/animation (game-art),
  human-facing screens/flows/copy (ux-design), or MCP/runner plumbing.
---

# Game Design for Hoard Hurt Help

You are the game designer for a spectator game — the person who decides what the
moves are worth, how a round is won, and whether watching a match feels like a
fight or like watching paint dry. Your medium is the payoff table: the point
values in `rules.py`, the resolution math in `resolver.py`, the win/tiebreak
rules, and the round/turn/endgame structure. A few integers in those files decide
whether four LLM agents claw at each other or settle into a cozy tie.

Every diagnosis you make is grounded in two things: **real data from a played
game** (from the analyzer) and **prior art from real board games** (from the
reference file). Never propose a fix without measuring the game first, and never
stop at "here's a possible fix" without checking whether published game design
has already solved the same problem.

## The five dimensions — what to evaluate

A game can fail on any of these independently. Measure all of them.

### 1. Drama / tension curve
Is there a story? Leads should change, alliances should strain, the finale should
matter. The six ingredients of drama: **temptation** (betrayal must sometimes
pay), **vulnerability** (cooperation must be exploitable), **scarcity** (not
everyone can win at once), **shifting fortunes** (leads change), **decisive
endgame** (the final turn is the highest-stakes, not the lowest), and **live
aggression** (the attack move is selfishly worth using).

The anti-pattern: a stable cooperative equilibrium where mutual help is dominant,
everyone ties at the ceiling, and the "attack" button is never worth pressing.

### 2. Balance — is there a dominant strategy?
A dominant strategy is one that is *always* the rational choice regardless of
what others do. It kills interesting decisions (Sid Meier: "a game is a series of
interesting decisions"). In HHH the dominant strategy risk is "find a mutual-HELP
partner, lock in, farm the bonus forever." Ask: is there any individual round in
which a different strategy outperformed the dominant one? If no, the game is
solved and uninteresting.

### 3. Comeback — can a losing player recover?
Two kinds of comeback: **within a round** (can someone 20 pts behind at the
midpoint still win?), and **across rounds** (can a player at 1 round-win still
beat a player at 4?). Per-round resets already provide cross-round recovery — the
main risk is within-round mathematical impossibility once two HELP pairs lock in.
Check: at the midpoint, was the gap closable in the remaining turns? A game where
comebacks are mathematically impossible most of the time is not suspenseful.

### 4. Player count scaling — does it work at 4 vs 15–20?
At **4 players**: exactly 2 mutual-HELP pairs fit. Both can reach the score
ceiling simultaneously → everyone ties. The pairing problem IS the drama problem.

At **8–12 players**: pairing competition emerges — you can't guarantee a partner,
HURT has more strategic targets, and the leaderboard is wider and harder to read.

At **15–20 players**: natural scarcity kicks in. Not everyone can pair up. Bots
compete for HELP partners, solo HOARD becomes more attractive, and HURT on a
runaway leader is often worth the opportunity cost. Many drama problems that
require rule changes at 4 players fix themselves at 15.

Always state which player counts a proposed fix is tested/valid for. A change
that helps at 4 may be overkill or broken at 15.

### 5. Bad states — can the game get stuck or become obviously decided?
- **Ceiling lock**: all players hit the same maximum score; round splits 0.25 each.
- **Mathematical lock**: winner determined by turn 3; remaining turns are a
  formality.
- **Comeback-impossible**: trailer can't close the gap even with perfect play.
- **Match clinched early**: one player's round-win total makes the final standings
  inevitable before the last round.
- **Kingmaking**: a player who can't win themselves chooses who does (especially
  dangerous when one player is mathematically out of the match).

## What you're working with

Read the files you intend to change in full before proposing a number.

### Payoff and rules levers (the fast ones to change)
- **`app/games/hoard_hurt_help/rules.py`** — `HOARD_POINTS`, `HELP_POINTS`,
  `HURT_POINTS`, `MUTUAL_HELP_BONUS`, `MUTUAL_HELP_FLOOR`, and `RULES_TEXT`
  (shipped verbatim to every bot). If you change a number, this text must change
  too — or bots play a different game than the engine scores. Note `RULES_TEXT` is
  **built**, not hand-written: it wraps `GAME_RULES_TEXT`, which comes from
  `_render_game_rules_text(mode=...)`, so a payout that varies by mutual-help mode
  renders per mode rather than being restated in prose.
- **`app/games/hoard_hurt_help/scoring.py`** — the per-turn math. `resolve_turn`
  applies payoffs, the mutual bonus, and the score floor. This is PD-specific and
  lives with the game module, not the platform engine.
- **`app/engine/resolver.py`** — the match-level math that stayed
  game-agnostic: `award_round_winners` handles ties, `finalize_game` handles the
  match tiebreak.
- **`app/games/hoard_hurt_help/game.py`** — `move_effect()` (viewer display;
  keep in step with the resolver), `validate_move`.

### Structural levers (often overlooked, often more powerful)
- **`config_defaults()` in `game.py`** — `total_rounds`, `turns_per_round`,
  `per_turn_deadline_seconds`, `min_players`, `max_players`. Changing rounds or
  turns changes how long an alliance lock persists, how many chances a comeback
  has, and how consequential each turn is. More turns per round → pair-locking
  dominates more. Fewer rounds → each round is higher-stakes. More players →
  natural scarcity.
- **`award_round_winners` in `resolver.py`** — the tiebreak. Currently: ties
  split the win (`1/N` each). Changing to winner-take-all, a sudden-death turn,
  or "fewest-turns-at-the-lead wins" all fundamentally change whether cooperation
  is safe.
- **`app/games/hoard_hurt_help/strategy.py`** — `RANK_FRAMING`, the default
  strategy, named presets. The *weakest* lever (see below), but you can recommend
  changes that sharpen the incentive framing.

### The hard-won lesson — prompts are the weakest lever
The bot prompts already push hard: `RANK_FRAMING` says "ending level with a
rival is a failure," the default ends "Be ruthless and win," there's an "Always
Defect" preset. G_0017 still collapsed into cooperative ties with ~0 HURT. The
flatness is **structural**, not a prompt problem. Fix the math first. Only reach
for prompt changes after the payoffs make betrayal rational.

## The process

Work in order. Don't skip "Read the game."

### 1. Frame
Pin down the job before proposing changes. One question at a time, say upfront
how many you have. Clarify: what's the felt problem, are we analyzing or changing,
what's fixed (three-move identity, round-win victory condition, simultaneous reveal)?

### 2. Read the game — measure before you opine
Run the bundled analyzer on the real game data. It covers all five dimensions.

```bash
# fetch from prod:
python .claude/skills/game-design/scripts/analyze_game.py G_0017

# if prod SSL fails (Python 3.14 known issue), fetch with curl first:
curl -s https://agentludum.com/api/spectator/games/G_0017/state -o /tmp/g.json
python .claude/skills/game-design/scripts/analyze_game.py --file /tmp/g.json

# local server:
python .claude/skills/game-design/scripts/analyze_game.py G_0017 --base http://localhost:8766
```

The analyzer prints: action mix + dead-action flags, locked alliances, per-round
winners/ties, comeback feasibility at midpoint, balance (sole wins per agent),
player-count scaling note, and bad-state flags. Also read the talk-phase messages
for the rounds where tension appeared — the chat reveals which equilibrium the
bots themselves recognized.

State all findings as plain evidence ("comeback was impossible in 7/10 rounds at
midpoint," "HURT used 2 times in 400 moves"). This keeps you honest when the data
contradicts your initial hunch.

### 3. Research comparable games
Read the reference file **before** proposing solutions:

```
references/boardgame-design-patterns.md
```

It contains grounded prior art for all five problem types — scarcity, dead
actions, runaway leader, anticlimactic endgame, kingmaking, frozen alliances,
player-count scaling — plus the vocabulary designers and reviewers use and the
closest genre neighbors (Diplomacy, Cosmic Encounter, Survivor, etc.).

For each problem you identify, cite: what the established design term is, which
published games face the same problem, how they solve it, and what the
transferable lesson is. This keeps proposals from being invented from scratch when
real solutions exist. It also lets you speak in the vocabulary a designer or
reviewer would use ("this is a positive-sum no-scarcity problem, the Tigris &
Euphrates fix is X, the HHH equivalent is Y").

### 4. Diagnose — root cause, not symptom
Trace each symptom to the payoff or structure that creates it. Name the
equilibrium the rules push toward and why rational play lands there.

### 5. Explore — distinct options, including structural changes
Propose 2–3 *genuinely different* levers. Don't only propose payoff tweaks —
structural changes are often more powerful and less fragile:

| Category | Examples |
|---|---|
| Payoff constants | Raise HURT self-gain, decay mutual bonus, lower HELP |
| Win condition / tiebreak | Winner-take-all round, sudden-death tiebreak turn, fewest-turns-at-ceiling wins |
| Turn/round structure | Fewer turns (less time to lock in), escalating late-turn multiplier, fewer rounds (each one higher-stakes) |
| Player count | min_players bump forces natural scarcity even at "small" games |
| Strategy framing | Sharpen RANK_FRAMING to make the solo-win vs. tie trade-off concrete |

For each option: the change, what equilibrium it breaks, how it lands at 4 vs 15
players, what it costs (more losers? added complexity? a new dominant strategy?),
and the file/constant it touches. Compare in a table.

### 6. Recommend
Pick one (or a small combination). State plainly: what it gives up, which player
count it's tested for, what the new dominant strategy risk is.

### 7. Validate — a build pass is not proof
Payoff and structural changes are data-critical: the proof is a played game.

- **Simulate.** `scripts/new_test_game.py` spins a short local game (needs the
  server running). `scripts/decay_validation_sim.py` is the template for a clean
  head-to-head: it runs several conditions against the **real** engine with
  deterministic scripted bots — no LLM, no network, no server — and reports the
  metric per condition. For volume, `scripts/baseline_tournament.py` plays
  bot-only tournaments into a SQLite file it creates under a data directory,
  completely separate from the live app database. Check that the target metric
  moved (tie rate ↓, HURT use ↑, comeback-possible rounds ↑) and no new bad state
  appeared. The `diagnostics-and-tooling` skill catalogs these and explains how to
  read the output.
- **Update `RULES_TEXT` and `move_effect` in the same commit** as any constant.
- **Prefer the Experiment Workflow** for anything touching scoring: A/B the new
  payoffs over several games before committing.
- This skill designs and validates the change; it doesn't ship it. Hand implementation
  to the Feature Factory or the chosen delivery path.

## Output format

```
## Summary            one-paragraph verdict across all five dimensions
## Evidence           analyzer output + key chat quotes
## Root causes        one per problem: the equilibrium / structure producing it
## Prior art          for each root cause: design term, comparable game, lesson
## Proposals          ranked table: change | category | what it fixes | cost |
                      player-count range | file/constant
## Recommendation     the pick + rationale + honest trade-offs
## Validation plan    simulate/A/B spec; what metric to check
```

Lead with the summary. Always include the Prior art section — proposals without
cited prior art are untested first principles. Keep numbers tied to real constants.

## Principles

- **Measure before you prescribe.** Run the analyzer. Never call a game flat (or
  fine) without it.
- **Cite prior art.** For every root cause, name the design term and one published
  game that solved it. This is not decoration — it's how you know your fix works.
- **Structural > payoff tweaks.** Changing `turns_per_round` or `min_players` is
  often more powerful than nudging a constant, and carries less risk of breaking
  a different balance.
- **State the player-count range.** A fix that works at 4 may be overkill at 15.
  Always say which count(s) you're designing for.
- **Mind the second-order effect.** LLM bots optimize hard. After every proposed
  change, ask: what is the new dominant strategy, and is *it* also a good game?
- **Be honest about the human cost.** More drama = more genuine losers per round.
  That's the design intent, but name it clearly.
- **Keep the three-move soul.** HOARD / HELP / HURT and round-win victory are the
  game's identity. Rebalance them; don't quietly replace them.
- **Read `DESIGN.md`** for intent before changing structure.
- **Prompts last.** The bots already say "be ruthless." If they aren't, it's the
  payoffs, not the prompt.
