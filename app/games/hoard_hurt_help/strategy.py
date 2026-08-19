"""Prisoner's Dilemma strategy presets + the default pre-fill.

These belong to the PD game module (game #1), not the platform — a different
game ships its own. The join/player UI gets them via the GameModule contract
(`strategy_presets()` / `default_strategy()`), never by importing this directly.

Every preset and the default share `RANK_FRAMING`, which is now a single line:
the objective. Everything else it once carried either restated a rule the agent
reads in `base_prompt` on the same turn, or was true of only some matches — see
the note on the constant for each removal and why.

**Read this before adding to `RANK_FRAMING`.** It is shared by all eight presets,
so anything added here pushes every one of them the same direction, and the
roster exists to make them behave differently. The bar for a new line is that the
rules cannot give it and it applies to every strategy — not merely that it is
true or useful. `.claude/skills/game-design/` records the wider lesson from
G_0017: prompts are the weakest lever here, and a flat game is a payoff problem
first.

The roster is Tit-for-Tat, Loyal Partner, Buzzer-Beater, Dealmaker, Underdog's
Champion, Kingslayer, Sandbagger, Salvager — in that order, because the join UI
pre-selects the first one. Eight is the practical ceiling: once the only route
past the tie is a betrayal, a strategy is fully described by who you partner and
when you strike, and those combinations are now covered. A ninth would duplicate
one of these. Grim
Trigger, Pavlov, Always Defect and Generous Tit-for-Tat were dropped rather than
rewritten: none of them can win. In M_6442 each either played out as plain
Tit-for-Tat or, for Always Defect, aimed at the highest-scoring opponent — the
player least likely to be helping it — so its attacks collected nothing and it
finished last on 12 points against a winner on 184. Dropping a preset does not
change agents that already exist; the prompt text is copied into the agent at
creation time.

Every surviving preset must have a route past the tie. Income in a turn is
``4N + 2`` where N is how many players HELP you (``4N + 6`` if you betray one of
them), so an extra helper is worth 4 while betraying one is worth 6. Under the v5
payoffs those were 4 and 4 — recruiting tied the knife, so the game was purely a
contest for helpers. At v6 the knife edges ahead, which is the whole point of the
change: a preset may now bid for the strike as well as for the helpers.

Two routes remain rejected. Pure denial still fails, because HURTing a player who
is not helping you pays the attacker nothing however large HURT_POINTS grows — it
leaves you below the bystanders who did nothing. Courting two backers at once
still fails, since one HELP a turn rotated between two of them pays each only 3
against the 6 any ordinary pact pays, so they leave.

Coercion ("help me or I HURT you") was rejected under v5 and is NOT rejected under
v6. The old arithmetic was that refusing paid 6 - 4 = 2 against complying's 0, so
the threat was smaller than the pact it asked a player to give up. At
HURT_POINTS = 8 refusing pays 6 - 8 = -2, so the threat now bites. No preset bids
for it yet; it is an open route, not a covered one.

Salvager is the exception that proves that last rule, and it is worth spelling
out because it looks like the rejected route. Nobody in contention will help you
for free — they give up a pact worth 6. A player who is mathematically OUT of the
round gives up only a hoard, and their in-round score resets to zero at the round
boundary anyway, so those points only ever touch the match tiebreak. Their help is
cheap for them and worth a full share to you. That asymmetry between players'
round positions is the only place a second helper can come from, and no other
preset looks for it.

**The v7 pot weakens this premise and it has not been re-measured.** When HOARD
paid a flat 2, a dead player really was giving up almost nothing. Now hoarding
pays a share of a contested pot — up to the whole pot if they go alone — and a
player out of the round is exactly who has no reason not to take it. Salvager is
asking them to give up their best remaining move, not their worst. Whether the
preset still earns its place is an open question for the first v7 matches. In M_6442's round 4 the scores
were 30/30/30/30/10/10/8/4 — four players out of the round, four donors, ignored
by everyone.

Sandbagger works at match scale rather than turn scale. Round wins accumulate all
match but credibility is spent once, so it hoards credibility and spends it late:
sharing every round pays about 0.9 round wins, one late betrayal pays 1.75, and
playing harmless for five rounds then taking the last two pays about 2.6.

That last dead end is what shapes Underdog's Champion, and it is why the preset
keeps ONE partner rather than building a bloc. Its route past the tie is a
betrayal, the same route Buzzer-Beater uses, but the two differ in both halves
of the decision. Buzzer-Beater picks any partner and times the knife by the
calendar (the last turn, so nobody can answer). The Champion picks a partner who
has just been abandoned and times the knife by the scoreboard, striking only in
the narrow band where +8 clears the leader but +6 does not. The target choice is
what makes it repeatable: a rescued partner's alternative is hoarding alone for
about 10 a round against roughly 25 with the Champion even counting the knife,
so unlike Buzzer-Beater's victim they have no better offer and come back.

Three presets now win by betrayal, and what separates them is WHO they partner
and WHEN they strike, not the strike itself — betraying anyone pays the attacker
the same, so the choice of victim is worth nothing to your own score and
everything to the other side of the table. Buzzer-Beater takes whatever partner
is handy and times the knife by the calendar. The Champion takes a partner who
was just abandoned, which is what makes it repeatable. Kingslayer takes the
current leader, because that is the only betrayal that both pays you AND moves
the player you are chasing, so it can overhaul a lead no other single action
reaches. Kingslayer's limit is reach: the strike shifts the gap by a fixed
amount, so a big enough lead is out of range, and the preset says to stay honest
rather than strike on hope. Note that the bare version of its move — HURTing the
leader when they are NOT helping you — is the pure denial already rejected
above. The returning help is what makes it pay.

Phrasing constraint: `tests/test_per_game_strategy.py` forbids these prompts
from repeating the base prompt's wording, so they must not contain the literal
"target_id" (or the other phrases listed there), and every one of them must
keep the words "Prioritize round wins".
"""

