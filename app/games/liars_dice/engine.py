"""Pure Liar's Dice rules.

No DB access, no async. Shared by the game module and the bot logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random
from typing import Any

from app.games.base import GameError


@dataclass(frozen=True)
class Bid:
    quantity: int
    face: int


@dataclass(frozen=True)
class BidMove:
    quantity: int
    face: int


@dataclass(frozen=True)
class ChallengeMove:
    pass


Move = BidMove | ChallengeMove


def parse_move(raw: dict[str, Any]) -> Move:
    if not isinstance(raw, dict):
        raise GameError("MALFORMED_MOVE", "move must be an object.")
    kind = raw.get("type")
    if not isinstance(kind, str):
        raise GameError("MALFORMED_MOVE", "move.type is required.")
    kind = kind.upper()
    if kind == "CHALLENGE":
        return ChallengeMove()
    if kind != "BID":
        raise GameError("MALFORMED_MOVE", "move.type must be BID or CHALLENGE.")
    quantity = raw.get("quantity")
    face = raw.get("face")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise GameError("MALFORMED_MOVE", "move.quantity must be a positive integer.")
    if not isinstance(face, int) or isinstance(face, bool):
        raise GameError("MALFORMED_MOVE", "move.face must be an integer.")
    return BidMove(quantity=quantity, face=face)


def count_for(face: int, all_dice: list[int], *, wild: bool) -> int:
    total = sum(1 for die in all_dice if die == face)
    if wild and face != 1:
        total += sum(1 for die in all_dice if die == 1)
    return total


def resolve_showdown(bid: Bid, all_dice: list[int], *, wild: bool) -> tuple[bool, int]:
    actual_count = count_for(bid.face, all_dice, wild=wild)
    return actual_count >= bid.quantity, actual_count


def _bid_key(bid: Bid, *, wild: bool) -> tuple[int, int]:
    if wild:
        if bid.face == 1:
            return (3 * bid.quantity, 0)
        return (bid.quantity + (bid.quantity - 1) // 2, bid.face)
    return (bid.quantity, bid.face)


def _is_valid_bid_shape(bid: Bid) -> bool:
    return bid.quantity >= 1 and 1 <= bid.face <= 6


def is_legal_raise(prev: Bid | None, nxt: Bid, *, wild: bool) -> bool:
    if not _is_valid_bid_shape(nxt):
        return False
    if prev is None:
        return nxt.face in range(2, 7)
    if not _is_valid_bid_shape(prev):
        return False
    return _bid_key(nxt, wild=wild) > _bid_key(prev, wild=wild)


def min_legal_raise(prev: Bid | None, total_dice: int, *, wild: bool) -> Bid | None:
    if prev is None:
        return Bid(1, 2)
    candidates: list[Bid] = []
    if wild:
        for quantity in range(1, total_dice + 1):
            candidates.extend(Bid(quantity, face) for face in range(2, 7))
            candidates.append(Bid(quantity, 1))
    else:
        for quantity in range(1, total_dice + 1):
            candidates.extend(Bid(quantity, face) for face in range(1, 7))
    legal = [bid for bid in candidates if is_legal_raise(prev, bid, wild=wild)]
    if not legal:
        return None
    return min(legal, key=lambda bid: _bid_key(bid, wild=wild))


def roll(n: int, rng: Random) -> list[int]:
    return [rng.randint(1, 6) for _ in range(max(0, n))]
