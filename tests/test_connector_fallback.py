"""Tests for the connector's fail-loudly behaviour.

Covers:
- circuit-breaker threshold helper (_poll_failed)
- _decide returning is_connector_fallback=True on LLM failure
- server-side: is_connector_fallback=True sets was_defaulted=True on TurnSubmission
- server-side: is_connector_fallback=True sets was_defaulted=True on TurnMessage
- a genuine (non-fallback) submission sets was_defaulted=False
- concurrency: the in-flight release callback frees a session slot (even on crash)
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.engine.tokens import generate_turn_token
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


def test_decide_attaches_model_failure_marker(connector, monkeypatch) -> None:
    """A real model-subprocess failure attaches a model_failure marker so
    _handle_turn can flip the model's verification status (fail-loud, slice 3)."""

    class UnavailableAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body, framing, model, session):
            raise RuntimeError("model not found (404)")

        def resume(self, *, body, model, session):
            raise RuntimeError("model not found (404)")

    monkeypatch.setitem(connector._ADAPTERS, "claude", UnavailableAdapter())
    turn = _make_turn(phase="act")
    sess = connector._GameSession(provider="claude", model="claude-opus-4-8")

    decision = connector._decide(turn, sess)
    assert decision.get("model_failure") == {
        "provider": "claude",
        "model": "claude-opus-4-8",
        "outcome": "failed",
        "error_text": "model not found (404)",
    }


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


# ---------------------------------------------------------------------------
# Deadline-aware budgeting — keep the move inside the phase deadline
# ---------------------------------------------------------------------------


def test_phase_time_budget_none_when_no_deadline(connector) -> None:
    assert connector._phase_time_budget({"phase": "act"}) is None


def test_phase_time_budget_reserves_submit_buffer(connector) -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    cur = {"deadline": (now + timedelta(seconds=60)).isoformat()}
    assert connector._phase_time_budget(cur, now=now) == 60 - connector._SUBMIT_BUFFER_SECONDS


def test_phase_time_budget_negative_when_deadline_passed(connector) -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    cur = {"deadline": (now - timedelta(seconds=5)).isoformat()}
    assert connector._phase_time_budget(cur, now=now) < 0


class _ExplodingAdapter:
    default_model = "claude-haiku-4-5"

    def first(self, **kwargs):
        raise AssertionError("model must not be called when there isn't time")

    def resume(self, **kwargs):
        raise AssertionError("model must not be called when there isn't time")


def test_decide_skips_dead_phase_when_deadline_passed(connector, monkeypatch) -> None:
    """A past-deadline phase returns None (skip) — no submit, no model call.

    Submitting here would only 410 and busy-loop; the caller must skip to the
    next live phase instead.
    """
    monkeypatch.setitem(connector._ADAPTERS, "claude", _ExplodingAdapter())

    turn = _make_turn(phase="act")
    turn["current"]["deadline"] = (
        datetime.now(timezone.utc) - timedelta(seconds=3)
    ).isoformat()
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    assert connector._decide(turn, sess) is None


def test_decide_falls_back_when_some_time_but_not_enough(connector, monkeypatch) -> None:
    """A little time left (but not enough to think) → a real fallback that can land."""
    monkeypatch.setitem(connector._ADAPTERS, "claude", _ExplodingAdapter())

    turn = _make_turn(phase="act")
    # ~12s out → budget ≈ 12 - 8 = 4s: positive but below _MIN_MODEL_SECONDS.
    turn["current"]["deadline"] = (
        datetime.now(timezone.utc) + timedelta(seconds=12)
    ).isoformat()
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    decision = connector._decide(turn, sess)
    assert decision is not None
    assert decision.get("is_connector_fallback") is True
    assert decision.get("action") == "HOARD"


def test_decide_bounds_model_call_to_remaining_time(connector, monkeypatch) -> None:
    """The model call is budgeted to the time left, not the full _TURN_TIMEOUT,
    and the budget context is cleared afterwards."""
    seen: dict[str, float | None] = {}

    class BudgetSpyAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body, framing, model, session):
            seen["budget"] = connector._call_timeout.get()
            session.token = "tok"
            return '{"action":"HOARD","target_id":null,"thinking":"x"}', None

        def resume(self, *, body, model, session):
            seen["budget"] = connector._call_timeout.get()
            return '{"action":"HOARD","target_id":null,"thinking":"x"}', None

    monkeypatch.setitem(connector._ADAPTERS, "claude", BudgetSpyAdapter())

    turn = _make_turn(phase="act")
    turn["current"]["deadline"] = (
        datetime.now(timezone.utc) + timedelta(seconds=45)
    ).isoformat()
    sess = connector._GameSession(provider="claude", model="claude-haiku-4-5")

    connector._decide(turn, sess)

    assert seen["budget"] is not None
    assert 20 < seen["budget"] < 45 < connector._TURN_TIMEOUT
    assert connector._call_timeout.get() is None  # reset after the call


