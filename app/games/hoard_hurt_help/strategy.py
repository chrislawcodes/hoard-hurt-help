"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.

Every preset and the default share `RANK_FRAMING`: the reminder to prioritize
round wins while accounting for fractional wins on ties and total score as the
match tiebreaker. It is woven into each strategy so even the cooperative ones
play to actually win the match.
"""

from __future__ import annotations

from app.games.base import StrategyPreset

# Shared "what winning means" lens, woven into the default and every preset.
RANK_FRAMING = """How winning works — weigh every move against this:
- Prioritize round wins. Sole first place earns a full round win; ties split the win equally among the tied leaders.
- Track your rank, but do not ignore your score: if agents finish the match tied on round wins, total score is the tiebreaker.
- As each round nears its end, decide how aggressively to pursue sole first place or deny a rival based on your strategy and the current standings."""

PD_DEFAULT_STRATEGY = f"""{RANK_FRAMING}

Adapt proven iterated Prisoner's Dilemma tactics to this multiplayer setting. Track promises, betrayals, alliances, and changes in rank. Reward useful cooperation, punish repeated exploitation, and adjust aggressively near the end of each round.
"""

PD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="tit_for_tat",
        name="Tit-for-Tat",
        description="Cooperate first, then mirror your opponent's last move exactly.",
        prompt=f"""{RANK_FRAMING}

Strategy: Tit-for-Tat.
- First turn: Help a random opponent.
- Every subsequent turn: do to each opponent exactly what they did to you last turn. Help returned → Help back. Hurt received → Hurt back. Hoard → Hoard.
- Never strike first. Forgive and return to cooperation the moment they de-escalate.
- Cooperation is a tool to climb past the field, not a destination: keep mirroring, but make sure you finish the round ahead of the very partners you cooperate with.""",
    ),
    StrategyPreset(
        id="grim_trigger",
        name="Grim Trigger",
        description="Cooperate fully until betrayed — then punish that player forever.",
        prompt=f"""{RANK_FRAMING}

Strategy: Grim Trigger.
- Cooperate (Help) with everyone until any player Hurts you.
- After the first betrayal by a player, Hurt that player every remaining turn — no exceptions, no forgiveness.
- Continue cooperating fully with players who have not betrayed you.
- Even while cooperating, remember the round is won alone: don't hand a clean co-op partner the lead you need for yourself.""",
    ),
    StrategyPreset(
        id="pavlov",
        name="Pavlov",
        description="Repeat what scored well last turn; switch if it scored poorly.",
        prompt=f"""{RANK_FRAMING}

Strategy: Pavlov (Win-Stay, Lose-Shift).
- If your last action improved your RANK (not just your score) → repeat it next turn.
- If your last action left you flat or losing ground in the standings → switch to a different action.
- Track your standing each turn, not just your points. Adapt faster than your opponents can predict you.
- Don't commit to any fixed pattern — let results drive every decision.""",
    ),
    StrategyPreset(
        id="always_defect",
        name="Always Defect",
        description="Pure aggression — Hurt the leader every single turn.",
        prompt=f"""{RANK_FRAMING}

Strategy: Always Defect.
- Every turn, Hurt the highest-scoring opponent — keep anyone from running away with the round.
- Never Help anyone — cooperation only benefits your enemies.
- If scores are tied, target whoever you have the most conflict history with.""",
    ),
    StrategyPreset(
        id="generous_tft",
        name="Generous Tit-for-Tat",
        description="Mirror defection but forgive ~1-in-10 retaliations to escape punishment loops.",
        prompt=f"""{RANK_FRAMING}

Strategy: Generous Tit-for-Tat.
- Cooperate first. Mirror each opponent's last move as in standard Tit-for-Tat.
- When retaliating, randomly forgive roughly 1 in 10 times — Help instead of Hurt.
- Forgiveness breaks mutual destruction cycles and signals a preference for cooperation.
- Forgive more readily early; late in the round, stop forgiving rivals who are tied with or ahead of you — that lead is the round you're trying to win.""",
    ),
]
