"""Unit tests for the Liar's Dice pure rules engine.

All tests are pure sync — no DB, no async, no fixtures required.
Run with:  pytest -q tests/test_liars_dice_engine.py
"""

from __future__ import annotations

import math
import random

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


# ===========================================================================
# parse_move
# ===========================================================================


class TestParseMove:
    """parse_move: valid shapes and every malformed case."""

    def test_challenge_lowercase(self) -> None:
        result = parse_move({"type": "challenge"})
        assert isinstance(result, ChallengeMove)

    def test_challenge_uppercase(self) -> None:
        result = parse_move({"type": "CHALLENGE"})
        assert isinstance(result, ChallengeMove)

    def test_bid_valid(self) -> None:
        result = parse_move({"type": "BID", "quantity": 3, "face": 5})
        assert isinstance(result, BidMove)
        assert result.quantity == 3
        assert result.face == 5

    def test_bid_face_1_aces(self) -> None:
        result = parse_move({"type": "BID", "quantity": 2, "face": 1})
        assert isinstance(result, BidMove)
        assert result.face == 1

    def test_bid_face_6(self) -> None:
        result = parse_move({"type": "BID", "quantity": 1, "face": 6})
        assert isinstance(result, BidMove)

    def test_not_a_dict(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move("BID")  # type: ignore[arg-type]
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_missing_type(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"quantity": 3, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_type_not_string(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": 42, "quantity": 3, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_unknown_type(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "FOLD"})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_missing_quantity(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_missing_face(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_quantity_not_int(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": "3", "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_not_int(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3, "face": "5"})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_quantity_float_rejected(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3.0, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_float_rejected(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3, "face": 5.0})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_quantity_bool_rejected(self) -> None:
        # bool is a subclass of int in Python, but we explicitly reject it.
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": True, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_bool_rejected(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3, "face": False})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_quantity_zero(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 0, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_quantity_negative(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": -1, "face": 5})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_zero(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3, "face": 0})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_seven(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 3, "face": 7})
        assert exc_info.value.code == "MALFORMED_MOVE"

    def test_bid_face_negative(self) -> None:
        with pytest.raises(GameError) as exc_info:
            parse_move({"type": "BID", "quantity": 1, "face": -1})
        assert exc_info.value.code == "MALFORMED_MOVE"


# ===========================================================================
# count_for
# ===========================================================================


class TestCountFor:
    """count_for: wild on/off, all faces including ace."""

    # --- wild=False ---

    def test_wild_off_exact(self) -> None:
        assert count_for(3, [1, 2, 3, 3, 3], wild=False) == 3

    def test_wild_off_no_aces_counted(self) -> None:
        # With wild=False, aces (1s) are NOT counted for face 3.
        assert count_for(3, [1, 1, 3, 3], wild=False) == 2

    def test_wild_off_face1_counts_only_ones(self) -> None:
        assert count_for(1, [1, 1, 2, 3], wild=False) == 2

    def test_wild_off_empty(self) -> None:
        assert count_for(4, [], wild=False) == 0

    def test_wild_off_none_match(self) -> None:
        assert count_for(6, [1, 2, 3, 4, 5], wild=False) == 0

    # --- wild=True, face != 1 ---

    def test_wild_on_face3_counts_aces_too(self) -> None:
        # 2 threes + 1 ace = 3 total.
        assert count_for(3, [1, 3, 3, 4], wild=True) == 3

    def test_wild_on_face2_no_match(self) -> None:
        # 0 twos + 2 aces = 2 total.
        assert count_for(2, [1, 1, 3, 4], wild=True) == 2

    def test_wild_on_all_aces(self) -> None:
        # face=5, no fives but 3 aces.
        assert count_for(5, [1, 1, 1], wild=True) == 3

    def test_wild_on_empty(self) -> None:
        assert count_for(4, [], wild=True) == 0

    # --- wild=True, face == 1 (no double-count) ---

    def test_wild_on_face1_no_double_count(self) -> None:
        # 2 aces — should return 2, not 4.
        assert count_for(1, [1, 1, 2, 3], wild=True) == 2

    def test_wild_on_face1_no_others_counted(self) -> None:
        # 0 aces, even though wild=True.
        assert count_for(1, [2, 3, 4, 5, 6], wild=True) == 0

    def test_wild_on_face1_all_ones(self) -> None:
        assert count_for(1, [1, 1, 1, 1], wild=True) == 4


