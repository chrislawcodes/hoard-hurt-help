"""Tests for poll pacing — how soon the server tells the play loop to ask again.

``pace_idle`` is pure, so most of this is fast table-driven unit testing. The goal
it protects: ask as rarely as possible without missing a turn, paced off the
soonest game, with naps capped so they never overshoot a start.
"""

from __future__ import annotations

from app.engine.agent_idle import (
    LONG_POLL_HOLD_SECONDS,
    LONG_POLL_LEAD_SECONDS,
    NEAR_START_WINDOW_SECONDS,
    POLL_IN_PLAY_SECONDS,
    POLL_NEAR_START_SECONDS,
    POLL_WAITING_SECONDS,
    IdleStatus,
    pace_idle,
)


def _idle(
    *,
    has_game: bool = False,
    has_live_game: bool = False,
    seconds_to_next_start: int | None = None,
) -> IdleStatus:
    return IdleStatus(
        has_game=has_game,
        has_live_game=has_live_game,
        seconds_to_next_start=seconds_to_next_start,
        idle_seconds=0,
        should_stop=False,
        stop_reason=None,
    )


def test_live_game_long_polls():
    """In a live game a turn could open any second: hold the line, re-ask soon."""
    hold, nxt = pace_idle(_idle(has_game=True, has_live_game=True))
    assert hold == LONG_POLL_HOLD_SECONDS
    assert nxt == POLL_IN_PLAY_SECONDS


def test_no_game_at_all_waits_five_minutes_no_hold():
    """Nothing scheduled → no long-poll, the cheap 5-minute waiting cadence."""
    hold, nxt = pace_idle(_idle(has_game=False))
    assert hold == 0.0
    assert nxt == POLL_WAITING_SECONDS


def test_final_minute_before_start_long_polls_early():
    """Inside the long-poll lead, hold the line so the opening turn is caught
    instantly — its 60s deadline can't be missed by a nap."""
    hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=30))
    assert hold == LONG_POLL_HOLD_SECONDS
    assert nxt == POLL_IN_PLAY_SECONDS


def test_within_five_minutes_checks_about_every_minute():
    """A game ~4 minutes out gets the ~1-minute cadence, no hold."""
    hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=240))
    assert hold == 0.0
    assert nxt == POLL_NEAR_START_SECONDS


def test_far_off_game_waits_five_minutes():
    """A game 25 minutes out gets the cheap 5-minute cadence."""
    hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=1500))
    assert hold == 0.0
    assert nxt == POLL_WAITING_SECONDS


def test_far_nap_capped_so_it_lands_on_the_near_start_window():
    """Just outside 5 minutes: nap shrinks so the next ask lands in (or before)
    the near-start window — never sleeping past it."""
    seconds = NEAR_START_WINDOW_SECONDS + 90  # 6.5 min out
    hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=seconds))
    assert hold == 0.0
    # Capped so we don't overshoot: next ask is at/inside the near-start window.
    assert seconds - nxt <= NEAR_START_WINDOW_SECONDS


def test_near_start_nap_capped_so_it_lands_on_the_long_poll_lead():
    """Just inside 5 minutes: nap shrinks so the next ask lands in (or before) the
    long-poll lead window — so we're holding the line before the start."""
    seconds = LONG_POLL_LEAD_SECONDS + 30  # 90s out
    hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=seconds))
    assert hold == 0.0
    assert seconds - nxt <= LONG_POLL_LEAD_SECONDS


def test_naps_never_go_below_the_in_play_floor():
    """Even at a lane boundary the wait never drops to a money-burning sub-second."""
    for seconds in range(0, NEAR_START_WINDOW_SECONDS + 600, 7):
        hold, nxt = pace_idle(_idle(has_game=True, seconds_to_next_start=seconds))
        if hold == 0.0:
            assert nxt >= POLL_IN_PLAY_SECONDS


def test_live_game_wins_over_a_scheduled_one():
    """If any seated game is live, that beats a far scheduled start — the soonest
    real demand sets the pace."""
    hold, nxt = pace_idle(
        _idle(has_game=True, has_live_game=True, seconds_to_next_start=1500)
    )
    assert hold == LONG_POLL_HOLD_SECONDS
    assert nxt == POLL_IN_PLAY_SECONDS
