"""Admin-only games are hidden from non-admins.

A game under construction ships `GameConfig.admin_only=True`. The registry helpers
(`is_admin_only`, `visible_types`) and the web access helper (`_can_view_game`)
must keep it out of every non-admin surface (lobby, catalog, leaderboard,
create/join) while admins still see it. PD stays public (admin_only defaults
False), so existing surfaces are unaffected.
"""

from __future__ import annotations

import app.games as registry
from app.games.base import BaseGameModule, GameConfig
from app.models.user import User, UserRole
from app.routes.web_support import _can_view_game


class _HiddenGame(BaseGameModule):
    game_type = "hidden-test"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1, turns_per_round=1, per_turn_deadline_seconds=30,
            min_players=2, max_players=6, simultaneous=False, admin_only=True,
        )


registry.register(_HiddenGame())


def _admin() -> User:
    return User(google_sub="a", email="admin@x.com", role=UserRole.ADMIN)


def _regular() -> User:
    return User(google_sub="u", email="user@x.com", role=UserRole.USER)


def test_is_admin_only_reads_the_flag() -> None:
    assert registry.is_admin_only("hidden-test") is True
    assert registry.is_admin_only("hoard-hurt-help") is False
    assert registry.is_admin_only("no-such-game") is False  # unknown → not gated


def test_visible_types_excludes_admin_only_unless_included() -> None:
    public = registry.visible_types(include_admin_only=False)
    assert "hidden-test" not in public
    assert "hoard-hurt-help" in public

    everything = registry.visible_types(include_admin_only=True)
    assert "hidden-test" in everything
    assert everything == registry.known_types()


def test_can_view_game_gates_non_admins() -> None:
    # Hidden game: only admins may view it.
    assert _can_view_game(_admin(), "hidden-test") is True
    assert _can_view_game(_regular(), "hidden-test") is False
    assert _can_view_game(None, "hidden-test") is False  # anonymous

    # Public game: everyone, including anonymous.
    assert _can_view_game(_regular(), "hoard-hurt-help") is True
    assert _can_view_game(None, "hoard-hurt-help") is True