# ===========================================================================
# resolve_showdown
# ===========================================================================


class TestResolveShowdown:
    """resolve_showdown: boundary (count==quantity) holds; one-below fails."""

    def test_exact_match_holds(self) -> None:
        # Exactly 3 twos, bid is (3, 2) — should hold.
        bid = Bid(quantity=3, face=2)
        holds, actual = resolve_showdown(bid, [2, 2, 2, 3, 4], wild=False)
        assert holds is True
        assert actual == 3

    def test_one_below_fails(self) -> None:
        bid = Bid(quantity=3, face=2)
        holds, actual = resolve_showdown(bid, [2, 2, 3, 4, 5], wild=False)
        assert holds is False
        assert actual == 2

    def test_above_holds(self) -> None:
        bid = Bid(quantity=2, face=2)
        holds, actual = resolve_showdown(bid, [2, 2, 2, 5, 6], wild=False)
        assert holds is True
        assert actual == 3

    def test_wild_on_exact_via_aces(self) -> None:
        # bid (3, 3): 2 threes + 1 ace = 3 total — holds exactly.
        bid = Bid(quantity=3, face=3)
        holds, actual = resolve_showdown(bid, [1, 3, 3, 4, 5], wild=True)
        assert holds is True
        assert actual == 3

    def test_wild_on_one_below_fails(self) -> None:
        # bid (4, 3): 2 threes + 1 ace = 3, one short.
        bid = Bid(quantity=4, face=3)
        holds, actual = resolve_showdown(bid, [1, 3, 3, 4, 5], wild=True)
        assert holds is False
        assert actual == 3

    def test_wild_on_ace_bid_no_double_count(self) -> None:
        # bid (2, 1): 2 aces — should hold.
        bid = Bid(quantity=2, face=1)
        holds, actual = resolve_showdown(bid, [1, 1, 3, 4], wild=True)
        assert holds is True
        assert actual == 2

    def test_wild_on_ace_bid_one_below_fails(self) -> None:
        # bid (3, 1): only 2 aces.
        bid = Bid(quantity=3, face=1)
        holds, actual = resolve_showdown(bid, [1, 1, 3, 4], wild=True)
        assert holds is False
        assert actual == 2

    def test_zero_dice_fails_any_positive_bid(self) -> None:
        bid = Bid(quantity=1, face=5)
        holds, actual = resolve_showdown(bid, [], wild=False)
        assert holds is False
        assert actual == 0


# ===========================================================================
# is_legal_raise
# ===========================================================================