from __future__ import annotations

from app.games.base import StrategyPreset

# The one line every preset and the default carry. One line, because one line is
# all that is true of every match.
#
# What used to be here: bullets on the tie split, the tiebreaker, one action per
# turn, and the score floor — each restating a rule the agent reads in
# `base_prompt` on the same turn — plus an "even swaps leave you level, so
# winning takes asymmetry" insight.
#
# That last one came out for being WRONG, not merely redundant. It describes the
# FLAT mutual-help modes only. Under `decay` a pair on its fifth swap banks 4
# while a fresh pair banks 8; under `no_repeats` a pair that swapped last turn
# banks 4 against a rotating pair's 8 — those modes exist precisely to break the
# symmetry it asserted, and `decay` is LEGACY_MUTUAL_HELP_MODE, so every
# pre-switch match ran it. Even under a flat mode it needs no HURT, no third
# helper, and no score-floor clipping to hold. Stated as a law in the block all
# eight presets share, it pointed every agent the same wrong way.
#
# Two tests before adding a line here: (a) is it true in EVERY mode, and (b) can
# the rules not already give it? Anything failing either belongs in the one preset
# that needs it — which is what #689 did with "damage lands late", pushing it down
# into Buzzer-Beater rather than back up here.
#
# "Prioritize round wins" is deliberately not "…not points": total score is the
# match tiebreaker, so points decide when round wins are level.
RANK_FRAMING = "Prioritize round wins."

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
* HELP whoever has repaid you most reliably. Never HURT first.
* When a player HURTs you, answer that ONE player next turn. One grudge at a time, the most recent, dropped when they stop.
* Answer while they are still HELPing you: that pays you 10. HURTing a player who is not HELPing you pays 0.
* Never HELP a player who has HURT you before on a round's last turn.
* Line up a second helper. One partner leaves you level.""",
    ),
    StrategyPreset(
        id="loyal_partner",
        name="Loyal Partner",
        description="Back one partner completely — until they HURT you, then find another.",
        prompt=f"""{RANK_FRAMING}

