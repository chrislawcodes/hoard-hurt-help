"""Runner payload tests for per-agent sessions and model selection."""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def test_decide_records_codex_usage(runner, monkeypatch):
    stdout = "\n".join(
        [
            '{"type":"thread.started","thread_id":"thread-123"}',
            '{"type":"turn.started"}',
            (
                '{"type":"turn.completed","usage":{'
                '"input_tokens":11434,'
                '"cached_input_tokens":4480,'
                '"output_tokens":32,'
                '"reasoning_output_tokens":21'
                "}}"
            ),
        ]
    )

    class Proc:
        def __init__(self, stdout_text: str) -> None:
            self.returncode = 0
            self.stdout = stdout_text
            self.stderr = ""

    def fake_run(argv, stdin_input=None):
        out_file = Path(argv[argv.index("--output-last-message") + 1])
        out_file.write_text('{"action":"HOARD","target_id":null,"thinking":"x"}')
        return Proc(stdout)

    recorded: list[tuple[str, int, int, dict[str, int]]] = []

    def fake_record_usage(game_id, cur, usage, sess):
        recorded.append((game_id, cur["round"], cur["turn"], dict(usage)))

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_record_usage", fake_record_usage)

    turn = _turn(
        match_id="M_1",
        agent_id="A",
        agent_name="Alpha",
        model="gpt-5.4-mini",
        version_no=1,
        turn_no=2,
        token="turn-a-2",
    )
    sess = runner._GameSession(provider="openai", model="gpt-5.4-mini")

    move = runner._decide(turn, sess)

    assert move == {"action": "HOARD", "target_id": None, "thinking": "x"}
    assert sess.token == "thread-123"
    assert recorded == [
        (
            "M_1",
            1,
            2,
            {
                "fresh_in": 6954,
                "cache_write": 0,
                "cache_read": 4480,
                "out": 53,
            },
        )
    ]


def test_decide_records_gemini_usage(runner, monkeypatch):
    stdout = json.dumps(
        {
            "session_id": "fd9a7884-4dbd-4251-808c-8cadea13dbb9",
            "response": '{"action":"HOARD","target_id":null,"thinking":"x"}',
            "stats": {
                "models": {
                    "gemini-3-flash-preview": {
                        "tokens": {
                            "input": 9291,
                            "prompt": 9291,
                            "candidates": 5,
                            "total": 9331,
                            "cached": 4480,
                            "thoughts": 35,
                            "tool": 0,
                        }
                    }
                }
            },
        }
    )

    class Proc:
        def __init__(self, stdout_text: str) -> None:
            self.returncode = 0
            self.stdout = stdout_text
            self.stderr = ""

    def fake_run(argv, stdin_input=None):
        return Proc(stdout)

    recorded: list[tuple[str, int, int, dict[str, int]]] = []

    def fake_record_usage(game_id, cur, usage, sess):
        recorded.append((game_id, cur["round"], cur["turn"], dict(usage)))

    monkeypatch.setattr(runner, "_run", fake_run)
    monkeypatch.setattr(runner, "_record_usage", fake_record_usage)

    turn = _turn(
        match_id="M_2",
        agent_id="A",
        agent_name="Alpha",
        model="gemini-3-flash-preview",
        version_no=1,
        turn_no=3,
        token="turn-a-3",
    )
    sess = runner._GameSession(provider="gemini", model="gemini-3-flash-preview")

    move = runner._decide(turn, sess)

    assert move == {"action": "HOARD", "target_id": None, "thinking": "x"}
    assert sess.token is not None
    assert recorded == [
        (
            "M_2",
            1,
            3,
            {
                "fresh_in": 4811,
                "cache_write": 0,
                "cache_read": 4480,
                "out": 40,
            },
        )
    ]
