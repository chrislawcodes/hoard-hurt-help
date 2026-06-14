"""Liar's Dice — pure rules engine.

All functions are synchronous and dependency-free.  No database, no async, no
platform imports except GameError (used to signal illegal moves or bad input).

Dudo-style ace rules
--------------------
When wild=True the die face 1 (aces) acts as a wild card:
  - count_for(face, dice, wild=True) for face != 1: count face PLUS count of 1s.
  - count_for(1,   dice, wild=True): count only 1s (no double-count).

Bidding order (wild=True)
  normal->normal  : quantity up, OR same quantity + higher face (faces 2-6).
  normal->aces    : quantity >= ceil(prev_quantity / 2).
  aces  ->normal  : quantity >= 2*prev_quantity + 1.
  aces  ->aces    : quantity strictly up.

min_legal_raise ordering notes
-------------------------------
Within normal faces (2..6) bids are ordered lexicographically:
  (quantity, face).  Smallest legal successor to (q, f) is:
    (q, f+1) if f < 6, else (q+1, 2) — subject to total_dice ceiling.
For aces->normal the minimum successor quantity is 2*prev_quantity+1.
For normal->aces  the minimum successor quantity is ceil(prev_quantity/2).
For aces->aces    the minimum successor quantity is prev_quantity+1.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from app.games.base import GameError


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Bid:
    """A standing bid: `quantity` dice showing `face`."""

    quantity: int  # >= 1
    face: int  # 1..6 (1 = aces)


@dataclass(frozen=True)
class BidMove:
    """A player's move: raise the bid to (quantity, face)."""

    quantity: int
    face: int


@dataclass(frozen=True)
class ChallengeMove:
    """A player's move: challenge the current bid."""


Move = BidMove | ChallengeMove


# ---------------------------------------------------------------------------
# parse_move
# ---------------------------------------------------------------------------


def parse_move(raw: dict[str, Any]) -> Move:
    """Parse a raw move dict into a typed Move.

    Accepts::
        {"type": "BID", "quantity": 3, "face": 5}
        {"type": "CHALLENGE"}

    Raises:
        GameError("MALFORMED_MOVE", ...) on any shape/type/range error.
    """
    if not isinstance(raw, dict):
        raise GameError("MALFORMED_MOVE", "Move must be a JSON object.")

    move_type = raw.get("type")
    if not isinstance(move_type, str):
        raise GameError("MALFORMED_MOVE", "Move must include a string 'type' field.")

    upper = move_type.strip().upper()

    if upper == "CHALLENGE":
        return ChallengeMove()

    if upper == "BID":
        if "quantity" not in raw:
            raise GameError("MALFORMED_MOVE", "BID move missing 'quantity' field.")
        if "face" not in raw:
            raise GameError("MALFORMED_MOVE", "BID move missing 'face' field.")

        quantity = raw["quantity"]
        face = raw["face"]

        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise GameError(
                "MALFORMED_MOVE", f"BID 'quantity' must be an integer, got {type(quantity).__name__}."
            )
        if not isinstance(face, int) or isinstance(face, bool):
            raise GameError(
                "MALFORMED_MOVE", f"BID 'face' must be an integer, got {type(face).__name__}."
            )

        if quantity < 1:
            raise GameError("MALFORMED_MOVE", f"BID 'quantity' must be >= 1, got {quantity}.")
        if face < 1 or face > 6:
            raise GameError("MALFORMED_MOVE", f"BID 'face' must be 1..6, got {face}.")

        return BidMove(quantity=quantity, face=face)

    raise GameError("MALFORMED_MOVE", f"Unknown move type {move_type!r}. Expected 'BID' or 'CHALLENGE'.")


# ---------------------------------------------------------------------------
# count_for
# ---------------------------------------------------------------------------


