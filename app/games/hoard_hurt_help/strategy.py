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

The roster is Tit-for-Tat, Loyal Partner, Turncoat, Dealmaker, Underdog's
Champion, Sandbagger, Hoarder — in that order, because the join UI pre-selects
the first one. Seven is the practical ceiling: once the only route past the tie
is a betrayal, a strategy is fully described by who you partner and when you
strike, and those combinations are now covered. An eighth would duplicate one of
these — which is exactly why KINGSLAYER was retired at v8, see below. Grim
Trigger, Pavlov, Always Defect and Generous Tit-for-Tat were dropped rather than
rewritten: none of them can win. In M_6442 each either played out as plain
Tit-for-Tat or, for Always Defect, aimed at the highest-scoring opponent — the
player least likely to be helping it — so its attacks collected nothing and it
finished last on 12 points against a winner on 184. Dropping a preset does not
change agents that already exist; the prompt text is copied into the agent at
creation time.

Every surviving preset must have a route past the tie. Income in a turn is ``4N``
from helpers, plus whichever ONE move you make: a mutual pact, a betrayal of a
helper, or a share of the HOARD pot. Up to v6 the pot was a flat 2 that nobody
would ever choose, so every preset was a different bid for helpers and the roster
was variations on one idea.

The v8 payoffs put those three routes in a strict order, and every preset below
is written to it. When somebody is HELPing you, your turn is worth 6 for a pact,
HOARD_POT_POINTS plus their help for the pot (12 alone, 8 with company, less
after that), and HELP_POINTS + BETRAYAL_BONUS for betraying them (18). So
betrayal is the best single turn in the game, hoarding alone is second, and a
pact is the best REPEATABLE turn — the only one that still pays on turn thirty.
That ordering is the whole design: cooperate to earn, defect once to cash out,
and take the pot when nobody will deal with you.

Timing note, because three presets used to get it wrong: a HURT does the SAME
damage on every turn but the first. A round score is clipped at zero and nothing
else, so an early hit is only wasted when it would push the victim below zero —
after that, waiting changes nothing and merely risks the chance never coming
round again. Presets time a strike off the VICTIM'S SCORE ("as soon as the hit
will not drop them to zero"), never off the calendar.

Two routes remain rejected. Pure denial still fails, because HURTing a player who
is not helping you pays the attacker nothing however large HURT_POINTS grows — it
leaves you below the bystanders who did nothing. HURT_POINTS is deliberately held
at parity with a turn's income for that reason: raising it to 12 was tested and
turned two thirds of turns into a pile-on, halving winning scores, because denial
needs no precondition and so nothing rate-limits it. Betrayal is where the knife
belongs; it needs a willing victim, which limits it on its own. Courting two
backers at once still fails, since one HELP a turn rotated between two of them
pays each only 3 against the 6 any ordinary pact pays, so they leave.

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
match but credibility is spent once, so it hoards credibility and spends it late.
v8 roughly tripled what that saved-up strike is worth: a betrayal beats a pact
turn by 12 where it used to beat it by 4, which is about 0.43 of a round rather
than 0.14. It is the one preset that must NOT answer a betrayal in kind — hitting
back would spend the clean record it is saving, so its only early answer is to
stop helping the offender.

That last dead end is what shapes Underdog's Champion, and it is why the preset
keeps ONE partner rather than building a bloc. Its route past the tie is a
betrayal, the same route Turncoat uses, but the two differ in both halves of the
decision. Turncoat buys a partner's trust, spends it, and moves on to someone it
has not burned yet. The Champion picks a partner who
has just been abandoned and times the knife by the scoreboard, striking only in
the narrow band where +8 clears the leader but +6 does not. The target choice is
what makes it repeatable: a rescued partner's alternative is hoarding alone for
about 10 a round against roughly 25 with the Champion even counting the knife,
so unlike Turncoat's victim they have no better offer and come back.

Two presets now win by betrayal, and what separates them is WHO they partner and
WHEN they strike, not the strike itself — betraying anyone pays the attacker the
same, so the choice of victim is worth nothing to your own score and everything
to the other side of the table. Turncoat takes whatever partner will trust it and
burns through them one at a time. Sandbagger takes the leader, but only once, and
only after a whole match of provable honesty has made the leader willing.

