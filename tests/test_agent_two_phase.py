"""Agent API tests for the two-phase talk→act contract."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, Match, GameState, Player, Turn, TurnMessage, TurnSubmission
from tests.factories import seat_player


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    """Rebind the production session factory to an in-memory SQLite database."""
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr("app.routes.agent_api._last_poll", {})
    monkeypatch.setattr("app.routes.agent_api._last_pull", {})

    yield test_factory

    await test_engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_game(
    reset_db: async_sessionmaker,
    *,
    n_players: int = 2,
) -> tuple[Match, list[Player]]:
    async with reset_db() as db:
        game = Match(
            id="G_007",
            name="two-phase",
            state=GameState.ACTIVE,
            scheduled_start=datetime.now(timezone.utc),
            started_at=datetime.now(timezone.utc),
            per_turn_deadline_seconds=60,
            total_rounds=1,
            turns_per_round=1,
        )
        db.add(game)
        await db.flush()
        players: list[Player] = []
        for i in range(n_players):
            player = await seat_player(db, game.id, f"AI_{i}", i=i)
            players.append(player)
        await db.commit()
        return game, players


async def _open_turn(
    reset_db: async_sessionmaker,
    match_id: str,
    *,
    round_num: int = 1,
    turn_num: int = 1,
    phase: str = "talk",
    token: str | None = None,
) -> Turn:
    async with reset_db() as db:
        now = datetime.now(timezone.utc)
        turn = Turn(
            match_id=match_id,
            round=round_num,
            turn=turn_num,
            turn_token=token or generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase=phase,
        )
        db.add(turn)
        await db.commit()
        await db.refresh(turn)
        return turn


async def _message_by_player(
    reset_db: async_sessionmaker, turn_id: int, player_id: int
) -> TurnMessage | None:
    async with reset_db() as db:
        return (
            await db.execute(
                select(TurnMessage).where(
                    TurnMessage.turn_id == turn_id, TurnMessage.player_id == player_id
                )
            )
        ).scalar_one_or_none()


async def _submission_by_player(
    reset_db: async_sessionmaker, turn_id: int, player_id: int
) -> TurnSubmission | None:
    async with reset_db() as db:
        return (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn_id, TurnSubmission.player_id == player_id
                )
            )
        ).scalar_one_or_none()


def _json_text(body: object) -> str:
    return json.dumps(body, sort_keys=True)


async def test_message_talk_phase_is_idempotent_and_act_phase_signals_window_closed(
    client, reset_db
):
    game, players = await _seed_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="talk")
    key = players[0]._test_key

    r1 = await client.post(
        f"/api/games/{game.id}/message",
        params={"agent_turn_token": f"{turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "message": "public alpha",
            "thinking": "secret-message-1",
        },
    )
    assert r1.status_code == 202, r1.text
    body1 = r1.json()
    assert body1["status"] == "accepted"
    assert "thinking" not in body1

    row1 = await _message_by_player(reset_db, turn.id, players[0].id)
    assert row1 is not None
    assert row1.text == "public alpha"
    assert row1.thinking == "secret-message-1"
    assert row1.was_defaulted is False

    r2 = await client.post(
        f"/api/games/{game.id}/message",
        params={"agent_turn_token": f"{turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "message": "public beta",
            "thinking": "secret-message-2",
        },
    )
    assert r2.status_code == 202, r2.text
    row2 = await _message_by_player(reset_db, turn.id, players[0].id)
    assert row2 is not None
    assert row2.text == "public alpha"
    assert row2.thinking == "secret-message-1"

    # The talk window closes and the turn moves on to act — keeping the SAME token
    # (the loop no longer re-mints it at the talk->act handoff).
    async with reset_db() as db:
        fresh_turn = (await db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
        fresh_turn.phase = "act"
        await db.commit()

    r3 = await client.post(
        f"/api/games/{game.id}/message",
        params={"agent_turn_token": f"{turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "message": "too late to talk",
            "thinking": "secret-message-3",
        },
    )
    # A late talk is not an error: the server says the window closed and points the
    # agent at the act phase, with the same token it already holds. Private
    # `thinking` is never echoed back.
    assert r3.status_code == 202, r3.text
    body3 = r3.json()
    assert body3["status"] == "talk_window_closed"
    assert body3["phase"] == "act"
    assert body3["turn_token"] == turn.turn_token
    assert "secret-message-3" not in r3.text
    assert "thinking" not in body3


async def test_submit_talk_phase_rejects_then_act_phase_stores_thinking(client, reset_db):
    game, players = await _seed_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="talk")
    key = players[0]._test_key

    r1 = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": f"{turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "action": "HOARD",
            "target_id": None,
            "message": "ignored in talk phase",
            "thinking": "secret-act-1",
        },
    )
    assert r1.status_code == 409, r1.text
    assert r1.json()["detail"]["error"]["code"] == "WRONG_PHASE"

    act_token = generate_turn_token()
    async with reset_db() as db:
        fresh_turn = (await db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
        fresh_turn.phase = "act"
        fresh_turn.turn_token = act_token
        await db.commit()

    r2 = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": f"{act_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": act_token,
            "action": "HELP",
            "target_id": players[1].seat_name,
            "message": "legacy action message",
            "thinking": "secret-act-2",
        },
    )
    assert r2.status_code == 202, r2.text
    body2 = r2.json()
    assert body2["status"] == "accepted"
    assert "thinking" not in body2

    row = await _submission_by_player(reset_db, turn.id, players[0].id)
    assert row is not None
    assert row.action == "HELP"
    assert row.target_player_id is not None
    assert row.message == "legacy action message"
    assert row.thinking == "secret-act-2"


async def test_turn_current_is_talk_then_act_with_talk_messages(client, reset_db):
    game, players = await _seed_game(reset_db)
    key = players[0]._test_key

    talk_turn = await _open_turn(reset_db, game.id, phase="talk")
    r1 = await client.get(f"/api/games/{game.id}/turn", headers={"X-Connection-Key": key})
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["status"] == "your_turn"
    assert body1["current"]["phase"] == "talk"
    assert body1["current"]["turn_token"] == talk_turn.turn_token
    assert body1["current"]["talk_messages"] == []

    act_turn = await _open_turn(
        reset_db,
        game.id,
        round_num=1,
        turn_num=2,
        phase="act",
        token=generate_turn_token(),
    )
    async with reset_db() as db:
        db_turn = (await db.execute(select(Turn).where(Turn.id == act_turn.id))).scalar_one()
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[0].id,
                text="public from a",
                thinking="secret-current-a",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[1].id,
                text="public from b",
                thinking="secret-current-b",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    r2 = await client.get(
        f"/api/games/{game.id}/turn", headers={"X-Connection-Key": players[1]._test_key}
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["status"] == "your_turn"
    assert body2["current"]["phase"] == "act"
    assert body2["current"]["turn_token"] == act_turn.turn_token
    assert body2["current"]["talk_messages"] == [
        {"agent_id": players[0].seat_name, "message": "public from a"},
        {"agent_id": players[1].seat_name, "message": "public from b"},
    ]

    r3 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["status"] == "your_turn"
    assert body3["match_id"] == game.id
    assert body3["current"]["phase"] == "act"
    assert body3["current"]["talk_messages"] == [
        {"agent_id": players[0].seat_name, "message": "public from a"},
        {"agent_id": players[1].seat_name, "message": "public from b"},
    ]


async def test_next_turn_stops_reserving_talk_turn_after_message(
    client, reset_db, monkeypatch
):
    """After a player submits its talk message, the next-turn loop must not keep
    re-serving the same talk turn. It returns 'waiting' instead, so the loop
    long-polls for the act phase rather than hammering the server with the full
    turn payload every poll (which trips client-side loop detectors)."""
    # Don't actually hold the request open — return the idle payload at once.
    monkeypatch.setattr("app.engine.agent_idle.LONG_POLL_HOLD_SECONDS", 0)

    game, players = await _seed_game(reset_db)
    key = players[0]._test_key
    talk_turn = await _open_turn(reset_db, game.id, phase="talk")

    # Before talking: it's the player's turn (talk phase).
    r1 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r1.status_code == 200, r1.text
    assert r1.json()["status"] == "your_turn"

    # The player submits its talk message.
    async with reset_db() as db:
        db_turn = (
            await db.execute(select(Turn).where(Turn.id == talk_turn.id))
        ).scalar_one()
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[0].id,
                text="my talk",
                thinking="secret",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    # After talking: the same talk turn is no longer served — we wait for act.
    r2 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "waiting"

    # When the phase flips to act, the turn is served again — exactly once.
    async with reset_db() as db:
        db_turn = (
            await db.execute(select(Turn).where(Turn.id == talk_turn.id))
        ).scalar_one()
        db_turn.phase = "act"
        await db.commit()

    r3 = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["status"] == "your_turn"
    assert body3["current"]["phase"] == "act"


async def test_agent_endpoints_do_not_leak_thinking(client, reset_db):
    game, players = await _seed_game(reset_db)
    key = players[0]._test_key

    resolved_turn = await _open_turn(reset_db, game.id, phase="act")
    async with reset_db() as db:
        db_turn = (await db.execute(select(Turn).where(Turn.id == resolved_turn.id))).scalar_one()
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[0].id,
                text="resolved public a",
                thinking="resolved-think-a",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[1].id,
                text="resolved public b",
                thinking="resolved-think-b",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnSubmission(
                turn_id=db_turn.id,
                player_id=players[0].id,
                action="HELP",
                target_player_id=players[1].id,
                message="legacy resolved a",
                thinking="resolved-submit-a",
                points_delta=4,
                round_score_after=4,
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnSubmission(
                turn_id=db_turn.id,
                player_id=players[1].id,
                action="HOARD",
                target_player_id=None,
                message="legacy resolved b",
                thinking="resolved-submit-b",
                points_delta=2,
                round_score_after=2,
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db_turn.resolved_at = datetime.now(timezone.utc)
        await db.commit()

    open_turn = await _open_turn(reset_db, game.id, round_num=1, turn_num=2, phase="act")
    async with reset_db() as db:
        db_turn = (await db.execute(select(Turn).where(Turn.id == open_turn.id))).scalar_one()
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[0].id,
                text="open public a",
                thinking="open-think-a",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        db.add(
            TurnMessage(
                turn_id=db_turn.id,
                player_id=players[1].id,
                text="open public b",
                thinking="open-think-b",
                was_defaulted=False,
                submitted_at=datetime.now(timezone.utc),
            )
        )
        await db.commit()

    endpoints = [
        (f"/api/games/{game.id}/turn", "GET", {"headers": {"X-Connection-Key": key}}),
        (
            "/api/agent/next-turn",
            "GET",
            {"headers": {"X-Connection-Key": key}},
        ),
        (f"/api/games/{game.id}/chat", "GET", {"headers": {"X-Connection-Key": key}}),
        (
            f"/api/games/{game.id}/history/opponents/{players[1].seat_name}",
            "GET",
            {"headers": {"X-Connection-Key": key}},
        ),
        (f"/api/games/{game.id}/turns/1/1", "GET", {"headers": {"X-Connection-Key": key}}),
    ]
    secrets = [
        "resolved-think-a",
        "resolved-think-b",
        "resolved-submit-a",
        "resolved-submit-b",
        "open-think-a",
        "open-think-b",
    ]

    for path, method, kwargs in endpoints:
        response = await getattr(client, method.lower())(path, **kwargs)
        assert response.status_code == 200, response.text
        text = _json_text(response.json())
        for secret in secrets:
            assert secret not in text, (path, secret, text)
