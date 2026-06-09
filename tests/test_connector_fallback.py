"""Tests for the connector's fail-loudly behaviour.

Covers:
- circuit-breaker threshold helper (_poll_failed)
- _decide returning is_connector_fallback=True on LLM failure
- server-side: is_connector_fallback=True sets was_defaulted=True on TurnSubmission
- server-side: is_connector_fallback=True sets was_defaulted=True on TurnMessage
- a genuine (non-fallback) submission sets was_defaulted=False
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.tokens import generate_turn_token
from app.main import app
from app.models import Base, Match, GameState, Player, Turn, TurnMessage, TurnSubmission
from tests.factories import seat_player

# ---------------------------------------------------------------------------
# Load the connector script as a module (same approach as test_runner_payload)
# ---------------------------------------------------------------------------

_CONNECTOR = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"


@pytest.fixture(scope="module")
def connector() -> object:
    spec = importlib.util.spec_from_file_location("agentludum_connector_fb", _CONNECTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Circuit-breaker unit tests (pure, no I/O)
# ---------------------------------------------------------------------------


def test_poll_failed_returns_false_below_threshold(connector) -> None:
    assert connector._poll_failed(0) is False
    assert connector._poll_failed(connector._POLL_FAIL_THRESHOLD - 1) is False


def test_poll_failed_returns_true_at_threshold(connector) -> None:
    assert connector._poll_failed(connector._POLL_FAIL_THRESHOLD) is True


def test_poll_failed_returns_true_above_threshold(connector) -> None:
    assert connector._poll_failed(connector._POLL_FAIL_THRESHOLD + 10) is True


def test_poll_fail_threshold_is_reasonable(connector) -> None:
    # 24 failures × ~5 s each ≈ 2 minutes before giving up — sanity check.
    assert 10 <= connector._POLL_FAIL_THRESHOLD <= 120


# ---------------------------------------------------------------------------
# _decide fallback unit tests
# ---------------------------------------------------------------------------


def _make_turn(
    *,
    match_id: str = "M_1",
    agent_id: str = "A",
    phase: str = "act",
    token: str = "t1",
) -> dict:
    return {
        "status": "your_turn",
        "match_id": match_id,
        "agent_id": agent_id,
        "agent_name": "Alpha",
        "model": "claude-haiku-4-5",
        "version_no": 1,
        "static": {
            "your_agent_id": "seat-A",
            "all_agent_ids": ["seat-A", "seat-other"],
            "your_strategy": "Play to win.",
            "rules": "Rules",
        },
        "history": [],
        "scoreboard": [],
        "current": {"round": 1, "turn": 1, "phase": phase, "turn_token": token},
        "game_id": match_id,
    }


def test_decide_sets_is_connector_fallback_on_act_failure(connector, monkeypatch) -> None:
    """When the LLM subprocess raises RuntimeError, _decide marks is_connector_fallback."""

    class BrokenAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body, framing, model, session):
            raise RuntimeError("subprocess exploded")

        def resume(self, *, body, model, session):
            raise RuntimeError("subprocess exploded")

    monkeypatch.setitem(connector._ADAPTERS, "claude", BrokenAdapter())

    turn = _make_turn(phase="act")
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    decision = connector._decide(turn, sess)

    assert decision.get("is_connector_fallback") is True
    assert decision.get("action") == "HOARD"
    assert sess.token is None  # session was reset


def test_decide_sets_is_connector_fallback_on_talk_failure(connector, monkeypatch) -> None:
    class BrokenAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body, framing, model, session):
            raise subprocess.TimeoutExpired("claude", 180)

        def resume(self, *, body, model, session):
            raise subprocess.TimeoutExpired("claude", 180)

    monkeypatch.setitem(connector._ADAPTERS, "claude", BrokenAdapter())

    turn = _make_turn(phase="talk")
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    decision = connector._decide(turn, sess)

    assert decision.get("is_connector_fallback") is True
    assert decision.get("message") == ""


def test_decide_no_fallback_flag_on_success(connector, monkeypatch) -> None:
    class GoodAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body, framing, model, session):
            session.token = "tok-123"
            return '{"action":"HELP","target_id":"seat-other","thinking":"go"}', None

        def resume(self, *, body, model, session):
            return '{"action":"HOARD","target_id":null,"thinking":"stay"}', None

    monkeypatch.setitem(connector._ADAPTERS, "claude", GoodAdapter())

    turn = _make_turn(phase="act")
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    decision = connector._decide(turn, sess)

    assert "is_connector_fallback" not in decision or not decision["is_connector_fallback"]


# ---------------------------------------------------------------------------
# Server-side HTTP tests: is_connector_fallback persists was_defaulted
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def reset_db(monkeypatch):
    """Fresh in-memory SQLite for each test."""
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


async def _seed_active_game(
    reset_db: async_sessionmaker, *, n_players: int = 2
) -> tuple[Match, list[Player]]:
    async with reset_db() as db:
        game = Match(
            id="G_CB1",
            name="circuit-breaker-test",
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
    phase: str = "act",
    token: str | None = None,
) -> Turn:
    async with reset_db() as db:
        now = datetime.now(timezone.utc)
        turn = Turn(
            match_id=match_id,
            round=1,
            turn=1,
            turn_token=token or generate_turn_token(),
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
            phase=phase,
        )
        db.add(turn)
        await db.commit()
        await db.refresh(turn)
        return turn


@pytest.mark.asyncio
async def test_submit_with_connector_fallback_sets_was_defaulted(client, reset_db) -> None:
    """is_connector_fallback=True on /submit stores was_defaulted=True in DB."""
    game, players = await _seed_active_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="act")
    key = players[0]._test_key
    agent_turn_token = f"{turn.turn_token}:{players[0].agent_id}:{game.id}"

    r = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "action": "HOARD",
            "target_id": None,
            "thinking": "",
            "is_connector_fallback": True,
        },
    )
    assert r.status_code == 202, r.text

    async with reset_db() as db:
        row = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == players[0].id,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.was_defaulted is True
    assert row.action == "HOARD"


@pytest.mark.asyncio
async def test_submit_without_fallback_flag_sets_was_defaulted_false(client, reset_db) -> None:
    """A normal submit (no is_connector_fallback) sets was_defaulted=False."""
    game, players = await _seed_active_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="act")
    key = players[0]._test_key
    agent_turn_token = f"{turn.turn_token}:{players[0].agent_id}:{game.id}"

    r = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "action": "HOARD",
            "target_id": None,
            "thinking": "genuine reasoning",
        },
    )
    assert r.status_code == 202, r.text

    async with reset_db() as db:
        row = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == players[0].id,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.was_defaulted is False


@pytest.mark.asyncio
async def test_message_with_connector_fallback_sets_was_defaulted(client, reset_db) -> None:
    """is_connector_fallback=True on /message stores was_defaulted=True in DB."""
    game, players = await _seed_active_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="talk")
    key = players[0]._test_key
    agent_turn_token = f"{turn.turn_token}:{players[0].agent_id}:{game.id}"

    r = await client.post(
        f"/api/games/{game.id}/message",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "message": "",
            "thinking": "",
            "is_connector_fallback": True,
        },
    )
    assert r.status_code == 202, r.text

    async with reset_db() as db:
        row = (
            await db.execute(
                select(TurnMessage).where(
                    TurnMessage.turn_id == turn.id,
                    TurnMessage.player_id == players[0].id,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.was_defaulted is True


@pytest.mark.asyncio
async def test_fallback_submit_can_be_overridden_by_genuine_submit(client, reset_db) -> None:
    """A fallback submission (was_defaulted=True) can be replaced by a real move."""
    game, players = await _seed_active_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="act")
    key = players[0]._test_key
    agent_turn_token = f"{turn.turn_token}:{players[0].agent_id}:{game.id}"

    # First: connector fallback
    r1 = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "action": "HOARD",
            "target_id": None,
            "thinking": "",
            "is_connector_fallback": True,
        },
    )
    assert r1.status_code == 202, r1.text

    # Second: genuine move overrides the fallback
    r2 = await client.post(
        f"/api/games/{game.id}/submit",
        params={"agent_turn_token": agent_turn_token},
        headers={"X-Connection-Key": key},
        json={
            "turn_token": turn.turn_token,
            "action": "HELP",
            "target_id": players[1].seat_name,
            "thinking": "actually I want to help",
        },
    )
    assert r2.status_code == 202, r2.text

    async with reset_db() as db:
        rows = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == players[0].id,
                )
            )
        ).scalars().all()
    assert len(rows) == 1  # idempotent — only one row
    assert rows[0].action == "HELP"
    assert rows[0].was_defaulted is False
