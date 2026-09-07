"""The cooperator-wins tiebreak chain (app/engine/finish_order.py).

A completed match is ranked by, in order: round wins, total score, HELP
received, HELP given, HURT received, FEWEST HURT given, then HOARD points.
Level on all seven, the win is shared — no winner recorded, no seat-order or
id-based key invented to force one out. See
docs/operations/what-shipped-and-why.md for the rule and the evidence it was
measured against.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.finish_order import FinishRecord, load_finish_records, placement_groups, winner
from app.engine.resolver import finalize_game
from app.games.hoard_hurt_help.game import HoardHurtHelp
from app.models import GameState
from app.read_models.leaderboard import (
    _compute_placement_tiers,
    _load_match_bundles,
    _merge_same_key_participants,
    _resolve_placement_key,
)
from tests.factories import add_submission, make_match, make_turn, seat_player

# Post the leaderboard's cutoff, so a seeded match is actually ranked.
_AFTER_CUTOFF = datetime(2026, 6, 10, tzinfo=timezone.utc)

_BASE_STATS = dict(
    round_wins=2.0,
    total_score=10,
    help_received=1,
    help_given=1,
    hurt_received=1,
    hurt_given=1,
    hoard_points=1,
)


def _record(player_id: int, **overrides: float | int) -> FinishRecord:
    values = {**_BASE_STATS, **overrides}
    return FinishRecord(
        player_id=player_id,
        seat_name=f"P{player_id}",
        round_wins=values["round_wins"],
        total_score=values["total_score"],
        help_received=values["help_received"],
        help_given=values["help_given"],
        hurt_received=values["hurt_received"],
        hurt_given=values["hurt_given"],
        hoard_points=values["hoard_points"],
    )


# --- (a) The chain: a tie broken at each of the five extra keys in turn ---


def test_chain_breaks_tie_at_help_received() -> None:
    game = HoardHurtHelp()
    a, b = _record(1, help_received=2), _record(2, help_received=1)
    top = winner([a, b], game)
    assert top is not None
    assert top.player_id == 1


def test_chain_breaks_tie_at_help_given() -> None:
    game = HoardHurtHelp()
    a, b = _record(1, help_given=2), _record(2, help_given=1)
    top = winner([a, b], game)
    assert top is not None
    assert top.player_id == 1


def test_chain_breaks_tie_at_hurt_received() -> None:
    game = HoardHurtHelp()
    a, b = _record(1, hurt_received=2), _record(2, hurt_received=1)
    top = winner([a, b], game)
    assert top is not None
    assert top.player_id == 1


def test_chain_breaks_tie_at_hurt_given_favoring_fewer() -> None:
    """Fewer HURT given wins — the sign flip in HoardHurtHelp.match_placement_key."""
    game = HoardHurtHelp()
    fewer_hurt, more_hurt = _record(1, hurt_given=1), _record(2, hurt_given=3)
    top = winner([fewer_hurt, more_hurt], game)
    assert top is not None
    assert top.player_id == 1


def test_chain_breaks_tie_at_hoard_points() -> None:
    game = HoardHurtHelp()
    a, b = _record(1, hoard_points=2), _record(2, hoard_points=1)
    top = winner([a, b], game)
    assert top is not None
    assert top.player_id == 1


# --- (b) Shared win: level on all seven keys ---


def test_shared_win_when_level_on_all_seven_keys() -> None:
    game = HoardHurtHelp()
    a, b = _record(1), _record(2)  # identical stats
    records = [a, b]

    assert winner(records, game) is None
    groups = placement_groups(records, game)
    assert len(groups) == 1
    assert {r.player_id for r in groups[0]} == {1, 2}


async def test_finalize_game_stores_no_winner_on_full_tie(db) -> None:
    match = await make_match(db, "M_SHARED", state=GameState.ACTIVE)
    a = await seat_player(db, match.id, "A", i=0)
    b = await seat_player(db, match.id, "B", i=1)
    a.total_round_wins = b.total_round_wins = 2.0
    a.total_round_score = b.total_round_score = 10
    await db.commit()

    await finalize_game(db, match, HoardHurtHelp())
    await db.refresh(match)

    assert match.state == GameState.COMPLETED
    assert match.winner_player_id is None


# --- (c) Agreement: finalize_game's winner == leaderboard's sole top tier ---


async def test_finalize_game_winner_matches_leaderboard_top_tier(db) -> None:
    """A match decided at key 3 (HELP received): finalize_game's recorded
    winner is the same competitor the leaderboard puts alone in the match's
    top placement tier — the DB-backed finish order and the leaderboard's
    read of the same match can't disagree.
    """
    match = await make_match(db, "M_AGREE", state=GameState.ACTIVE, scheduled_start=_AFTER_CUTOFF)
    a = await seat_player(db, match.id, "A", i=0)
    b = await seat_player(db, match.id, "B", i=1)
    c = await seat_player(db, match.id, "C", i=2)
    a.total_round_wins = b.total_round_wins = c.total_round_wins = 1.0
    a.total_round_score = b.total_round_score = c.total_round_score = 10
    await db.commit()

    # C HELPs A: A alone gains a HELP-received credit. Everything else (round
    # wins, total score, help given, hurt received/given, hoard points) stays
    # tied between A and B, so this is decided at key 3.
    turn = await make_turn(db, match.id, round=1, turn=1)
    await add_submission(db, turn, c, action="HELP", target_player_id=a.id)
    await db.commit()

    await finalize_game(db, match, HoardHurtHelp())
    await db.refresh(match)
    assert match.winner_player_id == a.id

    bundles = await _load_match_bundles(db)
    bundle = bundles[match.id]
    participants = _merge_same_key_participants(bundle.participants)
    placement_key = _resolve_placement_key("hoard-hurt-help")
    _, first_place_keys = _compute_placement_tiers(participants, placement_key)

    assert len(first_place_keys) == 1
    assert next(iter(first_place_keys)) == str(a.agent_id)


# --- (d) load_finish_records: what counts, precisely ---


async def test_load_finish_records_counts_correctly(db) -> None:
    """Ignores unresolved turns and defaulted HELP/HURT rows; still counts a
    defaulted HOARD's points (the score floor paid out either way)."""
    match = await make_match(db, "M_COUNT", state=GameState.ACTIVE)
    a = await seat_player(db, match.id, "A", i=0)
    b = await seat_player(db, match.id, "B", i=1)
    await db.commit()

    # Turn 1 (resolved): B HELPs A, A HURTs B — both count.
    t1 = await make_turn(db, match.id, round=1, turn=1)
    await add_submission(db, t1, b, action="HELP", target_player_id=a.id)
    await add_submission(db, t1, a, action="HURT", target_player_id=b.id)

    # Turn 2 (resolved): B's HELP is defaulted — must not count either way.
    # A's HOARD is also defaulted — its points still count.
    t2 = await make_turn(db, match.id, round=1, turn=2)
    await add_submission(db, t2, b, action="HELP", target_player_id=a.id, was_defaulted=True)
    await add_submission(db, t2, a, action="HOARD", points_delta=4, was_defaulted=True)

    # Turn 3 is UNRESOLVED: this HELP must not count at all.
    t3 = await make_turn(db, match.id, round=1, turn=3, resolved=False)
    await add_submission(db, t3, b, action="HELP", target_player_id=a.id)

    await db.commit()

    records = {r.seat_name: r for r in await load_finish_records(db, match.id)}

    assert records["A"].help_received == 1  # turn 1 only
    assert records["A"].hurt_given == 1
    assert records["A"].hoard_points == 4  # defaulted HOARD still counts
    assert records["B"].help_given == 1  # not 2 — defaulted + unresolved excluded
    assert records["B"].hurt_received == 1
