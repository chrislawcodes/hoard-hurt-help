"""Trust scoring for bots."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from app.engine.game_records import ActionRecord

from .signals import TalkSignal


@dataclass(frozen=True)
class _TrustModel:
    help_last: int
    help_earlier: int
    mutual_help: int
    hurt_last: int
    hurt_earlier: int
    hurt_partner: int
    help_partner: int
    talk: float
    # Forgiveness window: how many rounds a betrayal is held against someone
    # before it fades to nothing. This is the only betrayal-specific dial — how
    # *hard* a betrayal lands is derived from this model's own hurt sensitivity
    # (`hurt_last`) below, so each personality is described once.
    forgive_rounds: int


# A betrayal (HURTing a helper) is just a much worse hurt, so its sting is a
# multiple of how much this personality already hates being hit (`hurt_last`):
# the full multiple when it is done to you, a smaller one when you only witness
# it against someone else. So a model that reacts hard to hits (bitter, twitchy)
# also reacts hard to betrayal, with no separate numbers to keep in sync.
BETRAYAL_SELF_FACTOR = 6
BETRAYAL_OTHER_FACTOR = 3

# Personalities differ in how long they hold a betrayal (`forgive_rounds`): open
# forgets fast, bitter ("Long Memory") holds on most of the match. Presets pair
# these models with strategies (see bot_presets.py), so different bots end up
# with different betrayal temperaments.
_MODELS: dict[str, _TrustModel] = {
    "open": _TrustModel(6, 3, 7, -4, -2, -1, 2, 1.5, 2),
    "even": _TrustModel(4, 2, 5, -6, -3, -2, 1, 1.0, 4),
    "careful": _TrustModel(2, 1, 3, -6, -3, -2, 1, 0.5, 5),
    "bitter": _TrustModel(3, 2, 4, -9, -5, -3, 1, 0.5, 7),
    "twitchy": _TrustModel(7, 4, 8, -10, -5, -3, 1, 1.0, 4),
}


def compute_trust_map(
    *,
    your_agent_id: str,
    all_agent_ids: Sequence[str],
    history: Sequence[ActionRecord],
    signals: Sequence[TalkSignal],
    trust_model: str,
) -> dict[str, int]:
    """Compute your trust in every other player from history + talk signals."""
    model = _MODELS.get(trust_model.lower(), _MODELS["even"])
    trust = {aid: 0 for aid in all_agent_ids if aid != your_agent_id}
    if not trust:
        return {}

    latest_rt = _latest_turn(history)
    latest_round = latest_rt[0] if latest_rt is not None else None

    # Direct action evidence.
    for record in history:
        if record.was_defaulted:
            continue
        actor = record.actor_id
        if actor == your_agent_id or actor not in trust:
            continue
        if record.target_id != your_agent_id:
            continue
        delta = 0
        if latest_rt is not None and (record.round, record.turn) == latest_rt:
            if record.action == "HELP":
                delta = model.help_last
            elif record.action == "HURT":
                delta = model.hurt_last
        elif latest_round is not None and record.round == latest_round:
            if record.action == "HELP":
                delta = model.help_earlier
            elif record.action == "HURT":
                delta = model.hurt_earlier
        trust[actor] = _clamp(trust[actor] + delta)

    # Mutual help: if you and another player HELPed each other in the same turn,
    # that relationship gets a stronger boost.
    mutuals = _mutual_help_partners(history, your_agent_id)
    for actor in mutuals:
        if actor in trust:
            trust[actor] = _clamp(trust[actor] + model.mutual_help)

    # Betrayal memory: a player who HURT a helper — you OR anyone else — is
    # remembered, and the hit fades over the rounds that follow at this bot's own
    # forgiveness rate. Betraying you personally stings most; a witnessed betrayal
    # is lighter. How hard it lands and how long it lasts both come from the trust
    # model, so a forgiving bot moves on while a bitter one keeps its distance.
    for attacker, victim, betray_round in _betrayals(history):
        if attacker == your_agent_id or attacker not in trust:
            continue
        rounds_since = max(0, (latest_round or betray_round) - betray_round)
        if rounds_since >= model.forgive_rounds:
            continue
        factor = BETRAYAL_SELF_FACTOR if victim == your_agent_id else BETRAYAL_OTHER_FACTOR
        base = model.hurt_last * factor
        penalty = round(base * (1 - rounds_since / model.forgive_rounds))
        trust[attacker] = _clamp(trust[attacker] + penalty)

    current_partner = _best_partner(trust)
    if current_partner is not None:
        for record in history:
            if record.was_defaulted:
                continue
            actor = record.actor_id
            if actor == your_agent_id or actor not in trust:
                continue
            if record.target_id != current_partner:
                continue
            if latest_rt is not None and (record.round, record.turn) == latest_rt:
                if record.action == "HELP":
                    trust[actor] = _clamp(trust[actor] + model.help_partner)
                elif record.action == "HURT":
                    trust[actor] = _clamp(trust[actor] + model.hurt_partner)

    # Talk nudges trust, but stays weaker than actions.
    for signal in signals:
        speaker = signal.speaker_id
        if speaker == your_agent_id or speaker not in trust:
            continue
        if signal.target_id is not None and signal.target_id != your_agent_id:
            continue
        if signal.kind in {"direct_mention", "cooperation_offer", "loyalty_claim"}:
            trust[speaker] = _clamp(trust[speaker] + _talk_delta(model, 1))
        elif signal.kind == "apology":
            trust[speaker] = _clamp(trust[speaker] + max(1, _talk_delta(model, 1)))
        elif signal.kind == "threat":
            trust[speaker] = _clamp(trust[speaker] - max(1, _talk_delta(model, 1)))

    # Broken expected mutual help: if someone made a cooperation offer and never
    # backed it with a HELP in the most recent turn, the offer is treated as a
    # small trust loss. This is intentionally narrow so it stays deterministic
    # without storing extra talk history.
    for signal in signals:
        speaker = signal.speaker_id
        if speaker == your_agent_id or speaker not in trust:
            continue
        if signal.kind != "cooperation_offer":
            continue
        if signal.target_id is not None and signal.target_id != your_agent_id:
            continue
        if latest_rt is None:
            continue
        backed = any(
            record.actor_id == speaker
            and record.target_id == your_agent_id
            and record.action == "HELP"
            and (record.round, record.turn) == latest_rt
            for record in history
            if not record.was_defaulted
        )
        if not backed:
            trust[speaker] = _clamp(trust[speaker] - 4)

    return trust


def _talk_delta(model: _TrustModel, base: int) -> int:
    return max(1, round(base * model.talk))


def _latest_turn(history: Sequence[ActionRecord]) -> tuple[int, int] | None:
    turns = [(a.round, a.turn) for a in history if not a.was_defaulted]
    return max(turns) if turns else None


def _mutual_help_partners(history: Sequence[ActionRecord], your_agent_id: str) -> set[str]:
    by_turn: dict[tuple[int, int], list[ActionRecord]] = defaultdict(list)
    for record in history:
        if not record.was_defaulted:
            by_turn[(record.round, record.turn)].append(record)

    partners: set[str] = set()
    for records in by_turn.values():
        helped = {
            (r.actor_id, r.target_id)
            for r in records
            if r.action == "HELP" and r.actor_id != r.target_id
        }
        for actor, target in helped:
            if actor == your_agent_id and target is not None and (target, actor) in helped:
                partners.add(target)
            if target == your_agent_id and (target, actor) in helped:
                partners.add(actor)
    return partners


def _betrayals(history: Sequence[ActionRecord]) -> list[tuple[str, str, int]]:
    """Every (attacker, victim, round) where the attacker HURT a player who was
    HELPing them that same turn — i.e. the attacker triggered the -8 betrayal.
    The round lets callers fade the memory over time.
    """
    by_turn: dict[tuple[int, int], list[ActionRecord]] = defaultdict(list)
    for record in history:
        if not record.was_defaulted:
            by_turn[(record.round, record.turn)].append(record)

    betrayals: list[tuple[str, str, int]] = []
    for (round_, _turn), records in by_turn.items():
        helped = {
            (r.actor_id, r.target_id)
            for r in records
            if r.action == "HELP" and r.target_id is not None
        }
        for r in records:
            if r.action == "HURT" and r.target_id is not None:
                # The victim HELPed the attacker this same turn → it's a betrayal.
                if (r.target_id, r.actor_id) in helped:
                    betrayals.append((r.actor_id, r.target_id, round_))
    return betrayals


def _best_partner(trust: dict[str, int]) -> str | None:
    trusted = [aid for aid, score in trust.items() if score > 0]
    if not trusted:
        return None
    top = max(trust[aid] for aid in trusted)
    best = [aid for aid in trusted if trust[aid] == top]
    if len(best) != 1:
        return None
    return best[0]


def _clamp(value: int) -> int:
    return max(-100, min(100, value))
