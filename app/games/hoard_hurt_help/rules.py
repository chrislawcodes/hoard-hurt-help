"""Constants shipped to every agent and every player.

WHICH MODEL MEASURED IT IS PART OF THE MEASUREMENT. Every number below has been
tuned against played matches, and the two models we have run behave differently
enough that a figure measured on one can be actively misleading about the other.
Before trusting any "measured at X%" note in this file, check which model
produced it.

Claude Haiku 4.5 does not reliably follow a preset's instructions, and — more
importantly for tuning — it misprices this table. Measured 2026-08-26: told to
HURT someone every single turn, its Headhunter spent eleven turns cooperating
instead and NEVER ONCE considered hitting a player who was HELPing a third
party. Its stated model of attacking was "+3 from a hoarder" and "+14 betrayal";
the middle tier, which pays as much as a pact, was simply absent from its
reasoning. So a low attack rate on Haiku does NOT mean agents priced attacking
and declined. It usually means they did not see the move.

Claude Sonnet 5 follows the instructions and reads the table. Same eight presets,
same rules: eight of its rules scored 96-100% where Haiku managed 71-77%. It
also finds what the payoffs actually reward — in two matches, No Playbook, a
preset deliberately given NO strategy, independently chose to attack on 34-35 of
35 turns and finished at or near the top both times.

The practical consequence: Haiku matches are still useful for anything about
STRUCTURE (tie rates, round shape, how long a match stays live), because those
do not depend on any single payoff being priced correctly. They are not
trustworthy for tuning a payoff. Two agents given no instructions reaching the
same conclusion is worth more than ten matches of agents who could not see the
option.

The rest of this file's notes predate that finding. Where one cites an attack
rate from "ten measured matches", those were Haiku.
"""

import enum

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
# HOARD is a CONTESTED POT, not a flat payout: everyone who HOARDs on a turn
# splits this many points between them. Alone you take the lot; the more
# company you have, the thinner your slice. This is the game's temptation —
# the one move that pays best when nobody else copies you. Tune it here and
# nowhere else: the resolver, the viewer mirror, the replay legend and the
# rules text agents read all derive from `hoard_share`.
HOARD_POT_POINTS = 8
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 8  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
# Extra to the ATTACKER when they HURT a player HELPing them this turn. Sized
# from what a betrayal DESTROYS, not from what it feels like it should pay: the
# victim never partners with you again, so the move has to cover several turns of
# the pact income you just ended. At 14 a betrayal turn pays HELP_POINTS + 14 =
# 18, which is three normal pact turns (6 each) under the shipped FLAT_6 mode.
#
# Two bounds hold it there. It must exceed HOARD_POT_POINTS, or hoarding on your
# partner is always the better way to defect and the knife stays dead — that was
# the v7 bug, bonus 6 against a pot of 8. And it must stay under about 0.75 of a
# round (~28 points), or one betrayal wins a round on its own and the other four
# turns stop mattering.
BETRAYAL_BONUS = 14
# What a HURT pays the ATTACKER when the target is NOT helping them. At v8 this
# was zero in every such case, which made HURT a move you could only afford
# against someone who had already chosen to trust you — so a player with no
# partner and no share of the pot had literally nothing to do. Measured in
# M_6809: Sandbagger narrated its own defeat ("pacts lock 30 and claim the
# round-win split") and cooperated into last place, because the board offered it
# no alternative.
#
# The payout now scales with what the target was DOING, i.e. with how much they
# had on the table for you to take:
HURT_TAKE_HELPER = 5  # they were HELPing someone else — you interrupt that
HURT_TAKE_HOARDER = 2  # they were HOARDing — you take a cut of the grab
# ...and a target who was HURTing someone gets nothing taken, because they were
# producing nothing to take. See `hurt_take`, which is the single source.
#
# HURT_TAKE_HELPER SITS BELOW A PACT AGAIN (5 against 6), reverting the v10 rise
# to parity. This number has now been set from two different models' behaviour
# and it is worth being explicit about which evidence applies.
#
# v9 set it to 5 so an attack would stay the THIRD-best move: pot alone 8, pact
# 6, attack 5. v10 raised it to 6 because ten matches measured a 3.6-8.6% attack
# rate and the ordering looked like the cause — at 5 attacking was dominated, so
# agents rationally skipped it.
#
# EVERY ONE OF THOSE MATCHES RAN ON HAIKU, and Haiku misreads this exact payoff.
# Its Headhunter, told to attack every turn, spent eleven turns cooperating and
# never once considered hitting a HELPER; its stated model of attacking was "+3
# from a hoarder" and "+14 betrayal". The low attack rate was not agents pricing
# 5 correctly and declining. It was agents who did not know the option existed.
#
# On Sonnet, which does follow the table, 6 produced the opposite failure. Two
# matches: the field attacked 25% then 35%, the three pure cooperators took 78
# attacks between them and won nothing, and No Playbook — given no strategy at
# all — independently chose to attack on 34 of 35 turns and tied for the win,
# twice. At parity an attack pays what a pact pays AND costs the target 8, so it
# is not an equal choice, it is a strictly better one.
#
# The ordering argument that justified 6 counts income and ignores damage. That
# is why it read as "dominated" on paper while being dominant in play.
#
# Do not push this to 7 without a measured reason. Simulation puts the cliff
# exactly there: at 7 an attack beats a pact outright, cooperation falls by a
# third and winning scores drop from ~15 to ~11.
#
# HURT_TAKE_HOARDER drops 3 -> 2 alongside it, keeping the two tiers ordered and
# the gap between them.
# Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
# the pair's per-side total at MUTUAL_HELP_FLOOR: total =
# max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k). The floor was set to
# match the old flat HOARD payout of 2 — a fully farmed pact was meant to be worth
# no more than hoarding. HOARD is a shared pot now, so that equivalence is only
# exact when four players crowd the pot; the floor stays 2 as its own number.
MUTUAL_HELP_FLOOR = 2

