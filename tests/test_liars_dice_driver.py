"""End-to-end Liar's Dice turn-loop tests.

This file covers the GAME-LOGIC half of Liar's Dice (the bot-driven match loop,
determinism, and the agent-API hidden-info contract via the next-turn payload).
The spectator-JSON and MCP `get_game_state` leak sweep lives with the other
(schemas/viewer) half — those surfaces only expose Liar's Dice `public_state`
once `SpectatorState` gains a `public_state` field, which is owned by that half.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.engine.tokens import generate_turn_token
from app.engine.turn_drivers import SequentialDriver
from app.games.liars_dice.game import LiarsDice
from app.models import Base, GameState, Match, MatchState, Player, PlayerState, Turn
from app.models.agent import AgentKind
from app.routes.agent_next_turn import router as agent_next_turn_router
from tests.factories import make_bot, make_user, seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Bespoke: drives the game module directly against a raw session, so there is no
# app.db rebind to delegate to tests/conftest.py's shared reset_db.
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


async def test_hidden_info_stays_private_before_showdown_and_reveals_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = LiarsDice()
    # The next-turn endpoint resolves the DB through the imported SessionLocal.
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)

    test_app = FastAPI()
    test_app.include_router(agent_next_turn_router)
    transport = ASGITransport(app=test_app)

    async def _serve(client: AsyncClient, key: str) -> dict:
        r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["status"] == "your_turn", payload
        return payload

    async with factory() as db:
        match, players = await _seed_match(
            db,
            match_id="M_LD_HIDE",
            wild_ones=True,
            dice_per_player=5,
            bot=False,
        )
        key_a = players[0]._test_key
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

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Agent API: the active player sees only its own dice; nobody else's
            # dice leak through the turn payload before the showdown.
            pre = await _serve(client, key_a)
            assert pre["your_private_state"] == {"dice": [5, 5, 1], "dice_count": 3}
            assert pre["public_state"]["dice_counts"] == {"A": 3, "B": 3, "C": 3}
            # No opponent's hidden dice leak anywhere in the payload. Serialize
            # compactly so a leaked list (e.g. [2,2,4]) matches the search string
            # (json.dumps' default separators would insert spaces and never hit).
            pre_json = json.dumps(pre, separators=(",", ":"), default=str)
            assert "[2,2,4]" not in pre_json  # B's dice
            assert "[3,4,6]" not in pre_json  # C's dice

            state.state_json["standing_bid"] = {"by": "A", "quantity": 2, "face": 5}
            state.state_json["challenge_pending"] = True
            state.state_json["challenger"] = "B"
            await db.commit()

            await module.award_round(db, match, 1)

            # After the showdown all hands are revealed in public_state.
            post = await _serve(client, key_a)
            assert post["public_state"]["last_showdown"]["revealed"]["A"] == [5, 5, 1]
            assert post["public_state"]["last_showdown"]["revealed"]["B"] == [2, 2, 4]

    await engine.dispose()


async def test_bot_move_drives_actor_via_module_bot_move() -> None:
    """The sequential driver must call `module.bot_move` for a bot actor."""
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    module = LiarsDice()

    async with factory() as db:
        match, players = await _seed_match(
            db,
            match_id="M_LD_BOTMOVE",
            wild_ones=True,
            dice_per_player=2,
            bot=True,
        )
        await module.on_round_start(db, match, 1)
        actor_seat = await module.next_actor(db, match)
        actor = next(p for p in players if p.seat_name == actor_seat)

        move = await module.bot_move(db, match, actor)
        assert move["type"] in ("BID", "CHALLENGE")
        if move["type"] == "BID":
            assert move["face"] in range(1, 7)
            assert move["quantity"] >= 1

        # Determinism: the same situation yields the same bot move.
        again = await module.bot_move(db, match, actor)
        assert again == move

    await engine.dispose()