class TestIsLegalRaise:
    """is_legal_raise: full table of opening, wild=False, and all ace transitions."""

    # --- Opening (prev=None) ---

    def test_opening_face2_ok(self) -> None:
        assert is_legal_raise(None, Bid(1, 2), wild=False) is True

    def test_opening_face6_ok(self) -> None:
        assert is_legal_raise(None, Bid(5, 6), wild=False) is True

    def test_opening_face1_illegal(self) -> None:
        # Cannot open on aces.
        assert is_legal_raise(None, Bid(1, 1), wild=False) is False

    def test_opening_face1_illegal_wild_on(self) -> None:
        assert is_legal_raise(None, Bid(2, 1), wild=True) is False

    def test_opening_face2_wild_on(self) -> None:
        assert is_legal_raise(None, Bid(1, 2), wild=True) is True

    def test_opening_quantity0_illegal(self) -> None:
        assert is_legal_raise(None, Bid(0, 3), wild=False) is False

    # --- wild=False: normal ordering ---

    def test_wild_off_quantity_up_legal(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(3, 3), wild=False) is True

    def test_wild_off_same_quantity_face_up_legal(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(2, 4), wild=False) is True

    def test_wild_off_same_quantity_same_face_illegal(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(2, 3), wild=False) is False

    def test_wild_off_quantity_down_illegal(self) -> None:
        assert is_legal_raise(Bid(3, 3), Bid(2, 3), wild=False) is False

    def test_wild_off_same_quantity_face_down_illegal(self) -> None:
        assert is_legal_raise(Bid(2, 5), Bid(2, 4), wild=False) is False

    def test_wild_off_face1_treated_as_ordinary(self) -> None:
        # wild=False: face 1 is just another face; quantity-up is fine.
        assert is_legal_raise(Bid(2, 1), Bid(3, 1), wild=False) is True

    def test_wild_off_face1_same_quantity_face_down_illegal(self) -> None:
        # wild=False: (2, 2) -> (2, 1) should be illegal (face goes down).
        assert is_legal_raise(Bid(2, 2), Bid(2, 1), wild=False) is False

    # --- wild=True: normal->normal ---

    def test_wild_on_normal_quantity_up(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(3, 3), wild=True) is True

    def test_wild_on_normal_same_qty_face_up(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(2, 5), wild=True) is True

    def test_wild_on_normal_same_qty_face_same_illegal(self) -> None:
        assert is_legal_raise(Bid(2, 3), Bid(2, 3), wild=True) is False

    def test_wild_on_normal_qty_down_illegal(self) -> None:
        assert is_legal_raise(Bid(3, 4), Bid(2, 4), wild=True) is False

    # --- wild=True: normal->aces ---

    def test_normal_to_aces_ceil_exact(self) -> None:
        # prev.quantity=6, ceil(6/2)=3 aces — legal at exactly 3.
        assert is_legal_raise(Bid(6, 4), Bid(3, 1), wild=True) is True

    def test_normal_to_aces_ceil_odd(self) -> None:
        # prev.quantity=5, ceil(5/2)=3 aces — legal at 3, illegal at 2.
        assert is_legal_raise(Bid(5, 4), Bid(3, 1), wild=True) is True
        assert is_legal_raise(Bid(5, 4), Bid(2, 1), wild=True) is False

    def test_normal_to_aces_above_ceil(self) -> None:
        assert is_legal_raise(Bid(4, 3), Bid(3, 1), wild=True) is True

    def test_normal_to_aces_below_ceil_illegal(self) -> None:
        # ceil(4/2)=2; quantity 1 aces is illegal.
        assert is_legal_raise(Bid(4, 3), Bid(1, 1), wild=True) is False

    def test_normal_to_aces_ceil_1(self) -> None:
        # prev.quantity=1, ceil(1/2)=1 ace — legal.
        assert is_legal_raise(Bid(1, 2), Bid(1, 1), wild=True) is True

    def test_normal_to_aces_ceil_2_exact(self) -> None:
        # prev.quantity=2, ceil(2/2)=1 ace — legal at 1.
        assert is_legal_raise(Bid(2, 5), Bid(1, 1), wild=True) is True

    def test_normal_to_aces_quantity_zero_illegal(self) -> None:
        assert is_legal_raise(Bid(2, 5), Bid(0, 1), wild=True) is False

    # --- wild=True: aces->normal ---

    def test_aces_to_normal_exact_2x_plus_1(self) -> None:
        # prev=(3,1) aces: need >= 2*3+1=7 for any normal face.
        assert is_legal_raise(Bid(3, 1), Bid(7, 2), wild=True) is True

    def test_aces_to_normal_below_2x_plus_1_illegal(self) -> None:
        # 2*3+1=7; 6 is not enough.
        assert is_legal_raise(Bid(3, 1), Bid(6, 2), wild=True) is False

    def test_aces_to_normal_above_2x_plus_1(self) -> None:
        assert is_legal_raise(Bid(3, 1), Bid(8, 5), wild=True) is True

    def test_aces_to_normal_prev1_boundary(self) -> None:
        # prev=(1,1): need >= 2*1+1=3.
        assert is_legal_raise(Bid(1, 1), Bid(3, 2), wild=True) is True
        assert is_legal_raise(Bid(1, 1), Bid(2, 2), wild=True) is False

    def test_aces_to_normal_prev2_boundary(self) -> None:
        # prev=(2,1): need >= 5.
        assert is_legal_raise(Bid(2, 1), Bid(5, 2), wild=True) is True
        assert is_legal_raise(Bid(2, 1), Bid(4, 6), wild=True) is False

    # --- wild=True: aces->aces ---

    def test_aces_to_aces_strictly_up(self) -> None:
        assert is_legal_raise(Bid(2, 1), Bid(3, 1), wild=True) is True

    def test_aces_to_aces_same_illegal(self) -> None:
        assert is_legal_raise(Bid(2, 1), Bid(2, 1), wild=True) is False

    def test_aces_to_aces_down_illegal(self) -> None:
        assert is_legal_raise(Bid(3, 1), Bid(2, 1), wild=True) is False