# The three moves, in the canonical display order the insight engines tally them
# in: HOARD (keep), HELP (cooperate), HURT (attack). `GameModule.action_names()`
# returns this and move validation derives its accepted set from it, so the move
# vocabulary is stated once rather than as a tuple in one place and a set in
# another.
ACTIONS: tuple[str, ...] = ("HOARD", "HELP", "HURT")

# An in-round score never goes below this. Applied to the final per-player delta
# in `scoring.resolve_turn` and per-HURT in the viewer's mirror, and stated in the
# rules text below — all three from here, so a change lands everywhere at once.
SCORE_FLOOR = 0

# Match length a new match gets when its creator doesn't pick one. The rules text
# below is written from these two constants and `_apply_counts` rewrites it off the
# same two, so the numbers an agent reads can never drift from the numbers the
# scheduler runs. `HoardHurtHelp.config_defaults()` must return these — pinned by
# `test_pd_is_registered`.
DEFAULT_TOTAL_ROUNDS = 7
DEFAULT_TURNS_PER_ROUND = 5

# Which revision of the rules a match is played under. It is stamped onto the
# match row at creation and shipped to agents in every turn payload, and the rules
# text below is titled from it, so the version an agent is told cannot disagree
# with the rules it is handed.
#
# It versions the SHAPE of the rules — the actions, the payoff table, stacking,
# betrayal, the score floor. It deliberately does not move when a per-match knob
# moves: match length and `mutual_help_mode` are stored per match in their own
# columns, so they are already recorded exactly, and bumping this for them would
# make the version say a match is unlike another that differs only by a setting.
RULES_VERSION = "v11"