Strategy: Loyal Partner.
* Pick one partner and HELP them every turn. Never HURT anyone.
* If your partner HURTs you, stop HELPing them. Pick a new partner and say why.
* Ask other players for HELP in the talk phase. Say openly that you always repay.
* Two players HELPing you in one turn pays 10; a clean pair banks 6.""",
    ),
    StrategyPreset(
        id="buzzer_beater",
        name="Buzzer-Beater",
        description="Court help all match, then HURT at the buzzer — but only when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Buzzer-Beater.
* Be worth helping all match. Ask for HELP in the talk phase every turn and repay reliably. Do not settle for one helper — your HURT is only ever worth the HELP coming your way that turn.
* HURT on the LAST turn of a round, never earlier. An early HURT is absorbed by the score floor; the same move at a round's end can decide it.
* HURT late in the MATCH too. Betray early and you spend the rest of the match as the player nobody helps.
* Only HURT when it wins you the round. Compare where you finish if you HELP against where you finish if you HURT. If HELP already wins, or HURT still leaves you short of first, HELP.
* HURTing a player who is not HELPing you that turn pays 0 and costs you the turn. Never HURT someone who has gone quiet, started hoarding, or been burned by you before.""",
    ),
    StrategyPreset(
        id="dealmaker",
        name="Dealmaker",
        description="Win the competition for help — get two players helping you at once.",
        prompt=f"""{RANK_FRAMING}

Strategy: Dealmaker.
* You win by being helped more than anyone else, not by attacking. Never HURT.
* Work the talk phase every turn. Ask directly, name who you will repay next turn, then do it.
* You have one HELP. Spend it on whoever is closest to giving up on you — the player you have owed longest.
* Two players HELPing you in one turn pays 10; a clean pair banks 6.""",
    ),
    StrategyPreset(
        id="underdogs_champion",
        name="Underdog's Champion",
        description="Take in the freshly abandoned, stay loyal, and HURT only when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Underdog's Champion.
* Recruit the freshly abandoned. When a pact breaks — betrayed, dropped, or a HELP left unanswered — reach that player the same turn. Do not waste talk on the paired.
* Say what they earn alone, what they earn with you, and that you will repay every turn.
* HELP them every turn.
* HURT them only on a round's last turn, and only when it wins you the round. If HELP already wins, or HURT still leaves you short of first, HELP.
* Never HURT a THIRD PARTY: it pays 0 and burns the reputation that brings the abandoned to you.""",
    ),
    StrategyPreset(
        id="kingslayer",
        name="Kingslayer",
        description="Partner whoever is winning, stay genuinely loyal, then take them down when it wins the round.",
        prompt=f"""{RANK_FRAMING}

Strategy: Kingslayer.
* Partner whoever is winning. Offer a real pact and mean it.
* HELP them every turn.
* HURT them only when it wins you the round. HELP pays you 6; HURT while they HELP you pays you 10 and costs them 8, so the gap closes eighteen points in one turn.
* Next round, whoever leads is your partner. No grudges.
* HOARD if you think your partner will HURT you.""",
    ),
    StrategyPreset(
        id="sandbagger",
        name="Sandbagger",
        description="Look harmless for most of the match, then take the last two rounds.",
        prompt=f"""{RANK_FRAMING}

Strategy: Sandbagger.
* Spend the early rounds buying trust, not points. HELP generously, keep every promise, and let others take the early rounds.
* Never HURT anyone in the first two thirds of the match, and say so out loud: point out, honestly, that you have never attacked.
* From the second-to-last round, cash in. Partner whoever is winning, HELP them all round, then HURT them on the final turn.
* If a round is out of reach even with the HURT, HELP instead. A wasted HURT costs you the partner you need for the final round.""",
    ),
    StrategyPreset(
        id="salvager",
        name="Salvager",
        description="Buy turns from players whose round is already lost — their help is nearly free.",
        prompt=f"""{RANK_FRAMING}

Strategy: Salvager.
* Every turn, split the table into players whose round is still live and players who are mathematically out of it. That second group is your whole strategy.
* Ask that group directly for HELP and say why it is cheap: their turn is about to be wiped and yours is not. Promise repayment next round, then pay it.
* Two players HELPing you in one turn pays 10; a clean pair banks 6. Players who are out are the only place that second helper comes from.
* When YOUR round is the dead one, run it in reverse: stop chasing points about to vanish and buy goodwill for the rounds that still matter.""",
    ),
]