# ===========================================================================
# min_legal_raise
# ===========================================================================


class TestMinLegalRaise:
    """min_legal_raise: opening, mid-range, face==6 rollover, ceiling, ace cases,
    and invariant sweep."""

    # --- Opening ---

    def test_opening_returns_bid_1_2(self) -> None:
        result = min_legal_raise(None, 10, wild=False)
        assert result == Bid(quantity=1, face=2)

    def test_opening_wild_on_returns_bid_1_2(self) -> None:
        result = min_legal_raise(None, 10, wild=True)
        assert result == Bid(quantity=1, face=2)

    # --- wild=False mid-range ---

    def test_mid_face_increments(self) -> None:
        result = min_legal_raise(Bid(2, 3), 10, wild=False)
        assert result == Bid(quantity=2, face=4)

    def test_face6_rolls_quantity(self) -> None:
        # wild=False: face 1 is ordinary, so after (2,6) the minimum is (3,1).
        result = min_legal_raise(Bid(2, 6), 10, wild=False)
        assert result == Bid(quantity=3, face=1)

    def test_ceiling_returns_none(self) -> None:
        # At (total_dice, 6) there is no higher bid.
        result = min_legal_raise(Bid(10, 6), 10, wild=False)
        assert result is None

    def test_face6_quantity_ceiling_returns_none(self) -> None:
        result = min_legal_raise(Bid(5, 6), 5, wild=False)
        assert result is None

    # --- wild=True mid-range ---

    def test_wild_mid_face_increments(self) -> None:
        # (2,3): smallest raise is (2,4). One ace (ceil(2/2)=1) is legal but ranks
        # ABOVE every (2,x) — k aces sit near 2k normal dice — so it is NOT smaller.
        result = min_legal_raise(Bid(2, 3), 10, wild=True)
        assert result == Bid(quantity=2, face=4)

    def test_wild_face5_same_q_next_face(self) -> None:
        # (3,5): smallest raise is (3,6). Two aces (ceil(3/2)=2) rank above four 6s,
        # far above (3,6), so the normal step wins.
        result = min_legal_raise(Bid(3, 5), 10, wild=True)
        assert result == Bid(quantity=3, face=6)

    def test_wild_face6_quantity_rollover(self) -> None:
        # (4,6): here the ace switch IS the next step — two aces (ceil(4/2)=2) sit
        # exactly between four 6s and five 2s, so (2,1) is genuinely the smallest.
        result = min_legal_raise(Bid(4, 6), 10, wild=True)
        assert result == Bid(quantity=2, face=1)

    def test_wild_aces_to_aces_next(self) -> None:
        # prev=(2,1) two aces: aces->aces is (3,1); aces->normal is (5,2)=2*2+1.
        # Five 2s ranks below three aces, so the smallest raise is (5,2).
        result = min_legal_raise(Bid(2, 1), 10, wild=True)
        assert result == Bid(quantity=5, face=2)

    def test_wild_aces_to_aces_vs_normal(self) -> None:
        # prev=(4,1) four aces: aces->normal is (9,2)=2*4+1, which ranks below
        # five aces, so (9,2) is the smallest raise.
        result = min_legal_raise(Bid(4, 1), 10, wild=True)
        assert result == Bid(quantity=9, face=2)

    def test_wild_aces_ceiling_only_normal_reachable(self) -> None:
        # prev=(5,1) aces with total_dice=10: aces-next=(6,1)<=10, normal-next=(11,2)>10.
        result = min_legal_raise(Bid(5, 1), 10, wild=True)
        # Only aces-next (6,1) is in range.
        assert result == Bid(quantity=6, face=1)

    def test_wild_aces_ceiling_none(self) -> None:
        # prev=(5,1) with total_dice=5: aces-next=(6,1)>5, normal-next=(11,2)>5.
        result = min_legal_raise(Bid(5, 1), 5, wild=True)
        assert result is None

    def test_wild_normal_ceiling_only_ace_option(self) -> None:
        # prev=(5,6) total_dice=5: normal-next would be (6,2)>5.
        # ace option: ceil(5/2)=3 aces <=5, so that's it.
        result = min_legal_raise(Bid(5, 6), 5, wild=True)
        assert result == Bid(quantity=3, face=1)

    def test_wild_normal_ceiling_no_option(self) -> None:
        # prev=(5,6) total_dice=2: normal-next=(6,2)>2; ace=(3,1)>2 — None.
        result = min_legal_raise(Bid(5, 6), 2, wild=True)
        assert result is None

    # --- Invariant sweep ---

    def _all_bids(self, total: int) -> list[Bid | None]:
        """All bids including None (opening)."""
        bids: list[Bid | None] = [None]
        for q in range(1, total + 1):
            for f in range(1, 7):
                bids.append(Bid(quantity=q, face=f))
        return bids

    def test_invariant_sweep_wild_off(self) -> None:
        """Every non-None min_legal_raise result must satisfy is_legal_raise and <=total."""
        total = 8
        failures: list[str] = []
        for prev in self._all_bids(total):
            result = min_legal_raise(prev, total, wild=False)
            if result is None:
                continue
            if result.quantity > total:
                failures.append(f"prev={prev!r}: result quantity {result.quantity} > {total}")
            if not is_legal_raise(prev, result, wild=False):
                failures.append(f"prev={prev!r}: result {result!r} not legal")
        assert not failures, "\n".join(failures)

    def test_invariant_sweep_wild_on(self) -> None:
        """Every non-None min_legal_raise result must satisfy is_legal_raise and <=total."""
        total = 8
        failures: list[str] = []
        for prev in self._all_bids(total):
            result = min_legal_raise(prev, total, wild=True)
            if result is None:
                continue
            if result.quantity > total:
                failures.append(f"prev={prev!r}: result quantity {result.quantity} > {total}")
            if not is_legal_raise(prev, result, wild=True):
                failures.append(f"prev={prev!r}: result {result!r} not legal (wild=True)")
        assert not failures, "\n".join(failures)

    def test_invariant_sweep_minimality_wild_off(self) -> None:
        """For wild=False, no legal bid should be 'smaller' than min_legal_raise."""
        total = 6
        for prev in self._all_bids(total):
            minimum = min_legal_raise(prev, total, wild=False)
            if minimum is None:
                continue
            # All bids with smaller (quantity, face) that are legal should not exist.
            for q in range(1, minimum.quantity + 1):
                for f in range(1, 7):
                    if (q, f) >= (minimum.quantity, minimum.face):
                        break
                    candidate = Bid(quantity=q, face=f)
                    if is_legal_raise(prev, candidate, wild=False):
                        pytest.fail(
                            f"prev={prev!r}: {candidate!r} < minimum {minimum!r} but legal"
                        )

    def test_wild_on_normal_to_ace_ceil_large(self) -> None:
        # prev=(10,3): smallest raise is (10,4). Five aces (ceil(10/2)=5) are legal
        # but rank above ten 6s, so the normal step is smaller.
        result = min_legal_raise(Bid(10, 3), 20, wild=True)
        assert result == Bid(quantity=10, face=4)
        assert is_legal_raise(Bid(10, 3), Bid(10, 4), wild=True)