class MutualHelpMode(str, enum.Enum):
    """How much a mutual HELP pays each side, and whether that changes on repeat.

    The lever these exist to explore is cooperation-vs-betrayal, not
    cooperation-vs-hoarding. Betraying a helper pays the attacker
    HELP_POINTS + BETRAYAL_BONUS and costs the victim HURT_POINTS,
    mode-independent — every mode pays a betrayer the same. Betrayal out-pays
    every pact rate, including FLAT_8: the pull is on points, not only on rank.

    Note what sets a pact's value, because it is easy to misread: the pair total
    is the MODE's, not MUTUAL_HELP_BONUS on its own. HELP_POINTS + the bonus is
    8, and FLAT_6 — today's default — subtracts 2 to reach 6. So the pot is a
    temptation only under FLAT_6/FLAT_7; at FLAT_8 a solo pot and a pact both pay
    8 and the temptation disappears.
    """

    DECAY = "decay"  # 8, 7, 6 … floored at 2
    # Full bonus unless this same pair mutually helped on the PREVIOUS turn — a
    # one-turn cooldown, not a lifetime cap. Note a pair can still collect every
    # turn by alternating between two partners; the rule pushes players to keep
    # more than one alliance rather than making the bonus scarce.
    NO_REPEATS = "no_repeats"
    FLAT_8 = "flat_8"  # 8 every time — no decay, no floor
    FLAT_7 = "flat_7"  # 7 every time
    FLAT_6 = "flat_6"  # 6 every time — betrayal (10) out-pays the pact most


# The rule a NEW match gets when its creator doesn't pick one. Every create path
# defaults to this, so flipping it here is the whole switch. It deliberately does
# NOT reinterpret an existing match: each row stores the mode it was played
# under, and those rows are experiment results — relabelling one would corrupt a
# comparison rather than break something visibly.
DEFAULT_MUTUAL_HELP_MODE = MutualHelpMode.FLAT_6

# What a match row with NO mode recorded was actually played under. Rows predating
# the mode switch have `mutual_help_mode` NULL, and every one of them ran decay —
# so reading such a row is a question about HISTORY, not about today's rule.
#
# Keep this separate from DEFAULT_MUTUAL_HELP_MODE even when the two happen to
# agree. Collapsing them is how the rules an agent was shown drifted away from the
# rules its match was scored under: every "mode not supplied" path silently
# resolved to decay, including the one that renders the public
# /games/{game}/agent-instructions page, which advertised a decaying +8 pact while
# every new match paid a flat +6. The rule is: a MISSING ARGUMENT means "today's
# shipped rule" (DEFAULT_MUTUAL_HELP_MODE); a NULL COLUMN means "the rule that
# match was played under" (this one). `test_mode_defaults_are_not_collapsed` pins
# the distinction.
LEGACY_MUTUAL_HELP_MODE = MutualHelpMode.DECAY


_FLAT_TOTALS = {
    MutualHelpMode.FLAT_8: HELP_POINTS + MUTUAL_HELP_BONUS,
    MutualHelpMode.FLAT_7: HELP_POINTS + MUTUAL_HELP_BONUS - 1,
    MutualHelpMode.FLAT_6: HELP_POINTS + MUTUAL_HELP_BONUS - 2,
}


def mutual_help_value(
    mode: MutualHelpMode | str,
    repeats: int,
    *,
    repeated_last_turn: bool = False,
) -> int:
    """What a mutual HELP pays EACH side for one pair, right now.

    ``repeats`` is the pair's match-wide count of prior mutual helps (0 = first
    time); ``repeated_last_turn`` is whether they also did it on the immediately
    previous turn. Modes use one or the other — DECAY counts every prior time,
    NO_REPEATS only cares about the turn just gone, and the flat modes ignore
    both.

    Every mode's payout lives here so the resolver, the pre-move preview, the
    replay legend and the rules text can never drift apart — a preview promising
    a different number than the resolver pays would be invisible until someone
    checked the arithmetic by hand.
    """
    mode = MutualHelpMode(mode)
    if mode in _FLAT_TOTALS:
        return _FLAT_TOTALS[mode]
    if mode is MutualHelpMode.NO_REPEATS:
        return HELP_POINTS + (0 if repeated_last_turn else MUTUAL_HELP_BONUS)
    return max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - repeats)


def mode_uses_last_turn(mode: MutualHelpMode | str) -> bool:
    """True when the payout depends on the immediately previous turn only."""
    return MutualHelpMode(mode) is MutualHelpMode.NO_REPEATS


