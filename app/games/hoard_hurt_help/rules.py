"""Constants shipped to every agent and every player."""

from app.agent_prompt import RESPONSE_PROTOCOL

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
BETRAYAL_HURT_POINTS = 8  # HURT lands this hard when the target is HELPing you this turn
# Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
# the pair's per-side total at MUTUAL_HELP_FLOOR (= HOARD_POINTS, so a farmed pact is
# no better than hoarding): total = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k).
MUTUAL_HELP_FLOOR = 2

GAME_RULES_TEXT = f"""# Hoard-Hurt-Help — Official Rules (v3)

The goal is to win more rounds than any other agent over the course of the game.

## Actions

In the act phase, choose exactly one action. You cannot target yourself.

- **HOARD** — You gain +{HOARD_POINTS} points.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — You gain 0 points; the target loses {HURT_POINTS} points.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each.
- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, your HURT lands for -{BETRAYAL_HURT_POINTS} instead of -{HURT_POINTS}. You still receive their +{HELP_POINTS} help, so betraying a helper is a +{HELP_POINTS} / -{BETRAYAL_HURT_POINTS} swing. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## Score floor

Round scores are clipped at 0. HURTing a player already at 0 still costs the attacker their turn but has no effect on the target.

## Round and game structure

- A game has **7 rounds**, each with **7 turns** (49 turns total).
- In-round score resets to 0 at the start of every round.
- The player with the highest in-round score after turn 7 wins the round and gets **1 round-win**. Ties split the round-win equally (1/N each).
- The player with the most round-wins after all 7 rounds wins the game.
- **Tiebreaker:** highest total in-round score summed across all rounds.

## Turn structure: talk, then act

Each turn has a talk phase followed by an act phase:

1. **Talk phase.** Broadcast one public message. Messages are revealed simultaneously once everyone has submitted or the deadline passes.
2. **Act phase.** After seeing all talk messages, choose your action. Actions resolve simultaneously.
"""

RULES_TEXT = f"""{GAME_RULES_TEXT}
## Response format

{RESPONSE_PROTOCOL}
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."


def make_game_rules_text(total_rounds: int = 7, turns_per_round: int = 7) -> str:
    """Return semantic game rules with the actual round/turn counts."""
    if total_rounds == 7 and turns_per_round == 7:
        return GAME_RULES_TEXT
    return (
        GAME_RULES_TEXT
        .replace("**7 rounds**", f"**{total_rounds} rounds**")
        .replace("**7 turns**", f"**{turns_per_round} turns**")
        .replace("(49 turns total)", f"({total_rounds * turns_per_round} turns total)")
        .replace("after turn 7", f"after turn {turns_per_round}")
        .replace("after all 7 rounds", f"after all {total_rounds} rounds")
    )


def make_rules_text(total_rounds: int = 7, turns_per_round: int = 7) -> str:
    """Return official rules plus the canonical response contract."""
    return (
        f"{make_game_rules_text(total_rounds, turns_per_round)}"
        f"## Response format\n\n{RESPONSE_PROTOCOL}\n"
    )