# ---------------------------------------------------------------------------
# Move POST contract — the connector must send agent_turn_token (regression)
# ---------------------------------------------------------------------------


def _payload_for(turn: Turn, player: Player, match_id: str, *, phase: str) -> dict:
    """A minimal next-turn payload shaped like the server's response."""
    return {
        "agent_turn_token": f"{turn.turn_token}:{player.agent_id}:{match_id}",
        "current": {
            "round": 1,
            "turn": 1,
            "phase": phase,
            "turn_token": turn.turn_token,
        },
    }


def test_move_request_includes_agent_turn_token(connector) -> None:
    """Both /message and /submit must carry agent_turn_token in the query params."""

    class _P:
        agent_id = 7

    turn = type("T", (), {"turn_token": "tok-9"})()
    talk_url, talk_params, _ = connector._move_request(
        "", "M_1", _payload_for(turn, _P(), "M_1", phase="talk"), {"message": "hi"}
    )
    act_url, act_params, _ = connector._move_request(
        "", "M_1", _payload_for(turn, _P(), "M_1", phase="act"), {"action": "HOARD"}
    )

    assert talk_url.endswith("/message") and act_url.endswith("/submit")
    assert talk_params["agent_turn_token"] == "tok-9:7:M_1"
    assert act_params["agent_turn_token"] == "tok-9:7:M_1"


@pytest.mark.asyncio
async def test_connector_submit_lands_on_real_endpoint(connector, client, reset_db) -> None:
    """The connector's POST (as built by _move_request) is accepted by the live
    /submit endpoint and records a genuine, non-defaulted move.

    Regression: the connector omitted the required agent_turn_token query param,
    so every submission 422'd and the agent defaulted every single turn.
    """
    game, players = await _seed_active_game(reset_db)
    turn = await _open_turn(reset_db, game.id, phase="act")
    player = players[0]

    payload = _payload_for(turn, player, game.id, phase="act")
    decision = {"action": "HOARD", "target_id": None, "thinking": "bank it"}
    url, params, body = connector._move_request("", game.id, payload, decision)

    assert "agent_turn_token" in params  # the missing piece that caused the bug

    r = await client.post(
        url, params=params, headers={"X-Connection-Key": player._test_key}, json=body
    )
    assert r.status_code == 202, r.text

    async with reset_db() as db:
        row = (
            await db.execute(
                select(TurnSubmission).where(
                    TurnSubmission.turn_id == turn.id,
                    TurnSubmission.player_id == player.id,
                )
            )
        ).scalar_one_or_none()
    assert row is not None
    assert row.was_defaulted is False  # a real move landed — not a default
    assert row.action == "HOARD"


# ---------------------------------------------------------------------------
# Time-budget awareness — the model is told how long it has each phase
# ---------------------------------------------------------------------------


def test_time_left_note_reports_seconds_from_deadline(connector) -> None:
    cur = {
        "phase": "act",
        "deadline": (datetime.now(timezone.utc) + timedelta(seconds=50)).isoformat(),
    }
    note = connector._time_left_note(cur)
    assert "to reply" in note
    # ~50s minus the submit buffer, rounded down — a small positive number.
    assert any(str(n) in note for n in range(35, 50))


def test_time_left_note_empty_without_deadline(connector) -> None:
    assert connector._time_left_note({"phase": "act"}) == ""


def test_phase_suffix_includes_the_clock(connector) -> None:
    cur = {
        "phase": "talk",
        "deadline": (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat(),
    }
    suffix = connector._phase_suffix(cur)
    assert "TALK PHASE" in suffix
    assert "to reply" in suffix


def test_protocol_states_the_deadline_and_200_character_limits(connector) -> None:
    assert "hard deadline" in connector._PROTOCOL
    assert "max 200 chars" in connector._PROTOCOL
    assert "max 500 chars" not in connector._PROTOCOL


# ---------------------------------------------------------------------------
# Concurrency: the in-flight release callback (pure, no real threads needed)
# ---------------------------------------------------------------------------


def test_release_cb_frees_the_session_slot_on_success(connector) -> None:
    key = ("7", "M_0701")
    in_flight = {key}
    lock = threading.Lock()
    fut: Future = Future()
    fut.add_done_callback(connector._make_release_cb(key, in_flight, lock))
    fut.set_result(None)  # fires the callback
    assert key not in in_flight


def test_release_cb_frees_the_slot_even_when_the_worker_crashes(
    connector, capsys
) -> None:
    """A crashed worker must not wedge its session forever: the slot is freed and
    the crash is surfaced on stderr rather than swallowed."""
    key = ("9", "M_0702")
    in_flight = {key}
    lock = threading.Lock()
    fut: Future = Future()
    fut.add_done_callback(connector._make_release_cb(key, in_flight, lock))
    fut.set_exception(RuntimeError("boom"))
    assert key not in in_flight
    err = capsys.readouterr().err
    assert "crashed" in err
    assert "boom" in err