def count_for(face: int, all_dice: list[int], *, wild: bool) -> int:
    """Count how many dice in `all_dice` satisfy the bid on `face`.

    When wild=True and face != 1: count dice equal to `face` PLUS aces (face 1).
    When wild=True and face == 1: count only aces (no double-count).
    When wild=False: count only dice equal to `face`.
    """
    exact = sum(1 for d in all_dice if d == face)
    if wild and face != 1:
        aces = sum(1 for d in all_dice if d == 1)
        return exact + aces
    return exact


# ---------------------------------------------------------------------------
# resolve_showdown
# ---------------------------------------------------------------------------


def resolve_showdown(bid: Bid, all_dice: list[int], *, wild: bool) -> tuple[bool, int]:
    """Resolve a challenge against `bid`.

    Returns (holds, actual_count) where holds=True means the bid was valid
    (actual >= quantity, including exact match).
    """
    actual = count_for(bid.face, all_dice, wild=wild)
    return actual >= bid.quantity, actual


# ---------------------------------------------------------------------------
# is_legal_raise
# ---------------------------------------------------------------------------


def is_legal_raise(prev: Bid | None, nxt: Bid, *, wild: bool) -> bool:
    """Return True iff `nxt` is a legal raise given `prev`.

    Opening bid (prev is None):
      - face must be 2..6 (aces not allowed on opening).
      - quantity >= 1.

    wild=False: standard ordering — quantity up, or same quantity + higher face.
    wild=True (Dudo ace rules):
      normal->normal (both faces != 1): quantity up OR same quantity + higher face.
      normal->aces   (nxt.face == 1):  nxt.quantity >= ceil(prev.quantity / 2).
      aces  ->normal (prev.face == 1): nxt.quantity >= 2 * prev.quantity + 1.
      aces  ->aces   (both face == 1): nxt.quantity > prev.quantity.
    """
    if nxt.quantity < 1 or nxt.face < 1 or nxt.face > 6:
        return False

    if prev is None:
        # Opening bid: aces not allowed; any quantity >= 1 on faces 2..6.
        return nxt.face in range(2, 7)

    if not wild:
        # Standard lexicographic ordering on (quantity, face).
        return nxt.quantity > prev.quantity or (
            nxt.quantity == prev.quantity and nxt.face > prev.face
        )

    # wild=True — Dudo ace rules.
    prev_is_ace = prev.face == 1
    nxt_is_ace = nxt.face == 1

    if not prev_is_ace and not nxt_is_ace:
        # normal->normal
        return nxt.quantity > prev.quantity or (
            nxt.quantity == prev.quantity and nxt.face > prev.face
        )

    if not prev_is_ace and nxt_is_ace:
        # normal->aces: must halve (ceiling).
        return nxt.quantity >= math.ceil(prev.quantity / 2)

    if prev_is_ace and not nxt_is_ace:
        # aces->normal: must more than double.
        return nxt.quantity >= 2 * prev.quantity + 1

    # aces->aces: strictly up.
    return nxt.quantity > prev.quantity


# ---------------------------------------------------------------------------
# min_legal_raise
# ---------------------------------------------------------------------------


