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


def test_fetch_full_history_maps_spectator_state(runner, monkeypatch):
    """The catch-up pull maps the public spectator state into the same history
    shape the windowed poll payload uses: messages folded into each action, and
    the game-specific bid fields (quantity/face) dropped, just like the payload."""

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "scoreboard": [{"agent_id": "seat-A", "round_score": 4, "round_wins": 0}],
                "history": [
                    {
                        "round": 1,
                        "turn": 1,
                        "messages": [{"agent_id": "seat-A", "message": "hi"}],
                        "actions": [
                            {
                                "agent_id": "seat-A",
                                "action": "HURT",
                                "target_id": "seat-B",
                                "quantity": None,
                                "face": None,
                                "points_delta": 0,
                            }
                        ],
                    }
                ],
            }

    captured: dict[str, object] = {}

    def fake_get(url, timeout=None):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(runner.httpx, "get", fake_get)

    result = runner._fetch_full_history("http://x", "M_1")

    assert captured["url"] == "http://x/api/spectator/games/M_1/state"
    assert result is not None
    history, scoreboard = result
    assert scoreboard == [{"agent_id": "seat-A", "round_score": 4, "round_wins": 0}]
    assert history == [
        {
            "round": 1,
            "turn": 1,
            "actions": [
                {
                    "agent_id": "seat-A",
                    "action": "HURT",
                    "target_id": "seat-B",
                    "message": "hi",
                    "points_delta": 0,
                }
            ],
        }
    ]


def test_fetch_full_history_fails_open_on_error(runner, monkeypatch):
    """A failed pull returns None (fail-open) so the caller still primes with the
    windowed history it already has — the agent keeps playing."""
    import httpx

    def boom(url, timeout=None):
        raise httpx.HTTPError("network down")

    monkeypatch.setattr(runner.httpx, "get", boom)
    assert runner._fetch_full_history("http://x", "M_1") is None


def test_handle_turn_primes_new_session_with_full_history(runner, monkeypatch):
    """A brand-new chained session is primed with the FULL fetched history, not the
    small window the poll payload carries — so a mid-game restart still opens with
    the whole game in view."""
    full_history = [
        {
            "round": 1,
            "turn": 1,
            "actions": [
                {"agent_id": "seat-A", "action": "HOARD", "target_id": None,
                 "message": "", "points_delta": 2}
            ],
        }
    ]
    full_scoreboard = [{"agent_id": "seat-A", "round_score": 2, "round_wins": 0}]
    monkeypatch.setattr(
        runner, "_fetch_full_history", lambda base, match_id: (full_history, full_scoreboard)
    )

    captured: dict[str, object] = {}

    def fake_decide(turn, sess):
        captured["history"] = turn.get("history")
        captured["scoreboard"] = turn.get("scoreboard")
        return None  # short-circuit before the submit POST

    monkeypatch.setattr(runner, "_decide", fake_decide)

    # The poll payload carries only the recent window; the prime must override it.
    turn = _turn(
        match_id="M_1", agent_id="A", agent_name="Alpha", model="claude-haiku-4-5",
        version_no=1, turn_no=2, token="t",
        history=[{"round": 1, "turn": 1, "actions": []}],
    )
    sess = runner._GameSession(provider="claude", model="claude-haiku-4-5")  # token=None

    runner._handle_turn("http://x", {"X-Connection-Key": "k"}, turn, sess)

    assert captured["history"] == full_history
    assert captured["scoreboard"] == full_scoreboard


def test_handle_turn_resumed_session_skips_the_full_history_pull(runner, monkeypatch):
    """A continuing session reads its delta from the windowed payload and does NOT
    make the extra catch-up request — the pull is for fresh sessions only."""
    called = {"fetched": False}

    def fake_fetch(base, match_id):
        called["fetched"] = True
        return ([], [])

    monkeypatch.setattr(runner, "_fetch_full_history", fake_fetch)

    captured: dict[str, object] = {}

    def fake_decide(turn, sess):
        captured["history"] = turn.get("history")
        return None

    monkeypatch.setattr(runner, "_decide", fake_decide)

    windowed = [{"round": 1, "turn": 3, "actions": []}]
    turn = _turn(
        match_id="M_1", agent_id="A", agent_name="Alpha", model="claude-haiku-4-5",
        version_no=1, turn_no=4, token="t", history=windowed,
    )
    sess = runner._GameSession(provider="claude", model="claude-haiku-4-5", token="session-1")

    runner._handle_turn("http://x", {"X-Connection-Key": "k"}, turn, sess)

    assert called["fetched"] is False
    assert captured["history"] == windowed