# ===========================================================================
# roll
# ===========================================================================


class TestRoll:
    """roll: determinism, range, length."""

    def test_deterministic_same_seed(self) -> None:
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        assert roll(5, rng1) == roll(5, rng2)

    def test_different_seeds_differ(self) -> None:
        rng1 = random.Random(1)
        rng2 = random.Random(9999)
        # Extremely unlikely to be identical.
        assert roll(10, rng1) != roll(10, rng2)

    def test_correct_length(self) -> None:
        rng = random.Random(0)
        assert len(roll(7, rng)) == 7

    def test_zero_dice(self) -> None:
        rng = random.Random(0)
        assert roll(0, rng) == []

    def test_all_values_in_range(self) -> None:
        rng = random.Random(123)
        dice = roll(200, rng)
        assert all(1 <= d <= 6 for d in dice)

    def test_all_faces_appear(self) -> None:
        # With 600 dice and seed=0 every face should appear.
        rng = random.Random(0)
        dice = roll(600, rng)
        assert set(dice) == {1, 2, 3, 4, 5, 6}

    def test_deterministic_sequence_exact(self) -> None:
        # Regression: pin the exact output for a small fixed seed.
        rng = random.Random(7)
        result = roll(6, rng)
        # Recompute expected with the same stdlib.
        rng2 = random.Random(7)
        expected = [rng2.randint(1, 6) for _ in range(6)]
        assert result == expected


