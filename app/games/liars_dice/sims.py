"""Deterministic bot decisions for Liar's Dice."""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Any

from app.games.liars_dice.engine import Bid, min_legal_raise


def _tail_probability(successes_needed: int, trials: int, p: float) -> float:
    if successes_needed <= 0:
        return 1.0
    if successes_needed > trials:
        return 0.0
    total = 0.0
    for k in range(successes_needed, trials + 1):
        total += math.comb(trials, k) * (p**k) * ((1 - p) ** (trials - k))
    return total


def _bid_probability(standing: dict[str, Any], my_dice: list[int], public_state: dict[str, Any]) -> float:
    wild = bool(public_state.get("wild_ones", True))
    quantity = int(standing.get("quantity", 0))
    face = int(standing.get("face", 0))
    known = sum(1 for die in my_dice if die == face or (wild and face != 1 and die == 1))
    remaining_needed = quantity - known
    unknown_dice = max(0, int(sum(public_state.get("dice_counts", {}).values())) - len(my_dice))
    p = 1 / 6 if face == 1 or not wild else 2 / 6
    return _tail_probability(remaining_needed, unknown_dice, p)


def _opening_bid(my_dice: list[int], public_state: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    wild = bool(public_state.get("wild_ones", True))
    counts = Counter(die for die in my_dice if 1 <= die <= 6)
    faces = [face for face in range(2, 7)]
    face = max(faces, key=lambda f: (counts.get(f, 0), f))
    quantity = max(1, counts.get(face, 0))
    if quantity < len(my_dice) and rng.random() < 0.2:
        quantity += 1
    if not wild and counts.get(1, 0) > counts.get(face, 0):
        face = 1
        quantity = max(1, counts.get(1, 0))
    return {"type": "BID", "quantity": quantity, "face": face}


def _advance_bid(
    standing: dict[str, Any], total_dice: int, wild: bool, rng: random.Random
) -> dict[str, Any]:
    prev = Bid(quantity=int(standing["quantity"]), face=int(standing["face"]))
    nxt = min_legal_raise(prev, total_dice, wild=wild)
    if nxt is None:
        return {"type": "CHALLENGE"}
    stronger = min_legal_raise(nxt, total_dice, wild=wild)
    if stronger is not None and rng.random() < 0.35:
        return {"type": "BID", "quantity": stronger.quantity, "face": stronger.face}
    return {"type": "BID", "quantity": nxt.quantity, "face": nxt.face}


def decide(public_state: dict[str, Any], my_dice: list[int], *, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    standing = public_state.get("standing_bid")
    wild = bool(public_state.get("wild_ones", True))
    total_dice = int(sum(public_state.get("dice_counts", {}).values()) or len(my_dice))
    if not isinstance(standing, dict):
        return _opening_bid(my_dice, public_state, rng)

    probability = _bid_probability(standing, my_dice, public_state)
    if probability < 0.33:
        return {"type": "CHALLENGE"}
    move = _advance_bid(standing, total_dice, wild, rng)
    if move["type"] == "CHALLENGE":
        return move
    if probability > 0.9 and rng.random() < 0.5:
        stronger = min_legal_raise(Bid(move["quantity"], move["face"]), total_dice, wild=wild)
        if stronger is not None:
            return {"type": "BID", "quantity": stronger.quantity, "face": stronger.face}
    return move
