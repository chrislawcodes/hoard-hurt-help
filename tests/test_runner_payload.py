"""Runner payload tests for per-agent sessions and model selection."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agentludum_connector.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("agentludum_connector", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _args(provider=None, model=None):
    return argparse.Namespace(provider=provider, model=model)


def _turn(
    *,
    match_id: str,
    agent_id: str,
    agent_name: str,
    model: str,
    version_no: int,
    turn_no: int,
    token: str,
    history: list[dict] | None = None,
) -> dict:
    return {
        "status": "your_turn",
        "match_id": match_id,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "model": model,
        "version_no": version_no,
        "static": {
            "your_agent_id": f"seat-{agent_id}",
            "all_agent_ids": [f"seat-{agent_id}", "seat-other"],
            "your_strategy": "Play to win.",
            "rules": "Rules",
        },
        "history": history or [],
        "scoreboard": [],
        "current": {"round": 1, "turn": turn_no, "phase": "act", "turn_token": token},
        "game_id": match_id,
    }


def test_model_prefix_infers_provider(runner):
    assert runner._resolve(
        _turn(
            match_id="M_1",
            agent_id="A",
            agent_name="Alpha",
            model="claude-sonnet-4-6",
            version_no=1,
            turn_no=1,
            token="t1",
        ),
        _args(),
    ) == ("claude", "claude-sonnet-4-6")


def test_sessions_are_scoped_by_agent_and_match(runner, monkeypatch):
    calls: list[tuple[str, str, str, str | None]] = []

    class FakeClaudeAdapter:
        default_model = "claude-haiku-4-5"

        def first(self, *, body: str, framing: str, model: str, session):
            calls.append(("first", model, session.token or "", framing[:16]))
            session.token = f"session-{model}"
            return '{"action":"HOARD","target_id":null,"thinking":"x"}', None

        def resume(self, *, body: str, model: str, session):
            calls.append(("resume", model, session.token or "", body[:16]))
            return '{"action":"HOARD","target_id":null,"thinking":"x"}', None

    monkeypatch.setitem(runner._ADAPTERS, "claude", FakeClaudeAdapter())

    sessions: dict[tuple[str, str], object] = {}

    turn_a_1 = _turn(
        match_id="M_1",
        agent_id="A",
        agent_name="Alpha",
        model="claude-haiku-4-5",
        version_no=1,
        turn_no=1,
        token="turn-a-1",
    )
    sess_a = runner._session_for_turn(turn_a_1, _args(), sessions)
    runner._decide(turn_a_1, sess_a)

    turn_b_1 = _turn(
        match_id="M_1",
        agent_id="B",
        agent_name="Beta",
        model="claude-opus-4-8",
        version_no=2,
        turn_no=1,
        token="turn-b-1",
    )
    sess_b = runner._session_for_turn(turn_b_1, _args(), sessions)
    runner._decide(turn_b_1, sess_b)

    turn_a_2 = _turn(
        match_id="M_1",
        agent_id="A",
        agent_name="Alpha",
        model="claude-haiku-4-5",
        version_no=1,
        turn_no=2,
        token="turn-a-2",
        history=[{"round": 1, "turn": 1, "agent_id": "seat-A"}],
    )
    sess_a_again = runner._session_for_turn(turn_a_2, _args(), sessions)
    runner._decide(turn_a_2, sess_a_again)

    assert sess_a is sess_a_again
    assert sess_b is sessions[("B", "M_1")]
    assert len(sessions) == 2
    assert calls[0][0:2] == ("first", "claude-haiku-4-5")
    assert calls[1][0:2] == ("first", "claude-opus-4-8")
    assert calls[2][0:2] == ("resume", "claude-haiku-4-5")

