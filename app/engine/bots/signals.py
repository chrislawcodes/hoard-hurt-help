"""Deterministic signal extraction from public talk."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal, Sequence

from app.schemas.agent import TalkMessage

SignalKind = Literal[
    "direct_mention",
    "cooperation_offer",
    "loyalty_claim",
    "threat",
    "apology",
    "leader_warning",
]

_OFFER_WORDS = ("help", "partner", "ally", "mutual", "pact", "lane", "pair")
_LOYALTY_WORDS = ("stay", "stick", "loyal", "continue", "keep")
_THREAT_WORDS = ("hurt", "hit", "punish", "retaliate", "attack", "target", "coming for")
_APOLOGY_WORDS = ("sorry", "truce", "repair", "reset", "forgive")
_LEADER_WORDS = ("leader", "ahead", "runaway", "top score", "too far ahead")


@dataclass(frozen=True)
class TalkSignal:
    speaker_id: str
    kind: SignalKind
    target_id: str | None
    strength: int
    message: str


def extract_talk_signals(
    messages: Sequence[TalkMessage], *, all_agent_ids: Sequence[str], leader_id: str | None = None
) -> list[TalkSignal]:
    """Translate free text into a small deterministic signal set.

    We aggregate multiple messages per speaker, then emit at most one signal of
    each kind per speaker per turn.
    """
    grouped: dict[str, list[str]] = {}
    for msg in messages:
        grouped.setdefault(msg.agent_id, []).append(msg.message)

    signals: list[TalkSignal] = []
    for speaker_id, parts in grouped.items():
        text = " ".join(parts).strip()
        if not text:
            continue
        lowered = text.lower()
        seen: set[SignalKind] = set()
        mention_target = _first_mentioned_agent(text, all_agent_ids, exclude=speaker_id)
        direct_target = _first_mentioned_agent(text, all_agent_ids, exclude=None)

        if direct_target is not None and "direct_mention" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="direct_mention",
                    target_id=direct_target,
                    strength=1,
                    message=text,
                )
            )
            seen.add("direct_mention")

        if _has_words(lowered, _OFFER_WORDS) and mention_target is not None and "cooperation_offer" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="cooperation_offer",
                    target_id=mention_target,
                    strength=_strength(lowered, ("mutual", "pact", "alliance", "partner")),
                    message=text,
                )
            )
            seen.add("cooperation_offer")

        if _has_words(lowered, _LOYALTY_WORDS) and mention_target is not None and "loyalty_claim" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="loyalty_claim",
                    target_id=mention_target,
                    strength=_strength(lowered, ("loyal", "stay", "stick")),
                    message=text,
                )
            )
            seen.add("loyalty_claim")

        if _has_words(lowered, _THREAT_WORDS) and mention_target is not None and "threat" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="threat",
                    target_id=mention_target,
                    strength=_strength(lowered, ("coming for", "punish", "retaliate", "attack")),
                    message=text,
                )
            )
            seen.add("threat")

        if _has_words(lowered, _APOLOGY_WORDS) and "apology" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="apology",
                    target_id=mention_target,
                    strength=_strength(lowered, ("sorry", "truce", "repair", "reset")),
                    message=text,
                )
            )
            seen.add("apology")

        if _has_words(lowered, _LEADER_WORDS) and leader_id is not None and _mentions(text, leader_id) and "leader_warning" not in seen:
            signals.append(
                TalkSignal(
                    speaker_id=speaker_id,
                    kind="leader_warning",
                    target_id=leader_id,
                    strength=_strength(lowered, ("runaway", "too far ahead", "top score")),
                    message=text,
                )
            )
            seen.add("leader_warning")

    return signals


def _has_words(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def _mentions(text: str, agent_id: str) -> bool:
    return _mention_regex(agent_id).search(text) is not None


def _first_mentioned_agent(
    text: str, agent_ids: Sequence[str], *, exclude: str | None
) -> str | None:
    for aid in sorted(agent_ids, key=lambda s: (-len(s), s)):
        if exclude is not None and aid == exclude:
            continue
        if _mentions(text, aid):
            return aid
    return None


def _strength(text: str, strong_words: Sequence[str]) -> int:
    return 3 if any(word in text for word in strong_words) else 1


def _mention_regex(agent_id: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(agent_id)}(?![A-Za-z0-9_])")

