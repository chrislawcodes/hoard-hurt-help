"""Per-strategy plan rules, registered in one place.

This is the structure that stops the bot subsystem's recurring copy-paste. Every
bot strategy is a ranked list of "do X to Y if Z, else fall through" rules. Before
this module each strategy was a hand-written ``if strategy == "...":`` block in
``choose_action_plan`` that re-typed the same ``BotPlan(intent, target, reason)
if cond else None`` idiom and the same trailing hoard fallback — so adding a bot
meant copying a block, and the duplication kept coming back.

Here a strategy is instead a small function that returns its ranked rules using
the shared row builders below (:func:`help_if`, :func:`hurt_if`, :func:`hoard`,
:func:`betray_if`, :func:`crowd_or`). It registers itself with
:func:`register_strategy` keyed by its internal id, and :func:`plan_for_strategy`
dispatches by id. A *new* bot adds one ``@register_strategy(...)`` function that
reuses these builders — it cannot copy an if-block because there is no if-chain
to copy.

The rules a strategy returns are exactly the ``list[BotPlan | None]`` the old
if-block produced (same intents, targets, reasons, and order), so behavior is
unchanged; only the way the list is assembled and selected moved here. The
runtime still turns intents into HELP/HURT/HOARD moves and validates them.

The shared "read the table" helpers a rule needs (best partner, recent helper,
runaway leader, cooperation offers, …) are bundled into :class:`PlanInputs` so a
rule body reads as a flat ranking, not a pile of re-derivations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from .signals import TalkSignal
from .types import BotContext, BotPlan, BotProfile

# A player trusted at or below this reads as a known traitor / hostile. Bots
# won't extend fresh cooperation to them (they may still HURT them). A personal
# betrayal — or, under the less forgiving trust models, a witnessed one — clears it.
HOSTILE_TRUST = -20

# When a runaway-leader rule fires. Most policing bots act at a 12-point gap;
# the standings-watchers (opportunist, endgame sniper) act sooner, at 8.
LEADER_GAP_GANG = 12
LEADER_GAP_CLAW = 8

# The turn from which the late-game / buzzer strategies switch behavior. Rounds
# run 7 turns, so 6 is "the last couple of turns" and 7 is the final turn.
LATE_GAME_TURN = 6
BUZZER_TURN = 7


@dataclass(frozen=True)
class PlanInputs:
    """Everything a strategy's rules read about the table this turn.

    Computed once by :func:`build_plan_inputs` and handed to every rule function,
    so a rule body is a flat ranking instead of re-deriving partners/helpers/etc.
    The ``*_fn`` callables are the seeded selectors from ``strategies.py``; they
    stay there (and stay byte-identical) and are injected here to avoid an import
    cycle and to keep the determinism behavior in its existing home.
    """

    context: BotContext
    profile: BotProfile
    trust_map: dict[str, int]
    partner: str | None
    strong_partner: str | None
    helper: str | None
    attacker: str | None
    aggressor: str | None
    leader: str | None
    leader_gap: int
    offers: list[str]
    most_hostile: str | None
    probe: str | None
    crowd: BotPlan | None


# A strategy is a function from the shared inputs to its ranked plan rows. ``None``
# entries are "this rule did not apply"; the runtime takes the first applicable,
# valid one.
StrategyRule = Callable[[PlanInputs], list[BotPlan | None]]

_REGISTRY: dict[str, StrategyRule] = {}


def register_strategy(strategy_id: str) -> Callable[[StrategyRule], StrategyRule]:
    """Register a strategy's rule function under its internal id.

    Adding a bot = writing one of these functions. It is a hard error to register
    the same id twice, so a copy-paste that forgets to rename is caught at import.
    """

    def _decorate(fn: StrategyRule) -> StrategyRule:
        if strategy_id in _REGISTRY:
            raise ValueError(f"strategy {strategy_id!r} is already registered")
        _REGISTRY[strategy_id] = fn
        return fn

    return _decorate


def registered_strategy_ids() -> frozenset[str]:
    """Every strategy id that has a rule function. The source of truth for
    ``VALID_STRATEGIES`` so the validator and the planner can never disagree."""
    return frozenset(_REGISTRY)


def plan_for_strategy(strategy_id: str, inputs: PlanInputs) -> list[BotPlan | None]:
    """Ranked plan rows for a strategy id, or the lone hoard fallback if unknown.

    Mirrors the old ``choose_action_plan``: an unrecognized strategy collapses to
    ``[hoard("fallback")]`` rather than raising, so a bad id degrades to safe play.
    """
    rule = _REGISTRY.get(strategy_id)
    if rule is None:
        return [hoard("fallback")]
    return rule(inputs)


# --- shared row builders ---------------------------------------------------
#
# These are the idioms every strategy used to re-type inline. A rule lists them
# in priority order; a row is dropped (``None``) when its target/condition is
# missing, exactly as the old ``BotPlan(...) if cond else None`` did.


def help_if(intent: str, target: str | None, reason: str) -> BotPlan | None:
    """A HELP-family plan row, kept only if it has a target."""
    return BotPlan(intent, target, reason) if target else None


def hurt_if(intent: str, target: str | None, reason: str, *, when: bool = True) -> BotPlan | None:
    """A HURT-family plan row, kept only if it has a target and ``when`` holds."""
    return BotPlan(intent, target, reason) if (target is not None and when) else None


def betray_if(target: str | None, reason: str) -> BotPlan | None:
    """The buzzer-betrayal row (a HURT on an expected helper)."""
    return BotPlan("betray_helper", target, reason) if target else None


def hoard(reason: str) -> BotPlan:
    """The always-applicable fallback row that ends every ranking."""
    return BotPlan("hoard_protect_score", None, reason)


def crowd_or(inputs: PlanInputs, fallback: BotPlan) -> BotPlan:
    """The crowd-follower's copied move, or ``fallback`` when there is no signal."""
    return inputs.crowd if inputs.crowd is not None else fallback


