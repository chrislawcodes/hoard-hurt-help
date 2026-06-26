"""Trust scoring for bots."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from app.engine.game_records import ActionRecord

from .signals import TalkSignal
from .strategies import latest_turn


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


_MODELS: dict[str, _TrustModel] = {
    "open": _TrustModel(6, 3, 7, -4, -2, -1, 2, 1.5),
    "even": _TrustModel(4, 2, 5, -6, -3, -2, 1, 1.0),
    "careful": _TrustModel(2, 1, 3, -6, -3, -2, 1, 0.5),
    "bitter": _TrustModel(3, 2, 4, -9, -5, -3, 1, 0.5),
    "twitchy": _TrustModel(7, 4, 8, -10, -5, -3, 1, 1.0),
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

    latest_rt = latest_turn(history)
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
