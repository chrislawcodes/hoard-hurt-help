"""Pure rules tests for Liar's Dice."""

from __future__ import annotations

from random import Random

import pytest

from app.games.base import GameError
from app.games.liars_dice.engine import (
    Bid,
    BidMove,
    ChallengeMove,
    count_for,
    is_legal_raise,
    min_legal_raise,
    parse_move,
    resolve_showdown,
    roll,
)


def _rank(bid: Bid, *, wild: bool) -> tuple[int, int]:
    if not wild:
        return (bid.quantity, bid.face)
    if bid.face == 1:
        return (3 * bid.quantity, 0)
    return (bid.quantity + (bid.quantity - 1) // 2, bid.face)


def test_parse_move_accepts_the_two_move_shapes() -> None:
    assert parse_move({"type": "BID", "quantity": 3, "face": 5}) == BidMove(3, 5)
    assert parse_move({"type": "challenge"}) == ChallengeMove()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"type": "BID"},
        {"type": "BID", "quantity": 0, "face": 5},
        {"type": "BID", "quantity": 1, "face": "5"},
        {"type": "NOPE"},
    ],
)
def test_parse_move_rejects_malformed_shapes(raw) -> None:
    with pytest.raises(GameError) as exc:
        parse_move(raw)  # type: ignore[arg-type]
    assert exc.value.code == "MALFORMED_MOVE"


@pytest.mark.parametrize(
    "face,wild,expected",
    [
        (1, False, 1),
        (1, True, 1),
        (5, False, 2),
        (5, True, 3),
    ],
)
def test_count_for_honors_wild_ones(face: int, wild: bool, expected: int) -> None:
    dice = [1, 2, 5, 5]
    assert count_for(face, dice, wild=wild) == expected


def test_resolve_showdown_ties_hold_the_bid() -> None:
    bid = Bid(quantity=2, face=5)
    holds, actual = resolve_showdown(bid, [5, 1, 2], wild=False)
    assert holds is False
    assert actual == 1

    holds, actual = resolve_showdown(bid, [5, 5, 1], wild=False)
    assert holds is True
    assert actual == 2


@pytest.mark.parametrize("wild", [False, True])
def test_is_legal_raise_matches_the_rank_table(wild: bool) -> None:
    bids = [Bid(quantity=q, face=face) for q in range(1, 5) for face in range(1, 7)]
    for prev in [None, *bids]:
        for nxt in bids:
            expected = False
            if prev is None:
                expected = nxt.face in range(2, 7)
            else:
                expected = _rank(prev, wild=wild) < _rank(nxt, wild=wild)
            assert is_legal_raise(prev, nxt, wild=wild) is expected


@pytest.mark.parametrize("wild", [False, True])
@pytest.mark.parametrize("total_dice", [1, 2, 3, 4, 5, 6])
def test_min_legal_raise_is_always_legal(wild: bool, total_dice: int) -> None:
    bids = [None] + [Bid(quantity=q, face=face) for q in range(1, total_dice + 1) for face in range(1, 7)]
    for prev in bids:
        nxt = min_legal_raise(prev, total_dice, wild=wild)
        if nxt is None:
            continue
        assert is_legal_raise(prev, nxt, wild=wild)


def test_gotcha_wild_false_after_six_goes_to_one() -> None:
    assert min_legal_raise(Bid(1, 6), 3, wild=False) == Bid(2, 1)
    assert is_legal_raise(Bid(1, 6), Bid(2, 1), wild=False) is True


def test_gotcha_wild_true_ace_slots_between_normal_raises() -> None:
    assert is_legal_raise(Bid(1, 6), Bid(1, 1), wild=True) is True
    assert is_legal_raise(Bid(1, 1), Bid(2, 2), wild=True) is False
    assert is_legal_raise(Bid(1, 1), Bid(3, 2), wild=True) is True


def test_min_legal_raise_ceiling_returns_none() -> None:
    assert min_legal_raise(Bid(3, 6), 3, wild=False) is None
    assert min_legal_raise(Bid(2, 1), 2, wild=True) is None


def test_ace_switch_boundaries_match_canonical_dudo_rules() -> None:
    """Independent ace-rule check — expected values come from the spec's formulas
    (normal->aces needs ceil(q/2) aces; aces->normal needs 2*a+1), NOT from the
    engine's internal _bid_key. This catches a wrong ace formula that a test
    mirroring the implementation cannot."""
    import math

    # normal -> aces: the minimum legal ace quantity above (q, 6) is ceil(q/2).
    for q in range(1, 7):
        need = math.ceil(q / 2)
        assert is_legal_raise(Bid(q, 6), Bid(need, 1), wild=True) is True
        if need - 1 >= 1:
            assert is_legal_raise(Bid(q, 6), Bid(need - 1, 1), wild=True) is False

    # aces -> normal: the minimum legal normal quantity above (a, 1) is 2*a + 1.
    for a in range(1, 4):
        need = 2 * a + 1
        assert is_legal_raise(Bid(a, 1), Bid(need, 2), wild=True) is True
        # one less quantity (at the top normal face) must NOT be legal.
        assert is_legal_raise(Bid(a, 1), Bid(need - 1, 6), wild=True) is False

    # ace -> ace is plain strictly-higher quantity.
    assert is_legal_raise(Bid(1, 1), Bid(2, 1), wild=True) is True
    assert is_legal_raise(Bid(2, 1), Bid(2, 1), wild=True) is False


def test_roll_is_seeded_and_bounded() -> None:
    rng = Random(123)
    assert roll(5, rng) == [1, 3, 1, 4, 3]
    rng = Random(123)
    assert roll(-1, rng) == []