# --- strategy rules --------------------------------------------------------
#
# Each function returns the same ranked rows its old if-block did. The comments
# carry over the design rationale for each personality.


def _coalition_rows(i: PlanInputs) -> list[BotPlan | None]:
    return [
        help_if("keep_partner", i.strong_partner, "trusted partner"),
        help_if("test_offer", i.offers[0] if i.offers else None, "cooperation offer"),
        help_if("reward_helper", i.helper, "recent helper"),
        help_if("start_partnership", i.partner, "best partner"),
        hoard("fallback"),
    ]


@register_strategy("coalition_seeker")
def _coalition_seeker(i: PlanInputs) -> list[BotPlan | None]:
    return _coalition_rows(i)


@register_strategy("pragmatist")
def _pragmatist(i: PlanInputs) -> list[BotPlan | None]:
    # Plays Coalition Seeker all round, then betrays at the buzzer: on the
    # final turn it HURTs the partner it expects to still HELP it. Because the
    # target is helping, the HURT lands for the full betrayal damage (-8) while
    # it still pockets their +4 — a swing big enough to steal the round-win. It
    # keeps talking cooperatively that turn (a false-mode bluff) so the partner
    # doesn't see it coming. Falls back to hoarding if it has no likely helper.
    if i.context.turn >= BUZZER_TURN:
        betray_target = i.strong_partner or i.partner or i.helper
        return [
            betray_if(betray_target, "betray at the buzzer"),
            hoard("stop sharing"),
        ]
    return _coalition_rows(i)


@register_strategy("loyal_partner")
def _loyal_partner(i: PlanInputs) -> list[BotPlan | None]:
    # Commits to players who have actually helped it: keeps a proven partner,
    # reciprocates a fresh helper, defends the bond. With no partner yet, it
    # throws out the occasional test HELP to feel out who gives back — then
    # locks onto whoever reciprocates.
    return [
        help_if("keep_partner", i.strong_partner, "proven partner"),
        help_if("repair_trust", i.partner, "trusted partner"),
        help_if("reward_helper", i.helper, "reciprocate help"),
        hurt_if("punish_attacker", i.attacker, "defend the bond"),
        help_if("test_offer", i.probe, "feel out a partner"),
        hoard("fallback"),
    ]


@register_strategy("grudger")
def _grudger(i: PlanInputs) -> list[BotPlan | None]:
    # "Long Memory": remembers betrayal AND help. Punishes a fresh attacker,
    # then rewards a fresh helper, and still gangs up on a runaway leader.
    return [
        hurt_if("punish_attacker", i.attacker, "remembers betrayal"),
        help_if("reward_helper", i.helper, "remembers help"),
        hurt_if("hurt_leader", i.leader, "runaway leader", when=i.leader_gap >= LEADER_GAP_GANG),
        hurt_if("punish_attacker", i.most_hostile, "old grudge"),
        hoard("fallback"),
    ]


