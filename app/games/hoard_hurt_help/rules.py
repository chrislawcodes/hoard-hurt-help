"""Constants shipped to every agent and every player."""

from app.agent_prompt import RESPONSE_PROTOCOL

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 4  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
BETRAYAL_BONUS = 4  # extra to the ATTACKER when they HURT a player HELPing them this turn
# Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
# the pair's per-side total at MUTUAL_HELP_FLOOR (= HOARD_POINTS, so a farmed pact is
# no better than hoarding): total = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k).
MUTUAL_HELP_FLOOR = 2

# The mutual-help paragraph is the only part that varies with a match's
# `mutual_help_decay` switch. ON = today's sliding decay (two bullets); OFF = a
# flat "+8 every time" with NO decay/floor/reset language.
_MUTUAL_HELP_ON = f"""- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each the first time a pair does it.
- **Mutual-help decays.** Each time the *same pair* repeats a mutual help in a match, the bonus drops by 1. So that pair's net falls +{HELP_POINTS + MUTUAL_HELP_BONUS}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 1}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 2}, … down to a floor of +{MUTUAL_HELP_FLOOR} each (no better than HOARD). The count is match-wide, not per round. Helping a *fresh* partner resets to +{HELP_POINTS + MUTUAL_HELP_BONUS} — farming one ally pays less over time than spreading pacts around."""

_MUTUAL_HELP_OFF = f"""- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each, every time. A pair earns the full +{HELP_POINTS + MUTUAL_HELP_BONUS} each on every mutual help, no matter how often they do it."""


def _render_game_rules_text(*, mutual_help_decay: bool) -> str:
    """Build the semantic rules body (default 5-round/7-turn counts) for one
    decay setting. ON swaps in the decay bullets; OFF the flat bullet."""
    mutual_section = _MUTUAL_HELP_ON if mutual_help_decay else _MUTUAL_HELP_OFF
    return f"""# Hoard-Hurt-Help — Official Rules (v5)

The goal is to win more rounds than any other agent over the course of the game.

## Actions

In the act phase, choose exactly one action. You cannot target yourself.

- **HOARD** — You gain +{HOARD_POINTS} points.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — You gain 0 points; the target loses {HURT_POINTS} points.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
{mutual_section}
- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, you gain an extra +{BETRAYAL_BONUS} bonus on top of the +{HELP_POINTS} help you still receive — so you net +{HELP_POINTS + BETRAYAL_BONUS} that turn. The player you HURT takes the normal -{HURT_POINTS}. Net swing: attacker +{HELP_POINTS + BETRAYAL_BONUS} / victim -{HURT_POINTS}. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## Score floor

Round scores are clipped at 0. HURTing a player already at 0 still costs the attacker their turn but has no effect on the target.

## Round and game structure

- A game has **5 rounds**, each with **7 turns** (35 turns total).
- In-round score resets to 0 at the start of every round.
- The player with the highest in-round score after turn 7 wins the round and gets **1 round-win**. Ties split the round-win equally (1/N each).
- The player with the most round-wins after all 5 rounds wins the game.
- **Tiebreaker:** highest total in-round score summed across all rounds.

## Turn structure: talk, then act

Each turn has a talk phase followed by an act phase:

1. **Talk phase.** Broadcast one public message. Messages are revealed simultaneously once everyone has submitted or the deadline passes.
2. **Act phase.** After seeing all talk messages, choose your action. Actions resolve simultaneously.
"""


# The default (decay ON) rules — kept as a module constant because callers and
# tests reference it directly and expect today's text.
GAME_RULES_TEXT = _render_game_rules_text(mutual_help_decay=True)

RULES_TEXT = f"""{GAME_RULES_TEXT}
## Response format

{RESPONSE_PROTOCOL}
"""

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."


def _apply_counts(text: str, total_rounds: int, turns_per_round: int) -> str:
    """Rewrite the literal 5-round/7-turn counts to the actual match counts.

    The count strings live only in the "Round and game structure" section, never
    in the mutual-help section, so this is safe for both decay settings.
    """
    if total_rounds == 5 and turns_per_round == 7:
        return text
    return (
        text
        .replace("**5 rounds**", f"**{total_rounds} rounds**")
        .replace("**7 turns**", f"**{turns_per_round} turns**")
        .replace("(35 turns total)", f"({total_rounds * turns_per_round} turns total)")
        .replace("after turn 7", f"after turn {turns_per_round}")
        .replace("after all 5 rounds", f"after all {total_rounds} rounds")
    )


def make_game_rules_text(
    total_rounds: int = 5, turns_per_round: int = 7, *, mutual_help_decay: bool = True
) -> str:
    """Return semantic game rules for this match's decay setting and round counts."""
    base = GAME_RULES_TEXT if mutual_help_decay else _render_game_rules_text(
        mutual_help_decay=False
    )
    return _apply_counts(base, total_rounds, turns_per_round)


def make_rules_text(
    total_rounds: int = 5, turns_per_round: int = 7, *, mutual_help_decay: bool = True
) -> str:
    """Return official rules plus the canonical response contract."""
    return (
        f"{make_game_rules_text(total_rounds, turns_per_round, mutual_help_decay=mutual_help_decay)}"
        f"## Response format\n\n{RESPONSE_PROTOCOL}\n"
    )
