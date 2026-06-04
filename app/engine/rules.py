"""Constants shipped to every agent and every player."""

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side when two players HELP each other

RULES_TEXT = f"""# Hoard-Hurt-Help — Official Rules (v2)

You are playing a multiplayer game called Hoard-Hurt-Help. The goal is to win more rounds than any other agent over the course of the game.

## Actions

Each turn you pick exactly one action. HOARD is the only self-targeting action; HELP and HURT require a target other than yourself.

- **HOARD** — You gain +{HOARD_POINTS} points. No target.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — You gain 0 points; the target loses {HURT_POINTS} points.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each.
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## Score floor

Round scores are clipped at 0. HURTing a player already at 0 still costs the attacker their turn but has no effect on the target.

## Round and game structure

- A game has **10 rounds**, each with **10 turns** (100 turns total).
- In-round score resets to 0 at the start of every round.
- The player with the highest in-round score after turn 10 wins the round and gets **1 round-win**. Ties split the round-win equally (1/N each).
- The player with the most round-wins after all 10 rounds wins the game.
- **Tiebreaker:** highest total in-round score summed across all rounds.

## Turn structure: talk, then act

Each turn has TWO phases. You make one submission per phase:

1. **Talk phase.** Broadcast one public message (max 200 characters). All messages are revealed simultaneously once everyone has submitted or the deadline passes.
2. **Act phase.** After seeing all talk messages, submit one action (HOARD/HELP/HURT). All actions resolve simultaneously.

## Submission contract

GET /turn (or get_next_turn) returns the current `phase` ("talk" or "act") and the matching `turn_token`. Pass your agent key in `X-Agent-Key`.

Talk phase — POST to the message URL:

    {{ "turn_token": "<token while phase=talk>", "message": "<public message, max 200 characters>", "thinking": "<private reasoning, max 200 characters>" }}

Act phase — POST to the submit URL:

    {{ "turn_token": "<token while phase=act>", "action": "HOARD" | "HELP" | "HURT", "target_id": "<another agent's id, or null for HOARD>", "thinking": "<private reasoning, max 200 characters>" }}

Include `thinking` on every submission (max 200 characters). The first valid submission per phase wins. Late submissions are rejected — a missed talk defaults to an empty message, a missed act defaults to HOARD.
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."


def make_rules_text(total_rounds: int = 10, turns_per_round: int = 10) -> str:
    """Return RULES_TEXT with the actual round/turn counts substituted in."""
    if total_rounds == 10 and turns_per_round == 10:
        return RULES_TEXT
    return (
        RULES_TEXT
        .replace("**10 rounds**", f"**{total_rounds} rounds**")
        .replace("**10 turns**", f"**{turns_per_round} turns**")
        .replace("(100 turns total)", f"({total_rounds * turns_per_round} turns total)")
        .replace("after turn 10", f"after turn {turns_per_round}")
        .replace("after all 10 rounds", f"after all {total_rounds} rounds")
    )
