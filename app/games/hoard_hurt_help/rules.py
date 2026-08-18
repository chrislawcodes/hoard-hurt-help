"""Constants shipped to every agent and every player."""

import enum

# Point values — single source of truth for the resolver (app/engine/resolver.py)
# and the watch view's per-move effect display (app/routes/web.py).
HOARD_POINTS = 2  # HOARD: actor gains this, no target
HELP_POINTS = 4  # HELP: target gains this, actor gains 0
HURT_POINTS = 8  # HURT: target loses this, actor gains 0
MUTUAL_HELP_BONUS = 4  # extra to each side on a pair's FIRST mutual HELP this match
BETRAYAL_BONUS = 6  # extra to the ATTACKER when they HURT a player HELPing them this turn
# Mutual help decays -1 each time the SAME pair repeats it within a match, flooring
# the pair's per-side total at MUTUAL_HELP_FLOOR (= HOARD_POINTS, so a farmed pact is
# no better than hoarding): total = max(MUTUAL_HELP_FLOOR, HELP_POINTS + MUTUAL_HELP_BONUS - k).
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
RULES_VERSION = "v6"


class MutualHelpMode(str, enum.Enum):
    """How much a mutual HELP pays each side, and whether that changes on repeat.

    The lever these exist to explore is cooperation-vs-betrayal, not
    cooperation-vs-hoarding. Betraying a helper pays the attacker
    HELP_POINTS + BETRAYAL_BONUS (= 10) and costs the victim HURT_POINTS (= 8),
    mode-independent — every mode pays a betrayer the same 10. Betrayal now
    out-pays every pact rate, including FLAT_8: the pull is on points, not only
    on rank. Under today's default FLAT_6 a knife swings 18 points between the
    two players in one turn (attacker 6 -> 10, victim 6 -> -8), which is what
    makes a last-turn betrayal able to decide a round.
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


def hoard_legend() -> str:
    """The one-line Hoard description for a replay legend.

    Sibling of `mutual_help_legend`, and here for the same reason: a legend must
    never advertise a number the resolver doesn't pay. These two lines were typed
    literals in two templates until the v6 payoff change moved HURT_POINTS and
    left both showing the old value to spectators.
    """
    return f"+{HOARD_POINTS} to yourself"


def hurt_legend() -> str:
    """The one-line Hurt description for a replay legend.

    The second number is the betrayal BONUS alone, matching the `+N betrayal`
    chip the turn feed renders — not the attacker's net for the turn, which also
    includes the help they still receive.
    """
    return (
        f"-{HURT_POINTS} to another; "
        f"+{BETRAYAL_BONUS} to you if betraying a helper"
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
- **Mutual-help decays.** Each time the *same pair* repeats a mutual help in a match, the bonus drops by 1. So that pair's net falls +{HELP_POINTS + MUTUAL_HELP_BONUS}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 1}, +{HELP_POINTS + MUTUAL_HELP_BONUS - 2}, … down to a floor of +{MUTUAL_HELP_FLOOR} each (no better than HOARD). The count is match-wide, not per round. Helping a *fresh* partner resets to +{HELP_POINTS + MUTUAL_HELP_BONUS} — farming one ally pays less over time than spreading pacts around."""

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

- **HOARD** — You gain +{HOARD_POINTS} points.
- **HELP [target]** — You gain 0 points; the target gains +{HELP_POINTS} points.
- **HURT [target]** — You gain 0 points; the target loses {HURT_POINTS} points.

## Stacking and combos

- **HELP stacks.** Multiple players HELPing the same target each contribute +{HELP_POINTS}.
- **HURT stacks.** Multiple players HURTing the same target each contribute -{HURT_POINTS}.
{mutual_section}
- **Betraying a helper.** If you HURT a player who is HELPing *you* on the same turn, you gain an extra +{BETRAYAL_BONUS} bonus on top of the +{HELP_POINTS} help you still receive — so you net +{HELP_POINTS + BETRAYAL_BONUS} that turn. The player you HURT takes the normal -{HURT_POINTS}. Net swing: attacker +{HELP_POINTS + BETRAYAL_BONUS} / victim -{HURT_POINTS}. (Moves resolve simultaneously, so this is a read on whether your target will help you.)
- HELP and HURT against the same target both resolve; the target's score moves by the net.

## Score floor

Round scores are clipped at {SCORE_FLOOR}. HURTing a player already at {SCORE_FLOOR} still costs the attacker their turn but has no effect on the target.

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

DEFAULT_MISSED_MESSAGE = "I did not submit a turn."


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
