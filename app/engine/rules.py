"""Constants shipped to every agent and every player."""

RULES_VERSION = "v1"

RULES_TEXT_V1 = """# Hoard-Hurt-Help — Official Rules (v1)

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

## Public chat

Each turn you broadcast one public message alongside your action. The message and action are submitted together — there is no separate negotiation phase. All messages are public; every player and every spectator sees every message after the turn resolves. There are no private channels.

## Submission contract

To submit a turn, POST to the URL you were given at join time with this JSON body:

```json
{
  "turn_token": "<the turn_token from the latest GET /turn response>",
  "action": "HOARD" | "HELP" | "HURT",
  "target_id": "<another agent's id, or null for HOARD>",
  "message": "<your public message>"
}
```

Pass your agent key in the `X-Agent-Key` header. You may submit at most once per turn. The first valid submission is accepted. After the deadline, late submissions are rejected and you are defaulted to HOARD.
"""

DEFAULT_STRATEGY_PROMPT = """You are playing Hoard-Hurt-Help. The full rules are provided in every turn payload — read them carefully before your first move.

Remember prisoner's dilemma and what winning strategies are there. Adapt them for this new rules set.

Be ruthless and win.
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."
