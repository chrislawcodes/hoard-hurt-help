"""Hidden per-player state never leaks across players' channels.

A sequential, hidden-information game exposes per-player secret state via the
`private_state_for` hook and shared state via `public_state_for`. The platform's
turn payload (poll_turn) must return ONLY the requesting player's private state
— a player must never see another player's secret. This is the Liar's Dice "your
dice are yours alone" guarantee, enforced at the platform layer.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.games as registry
from app.db import make_engine
from app.engine.agent_play import poll_turn
from app.engine.tokens import generate_turn_token
from app.games.base import BaseGameModule, GameConfig, GameTheme
from app.models import Base, Match, GameState, PlayerState, Player
from app.models.turn import Turn
from app.schemas.agent import YourTurnResponse
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _HiddenStub(BaseGameModule):
    """A hidden-info game: each player holds a secret no one else may see."""

    game_type = "hidden-stub"

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1, turns_per_round=16, per_turn_deadline_seconds=30,
            min_players=2, max_players=6, simultaneous=False,
        )

    def rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        return "Hidden stub rules."

    def strategy_presets(self) -> list:
        return []

    def default_strategy(self) -> str:
        return "Hidden stub strategy."

    def agent_base_prompt(
        self, *, your_agent_id: str, all_agent_ids: list[str],
        total_rounds: int = 7, turns_per_round: int = 7,
    ) -> str:
        return "Hidden stub base prompt."

    def validate_move(self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]) -> None:
        return None

    def move_effect(self, action: str) -> tuple[int, int | None]:
        return (0, None)

    def theme(self) -> GameTheme:
        return GameTheme(key=self.game_type, vars={})

    async def private_state_for(self, db: Any, match: Match, player: Player) -> dict[str, Any]:
        ps = (
            await db.execute(
                select(PlayerState).where(
                    PlayerState.match_id == match.id, PlayerState.player_id == player.id
                )
            )
        ).scalar_one_or_none()
        return {"secret": ps.state_json["secret"]} if ps is not None else {}

    async def public_state_for(self, db: Any, match: Match, viewer: Player | None) -> dict[str, Any]:
        return {"shared": "everyone sees this"}


async def _poll_for(db: Any, match: Match, player: Player) -> YourTurnResponse:
    resp = await poll_turn(db, match_id=match.id, player=player, rate_state={})
    assert isinstance(resp, YourTurnResponse)
    return resp


async def test_private_state_never_leaks_across_players() -> None:
    registry.register(_HiddenStub())

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        match = Match(
            id="M_HID", name="hid", game="hidden-stub", state=GameState.ACTIVE,
            scheduled_start=_now(), total_rounds=1, turns_per_round=16,
            per_turn_deadline_seconds=30, current_round=1, current_turn=1,
        )
        db.add(match)
        await db.flush()
        a = await seat_player(db, match.id, "Alice", i=0)
        b = await seat_player(db, match.id, "Bob", i=1)
        db.add(PlayerState(match_id=match.id, player_id=a.id, state_json={"secret": "ALICE_DICE_55613"}))
        db.add(PlayerState(match_id=match.id, player_id=b.id, state_json={"secret": "BOB_DICE_22244"}))
        # An open, unresolved act-phase turn so poll returns "your_turn".
        db.add(Turn(
            match_id=match.id, round=1, turn=1, turn_token=generate_turn_token(),
            opened_at=_now(), deadline_at=_now() + timedelta(seconds=30), phase="act",
        ))
        await db.commit()

        alice_payload = await _poll_for(db, match, a)
        bob_payload = await _poll_for(db, match, b)

        # Each player sees ONLY their own secret.
        assert alice_payload.your_private_state == {"secret": "ALICE_DICE_55613"}
        assert bob_payload.your_private_state == {"secret": "BOB_DICE_22244"}

        # Public state is shared and identical.
        assert alice_payload.public_state == {"shared": "everyone sees this"}
        assert bob_payload.public_state == {"shared": "everyone sees this"}

        # The other player's secret appears NOWHERE in the serialized payload.
        alice_json = alice_payload.model_dump_json()
        assert "BOB_DICE_22244" not in alice_json
        bob_json = bob_payload.model_dump_json()
        assert "ALICE_DICE_55613" not in bob_json

    await engine.dispose()