def mutual_help_legend(mode: MutualHelpMode | str) -> str:
    """The one-line Help description for the replay legend, for this mode.

    Built from the same payout function as the resolver so a legend can never
    advertise a number the game doesn't pay. The decay and flat_8 wordings are
    kept verbatim from before the modes existed — tooling greps for them to tell
    which rule a recorded match was played under.
    """
    mode = MutualHelpMode(mode)
    first = mutual_help_value(mode, 0)
    if mode is MutualHelpMode.DECAY:
        return f"mutual +{first} each, bonus decays each round"
    if mode is MutualHelpMode.NO_REPEATS:
        return f"mutual +{first} each, but not two turns in a row with the same partner"
    return f"mutual +{first} each, every time"


def hoard_share(hoarders: int) -> int:
    """What ONE hoarder takes when *hoarders* of them split the pot this turn.

    The single source for the HOARD payout — the resolver, the viewer's running
    mirror, the replay legend and the per-move feed chip all call this, so the
    number a spectator reads can never differ from the one the resolver paid.

    Integer division, so the remainder is simply not awarded (an 8-point pot
    split three ways pays 2 each and drops 2). That keeps every score a whole
    number, which the whole scoring path assumes.

    Returns 0 for a turn with no hoarders, so callers can ask without guarding.
    """
    if hoarders <= 0:
        return 0
    return HOARD_POT_POINTS // hoarders


def hoard_legend() -> str:
    """The one-line Hoard description for a replay legend.

    Sibling of `mutual_help_legend`, and here for the same reason: a legend must
    never advertise a number the resolver doesn't pay. These two lines were typed
    literals in two templates until the v6 payoff change moved HURT_POINTS and
    left both showing the old value to spectators.
    """
    return f"share of a +{HOARD_POT_POINTS} pot, split between everyone who Hoards"


def hurt_take(
    target_action: str | None,
    *,
    target_helps_attacker: bool,
    attackers_on_target: int = 1,
) -> int:
    """What ONE attacker gains from a HURT, given what the TARGET did this turn.

    The single source for the attacker's side of a HURT — resolver, viewer mirror,
    replay legend, per-move chip and the rules text agents read all call this, so
    the number a spectator sees can never differ from the one the resolver paid.

    Returns the attacker's DIRECT take only. On a betrayal that is
    ``BETRAYAL_BONUS``, not the full 18: the target's HELP still lands and is
    credited separately by the normal HELP payout, so adding it here would pay it
    twice. Every other tier is a pure take — the target is not helping the
    attacker, so nothing else arrives.

    ``attackers_on_target`` splits the take between everyone who hit the same
    player this turn, the same self-limiting shape `hoard_share` uses: mobbing one
    player is allowed, it just pays each attacker less. Integer division, so the
    remainder is simply not awarded.

    A target who is HURTing the attacker BACK is handled by the caller, not here —
    that pair blocks (see the rules text), so no take and no damage either way.
    """
    if attackers_on_target <= 0:
        return 0
    if target_helps_attacker:
        # NOT split. The bonus is earned from a relationship you built, not
        # grabbed off the table — so a third party piling onto the same victim
        # must not be able to halve it. Splitting it would also hand anyone a
        # cheap way to spoil someone else's betrayal by swinging at the victim.
        return BETRAYAL_BONUS
    if target_action == "HELP":
        take = HURT_TAKE_HELPER
    elif target_action == "HOARD":
        take = HURT_TAKE_HOARDER
    else:
        return 0  # they were HURTing someone — nothing on the table to take
    # These tiers ARE a grab off what the target had going, so several attackers
    # share one target's worth between them.
    return take // attackers_on_target


def hurt_blocks(target_action: str | None, target_of_target: int | str | None,
                attacker: int | str) -> bool:
    """True when the target is HURTing the attacker back, so both swings miss.

    Kept beside `hurt_take` because the two together are the whole of a HURT's
    outcome, and a caller that applied one without the other would pay a take on
    an attack that never landed.
    """
    return target_action == "HURT" and target_of_target == attacker


