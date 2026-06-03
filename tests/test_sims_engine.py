"""Tests for the deterministic Sims engine."""

from __future__ import annotations

from app.engine.game_records import ActionRecord
from app.engine.sims import (
    SimContext,
    SimProfile,
    choose_action_decision,
    choose_talk_decision,
    compute_trust_map,
    extract_talk_signals,
)
from app.schemas.agent import ScoreboardRow, TalkMessage


def _context(
    *,
    all_agent_ids: list[str] | None = None,
    your_agent_id: str = "AI_1",
    round_: int = 1,
    turn: int = 2,
    scoreboard: list[ScoreboardRow] | None = None,
    history: list[ActionRecord] | None = None,
    talk: list[TalkMessage] | None = None,
) -> SimContext:
    agent_ids = all_agent_ids or ["AI_1", "AI_2", "AI_3", "AI_10"]
    board = scoreboard or [
        ScoreboardRow(agent_id="AI_1", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=12, round_wins=0.0),
        ScoreboardRow(agent_id="AI_3", round_score=10, round_wins=0.0),
        ScoreboardRow(agent_id="AI_10", round_score=12, round_wins=0.0),
    ]
    return SimContext(
        game_id="G_1",
        round=round_,
        turn=turn,
        phase="act",
        your_agent_id=your_agent_id,
        all_agent_ids=agent_ids,
        history=history or [],
        scoreboard=board,
        current_talk_messages=talk or [],
    )


def test_extract_talk_signals_matches_exact_agent_ids() -> None:
    messages = [
        TalkMessage(agent_id="AI_2", message="AI_10, I can help you and keep things steady."),
    ]
    signals = extract_talk_signals(messages, all_agent_ids=["AI_1", "AI_2", "AI_10"])
    assert any(s.kind == "direct_mention" and s.target_id == "AI_10" for s in signals)
    assert any(s.kind == "cooperation_offer" and s.target_id == "AI_10" for s in signals)
    assert not any(s.target_id == "AI_1" for s in signals)


def test_trust_clamps_to_bounds() -> None:
    history = [
        ActionRecord(
            round=1,
            turn=turn,
            actor_id="AI_2",
            action="HURT",
            target_id="AI_1",
            message="",
            points_delta=-4,
            round_score_after=0,
            was_defaulted=False,
        )
        for turn in range(1, 51)
    ]
    trust = compute_trust_map(
        your_agent_id="AI_1",
        all_agent_ids=["AI_1", "AI_2"],
        history=history,
        signals=[],
        trust_model="even",
    )
    assert trust["AI_2"] == -100


def test_leader_pressure_hurts_the_leader() -> None:
    context = _context()
    profile = SimProfile(strategy="leader_pressure", truthfulness=80, trust_model="even", seed=42, version="v1")
    decision = choose_action_decision(context, profile)
    assert decision.move == {"action": "HURT", "target_id": "AI_2"}


def test_crowd_follower_copies_majority_action() -> None:
    history = [
        ActionRecord(
            round=1,
            turn=1,
            actor_id="AI_2",
            action="HELP",
            target_id="AI_3",
            message="",
            points_delta=0,
            round_score_after=0,
            was_defaulted=False,
        ),
        ActionRecord(
            round=1,
            turn=1,
            actor_id="AI_4",
            action="HELP",
            target_id="AI_3",
            message="",
            points_delta=0,
            round_score_after=0,
            was_defaulted=False,
        ),
        ActionRecord(
            round=1,
            turn=1,
            actor_id="AI_5",
            action="HURT",
            target_id="AI_1",
            message="",
            points_delta=0,
            round_score_after=0,
            was_defaulted=False,
        ),
    ]
    context = _context(history=history, scoreboard=[ScoreboardRow(agent_id=a, round_score=i, round_wins=0.0) for i, a in enumerate(["AI_1", "AI_2", "AI_3", "AI_4", "AI_5"], start=1)])
    profile = SimProfile(strategy="crowd_follower", truthfulness=80, trust_model="even", seed=99, version="v1")
    decision = choose_action_decision(context, profile)
    assert decision.move == {"action": "HELP", "target_id": "AI_3"}


def test_decisions_are_deterministic() -> None:
    context = _context()
    profile = SimProfile(strategy="coalition_seeker", truthfulness=80, trust_model="open", seed=7, version="v1")
    first = choose_talk_decision(context, profile)
    second = choose_talk_decision(context, profile)
    assert first == second


def test_seeded_tie_breaks_do_not_depend_on_agent_order() -> None:
    board = [
        ScoreboardRow(agent_id="AI_1", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=12, round_wins=0.0),
        ScoreboardRow(agent_id="AI_10", round_score=12, round_wins=0.0),
    ]
    context_a = _context(
        all_agent_ids=["AI_1", "AI_2", "AI_10"],
        scoreboard=board,
    )
    context_b = _context(
        all_agent_ids=["AI_10", "AI_2", "AI_1"],
        scoreboard=list(reversed(board)),
    )
    profile = SimProfile(strategy="leader_pressure", truthfulness=80, trust_model="even", seed=11, version="v1")
    assert choose_action_decision(context_a, profile).move == choose_action_decision(context_b, profile).move

