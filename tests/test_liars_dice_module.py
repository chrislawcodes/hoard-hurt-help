"""DB-backed tests for the Liar's Dice game module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.games.base import GameError
from app.games.liars_dice.game import LiarsDice
from app.models import Base, GameState, Match, MatchState, PlayerState, Turn, TurnSubmission
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
async def reset_db():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_match(
    db,
    *,
    match_id: str = "M_LD",
    wild_ones: bool = True,
    dice_per_player: int = 5,
    dice_by_seat: dict[str, list[int]] | None = None,
) -> tuple[Match, list]:
    module = LiarsDice()
    now = _now()
    match = Match(
        id=match_id,
        name="Liar's Dice",
        game=module.game_type,
        state=GameState.ACTIVE,
        scheduled_start=now,
        started_at=now,
        current_round=1,
        current_turn=1,
        per_turn_deadline_seconds=30,
        total_rounds=64,
        turns_per_round=256,
    )
    db.add(match)
    await db.flush()

    players = [
        await seat_player(db, match.id, "A", i=0),
        await seat_player(db, match.id, "B", i=1),
        await seat_player(db, match.id, "C", i=2),
    ]
    db.add(
        MatchState(
            match_id=match.id,
            state_json={"config": {"wild_ones": wild_ones, "dice_per_player": dice_per_player}},
        )
    )
    for player in players:
        dice = list((dice_by_seat or {}).get(player.seat_name, [1, 2, 3, 4, 5]))
        db.add(
            PlayerState(
                match_id=match.id,
                player_id=player.id,
                state_json={"dice": dice, "dice_count": len(dice)},
            )
        )
    turn = Turn(
        match_id=match.id,
        round=1,
        turn=1,
        turn_token="tk1",
        opened_at=now,
        deadline_at=now + timedelta(seconds=30),
        phase="act",
    )
    db.add(turn)
    await db.commit()
    return match, players


@pytest.mark.asyncio
async def test_config_defaults_and_theme() -> None:
    module = LiarsDice()
    cfg = module.config_defaults()
    assert cfg.min_players == 3
    assert cfg.max_players == 6
    assert cfg.simultaneous is False
    assert cfg.admin_only is True
    assert module.theme().key == "liars-dice"


@pytest.mark.asyncio
async def test_validation_snapshot_and_validate_move(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(
            db,
            dice_by_seat={"A": [5, 5, 1], "B": [2, 2, 2], "C": [3, 3, 3]},
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = {"by": "B", "quantity": 1, "face": 4}
        state.state_json["active_actor"] = "A"
        await db.commit()

        snapshot = await module.validation_snapshot(db, match, players[0])
        assert snapshot["standing_bid"] == {"by": "B", "quantity": 1, "face": 4}
        assert snapshot["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert snapshot["total_dice"] == 9
        assert snapshot["wild"] is True

        module.validate_move(
            {
                "type": "BID",
                "quantity": 2,
                "face": 5,
                **snapshot,
            },
            your_agent_id="A",
            all_agent_ids=["A", "B", "C"],
        )

        with pytest.raises(GameError) as exc:
            module.validate_move(
                {
                    "type": "BID",
                    "quantity": 2,
                    "face": 5,
                    **{**snapshot, "active_actor": "B"},
                },
                your_agent_id="A",
                all_agent_ids=["A", "B", "C"],
            )
        assert exc.value.code == "NOT_YOUR_TURN"

        with pytest.raises(GameError) as exc:
            module.validate_move(
                {"type": "CHALLENGE", "active_actor": "A"},
                your_agent_id="A",
                all_agent_ids=["A", "B", "C"],
            )
        assert exc.value.code == "NOTHING_TO_CHALLENGE"

        with pytest.raises(GameError) as exc:
            module.validate_move(
                {
                    "type": "BID",
                    "quantity": 2,
                    "face": 7,
                    **snapshot,
                },
                your_agent_id="A",
                all_agent_ids=["A", "B", "C"],
            )
        # main's parse_move rejects a face outside 1..6 at parse time with
        # MALFORMED_MOVE, so validate_move's later BAD_FACE branch never fires.
        assert exc.value.code == "MALFORMED_MOVE"

        with pytest.raises(GameError) as exc:
            module.validate_move(
                {
                    "type": "BID",
                    "quantity": 10,
                    "face": 5,
                    **snapshot,
                },
                your_agent_id="A",
                all_agent_ids=["A", "B", "C"],
            )
        assert exc.value.code == "BID_TOO_LARGE"

        with pytest.raises(GameError) as exc:
            module.validate_move(
                {
                    "type": "BID",
                    "quantity": 1,
                    "face": 3,
                    **snapshot,
                },
                your_agent_id="A",
                all_agent_ids=["A", "B", "C"],
            )
        assert exc.value.code == "ILLEGAL_RAISE"


@pytest.mark.asyncio
async def test_record_submission_advances_and_challenge_pauses_turn(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(
            db,
            dice_by_seat={"A": [5, 4, 3], "B": [2, 2, 2], "C": [1, 1, 1]},
        )
        turn = (
            await db.execute(
                select(Turn).where(Turn.match_id == match.id, Turn.round == 1, Turn.turn == 1)
            )
        ).scalar_one()

        await module.record_submission(
            db,
            turn,
            players[0],
            {"type": "BID", "quantity": 1, "face": 2},
            existing=None,
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert state.state_json["standing_bid"] == {"by": "A", "quantity": 1, "face": 2}
        assert state.state_json["active_actor"] == "B"
        assert state.state_json["challenge_pending"] is False
        assert (
            await db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        ).scalar_one().quantity == 1

        turn2 = Turn(
            match_id=match.id,
            round=1,
            turn=2,
            turn_token="tk2",
            opened_at=_now(),
            deadline_at=_now() + timedelta(seconds=30),
            phase="act",
        )
        db.add(turn2)
        await db.flush()
        await module.record_submission(
            db,
            turn2,
            players[1],
            {"type": "CHALLENGE"},
            existing=None,
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert state.state_json["challenge_pending"] is True
        assert state.state_json["challenger"] == "B"
        assert await module.next_actor(db, match) is None


@pytest.mark.asyncio
async def test_award_round_resolves_showdown_and_is_idempotent(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(
            db,
            wild_ones=False,
            dice_by_seat={"A": [5, 5, 2], "B": [1, 3, 4], "C": [6, 6, 6]},
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = {"by": "A", "quantity": 2, "face": 5}
        state.state_json["challenge_pending"] = True
        state.state_json["challenger"] = "B"
        await db.commit()

        await module.award_round(db, match, 1)
        await db.refresh(players[0])
        await db.refresh(players[1])
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert state.state_json["last_showdown"]["bid_holds"] is True
        assert state.state_json["last_showdown"]["winner"] == "A"
        assert state.state_json["last_showdown"]["loser"] == "B"
        assert state.state_json["last_showdown"]["revealed"]["A"] == [5, 5, 2]
        assert state.state_json["last_showdown"]["revealed"]["B"] == [1, 3, 4]
        assert players[0].total_round_wins == 1
        assert await _player_dice_count(db, players[1].id) == 2

        await module.award_round(db, match, 1)
        assert await _player_dice_count(db, players[1].id) == 2
        assert (
            await db.execute(
                select(MatchState).where(MatchState.match_id == match.id)
            )
        ).scalar_one().state_json["showdown_resolved_hand"] == 1


async def _player_dice_count(db, player_id: int) -> int:
    row = (
        await db.execute(select(PlayerState).where(PlayerState.player_id == player_id))
    ).scalar_one()
    return row.state_json["dice_count"]


@pytest.mark.asyncio
async def test_round_start_falls_back_to_default_config(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(db, dice_by_seat={})
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json = {}
        await db.commit()

        await module.on_round_start(db, match, 1)
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        assert state.state_json["config"] == {"wild_ones": True, "dice_per_player": 5}
        assert state.state_json["active_actor"] == "A"
        counts = (
            await db.execute(select(PlayerState).where(PlayerState.match_id == match.id))
        ).scalars().all()
        assert {row.state_json["dice_count"] for row in counts} == {5}


@pytest.mark.asyncio
async def test_private_and_public_state_surfaces(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(
            db,
            dice_by_seat={"A": [6, 6, 1], "B": [2, 2, 2], "C": [3, 3, 3]},
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = {"by": "A", "quantity": 2, "face": 6}
        state.state_json["active_actor"] = "B"
        await db.commit()

        private = await module.private_state_for(db, match, players[0])
        assert private == {"dice": [6, 6, 1], "dice_count": 3}

        public = await module.public_state_for(db, match, players[1])
        assert public["standing_bid"] == {"by": "A", "quantity": 2, "face": 6}
        assert public["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert "dice" not in public


@pytest.mark.asyncio
async def test_final_placement_and_match_placement_key(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(db)
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["elimination_order"] = ["B", "C"]
        await db.commit()

        assert await module.final_placement(db, match) == [players[0].id, players[2].id, players[1].id]
        assert module.match_placement_key(round_wins=1.5, total_score=3) == (3.0, 1.5)


@pytest.mark.asyncio
async def test_default_move_opening_and_ceiling(reset_db) -> None:
    module = LiarsDice()
    async with reset_db() as db:
        match, players = await _seed_match(db, dice_by_seat={"A": [1], "B": [1], "C": [1]})
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = None
        await db.commit()
        assert await module.default_move(db, match, players[0]) == {"type": "BID", "quantity": 1, "face": 2}

        state.state_json["config"]["wild_ones"] = False
        state.state_json["standing_bid"] = {"by": "A", "quantity": 3, "face": 6}
        await db.commit()
        assert await module.default_move(db, match, players[0]) == {"type": "CHALLENGE"}


@pytest.mark.asyncio
async def test_sc_hd_no_dice_faces_leak_to_spectator_or_mcp(reset_db) -> None:
    """SC-HD: a player's dice FACES must not reach the spectator JSON or the MCP
    `get_game_state` tool before the showdown. Both channels go through the same
    `app.routes.spectator_api.public_state` (mcp_server imports it directly), so
    one sweep covers both. After a showdown, the revealed dice DO become public."""
    import json

    from app.routes.spectator_api import public_state

    module = LiarsDice()
    async with reset_db() as db:
        match, _players = await _seed_match(
            db,
            wild_ones=False,
            dice_by_seat={"A": [5, 5, 2], "B": [1, 3, 4], "C": [6, 6, 6]},
        )
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["standing_bid"] = {"by": "A", "quantity": 2, "face": 5}
        await db.commit()

        # Pre-showdown: only dice COUNTS are public; no faces anywhere.
        before = (await public_state(match.id, db)).model_dump()
        ps = before["public_state"]
        assert ps["dice_counts"] == {"A": 3, "B": 3, "C": 3}
        assert ps.get("last_showdown") is None
        assert ps.get("showdowns") == []
        blob = json.dumps(before, default=str)
        for hand in ([5, 5, 2], [1, 3, 4], [6, 6, 6]):
            assert json.dumps(hand) not in blob, f"dice {hand} leaked pre-showdown"

        # Showdown reveals every hand publicly.
        state = (
            await db.execute(select(MatchState).where(MatchState.match_id == match.id))
        ).scalar_one()
        state.state_json["challenge_pending"] = True
        state.state_json["challenger"] = "B"
        await db.commit()
        await module.award_round(db, match, 1)

        after = (await public_state(match.id, db)).model_dump()
        revealed = after["public_state"]["last_showdown"]["revealed"]
        assert revealed == {"A": [5, 5, 2], "B": [1, 3, 4], "C": [6, 6, 6]}
