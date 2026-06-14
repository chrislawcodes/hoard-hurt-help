"""End-to-end Liar's Dice turn-loop tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from fastmcp.server.dependencies import AccessToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.agent_play import poll_turn
from app.engine.tokens import generate_turn_token
from app.engine.turn_drivers import SequentialDriver
from app.games.liars_dice.game import LiarsDice
from app.models import Base, GameState, Match, MatchState, Player, PlayerState, Turn
from app.models.agent import AgentKind
from app.routes.spectator_api import public_state
from mcp_server import server
from tests.factories import make_bot, make_user, seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token() -> AccessToken:
    return AccessToken(
        token="access-token-1",
        client_id="sub-123",
        scopes=["openid", "email", "profile"],
        subject="sub-123",
        claims={
            "sub": "sub-123",
            "email": "agent@example.com",
            "name": "Agent One",
            "given_name": "Agent",
            "family_name": "One",
            "email_verified": True,
        },
    )


@pytest.fixture(autouse=True)
async def reset_db():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seat_bot(db, match_id: str, seat_name: str, i: int) -> Player:
    user = await make_user(db, i)
    agent, _ = await make_bot(db, user, name=seat_name, kind=AgentKind.BOT)
    player = Player(match_id=match_id, user_id=user.id, agent_id=agent.id, seat_name=seat_name)
    db.add(player)
    await db.flush()
    return player


async def _seed_match(
    db,
    *,
    match_id: str,
    wild_ones: bool,
    dice_per_player: int,
    bot: bool = False,
) -> tuple[Match, list[Player]]:
    now = _now()
    match = Match(
        id=match_id,
        name=match_id,
        game="liars-dice",
        state=GameState.ACTIVE,
        scheduled_start=now,
        started_at=now,
        current_round=0,
        current_turn=0,
        per_turn_deadline_seconds=0,
        total_rounds=64,
        turns_per_round=256,
    )
    db.add(match)
    await db.flush()

    players: list[Player] = []
    for index, seat_name in enumerate(["A", "B", "C"]):
        if bot:
            players.append(await _seat_bot(db, match.id, seat_name, index))
        else:
            players.append(await seat_player(db, match.id, seat_name, index))

    db.add(
        MatchState(
            match_id=match.id,
            state_json={"config": {"wild_ones": wild_ones, "dice_per_player": dice_per_player}},
        )
    )
    await db.commit()
    return match, players


async def _run_bots_match(*, match_id: str, wild_ones: bool) -> str:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = LiarsDice()

    async with factory() as db:
        match, _players = await _seed_match(
            db,
            match_id=match_id,
            wild_ones=wild_ones,
            dice_per_player=1,
            bot=True,
        )
        await SequentialDriver().run_match(db, match, module)

        refreshed = (
            await db.execute(select(Match).where(Match.id == match_id))
        ).scalar_one()
        winner = (
            await db.execute(select(Player).where(Player.id == refreshed.winner_player_id))
        ).scalar_one()
        placement = await module.final_placement(db, refreshed)
        assert refreshed.state == GameState.COMPLETED
        assert len(placement) == 3
        result = winner.seat_name

    await engine.dispose()
    return result


@pytest.mark.asyncio
async def test_sequential_driver_completes_and_is_deterministic() -> None:
    for wild_ones in (True, False):
        first = await _run_bots_match(
            match_id=f"M_LD_BOTS_{'W' if wild_ones else 'N'}",
            wild_ones=wild_ones,
        )
        second = await _run_bots_match(
            match_id=f"M_LD_BOTS_{'W' if wild_ones else 'N'}",
            wild_ones=wild_ones,
        )
        assert first == second


@pytest.mark.asyncio
async def test_hidden_info_stays_private_before_showdown_and_reveals_after() -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = LiarsDice()

    async with factory() as db:
        match, players = await _seed_match(
            db,
            match_id="M_LD_HIDE",
            wild_ones=True,
            dice_per_player=5,
            bot=False,
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = None
        state.state_json["active_actor"] = "A"
        await db.flush()

        # Overwrite the generated dice with known values so the assertions are exact.
        dice_map = {"A": [5, 5, 1], "B": [2, 2, 4], "C": [3, 4, 6]}
        for player in players:
            db.add(
                PlayerState(
                    match_id=match.id,
                    player_id=player.id,
                    state_json={
                        "dice": dice_map[player.seat_name],
                        "dice_count": len(dice_map[player.seat_name]),
                    },
                )
            )
        turn = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=_now(),
            deadline_at=_now() + timedelta(seconds=30),
            phase="act",
        )
        db.add(turn)
        await db.commit()

        pre = await poll_turn(db, match_id=match.id, player=players[0], rate_state={})
        assert pre.status == "your_turn"
        assert pre.your_private_state == {"dice": [5, 5, 1], "dice_count": 3}
        assert pre.public_state["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert "[2,2,4]" not in pre.model_dump_json()

        spectator = await public_state(match_id=match.id, db=db)
        assert spectator.public_state["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert "[2,2,4]" not in spectator.model_dump_json()

        mcp_state = await server.get_game_state(match_id=match.id, db=db, token=_token())
        assert mcp_state.public_state["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert "[2,2,4]" not in mcp_state.model_dump_json()

        state.state_json["standing_bid"] = {"by": "A", "quantity": 2, "face": 5}
        state.state_json["challenge_pending"] = True
        state.state_json["challenger"] = "B"
        await db.commit()

        await module.award_round(db, match, 1)

        post = await poll_turn(db, match_id=match.id, player=players[0], rate_state={})
        assert post.public_state["last_showdown"]["revealed"]["A"] == [5, 5, 1]
        assert post.public_state["last_showdown"]["revealed"]["B"] == [2, 2, 4]

        spectator_after = await public_state(match_id=match.id, db=db)
        assert spectator_after.public_state["last_showdown"]["revealed"]["A"] == [5, 5, 1]
        assert spectator_after.public_state["last_showdown"]["revealed"]["B"] == [2, 2, 4]

        mcp_after = await server.get_game_state(match_id=match.id, db=db, token=_token())
        assert mcp_after.public_state["last_showdown"]["revealed"]["A"] == [5, 5, 1]
        assert mcp_after.public_state["last_showdown"]["revealed"]["B"] == [2, 2, 4]

    await engine.dispose()
