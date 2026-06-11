"""Scheduler integration tests for deterministic Sims."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import db as app_db
from app.engine import scheduler
from app.models import Base, Match, GameState, Player, Turn, TurnMessage, TurnSubmission, User
from app.models.agent import AgentKind, AgentStatus
from tests.factories import make_agent


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch, tmp_path):
    from app.db import make_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker as _factory

    test_engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'sims_scheduler.db'}")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    test_factory = _factory(test_engine, expire_on_commit=False)
    monkeypatch.setattr("app.db.SessionLocal", test_factory)
    monkeypatch.setattr("app.db.engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", test_factory)
    monkeypatch.setattr(scheduler, "SessionLocal", test_factory)
    yield test_factory
    await test_engine.dispose()


@pytest.fixture
async def db(reset_db):
    async with reset_db() as session:
        yield session


@pytest.fixture
def published(monkeypatch):
    events: list[tuple[str, str, dict]] = []

    async def fake_publish(channel: str, event_type: str, payload: dict) -> None:
        events.append((channel, event_type, payload))

    monkeypatch.setattr(scheduler, "publish", fake_publish)
    return events


async def _seed_sim_game(db: AsyncSession) -> tuple[Match, list[Player]]:
    game = Match(
        id="G_SIM",
        name="sim-game",
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
    sim_specs = [
        ("AI_00", "leader_pressure", 80, "even", 11),
        ("AI_01", "diplomat", 80, "open", 12),
    ]
    for i, (agent_id, strategy, truthfulness, trust_model, seed) in enumerate(sim_specs):
        user = User(google_sub=f"sub-{i}", email=f"sim{i}@test.com", name=f"sim{i}")
        db.add(user)
        await db.flush()
        agent, _key = await make_agent(
            db,
            user,
            name=f"bot-{agent_id}",
            kind=AgentKind.BOT,
            status=AgentStatus.ACTIVE,
            sim_strategy=strategy,
            sim_truthfulness=truthfulness,
            sim_trust_model=trust_model,
            sim_seed=seed,
            sim_version="v1",
        )
        player = Player(
            match_id=game.id,
            user_id=user.id,
            agent_id=agent.id,
            seat_name=agent_id,
        )
        db.add(player)
        await db.flush()
        players.append(player)

    await db.commit()
    return game, players


@pytest.mark.asyncio
async def test_scheduler_auto_submits_sim_talk_and_actions(db, published):
    game, players = await _seed_sim_game(db)

    await asyncio.wait_for(scheduler._run_game(game.id), timeout=5)

    async with scheduler.SessionLocal() as fresh_db:
        turn = (
            await fresh_db.execute(select(Turn).where(Turn.match_id == game.id))
        ).scalar_one()
        messages = (
            await fresh_db.execute(select(TurnMessage).where(TurnMessage.turn_id == turn.id))
        ).scalars().all()
        submissions = (
            await fresh_db.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)
            )
        ).scalars().all()
        fresh_game = (
            await fresh_db.execute(select(Match).where(Match.id == game.id))
        ).scalar_one()

    assert fresh_game.state is GameState.COMPLETED
    assert turn.talk_resolved_at is not None
    assert turn.resolved_at is not None
    assert len(messages) == len(players)
    assert len(submissions) == len(players)
    assert all(m.was_defaulted is False for m in messages)
    assert all(s.was_defaulted is False for s in submissions)
    assert all(m.text for m in messages)
    assert all(s.action in {"HOARD", "HELP", "HURT"} for s in submissions)
    assert [event for _, event, _ in published[:3]] == [
        "turn_opened",
        "turn_talked",
        "turn_opened",
    ]


@pytest.mark.asyncio
async def test_bot_targeted_move_records_internal_agent_id(db, monkeypatch):
    """A bot HELP/HURT must store the target's internal player id, not its seat name.

    Regression for the frozen-match bug: the bot path handed the public seat
    name straight to record_submission, which resolves a move's target by the
    integer Player.agent_id. On Postgres that raised
    `operator does not exist: integer = character varying` and silently killed
    the turn loop (the whole game froze); on SQLite it silently recorded
    target_player_id=None. Either way the move was wrong. The fix translates
    seat name -> agent_id before recording, like the real-agent API path.
    """
    from app.engine.sims import service
    from app.engine.sims.types import SimActionDecision
    from app.games import get as get_game_module

    game, players = await _seed_sim_game(db)
    actor, target = players[0], players[1]

    def fake_decision(context, profile):
        # Every bot HURTs some other seat, chosen by its public seat name.
        other = next(a for a in context.all_agent_ids if a != context.your_agent_id)
        return SimActionDecision(
            intent="hurt_leader",
            move={"action": "HURT", "target_id": other},
            thinking="t",
        )

    monkeypatch.setattr(service, "choose_bot_action_decision", fake_decision)

    async with scheduler.SessionLocal() as s:
        fresh_game = (
            await s.execute(select(Match).where(Match.id == game.id))
        ).scalar_one()
        turn = Turn(
            match_id=game.id,
            round=1,
            turn=1,
            turn_token="tok",
            opened_at=datetime.now(timezone.utc),
            deadline_at=datetime.now(timezone.utc),
            phase="act",
        )
        s.add(turn)
        await s.commit()
        await s.refresh(turn)
        module = get_game_module(fresh_game.game)
        posted = await service.auto_submit_bot_phase(
            s, fresh_game, turn, module, phase="act"
        )
        turn_id = turn.id

    assert posted == len(players)

    async with scheduler.SessionLocal() as s:
        subs = (
            await s.execute(
                select(TurnSubmission).where(TurnSubmission.turn_id == turn_id)
            )
        ).scalars().all()

    assert len(subs) == len(players)
    actor_sub = next(x for x in subs if x.player_id == actor.id)
    assert actor_sub.action == "HURT"
    # The buggy code stored None here (seat name never matched an integer FK).
    assert actor_sub.target_player_id == target.id


@pytest.mark.asyncio
async def test_turn_loop_crash_persists_incident(db, monkeypatch):
    """A crashed turn loop must leave a queryable incident row.

    Background loop tasks have no HTTP request, so before this their crashes
    never reached request_incidents and a frozen match looked silent in the DB.
    _run_game_guarded now records an incident keyed by match_id plus the
    round/turn it died on.
    """
    from app.models.request_incident import RequestIncident

    game, _players = await _seed_sim_game(db)

    async def boom(*args, **kwargs):
        raise RuntimeError("synthetic turn-loop crash")

    # Make the very first bot phase blow up the loop.
    monkeypatch.setattr(scheduler, "auto_submit_bot_phase", boom)

    with pytest.raises(RuntimeError, match="synthetic turn-loop crash"):
        await scheduler._run_game_guarded(game.id)

    async with scheduler.SessionLocal() as s:
        incidents = (
            await s.execute(
                select(RequestIncident).where(RequestIncident.match_id == game.id)
            )
        ).scalars().all()

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.error_type == "RuntimeError"
    assert "synthetic turn-loop crash" in incident.error_message
    assert incident.stage == "turn_loop"
    assert incident.method == "TASK"
    assert incident.path == "scheduler:_run_game"
    # The round/turn the loop died on is captured for debugging.
    assert '"round": 1' in (incident.context_json or "")
    assert "Traceback" in incident.stacktrace
