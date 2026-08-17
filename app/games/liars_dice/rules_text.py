"""Rules text for Liar's Dice."""

from __future__ import annotations


def make_game_rules_text(
    *,
    wild_ones: bool,
    dice_per_player: int,
    min_players: int,
    max_players: int,
    total_rounds: int = 7,
    turns_per_round: int = 7,
) -> str:
    """Return semantic game rules with actual round/turn counts and settings."""
    wild_section = (
        "**1s are wild.** When you bid on any face (2–6), 1s count as that face. "
        "When you bid on 1s, only 1s count (not double-counted). This makes ace bids "
        "powerful but risky: you must bid half the quantity to move from normal to aces, "
        "and more than double to move back."
    ) if wild_ones else "**No wild cards.** Bid directly on the face values you see."

    return f"""# Liar's Dice — Official Rules

The goal is to be the last player standing. Players are eliminated one at a time, and the final survivor wins.

## Setup

- **Table:** {min_players}–{max_players} players.
- **Starting hand:** {dice_per_player} dice per player.
- **Game length:** not fixed. The match runs until one player is left, however many hands that takes. Do not pace yourself against a turn count — pace against how many dice are still on the table.

## How it works

Each round, all players roll their dice and hide them. Players then bid on how many dice of a certain face are on the entire table (all players' hands combined). After each bid, the next player must raise the bid or challenge. A challenge reveals all dice and determines a loser.

## The bid

A bid is a claim: "There are at least N dice showing face F on the table."

- **Quantity (N):** 1 or more (up to total dice in play).
- **Face (F):** 1 through 6 (1 = aces).
- **Example:** "5 threes" means "at least 5 dice showing 3."

## Wild ones (aces)

{wild_section}

## Legal bids

You must always raise the standing bid. The definition of "raise" depends on wild ones:

**Without wild ones:** Raise by increasing quantity, or keep quantity the same and increase face (2 → 3 → 4 → 5 → 6).
- Example ladder: (1,2) → (1,3) → (2,2) → (3,2) → ...

**With wild ones (Dudo rules):**
- **Normal to normal (both faces 2–6):** Increase quantity, or keep quantity and increase face.
- **Normal to aces (bid on 1s):** Quantity must be at least ceil(previous ÷ 2). Example: "3 fives" → "2 aces" is legal (ceil(3/2) = 2).
- **Aces to normal (previous was 1s):** Quantity must be at least 2 × previous + 1. Example: "2 aces" → "5 twos" is legal (2 × 2 + 1 = 5).
- **Aces to aces:** Strictly increase quantity.

**Openings:** The first bid of a round must be on faces 2–6 (not 1s). Quantity 1 is always legal.

## Challenge

Instead of bidding, you may challenge the standing bid. A challenge says: "I don't believe that many of that face exist."

A challenge immediately reveals all dice on the table:
- **Bid holds (true):** Count all dice of that face. If the count ≥ bid quantity, the bid is correct. The challenger loses 1 die.
- **Bid fails (false):** If count < bid quantity, the bid was a lie. The player who made the bid loses 1 die.

## Elimination and winning

When a player loses their last die, they are eliminated. Play continues with remaining players. The game ends when only 1 player remains—that player wins.

## Round and match structure

- Each round, all remaining players roll. Players eliminated in the previous round do not roll.
- Bidding continues around the table until someone challenges or everyone has folded.
- After a challenge resolves, the next round begins. The loser of the showdown bids first in the next round.
- **The match ends when one player still has dice.** That player wins. There is no round limit to play toward.
- **Safety ceilings, not the shape of the game.** A round is cut off after {turns_per_round} turns if nobody has challenged, and the match is cut off after {total_rounds} rounds. Both sit far above any real game and are there so a stuck match cannot run forever — treat them as unreachable, not as a target."""
