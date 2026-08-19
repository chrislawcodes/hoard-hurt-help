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
Champion, Kingslayer, Sandbagger, Hoarder — in that order, because the join UI
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

Every surviving preset must have a route past the tie, and at v7 there are three
rather than one. Income in a turn is ``4N`` from helpers, plus whichever ONE move
you make: a mutual pact, a betrayal of a helper, or a share of the HOARD pot. Up
to v6 the pot was a flat 2 that nobody would ever choose, so every preset was a
different bid for helpers and the roster was eight variations on one idea. The
contested pot adds a second economy — points you take rather than points you are
given — and a real defect move, because hoarding while a partner still HELPs you
now out-earns the pact you broke. Half the roster reaches for it.

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

Hoarder replaces Salvager, which asked players whose round was already lost for
cheap HELP on the grounds that they were giving up only a flat-2 hoard. The pot
makes that the most valuable move those players have, so the premise died with
the rule it rested on. Hoarder inherits the slot from the other side: it lives in
the pot and works the talk phase to keep everyone else paired off and out of it.
It is the only preset whose default move is not HELP, which is what makes the new
mechanic testable — a pot nobody reaches for would measure as no change at all,
the way the v6 knife did.

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
        description="Hold one mutual HELP. Move on from anyone who stops repaying.",
        prompt=f"""{RANK_FRAMING}

Strategy: Tit-for-Tat.
* Get a mutual HELP with one player and keep it.
* If they don't repay, move on. Return if they do.
* If a partner betrays you, HURT them next round.
* Never give up a mutual bonus to answer a HURT from anyone else.
* In talk, ask for HELP and promise to repay.
* Don't HOARD.""",
    ),
    StrategyPreset(
        id="loyal_partner",
        name="Loyal Partner",
        description="Back one partner and never attack. Leave only after two unpaid turns.",
        prompt=f"""{RANK_FRAMING}

Strategy: Loyal Partner.
* Pick one partner and HELP them every turn.
* Never HURT anyone.
* Leave only if they fail to repay twice in a row. Say why, then pick a new partner.
* In talk, ask for HELP and promise to repay.
* Don't HOARD.""",
    ),
    StrategyPreset(
        id="buzzer_beater",
        name="Buzzer-Beater",
        description="Take the pot when it's quiet, and strike on a round's last turn.",
        prompt=f"""{RANK_FRAMING}

Strategy: Buzzer-Beater.
* HELP and repay to keep helpers coming. Ask for HELP in talk every turn.
* HOARD whenever few others look likely to. Take the pot when it's uncontested.
* Never HURT before a round's last turn.
* On the last turn: if the leader is HELPing you, HURT them. Otherwise HOARD.
* Never HURT a player who isn't HELPing you.""",
    ),
    StrategyPreset(
        id="dealmaker",
        name="Dealmaker",
        description="Offer a named trade every turn, and take the pot when nobody bites.",
        prompt=f"""{RANK_FRAMING}

Strategy: Dealmaker.
* Each turn, name one player in talk and offer a trade: they HELP you now, you HELP them next turn.
* If nobody takes it, HOARD.
* Always keep a promise you made.""",
    ),
    StrategyPreset(
        id="underdogs_champion",
        name="Underdog's Champion",
        description="Recruit from the bottom of the table and never betray a recruit.",
        prompt=f"""{RANK_FRAMING}

Strategy: Underdog's Champion.
* Recruit from the bottom half of the standings. Reach them in talk.
* Sell the pact: it pays every turn, while the pot shrinks with every taker.
* HELP your recruit and never betray them.
* If they stop repaying, recruit someone else.""",
    ),
    StrategyPreset(
        id="kingslayer",
        name="Kingslayer",
        description="Get the leader to invest in you, then cash out immediately.",
        prompt=f"""{RANK_FRAMING}

Strategy: Kingslayer.
* Offer the leader a pact so they HELP you.
* HURT them the first turn they do.
* After the hit, HOARD until you land another pact.
* Never HELP a leader twice in a row.
* Each round, target whoever leads now.""",
    ),
    StrategyPreset(
        id="sandbagger",
        name="Sandbagger",
        description="Play honest for most of the match, then spend that record late.",
        prompt=f"""{RANK_FRAMING}

Strategy: Sandbagger.
* Until the second-to-last round: HELP, repay every promise, and never HURT.
* Say often that you have never attacked anyone.
* From the second-to-last round: partner the leader, then HURT them on a round's last turn.
* HOARD any turn nobody will pact with you.""",
    ),
    StrategyPreset(
        id="hoarder",
        name="Hoarder",
        description="Live in the pot, and talk everyone else into pairing off.",
        prompt=f"""{RANK_FRAMING}

Strategy: Hoarder.
* HOARD by default.
* In talk, push everyone else to pair up with each other, so the pot stays yours.
* If too many are hoarding, HURT the leader instead.""",
    ),
]
