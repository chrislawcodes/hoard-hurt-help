"""Join-time strategy text for Liar's Dice."""

from __future__ import annotations

LD_DEFAULT_STRATEGY = (
    "Play to win Liar's Dice. "
    "Use the current standing bid, your dice, and the public dice counts. "
    "Raise when the current bid is likely true, bluff only when that is still sensible, "
    "and challenge when the standing bid looks unlikely. "
    "Never submit an illegal move."
)