def min_legal_raise(prev: Bid | None, total_dice: int, *, wild: bool) -> Bid | None:
    """Return the smallest legal bid above `prev`, or None at the ceiling.

    INVARIANT: any non-None result satisfies is_legal_raise(prev, result, wild=wild)
    and result.quantity <= total_dice.

    Ordering notes (wild=True, normal = face in 2..6):
      normal->normal : next is (q, face+1) if face<6, else (q+1, 2).
      normal->aces   : minimum is (ceil(q/2), 1).
                       But we also compare that against the next normal bid and
                       pick the smaller one — callers should consider both paths;
                       this function returns the absolute minimum legal bid.
                       For a single next-step advance we always try the two
                       successor candidates and pick the one with the smaller
                       (quantity, face) under the Dudo ordering convention:
                         aces at quantity k sort between normal (k-1, 6) and normal (k, 2).
      aces->normal   : minimum quantity is 2*prev.quantity+1, face starts at 2.
      aces->aces     : minimum quantity is prev.quantity+1.

    wild=False: standard lex order; opening always returns Bid(1,2).
    """
    if prev is None:
        return Bid(quantity=1, face=2)

    if not wild:
        # Lex order over (quantity, face) where face ranges 1..6.
        # Same quantity + next face first; if face is already 6, bump quantity
        # and start from face=1 (aces are ordinary in wild=False mode).
        if prev.face < 6:
            candidate = Bid(quantity=prev.quantity, face=prev.face + 1)
        else:
            next_q = prev.quantity + 1
            if next_q > total_dice:
                return None
            candidate = Bid(quantity=next_q, face=1)
        if candidate.quantity > total_dice:
            return None
        return candidate

    # wild=True
    prev_is_ace = prev.face == 1

    if prev_is_ace:
        # aces->aces: minimum is prev.quantity+1 aces.
        aces_next = prev.quantity + 1
        # aces->normal: minimum quantity is 2*prev.quantity+1, face=2.
        normal_next_q = 2 * prev.quantity + 1

        # Under Dudo conventions: aces at quantity k "slot in" above normal (k-1,6),
        # below normal (k, 2).  So the global minimum between aces-next and
        # normal-next is whichever has a smaller effective quantity.
        # normal-next requires normal_next_q dice; aces-next requires aces_next aces.
        # We compare raw quantities: the smallest valid bid wins.

        candidates: list[Bid] = []
        if aces_next <= total_dice:
            candidates.append(Bid(quantity=aces_next, face=1))
        if normal_next_q <= total_dice:
            candidates.append(Bid(quantity=normal_next_q, face=2))

        if not candidates:
            return None

        # Pick the candidate with the smaller quantity; ties break toward aces
        # (aces at k rank above normal at k-1, so a tie in quantity means normal wins).
        # Actually: aces(k) is ordered above normal(k-1,6) and below normal(k,2).
        # So if both candidates have the same quantity q, normal(q,2) < aces(q).
        # But here aces_next = prev.quantity+1 and normal_next_q = 2*prev.quantity+1
        # which can only collide when prev.quantity=0 (impossible), so no tie in practice.
        return min(candidates, key=lambda b: (b.quantity, 0 if b.face != 1 else 1))

    # prev is normal (face in 2..6)
    # Option 1: normal->normal next bid.
    if prev.face < 6:
        normal_next: Bid | None = Bid(quantity=prev.quantity, face=prev.face + 1)
    else:
        q = prev.quantity + 1
        normal_next = Bid(quantity=q, face=2) if q <= total_dice else None

    # Option 2: normal->aces (halve).
    ace_q = math.ceil(prev.quantity / 2)
    ace_candidate: Bid | None = Bid(quantity=ace_q, face=1) if ace_q <= total_dice else None

    # Both are legal by construction; pick the minimum.
    # Dudo ordering: aces(k) ranks above normal(k-1,6) and below normal(k,2).
    # Encode: aces(k) -> sort key (k, 1.5); normal(k, f) -> sort key (k, f).
    def _sort_key(b: Bid) -> tuple[int, float]:
        return (b.quantity, 1.5 if b.face == 1 else float(b.face))

    valid: list[Bid] = []
    if normal_next is not None and normal_next.quantity <= total_dice:
        valid.append(normal_next)
    if ace_candidate is not None:
        valid.append(ace_candidate)

    if not valid:
        return None

    result = min(valid, key=_sort_key)

    # Paranoia guard: confirm the invariant.
    assert is_legal_raise(prev, result, wild=wild), (
        f"min_legal_raise invariant broken: prev={prev!r} result={result!r}"
    )
    return result


# ---------------------------------------------------------------------------
# roll
# ---------------------------------------------------------------------------


def roll(n: int, rng: random.Random) -> list[int]:
    """Roll `n` dice using `rng`, returning values in 1..6."""
    return [rng.randint(1, 6) for _ in range(n)]
