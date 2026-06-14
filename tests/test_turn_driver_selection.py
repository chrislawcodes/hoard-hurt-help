"""The scheduler picks a turn driver from the game's simultaneous flag.

PD (simultaneous) runs on the SimultaneousDriver — its loop and helpers stay in
the scheduler module, unchanged. A sequential game selects the isolated
SequentialDriver, so changes to sequential play cannot reach PD's loop.
"""

from __future__ import annotations

from app.engine.scheduler import SimultaneousDriver, _select_driver
from app.engine.turn_drivers import SequentialDriver
from app.games.base import BaseGameModule, GameConfig
from app.games.hoard_hurt_help.game import HoardHurtHelp


class _SequentialModule(BaseGameModule):
    game_type = "seq-test"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1,
            turns_per_round=1,
            per_turn_deadline_seconds=30,
            min_players=2,
            max_players=6,
            simultaneous=False,
        )


def test_pd_selects_simultaneous_driver() -> None:
    assert isinstance(_select_driver(HoardHurtHelp()), SimultaneousDriver)


def test_sequential_game_selects_sequential_driver() -> None:
    assert isinstance(_select_driver(_SequentialModule()), SequentialDriver)
