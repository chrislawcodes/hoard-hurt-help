"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.

Every preset and the default share `RANK_FRAMING`: the reminder to prioritize
round wins while accounting for fractional wins on ties and total score as the
match tiebreaker. It is woven into each strategy so even the cooperative ones
play to actually win the match.

`RANK_FRAMING` also carries the three facts a preset cannot be written without,
all of which were measured in M_6442 rather than assumed:

* **One action, one target.** Classic Prisoner's Dilemma strategies say "mirror
  each opponent" — unexecutable here, where a turn buys a single action against
  a single player. Faced with an impossible instruction the model falls back to
  HELP, which is why four presets collapsed into one behaviour.
* **Even trading ties.** Two partners swapping mutual help both bank the same
  amount, so every clean pair finishes a round level. Beating the pack takes
  more helpers than anyone else, a betrayal, or dragging the leader down.
* **Damage lands late.** The score floor means a player near zero cannot lose
  much, so an early attack is largely absorbed.

Phrasing constraint: `tests/test_per_game_strategy.py` forbids these prompts
from repeating the base prompt's wording, so they must not contain the literal
"target_id" (or the other phrases listed there), and every one of them must
keep the words "Prioritize round wins".
"""

from __future__ import annotations

from app.games.base import StrategyPreset

# Shared "what winning means" lens, woven into the default and every preset.
RANK_FRAMING = """How winning works — weigh every move against this:
- Prioritize round wins. Sole first place earns a full round win; ties split the win equally among the tied leaders.
- Track your rank, but do not ignore your score: if agents finish the match tied on round wins, total score is the tiebreaker.
- You get ONE action against ONE player per turn. You cannot answer everybody, so every turn pick the single player who matters most and act on them.
- Swapping help evenly with one partner leaves you level with every other pair doing the same, and level is not a win. Getting clear takes one of three things: more players helping you in a turn than anyone else gets, HURTing a player who is HELPing you that same turn (you keep their help and take the bonus on top), or dragging the leader back below you.
- Damage lands late. A player near zero has little left to lose, so an attack early in a round is mostly absorbed; the same attack near the end can decide the round.
- As each round nears its end, decide how aggressively to pursue sole first place or deny a rival based on your strategy and the current standings."""

PD_DEFAULT_STRATEGY = f"""{RANK_FRAMING}

Adapt proven iterated Prisoner's Dilemma tactics to this multiplayer setting. Track promises, betrayals, alliances, and changes in rank. Reward useful cooperation, punish repeated exploitation, and adjust aggressively near the end of each round.
"""

PD_STRATEGY_PRESETS: list[StrategyPreset] = [
    StrategyPreset(
        id="tit_for_tat",
        name="Tit-for-Tat",
        description="Cooperate by default, then answer the one player who crossed you.",
        prompt=f"""{RANK_FRAMING}

Strategy: Tit-for-Tat.
- By default, HELP the player who has repaid you most reliably. Never strike first.
- When a player HURTs you, answer that ONE player on your very next turn. Hold one grudge at a time, always the most recent, and drop it the moment they stop.
- Time your answer well. Striking a player while they are still HELPing you is worth far more than striking one who is not, so hit back early in a round while they are still cooperating rather than saving it for the end.
- Never HELP a player who has already betrayed you once, on the last turn of any round. That is the turn they gain most from your trust and you can lose most by offering it.
- Cooperation is how you climb, not where you stop: keep swapping help, but line up a second helper so you do not finish level with the very partner you have been feeding.""",
    ),
    StrategyPreset(
        id="always_cooperate",
        name="Always Cooperate",
        description="Never attack, never hoard — help every turn and win on sheer volume.",
        prompt=f"""{RANK_FRAMING}

Strategy: Always Cooperate.
- HELP on every single turn, without exception. You never HOARD and you never HURT, not even after you are betrayed.
- Spend your help where it comes back: favour whoever helped you most recently, and say openly at every opportunity that you always repay, so being your partner is obviously worth it.
- Volume is your only route past a tie. One loyal partner leaves you exactly level with every other pair, so work on getting a second player to help you in the same turn.
- Being betrayed is a cost of this strategy, not a reason to abandon it. Take the hit and keep giving — the reputation is what keeps helpers coming.""",
    ),
    StrategyPreset(
        id="buzzer_beater",
        name="Buzzer-Beater",
        description="An honest partner all match, then a knife on the round's final turn.",
        prompt=f"""{RANK_FRAMING}

Strategy: Buzzer-Beater.
- Play as a genuinely reliable partner for almost the whole match. Build one pact, feed it every turn, and give your partner no reason to doubt you.
- Break it on the LAST turn of a round by HURTing that partner. You still collect the help they sent you that turn and take the betrayal bonus on top, which is the largest single swing available to anyone.
- Timing is the entire strategy. Betray in an early round and you hand the table the rest of the match to freeze you out; betray on the final turn of the final round and nobody gets a chance to answer.
- Only strike when you are confident they will actually HELP you that turn. The bonus needs a helper — against a partner who has gone quiet, started hoarding, or already been burned once, stay honest and take the safe points.""",
    ),
    StrategyPreset(
        id="dealmaker",
        name="Dealmaker",
        description="Win the competition for help — get two players helping you at once.",
        prompt=f"""{RANK_FRAMING}

Strategy: Dealmaker.
- You win by being helped more than anyone else, not by attacking. Two players helping you in one turn beats anything a two-person pact can produce.
- Work the talk phase every single turn. Ask directly, promise plainly, name exactly who you will repay next turn, and then actually do it.
- You only have one HELP to give, so spend it on whoever is closest to giving up on you — the player you have owed the longest. Let the patient ones wait a turn; chase the one about to walk.
- Never attack. Your reputation is your income, and a single betrayal can cost you every helper at once.
- Help is scarce: the table only has so many helping actions each turn, so every extra one you attract is one a rival does not get.""",
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
