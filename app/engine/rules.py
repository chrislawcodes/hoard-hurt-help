"""Constants shipped to every agent and every player."""

RULES_VERSION = "v1"

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side when two players HELP each other

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

Talk to the other agents — don't just narrate your own move. Propose deals, answer what others said to you last turn, build or break alliances, and try to convince rivals to help you or to turn on the leader. Your turn payload includes the full game history — every move and every message so far — so read the chat and answer what was aimed at you. A message only matters if it changes what someone does next turn, so make your case.

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

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."
