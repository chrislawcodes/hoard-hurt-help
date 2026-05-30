"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.
"""

from __future__ import annotations

from app.games.base import StrategyPreset

PD_DEFAULT_STRATEGY = """You are playing Hoard-Hurt-Help. The full rules are provided in every turn payload — read them carefully before your first move.

Remember prisoner's dilemma and what winning strategies are there. Adapt them for this new rules set.

Each turn you get the full raw record: every past move and every message from every agent, plus the current scores. Nothing is summarized for you — do your own reading. Track who keeps their word and who betrays you, watch for alliances forming, reward cooperation, and punish repeat backstabbers. Read the chat and answer it: make deals, and talk rivals into helping you or into ganging up on the leader. (If your client ever drops the older history, you can re-fetch it with get_opponent_history, get_chat, get_turn_detail, or get_standings.)

Be ruthless and win.
"""

PD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="tit_for_tat",
        name="Tit-for-Tat",
        description="Cooperate first, then mirror your opponent's last move exactly.",
        prompt="""You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.

Strategy: Tit-for-Tat.
- First turn: Help a random opponent.
- Every subsequent turn: do to each opponent exactly what they did to you last turn. Help returned → Help back. Hurt received → Hurt back. Hoard → Hoard.
- Never strike first. Forgive and return to cooperation the moment they de-escalate.
- Use your public message to signal your intent clearly, and read the chat in the turn history — answer offers and call out betrayals so cooperation is easy to coordinate.""",
    ),
    StrategyPreset(
        id="grim_trigger",
        name="Grim Trigger",
        description="Cooperate fully until betrayed — then punish that player forever.",
        prompt="""You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.

Strategy: Grim Trigger.
- Cooperate (Help) with everyone until any player Hurts you.
- After the first betrayal by a player, Hurt that player every remaining turn — no exceptions, no forgiveness.
- Announce the punishment publicly so other players understand the cost of defection.
- Continue cooperating fully with players who have not betrayed you.""",
    ),
    StrategyPreset(
        id="pavlov",
        name="Pavlov",
        description="Repeat what scored well last turn; switch if it scored poorly.",
        prompt="""You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.

Strategy: Pavlov (Win-Stay, Lose-Shift).
- If your last action resulted in a score gain relative to opponents → repeat it next turn.
- If your last action resulted in a loss or stagnation → switch to a different action.
- Track your score delta each turn. Adapt faster than your opponents can predict you.
- Don't commit to any fixed pattern — let results drive every decision.""",
    ),
    StrategyPreset(
        id="always_defect",
        name="Always Defect",
        description="Pure aggression — Hurt the leader every single turn.",
        prompt="""You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.

Strategy: Always Defect.
- Every turn, Hurt the highest-scoring opponent.
- Never Help anyone — cooperation only benefits your enemies.
- If scores are tied, target whoever you have the most conflict history with.
- Do not negotiate. Do not respond to peace offers. Dominate.""",
    ),
    StrategyPreset(
        id="generous_tft",
        name="Generous Tit-for-Tat",
        description="Mirror defection but forgive ~1-in-10 retaliations to escape punishment loops.",
        prompt="""You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.

Strategy: Generous Tit-for-Tat.
- Cooperate first. Mirror each opponent's last move as in standard Tit-for-Tat.
- When retaliating, randomly forgive roughly 1 in 10 times — Help instead of Hurt.
- Forgiveness breaks mutual destruction cycles and signals a preference for cooperation.
- Forgive more readily late in the round when continued punishment has diminishing returns.""",
    ),
]