# ===========================================================================
# Integration: parse + is_legal_raise round-trip
# ===========================================================================


class TestIntegration:
    """Quick round-trip: parse a BID and verify it can be tested for legality."""

    def test_bid_move_to_bid_legal(self) -> None:
        move = parse_move({"type": "BID", "quantity": 4, "face": 3})
        assert isinstance(move, BidMove)
        bid = Bid(quantity=move.quantity, face=move.face)
        prev = Bid(quantity=3, face=3)
        assert is_legal_raise(prev, bid, wild=False) is True

    def test_bid_move_to_bid_illegal(self) -> None:
        move = parse_move({"type": "BID", "quantity": 3, "face": 2})
        assert isinstance(move, BidMove)
        bid = Bid(quantity=move.quantity, face=move.face)
        prev = Bid(quantity=3, face=3)
        # Same quantity, lower face — illegal.
        assert is_legal_raise(prev, bid, wild=False) is False

    def test_challenge_move_type(self) -> None:
        move = parse_move({"type": "CHALLENGE"})
        assert isinstance(move, ChallengeMove)

    def test_full_round_wild(self) -> None:
        # Simulate a short ace-bidding sequence.
        opening = parse_move({"type": "BID", "quantity": 2, "face": 3})
        assert isinstance(opening, BidMove)
        prev = Bid(quantity=2, face=3)
        # Raise to aces: ceil(2/2)=1 ace — legal.
        ace_bid = Bid(quantity=1, face=1)
        assert is_legal_raise(prev, ace_bid, wild=True) is True
        # Back to normal: need >= 2*1+1=3.
        normal_back = Bid(quantity=3, face=2)
        assert is_legal_raise(ace_bid, normal_back, wild=True) is True
        # Showdown: 3 dice [1, 3, 3] -> count_for(2, ..., wild=True) = 1 ace = 1 < 3 — fails.
        holds, actual = resolve_showdown(normal_back, [1, 3, 3], wild=True)
        assert holds is False
        assert actual == 1  # one 2-matching die: 0 twos + 1 ace


# ===========================================================================
# Edge-case / regression tests
# ===========================================================================


