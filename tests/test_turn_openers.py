"""C2 characterization: the two turn-row openers diverge by design.

These pin the structural difference that makes C2 a NOT-a-true-duplicate: the
sequential opener (`SequentialDriver._open_actor_turn`) is a blind INSERT that
writes only `current_turn`, while the simultaneous opener
(`scheduler_turn_loop._open_turn`) is a get-or-create that writes BOTH
`current_round` and `current_turn`. A naive "unify into one opener" would make
the sequential path start writing `current_round` (or add a resume guard it
never had) — these tests fail if that happens.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.engine.scheduler_turn_loop import _open_turn
from app.engine.turn_drivers import SequentialDriver
from app.models import GameState, Match


async def _seed_game(db, *, current_round: int) -> Match:
    game = Match(
        id="G_C2",
        name="C2",
        state=GameState.ACTIVE,
        scheduled_start=datetime.now(timezone.utc),
        per_turn_deadline_seconds=60,
        current_round=current_round,
        current_turn=0,
    )
    db.add(game)
    await db.commit()
    return game


async def test_sequential_opener_does_not_touch_current_round(db) -> None:
    """C2-seq: blind INSERT, sets only current_turn, leaves current_round alone."""
    game = await _seed_game(db, current_round=5)
    turn = await SequentialDriver()._open_actor_turn(db, game, round_num=5, turn_num=3)
    assert turn.phase == "act"
    assert turn.round == 5 and turn.turn == 3
    assert game.current_turn == 3
    assert game.current_round == 5  # untouched by the opener (driver owns it)


async def test_simultaneous_opener_writes_round_and_is_get_or_create(db) -> None:
    """C2-sim: writes BOTH pointers and reuses the row on a resume."""
    game = await _seed_game(db, current_round=0)
    first = await _open_turn(db, game, round_num=2, turn_num=1)
    assert game.current_round == 2 and game.current_turn == 1
    assert first.phase == "talk"
    # Resume: a second call for the same (round, turn) returns the SAME row.
    second = await _open_turn(db, game, round_num=2, turn_num=1)
    assert second.id == first.id


async def test_talk_deadline_is_capped_below_the_match_window(db) -> None:
    """Talk gets the shorter TALK_DEADLINE_SECONDS even when the match allows more."""
    from app.engine.scheduler_turn_loop import TALK_DEADLINE_SECONDS

    game = await _seed_game(db, current_round=0)
    game.per_turn_deadline_seconds = 75  # act window; talk should still be capped
    await db.commit()
    turn = await _open_turn(db, game, round_num=1, turn_num=1)
    assert turn.phase == "talk"
    window = (turn.deadline_at - turn.opened_at).total_seconds()
    assert window == TALK_DEADLINE_SECONDS  # 45, not 75


async def test_act_phase_resets_to_the_full_match_window(db) -> None:
    """The talk->act transition restores the full per_turn_deadline_seconds."""
    from app.engine.scheduler_turn_loop import _begin_act_phase
    from app.engine.turn_clock import now_utc

    game = await _seed_game(db, current_round=0)
    game.per_turn_deadline_seconds = 75
    await db.commit()
    turn = await _open_turn(db, game, round_num=1, turn_num=1)
    await _begin_act_phase(db, game, turn)
    assert turn.phase == "act"
    remaining = (turn.deadline_at - now_utc()).total_seconds()
    assert 73 <= remaining <= 75  # ~75s, well above the 45s talk cap


def test_new_hoard_hurt_help_matches_default_to_a_75s_act_window() -> None:
    """Both creation paths give HHH a 75s act window (kept in sync)."""
    from app.games import get as get_game_module
    from app.routes.matches_user import _CREATE_DEFAULTS

    assert get_game_module("hoard-hurt-help").config_defaults().per_turn_deadline_seconds == 75
    assert _CREATE_DEFAULTS["per_turn_deadline_seconds"] == 75