KINGSLAYER WAS RETIRED AT v8, and the reason generalises — do not rebuild it.
Its rule was to court the current leader and betray them. Every measurement of it
failed the same way: it struck 0.3 times in a 35-move match and scored by hoarding
instead, because a profitable HURT needs the victim to be HELPing you that turn
and the leader is the player in the field with the least reason to. It is not a
tuning problem. Raising BETRAYAL_BONUS 6 -> 14 moved it from 0.60 round wins to
0.60; the best rebuild anyone could write (free knife plus denial only in a close
round) scored 0.07 against a plain cooperator's 0.49. The lesson is that
"befriend, then betray" only works when you may partner ANYONE — which is exactly
what Turncoat does, and why one works and the other cannot. The bare version of
Kingslayer's move — HURTing a leader who is NOT helping you — is the pure denial
already rejected above, and it stays rejected: HURT_POINTS is deliberately held
at parity with a turn's income so denial cannot pay, because denial has no
precondition and a profitable one lets the whole table mob the leader.

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
# into what is now Turncoat rather than back up here. That line has since gone
# too: at v8 a HURT does identical damage on every turn but the first, so there
# was no "late" for it to describe.
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
* If a partner betrays you, stop HELPing them, and HURT them as soon as the hit will not drop them to zero.
* Never give up a mutual bonus to answer a HURT from anyone else.
* In talk, ask for HELP, promise to repay, and warn that you always answer a betrayal.
* HOARD only on a turn when you have no partner.""",
    ),
    StrategyPreset(
        id="loyal_partner",
        name="Loyal Partner",
        description="Back one partner and never attack. Walk at once if attacked, slowly if ignored.",
        prompt=f"""{RANK_FRAMING}

Strategy: Loyal Partner.
* Pick one partner and HELP them every turn.
* Never HURT anyone.
* If a partner HURTs you, leave at once and never partner them again. Say why.
* Otherwise leave only if they fail to repay twice in a row, then pick a new partner.
* In talk, ask for HELP and promise to repay.""",
    ),
    StrategyPreset(
        id="turncoat",
        name="Turncoat",
        description="Befriend, betray, then start again with someone new.",
        prompt=f"""{RANK_FRAMING}

Strategy: Turncoat.
* Get a mutual HELP going with someone, and repay once so they trust you.
* HURT that partner as soon as the hit will not drop them to zero.
* Then start again with a player you have never betrayed.
* Never HURT a player who isn't HELPing you.
* HOARD any turn nobody will pact with you.""",
    ),
    StrategyPreset(
        id="dealmaker",
        name="Dealmaker",
        description="Offer a named swap every turn, and take the pot when nobody bites.",
        prompt=f"""{RANK_FRAMING}

Strategy: Dealmaker.
* Each turn, name one player in talk and offer a trade: you both HELP each other this turn.
* Rotate the offer until someone takes it, then keep dealing with whoever does.
* If nobody takes it, HOARD.
* Always keep a promise you made.
* If a partner betrays you, never deal with them again, and say so publicly.""",
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
* If a recruit betrays you, stop HELPing them, and HURT them as soon as the hit will not drop them to zero. Say so publicly.
* If they stop repaying, recruit someone else.""",
    ),
    StrategyPreset(
        id="sandbagger",
        name="Sandbagger",
        description="Play honest for most of the match, then spend that record late.",
        prompt=f"""{RANK_FRAMING}

Strategy: Sandbagger.
* Until the second-to-last round: HELP, repay every promise, and never HURT.
* Say often that you have never attacked anyone.
* If anyone betrays you before then, stop HELPing them and say why, but do not strike back — that would spend the record you are saving.
* From the second-to-last round: partner the leader, then HURT them as soon as the hit will not drop them to zero.
* HOARD any turn nobody will pact with you.""",
    ),
    StrategyPreset(
        id="hoarder",
        name="Hoarder",
        description="Live in the pot, and talk everyone else into pairing off.",
        prompt=f"""{RANK_FRAMING}

Strategy: Hoarder.
* HOARD by default, and never draw attention to it.
* In talk, push everyone else to pair up with each other, so the pot stays yours.
* If others start joining the pot, take a mutual HELP that turn instead.
* Keep one player willing to pact with you, and repay them whenever you use them.""",
    ),
]