class TestEdgeCases:
    """Miscellaneous boundary and regression tests."""

    def test_count_for_single_die_face_equals_bid(self) -> None:
        assert count_for(4, [4], wild=False) == 1

    def test_count_for_ace_wild_single_non_ace(self) -> None:
        assert count_for(3, [1], wild=True) == 1

    def test_resolve_showdown_returns_actual_count(self) -> None:
        bid = Bid(quantity=1, face=6)
        _, actual = resolve_showdown(bid, [1, 2, 3, 4, 5], wild=False)
        assert actual == 0

    def test_is_legal_raise_invalid_face_zero(self) -> None:
        assert is_legal_raise(Bid(1, 2), Bid(2, 0), wild=False) is False

    def test_is_legal_raise_invalid_face_seven(self) -> None:
        assert is_legal_raise(Bid(1, 2), Bid(2, 7), wild=False) is False

    def test_is_legal_raise_invalid_quantity_zero(self) -> None:
        assert is_legal_raise(Bid(1, 2), Bid(0, 3), wild=False) is False

    def test_min_legal_raise_face1_with_quantity_1_wild_on(self) -> None:
        # prev=(1,1) one ace: aces->normal is (3,2)=2*1+1, which ranks below two
        # aces, so the smallest raise is (3,2).
        result = min_legal_raise(Bid(1, 1), 10, wild=True)
        assert result == Bid(quantity=3, face=2)
        assert is_legal_raise(Bid(1, 1), Bid(3, 2), wild=True)

    def test_normal_to_aces_ceil_rounding(self) -> None:
        # prev=(7,3): ceil(7/2)=4 aces. Check boundary.
        assert is_legal_raise(Bid(7, 3), Bid(4, 1), wild=True) is True
        assert is_legal_raise(Bid(7, 3), Bid(3, 1), wild=True) is False

    def test_aces_to_normal_large(self) -> None:
        # prev=(4,1) aces: need >= 2*4+1=9.
        assert is_legal_raise(Bid(4, 1), Bid(9, 2), wild=True) is True
        assert is_legal_raise(Bid(4, 1), Bid(8, 6), wild=True) is False

    def test_ceil_formula_consistency(self) -> None:
        """ceil(q/2) == (q+1)//2 for all positive q."""
        for q in range(1, 50):
            assert math.ceil(q / 2) == (q + 1) // 2


# --- min_legal_raise minimality: independent guards added with the wild-ace fix ---
# These catch the prior bug where the wild-mode minimum jumped to an ace bid too
# early (e.g. "one 2" -> "one ace" instead of "one 3"). The legality suite above
# never caught it because the buggy answers were legal — just not the smallest.


@pytest.mark.parametrize(
    "wild,prev,expected",
    [
        # Smallest legal raises derived BY HAND from the Dudo rules, not from the
        # implementation — so a wrong implementation can't make the test agree.
        (True, (1, 2), (1, 3)),   # one 2 -> one 3 (NOT one ace)
        (True, (1, 6), (2, 2)),   # one 6 -> two 2s (one ace ranks above every (2,x))
        (True, (4, 3), (4, 4)),   # four 3s -> four 4s (aces sit after four 6s)
        (True, (2, 1), (5, 2)),   # two aces -> five 2s (2*2+1), which beats three aces
        (False, (1, 6), (2, 1)),  # no-wild: after (q,6) comes (q+1,1)
        (False, (3, 4), (3, 5)),  # no-wild: same quantity, next face
    ],
)
def test_min_legal_raise_returns_hand_verified_smallest(wild, prev, expected) -> None:
    assert min_legal_raise(Bid(*prev), total_dice=8, wild=wild) == Bid(*expected)


@pytest.mark.parametrize("wild", [True, False])
@pytest.mark.parametrize("total_dice", [3, 5, 8])
def test_min_legal_raise_is_truly_minimal(wild: bool, total_dice: int) -> None:
    """Returned raise must be legal AND no legal bid may be smaller than it.
    'Smaller' is measured by is_legal_raise (the rule), not by min_legal_raise's
    own logic — a non-circular minimality check."""
    starts: list[Bid | None] = [None]
    starts += [Bid(q, f) for q in range(1, total_dice + 1) for f in range(1, 7)]
    for prev in starts:
        result = min_legal_raise(prev, total_dice, wild=wild)
        legal = [
            Bid(q, f)
            for q in range(1, total_dice + 1)
            for f in range(1, 7)
            if is_legal_raise(prev, Bid(q, f), wild=wild)
        ]
        if not legal:
            assert result is None
            continue
        assert result is not None and is_legal_raise(prev, result, wild=wild)
        for other in legal:
            if other == result:
                continue
            assert is_legal_raise(result, other, wild=wild), (
                f"min_legal_raise({prev!r})={result!r} but {other!r} is legal and smaller "
                f"(wild={wild})"
            )
