"""Two-phase segregation tests: thinking stays HTML-only, leaves stay safe."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.resolver import finalize_talk_phase
from app.games.hoard_hurt_help.scoring import resolve_turn
from app.engine.scheduler import _begin_act_phase
from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Match, GameState, Player, Turn, TurnMessage, TurnSubmission
from tests.factories import seat_player


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_two_phase_game(
    reset_db: async_sessionmaker,
) -> tuple[Match, list[Player], Turn, Turn]:
    """Seed one resolved turn plus one open act turn with known thinking strings."""
    async with reset_db() as db:
        now = datetime.now(timezone.utc)
        game = Match(
            id="G_007",
            name="segregation",
            state=GameState.ACTIVE,
            scheduled_start=now,
            started_at=now,
            current_round=1,
            current_turn=2,
            total_rounds=1,
            turns_per_round=2,
        )
        db.add(game)
        await db.flush()

        players: list[Player] = []
        for i in range(2):
            player = await seat_player(db, game.id, f"AI_{i}", i=i)
            players.append(player)

        resolved_turn = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token="resolved-token",
            opened_at=now - timedelta(minutes=2),
            deadline_at=now - timedelta(minutes=1),
            phase="act",
            talk_resolved_at=now - timedelta(minutes=1, seconds=30),
            resolved_at=now - timedelta(minutes=1),
        )
        db.add(resolved_turn)
        await db.flush()
        db.add_all(
            [
                TurnMessage(
                    turn_id=resolved_turn.id,
                    player_id=players[0].id,
                    text="resolved public talk a",
                    thinking="resolved talk thinking a",
                    was_defaulted=False,
                    submitted_at=now - timedelta(minutes=1, seconds=50),
                ),
                TurnMessage(
                    turn_id=resolved_turn.id,
                    player_id=players[1].id,
                    text="resolved public talk b",
                    thinking="resolved talk thinking b",
                    was_defaulted=False,
                    submitted_at=now - timedelta(minutes=1, seconds=45),
                ),
                TurnSubmission(
                    turn_id=resolved_turn.id,
                    player_id=players[0].id,
                    action="HOARD",
                    target_player_id=None,
                    message="resolved public act a",
                    thinking="resolved act thinking a",
                    points_delta=2,
                    round_score_after=2,
                    was_defaulted=False,
                    submitted_at=now - timedelta(minutes=1, seconds=20),
                ),
                TurnSubmission(
                    turn_id=resolved_turn.id,
                    player_id=players[1].id,
                    action="HOARD",
                    target_player_id=None,
                    message="resolved public act b",
                    thinking="resolved act thinking b",
                    points_delta=2,
                    round_score_after=2,
                    was_defaulted=False,
                    submitted_at=now - timedelta(minutes=1, seconds=15),
                ),
            ]
        )

        open_turn = Turn(
            match_id=game.id,
            round=1,
            turn=2,
            turn_token="open-token",
            opened_at=now - timedelta(seconds=15),
            deadline_at=now + timedelta(minutes=1),
            phase="act",
        )
        db.add(open_turn)
        await db.flush()
        db.add_all(
            [
                TurnMessage(
                    turn_id=open_turn.id,
                    player_id=players[0].id,
                    text="open public talk a",
                    thinking="open talk thinking a",
                    was_defaulted=False,
                    submitted_at=now - timedelta(seconds=10),
                ),
                TurnMessage(
                    turn_id=open_turn.id,
                    player_id=players[1].id,
                    text="open public talk b",
                    thinking="open talk thinking b",
                    was_defaulted=False,
                    submitted_at=now - timedelta(seconds=9),
                ),
            ]
        )

        players[0].current_round_score = 2
        players[1].current_round_score = 2
        await db.commit()
        return game, players, resolved_turn, open_turn


def _assert_no_thinking(body: object, secrets: list[str]) -> None:
    """Thinking must be absent from JSON-shaped bot channels."""

    def walk(value: object) -> None:
        if isinstance(value, dict):
            assert "thinking" not in value
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(body)
    body_text = json.dumps(body, sort_keys=True)
    for secret in secrets:
        assert secret not in body_text


async def test_programmatic_channels_do_not_expose_thinking_and_viewer_does(
    client, reset_db
):
    game, players, _resolved_turn, open_turn = await _seed_two_phase_game(reset_db)
    secrets = [
        "resolved talk thinking a",
        "resolved talk thinking b",
        "resolved act thinking a",
        "resolved act thinking b",
        "open talk thinking a",
        "open talk thinking b",
    ]

    # The MCP tools proxy these same HTTP endpoints, so this matrix covers them transitively.
    endpoints = [
        (
            "/api/agent/next-turn",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (
            f"/api/games/{game.id}/state",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (
            f"/api/games/{game.id}/chat",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (
            f"/api/games/{game.id}/history/opponents/{players[1].seat_name}",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (
            f"/api/games/{game.id}/turns/1/1",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (
            f"/api/games/{game.id}/standings",
            {"headers": {"X-Connection-Key": players[0]._test_key}},
        ),
        (f"/api/spectator/games/{game.id}/state", {}),
    ]

    turn_payload: dict | None = None
    for path, kwargs in endpoints:
        response = await client.get(path, **kwargs)
        assert response.status_code == 200, response.text
        payload = response.json()
        if isinstance(payload, dict):
            assert "thinking" not in payload
        _assert_no_thinking(payload, secrets)
        if path == "/api/agent/next-turn":
            turn_payload = payload

    viewer = await client.get(f"/games/hoard-hurt-help/matches/{game.id}")
    assert viewer.status_code == 200, viewer.text
    for secret in secrets[:4]:
        assert secret in viewer.text

    # The current open act turn still exposes only public talk text, not reasoning.
    assert turn_payload is not None
    assert turn_payload["current"]["phase"] == "act"
    assert turn_payload["current"]["turn_token"] == open_turn.turn_token
    assert turn_payload["current"]["talk_messages"] == [
        {"agent_id": players[0].seat_name, "message": "open public talk a"},
        {"agent_id": players[1].seat_name, "message": "open public talk b"},
    ]


@pytest.mark.parametrize(
    "request_path, method_name, body_factory, expected_code",
    [
        (
            "/api/games/{match_id}/submit",
            "post",
            lambda turn_token, thinking, players: {
                "turn_token": turn_token,
                "action": "HOARD",
                "target_id": None,
                "message": "wrong phase",
                "thinking": thinking,
            },
            "WRONG_PHASE",
        ),
        (
            "/api/games/{match_id}/message",
            "post",
            lambda turn_token, thinking, players: {
                "turn_token": "stale-token",
                "message": "stale token",
                "thinking": thinking,
            },
            "STALE_TURN_TOKEN",
        ),
            (
                "/api/games/{match_id}/submit",
                "post",
                lambda turn_token, thinking, players: {
                    "turn_token": turn_token,
                    "action": "HELP",
                    "target_id": players[1].seat_name,
                    "message": "deadline passed",
                    "thinking": thinking,
                },
                "DEADLINE_PASSED",
        ),
    ],
)
async def test_error_envelopes_do_not_echo_thinking(
    client,
    reset_db,
    request_path: str,
    method_name: str,
    body_factory,
    expected_code: str,
):
    game, players, _resolved_turn, open_turn = await _seed_two_phase_game(reset_db)
    secret = f"secret-{expected_code.lower()}"

    if expected_code == "WRONG_PHASE":
        # Submitting an action while the turn is still in the talk phase.
        token = open_turn.turn_token
        async with reset_db() as db:
            turn = (await db.execute(select(Turn).where(Turn.id == open_turn.id))).scalar_one()
            turn.phase = "talk"
            await db.commit()
    elif expected_code == "STALE_TURN_TOKEN":
        token = open_turn.turn_token
    else:
        token = open_turn.turn_token
        async with reset_db() as db:
            turn = (await db.execute(select(Turn).where(Turn.id == open_turn.id))).scalar_one()
            turn.phase = "act"
            turn.deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await db.commit()

    response = await getattr(client, method_name)(
        request_path.format(match_id=game.id),
        params={"agent_turn_token": f"{open_turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": players[0]._test_key},
        json=body_factory(token, secret, players),
    )
    assert response.status_code in {409, 410}, response.text
    assert response.json()["detail"]["error"]["code"] == expected_code
    assert secret not in response.text


async def test_late_talk_returns_window_closed_without_echoing_thinking(client, reset_db):
    """A talk message that lands after the talk window closed gets a calm
    "talk_window_closed" answer (not an error), records no message, and never
    echoes the late talk's private `thinking`. The seed's open turn is already in
    the act phase, so posting a talk to it is exactly the slow-agent case."""
    game, players, _resolved_turn, open_turn = await _seed_two_phase_game(reset_db)
    secret = "secret-late-talk-thinking"

    response = await client.post(
        f"/api/games/{game.id}/message",
        params={"agent_turn_token": f"{open_turn.turn_token}:{players[0].agent_id}:{game.id}"},
        headers={"X-Connection-Key": players[0]._test_key},
        json={
            "turn_token": open_turn.turn_token,
            "message": "too late to talk",
            "thinking": secret,
        },
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "talk_window_closed"
    assert body["phase"] == "act"
    assert body["turn_token"] == open_turn.turn_token
    assert secret not in response.text
    assert "thinking" not in body

    # The late talk is not recorded — only the seed's original message remains.
    async with reset_db() as db:
        texts = (
            await db.execute(
                select(TurnMessage.text).where(
                    TurnMessage.turn_id == open_turn.id,
                    TurnMessage.player_id == players[0].id,
                )
            )
        ).scalars().all()
    assert "too late to talk" not in texts


async def test_left_player_between_phases_skips_talk_defaulting_and_act_resolves(
    reset_db,
):
    async with reset_db() as db:
        now = datetime.now(timezone.utc)
        game = Match(
            id="G_LEFT",
            name="left-between-phases",
            state=GameState.ACTIVE,
            scheduled_start=now,
            started_at=now,
            current_round=1,
            current_turn=1,
            total_rounds=1,
            turns_per_round=1,
        )
        db.add(game)
        await db.flush()
        players: list[Player] = []
        for i in range(2):
            players.append(await seat_player(db, game.id, f"AI_{i}", i=i))
        talk_turn = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(minutes=1),
            phase="talk",
        )
        db.add(talk_turn)
        await db.flush()
        db.add(
            TurnMessage(
                turn_id=talk_turn.id,
                player_id=players[0].id,
                text="hello before you leave",
                thinking="talk reasoning",
                was_defaulted=False,
                submitted_at=now,
            )
        )
        players[1].left_at = now + timedelta(seconds=1)
        await db.commit()

        await finalize_talk_phase(db, talk_turn)
        await _begin_act_phase(db, game, talk_turn)
        await resolve_turn(db, talk_turn)

        fresh_turn = (await db.execute(select(Turn).where(Turn.id == talk_turn.id))).scalar_one()
        messages = (
            await db.execute(select(TurnMessage).where(TurnMessage.turn_id == talk_turn.id))
        ).scalars().all()
        submissions = (
            await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == talk_turn.id))
        ).scalars().all()
        by_player_msg = {m.player_id: m for m in messages}
        by_player_sub = {s.player_id: s for s in submissions}
        refreshed_players = (
            await db.execute(select(Player).where(Player.match_id == game.id))
        ).scalars().all()

    assert fresh_turn.talk_resolved_at is not None
    assert fresh_turn.resolved_at is not None
    assert set(by_player_msg) == {players[0].id}
    assert by_player_msg[players[0].id].was_defaulted is False
    assert by_player_msg[players[0].id].text == "hello before you leave"
    assert players[1].id not in by_player_msg
    assert by_player_sub[players[1].id].was_defaulted is True
    assert by_player_sub[players[1].id].action == "HOARD"
    assert by_player_sub[players[0].id].was_defaulted is True
    assert by_player_sub[players[0].id].action == "HOARD"
    assert all(p.current_round_score >= 0 for p in refreshed_players)
    assert sum(p.current_round_score for p in refreshed_players) == 4


async def test_begin_act_phase_keeps_turn_token_stable(reset_db):
    """The talk->act handoff switches phase and resets the deadline but keeps the
    SAME turn_token. Re-minting it here used to drop a slow player's late talk
    (it arrived holding a now-defunct token); one stable token per turn fixes that."""
    async with reset_db() as db:
        now = datetime.now(timezone.utc)
        game = Match(
            id="G_STABLE",
            name="stable-token",
            state=GameState.ACTIVE,
            scheduled_start=now,
            started_at=now,
            current_round=1,
            current_turn=1,
            total_rounds=1,
            turns_per_round=1,
            per_turn_deadline_seconds=60,
        )
        db.add(game)
        await db.flush()
        talk_turn = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token=generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase="talk",
        )
        db.add(talk_turn)
        await db.commit()
        token_before = talk_turn.turn_token
        deadline_before = talk_turn.deadline_at

        await _begin_act_phase(db, game, talk_turn)

        fresh = (await db.execute(select(Turn).where(Turn.id == talk_turn.id))).scalar_one()

    assert fresh.phase == "act"
    assert fresh.turn_token == token_before  # token is stable across the handoff
    assert fresh.deadline_at >= deadline_before  # deadline reset for the act window