@register_strategy("leader_pressure")
def _leader_pressure(i: PlanInputs) -> list[BotPlan | None]:
    # A contender that polices the leader: it builds its own score through a
    # partnership to climb, but drops everything to hit anyone who runs away
    # with the game (12+ ahead of it).
    return [
        hurt_if("hurt_leader", i.leader, "runaway leader", when=i.leader_gap >= LEADER_GAP_GANG),
        help_if("keep_partner", i.strong_partner, "proven partner"),
        help_if("reward_helper", i.helper, "reciprocate help"),
        help_if("start_partnership", i.partner, "build to climb"),
        hoard("fallback"),
    ]


@register_strategy("opportunist")
def _opportunist(i: PlanInputs) -> list[BotPlan | None]:
    # Works the standings: cooperates when offered a deal OR when someone
    # actually helps it, and claws at the leader when it's falling behind.
    # (No more pointless rival-shoving; hoards when it's sitting pretty.)
    return [
        help_if("test_offer", i.offers[0] if i.offers else None, "took an offer"),
        help_if("reward_helper", i.helper, "reward real help"),
        hurt_if("hurt_leader", i.leader, "claw at the leader", when=i.leader_gap >= LEADER_GAP_CLAW),
        hoard("fallback"),
    ]


@register_strategy("endgame_sniper")
def _endgame_sniper(i: PlanInputs) -> list[BotPlan | None]:
    # Late game = the last couple of turns of the round (rounds are 7 turns).
    if i.context.turn >= LATE_GAME_TURN:
        return [
            hurt_if("hurt_leader", i.leader, "endgame", when=i.leader_gap >= LEADER_GAP_CLAW),
            help_if("keep_partner", i.partner, "late partner"),
            help_if("reward_helper", i.helper, "late helper"),
            hoard("fallback"),
        ]
    return [
        help_if("keep_partner", i.partner, "early partner"),
        help_if("reward_helper", i.helper, "helper"),
        hoard("fallback"),
    ]


@register_strategy("diplomat")
def _diplomat(i: PlanInputs) -> list[BotPlan | None]:
    # Experiment: rewards aggression. Helps whoever attacked someone last
    # turn (a bounty for hurting), then repays its own helpers.
    return [
        help_if("test_offer", i.aggressor, "reward the aggressor"),
        help_if("reward_helper", i.helper, "repay a helper"),
        hoard("fallback"),
    ]


@register_strategy("crowd_follower")
def _crowd_follower(i: PlanInputs) -> list[BotPlan | None]:
    # Sticks with anyone who helps it (so it can hold a partnership), but
    # otherwise just copies whatever the table did last turn.
    return [
        help_if("reward_helper", i.helper, "stick with a helper"),
        crowd_or(i, hoard("no crowd signal")),
    ]


def build_plan_inputs(
    context: BotContext,
    profile: BotProfile,
    trust_map: dict[str, int],
    signals: Sequence[TalkSignal],
    *,
    best_partner: Callable[..., str | None],
    most_hostile: Callable[..., str | None],
    probe_target: Callable[..., str | None],
    recent_helper: Callable[..., str | None],
    recent_attacker: Callable[..., str | None],
    recent_aggressor: Callable[..., str | None],
    cooperation_offers: Callable[..., list[str]],
    leader: Callable[..., str | None],
    leader_gap: Callable[..., int],
    should_probe: Callable[..., bool],
    crowd_plan: Callable[..., BotPlan | None],
) -> PlanInputs:
    """Read the table once into the bundle every rule consumes.

    The selectors are injected from ``strategies.py`` (their existing home) so the
    seeded-tiebreak determinism stays exactly where it was and there is no import
    cycle. The values computed here are the same ones the old top-of-function
    block computed, in the same way.
    """
    leader_id = leader(context)
    offers = [
        speaker
        for speaker in cooperation_offers(signals, context.your_agent_id)
        if trust_map.get(speaker, 0) > HOSTILE_TRUST
    ]
    return PlanInputs(
        context=context,
        profile=profile,
        trust_map=trust_map,
        partner=best_partner(context, profile, trust_map, minimum=20),
        strong_partner=best_partner(context, profile, trust_map, minimum=60),
        helper=recent_helper(context, trust_map),
        attacker=recent_attacker(context, trust_map),
        aggressor=recent_aggressor(context, trust_map),
        leader=leader_id,
        leader_gap=leader_gap(context, leader_id),
        offers=offers,
        most_hostile=most_hostile(context, profile, trust_map),
        probe=probe_target(context, profile, trust_map) if should_probe(context, profile) else None,
        crowd=crowd_plan(context),
    )