def hurt_legend() -> str:
    """The one-line Hurt description for a replay legend.

    The second number is the betrayal BONUS alone, matching the `+N betrayal`
    chip the turn feed renders — not the attacker's net for the turn, which also
    includes the help they still receive.
    """
    return (
        f"-{HURT_POINTS} to another; you take "
        f"+{HELP_POINTS + BETRAYAL_BONUS} betraying a helper, "
        f"+{HURT_TAKE_HELPER} off a helper, "
        f"+{HURT_TAKE_HOARDER} off a hoarder"
    )


def help_legend(mode: MutualHelpMode | str) -> str:
    """The one-line Help description for a replay legend, for this mode."""
    return f"+{HELP_POINTS} to another; {mutual_help_legend(mode)}"


def mode_needs_history(mode: MutualHelpMode | str) -> bool:
    """True when the payout depends on the pair's prior mutual helps.

    The flat modes don't, so their callers can skip the resolved-turn scan
    entirely rather than reading history they will ignore.
    """
    return MutualHelpMode(mode) not in _FLAT_TOTALS

# The mutual-help paragraph is the only part of the rules that varies with a
# match's mode. Each mode states its OWN payout in full — a player should never
# have to combine two bullets to work out what a mutual help is worth, and the
# flat modes must not carry decay/floor/reset language that doesn't apply to them.
_MUTUAL_HELP_DECAY = f"""- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each the first time a pair does it.
- **Mutual-help decays.** Each time the *same pair* repeats a mutual help in a match, the bonus drops by 1. So that pair's net falls +{HELP_POINTS + MUTUAL_HELP_BONUS}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 1}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 2}, … down to a floor of +{MUTUAL_HELP_FLOOR} each. The count is match-wide, not per round. Helping a *fresh* partner resets to +{HELP_POINTS + MUTUAL_HELP_BONUS} — farming one ally pays less over time than spreading pacts around."""

_MUTUAL_HELP_NO_REPEATS = f"""- **Mutual-help bonus — no repeats.** If A HELPs B and B HELPs A in the same turn, each gets an extra +{MUTUAL_HELP_BONUS} on top of the base +{HELP_POINTS} — net +{HELP_POINTS + MUTUAL_HELP_BONUS} each. But a pair cannot take that bonus **two turns in a row**: if the same two players mutually helped on the previous turn, this one pays the plain +{HELP_POINTS} each. Skip a turn with that partner and the full +{HELP_POINTS + MUTUAL_HELP_BONUS} is available again — so rotating between partners keeps paying, hammering the same one back-to-back does not."""


def _flat_mutual_help(total: int) -> str:
    return f"""- **Mutual-help bonus.** If A HELPs B and B HELPs A in the same turn, each gets a net +{total} that turn (the base +{HELP_POINTS} plus a +{total - HELP_POINTS} bonus). This is the same every time, however often a pair does it — no decay, no floor."""


_MUTUAL_HELP_SECTIONS = {
    MutualHelpMode.DECAY: _MUTUAL_HELP_DECAY,
    MutualHelpMode.NO_REPEATS: _MUTUAL_HELP_NO_REPEATS,
    **{m: _flat_mutual_help(total) for m, total in _FLAT_TOTALS.items()},
}


