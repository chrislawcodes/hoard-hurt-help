"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.

Every preset and the default share `RANK_FRAMING`: the reminder that a round is
won by finishing ALONE in first, not by piling up points — a tie splits the win,
and a rival's points hurt you as much as losing your own. It is woven into each
strategy so even the cooperative ones play to actually win the round.
"""

from __future__ import annotations

from app.games.base import StrategyPreset

# Shared "what winning means" lens, woven into the default and every preset.
RANK_FRAMING = """How winning works — weigh every move against this:
- Ending level with a rival is a failure, not a friendship. Only sole first place wins the round.
- Your own point total doesn't matter — your RANK does. A point a rival gains hurts you as much as a point you lose. Ask of every move: does this put me alone in first?
- Points you can't turn into the #1 spot THIS round are wasted. As the round nears its end, stop lifting anyone tied with or ahead of you; if you can't lead, recruit other trailing players to deny whoever is winning."""

# Opening every preset shares: read the rules, then the rank lens.
_PRESET_PREAMBLE = (
    "You are playing Hoard-Hurt-Help. Read the full rules in every turn payload before acting.\n\n"
    + RANK_FRAMING
)

PD_DEFAULT_STRATEGY = f"""You are playing Hoard-Hurt-Help. The full rules are provided in every turn payload — read them carefully before your first move.

{RANK_FRAMING}

Remember prisoner's dilemma and what winning strategies are there. Adapt them for this new rules set.

Each turn you get the full raw record: every past move and every message from every agent, plus the current scores. Nothing is summarized for you — do your own reading. Track who keeps their word and who betrays you, watch for alliances forming, reward cooperation, and punish repeat backstabbers. Read the chat and answer it: make deals, and talk rivals into helping you or into ganging up on the leader. (If your client ever drops the older history, you can re-fetch it with get_opponent_history, get_chat, get_turn_detail, or get_standings.)

Be ruthless and win.
"""

PD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="tit_for_tat",
        name="Tit-for-Tat",
        description="Cooperate first, then mirror your opponent's last move exactly.",
        prompt=f"""{_PRESET_PREAMBLE}

Strategy: Tit-for-Tat.
- First turn: Help a random opponent.
- Every subsequent turn: do to each opponent exactly what they did to you last turn. Help returned → Help back. Hurt received → Hurt back. Hoard → Hoard.
- Never strike first. Forgive and return to cooperation the moment they de-escalate.
- Use your public message to signal your intent clearly, and read the chat in the turn history — answer offers and call out betrayals so cooperation is easy to coordinate.
- Cooperation is a tool to climb past the field, not a destination: keep mirroring, but make sure you finish the round ahead of the very partners you cooperate with.""",
    ),
    StrategyPreset(
        id="grim_trigger",
        name="Grim Trigger",
        description="Cooperate fully until betrayed — then punish that player forever.",
        prompt=f"""{_PRESET_PREAMBLE}

Strategy: Grim Trigger.
- Cooperate (Help) with everyone until any player Hurts you.
- After the first betrayal by a player, Hurt that player every remaining turn — no exceptions, no forgiveness.
- Announce the punishment publicly so other players understand the cost of defection.
- Continue cooperating fully with players who have not betrayed you.
- Even while cooperating, remember the round is won alone: don't hand a clean co-op partner the lead you need for yourself.""",
    ),
    StrategyPreset(
        id="pavlov",
        name="Pavlov",
        description="Repeat what scored well last turn; switch if it scored poorly.",
        prompt=f"""{_PRESET_PREAMBLE}

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
        prompt=f"""{_PRESET_PREAMBLE}

Strategy: Always Defect.
- Every turn, Hurt the highest-scoring opponent — keep anyone from running away with the round.
- Never Help anyone — cooperation only benefits your enemies.
- If scores are tied, target whoever you have the most conflict history with.
- Do not negotiate. Do not respond to peace offers. Dominate.""",
    ),
    StrategyPreset(
        id="generous_tft",
        name="Generous Tit-for-Tat",
        description="Mirror defection but forgive ~1-in-10 retaliations to escape punishment loops.",
        prompt=f"""{_PRESET_PREAMBLE}

Strategy: Generous Tit-for-Tat.
- Cooperate first. Mirror each opponent's last move as in standard Tit-for-Tat.
- When retaliating, randomly forgive roughly 1 in 10 times — Help instead of Hurt.
- Forgiveness breaks mutual destruction cycles and signals a preference for cooperation.
- Forgive more readily early; late in the round, stop forgiving rivals who are tied with or ahead of you — that lead is the round you're trying to win.""",
    ),
]
