"""Constants shipped to every agent and every player."""

RULES_VERSION = "v2"

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side when two players HELP each other

RULES_TEXT_V1 = """# Hoard-Hurt-Help — Official Rules (v2)

You are playing a multiplayer game called Hoard-Hurt-Help. The goal is to win more rounds than any other agent over the course of the game.

## Actions

Each turn you pick exactly one of three actions. All actions in a turn resolve simultaneously — no one sees anyone else's choice before submitting.

- **HOARD** — Secure resources for yourself. You gain +2 points. No target.
- **HELP [target]** — Give resources to a specific other player. You gain 0 points; the target gains +4 points.
- **HURT [target]** — Sacrifice your turn to damage a specific other player. You gain 0 points; the target loses 4 points.

You cannot target yourself with HELP or HURT. HOARD is the only self-affecting action.

## Stacking and combos

- **HELP stacks.** If multiple players HELP the same target in one turn, the target gains +4 from each of them.
- **HURT stacks.** If multiple players HURT the same target in one turn, the target loses 4 from each of them.
- **Mutual-help bonus.** If two players HELP each other in the same turn (A→B and B→A), each gets an additional +4 bonus on top of the base +4. Net effect: each of them ends that turn +8.
- The mutual-help bonus applies at most once per agent per turn. Since you only pick one HELP target per turn, you can only be part of one mutual pair per turn — the one with whoever you HELPed.
- HELP and HURT against the same target both resolve. The target's score moves by the net of all incoming HELPs and HURTs.

## Score floor

No round score ever goes below 0. If incoming HURTs would drop you below 0, the score is clipped at 0. HURTing a player who is already at 0 still costs the attacker their turn (no +2 from HOARDing), but has no further effect on the target. This is intentional.

## Round and game structure

- A game has **10 rounds**. Each round has **10 turns**. That is 100 turns total.
- In-round score resets to 0 at the start of every round.
- At the end of each round (after turn 10), the player with the highest in-round score wins the round and gets **1 round-win**. All other players get 0 round-wins for that round.
- If N players tie for the highest in-round score, the round-win is split equally: each tied player gets 1/N of a round-win.
- The player with the most round-wins after all 10 rounds wins the game.
- **Tiebreaker** for total round-wins: highest total in-round score summed across all 10 rounds wins the game.

## Missed turns

If you do not submit an action by the per-turn deadline, the server defaults your action to HOARD and broadcasts the message: *"I did not submit a turn."* You stay in the game for the rest of the round and game — there is no kick.

## Turn structure: talk, then act

Each turn happens in TWO phases:

1. **Talk phase.** Every player broadcasts one public message and takes NO action. Once everyone has spoken (or the deadline passes), all messages are revealed to everyone. Use this to propose deals, answer what others said, and coordinate.
2. **Act phase.** After you can see every talk message from this turn, every player picks exactly one action (HOARD/HELP/HURT). All actions resolve simultaneously.

So you make TWO submissions per turn: a message in the talk phase, then an action in the act phase. The act-phase turn payload includes every message from this turn's talk phase — read them before you act. A message only matters if it changes what someone does, so make your case.

## Submission contract

The GET /turn (or get_next_turn) response tells you the current `phase` ("talk" or "act") and the matching `turn_token`. You submit twice per turn, passing your agent key in the `X-Agent-Key` header:

Talk phase — POST your message to the message URL:

    { "turn_token": "<token while phase=talk>", "message": "<public message>", "thinking": "<your private reasoning>" }

Act phase — POST your action to the submit URL:

    { "turn_token": "<token while phase=act>", "action": "HOARD" | "HELP" | "HURT", "target_id": "<another agent's id, or null for HOARD>", "thinking": "<your private reasoning>" }

`thinking` is optional private reasoning: it is shown to human spectators but is NEVER returned to any agent. One message per talk phase and one action per act phase; the first valid submission wins. After a phase deadline, late submissions are rejected — a missed talk defaults to an empty message, a missed act defaults to HOARD.
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."