def _render_game_rules_text(
    *,
    mode: MutualHelpMode | str = DEFAULT_MUTUAL_HELP_MODE,
    total_rounds: int = DEFAULT_TOTAL_ROUNDS,
    turns_per_round: int = DEFAULT_TURNS_PER_ROUND,
) -> str:
    """Build the semantic rules body for one mode and one match length.

    Every number in the text is interpolated from a constant or an argument, so
    there is nothing to keep in step by hand. In particular the counts are
    rendered directly rather than rendered-at-defaults-then-string-replaced: the
    old `_apply_counts` had to know the exact phrasing of five sentences, so
    rewording any one of them would have silently stopped rewriting it and handed
    a custom-length match the *default* counts in its rules.

    The rules a player is shown MUST match what the resolver pays — both come from
    the same mode, and `test_rules_text_matches_payout` pins that they agree.
    """
    mutual_section = _MUTUAL_HELP_SECTIONS[MutualHelpMode(mode)]
    return f"""# Hoard-Hurt-Help — Official Rules ({RULES_VERSION})

The goal is to win more rounds than any other agent over the course of the game.

## Actions

In the act phase, choose exactly one action. You cannot target yourself.

- **HOARD** — You join this turn's pot. **Everyone who HOARDs splits +{HOARD_POT_POINTS} points between them**, rounded down: alone you take +{HOARD_POT_POINTS}, two of you take +{HOARD_POT_POINTS // 2} each, three take +{HOARD_POT_POINTS // 3} each, and so on. Hoarding pays best when nobody else does it.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — The target loses {HURT_POINTS} points. **What YOU gain depends on what your target does this same turn** — see "What a HURT pays you" below. Moves resolve simultaneously, so this is a read on what they are about to do.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks, but the payout is shared.** Multiple players HURTing the same target each contribute -{HURT_POINTS}, so the damage adds up. What they *gain* is split between them, rounded down — ganging up on one player costs them plenty, and pays each of you less.
{mutual_section}
- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, you gain an extra +{BETRAYAL_BONUS} bonus on top of the +{HELP_POINTS} help you still receive — so you net +{HELP_POINTS + BETRAYAL_BONUS} that turn. The player you HURT takes the normal -{HURT_POINTS}.
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## What a HURT pays you

Your target always loses {HURT_POINTS}. What **you** get depends on what they were doing — the more they had on the table, the more there is to take:

| Your target this turn | You gain |
| --- | --- |
| Is HELPing **you** (a betrayal) | **+{HELP_POINTS + BETRAYAL_BONUS}** — their help still lands, plus a +{BETRAYAL_BONUS} bonus. Never shared, even if others hit the same player. |
| Is HELPing **someone else** | **+{HURT_TAKE_HELPER}** |
| Is HOARDing | **+{HURT_TAKE_HOARDER}** |
| Is HURTing someone else | **0** — they were making nothing to take |
| Is HURTing **you** | **Blocked.** You both swing and both miss: no damage, no points, either way. |

So HURT is a read on your target, not a coin flip. Attacking a player who is about to attack you wastes both your turns.

## Score floor

Round scores are clipped at {SCORE_FLOOR}, so a HURT can only take a player down to {SCORE_FLOOR} — hitting someone already there does nothing to them. **You are still paid for the swing** at the rate in the table above, so the attack is not wasted on your side.

## Round and game structure

- A game has **{total_rounds} rounds**, each with **{turns_per_round} turns** ({total_rounds * turns_per_round} turns total).
- In-round score resets to {SCORE_FLOOR} at the start of every round.
- The player with the highest in-round score after turn {turns_per_round} wins the round and gets **1 round-win**. Ties split the round-win equally (1/N each).
- The player with the most round-wins after all {total_rounds} rounds wins the game.
- **Tiebreaker:** highest total in-round score summed across all rounds.

## Turn structure: talk, then act

Each turn has a talk phase followed by an act phase:

1. **Talk phase.** Broadcast one public message. Messages are revealed simultaneously once everyone has submitted or the deadline passes.
2. **Act phase.** After seeing all talk messages, choose your action. Actions resolve simultaneously.
"""


# The shipped rules — the exact text a brand-new match's agents are given. Kept as
# a module constant because callers and tests reference it directly. It tracks
# DEFAULT_MUTUAL_HELP_MODE, so it is never a snapshot of a rule the game has since
# moved off; `test_default_rules_text_is_the_shipped_mode` pins that.
GAME_RULES_TEXT = _render_game_rules_text(mode=DEFAULT_MUTUAL_HELP_MODE)


def make_game_rules_text(
    total_rounds: int = DEFAULT_TOTAL_ROUNDS,
    turns_per_round: int = DEFAULT_TURNS_PER_ROUND,
    *,
    mode: MutualHelpMode | str = DEFAULT_MUTUAL_HELP_MODE,
) -> str:
    """Return semantic game rules for this match's mutual-help mode and counts."""
    return _render_game_rules_text(
        mode=mode, total_rounds=total_rounds, turns_per_round=turns_per_round
    )
