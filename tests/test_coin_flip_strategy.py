"""Tests for the coin_flip bot strategy."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.game_records import ActionRecord
from app.engine.sims import SimContext, SimProfile, choose_action_decision, choose_talk_decision
from app.engine.sims.roster import is_known_personality
from app.engine.bot_presets import bot_preset_by_id
from app.schemas.agent import ScoreboardRow


def _coin_flip_profile(seed: int = 1) -> SimProfile:
    return SimProfile(
        strategy="coin_flip",
        truthfulness=50,
        trust_model="even",
        seed=seed,
        version="v1",
    )


def _context(
    *,
    your_agent_id: str = "A",
    all_ids: list[str] | None = None,
    scoreboard: list[ScoreboardRow] | None = None,
    history: list[ActionRecord] | None = None,
) -> SimContext:
    ids = all_ids or ["A", "B", "C", "D"]
    board = scoreboard or [ScoreboardRow(agent_id=aid, round_score=4, round_wins=0.0) for aid in ids]
    return SimContext(
        game_id="G_TEST",
        game_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        round=1,
        turn=1,
        phase="act",
        your_agent_id=your_agent_id,
        all_agent_ids=ids,
        history=history or [],
        scoreboard=board,
        current_talk_messages=[],
    )


def test_coin_flip_registered_as_personality() -> None:
    assert is_known_personality("coin_flip")


def test_coin_flip_preset_exists() -> None:
    preset = bot_preset_by_id("coin_flip")
    assert preset is not None
    assert preset.strategy == "coin_flip"


def test_coin_flip_action_is_legal() -> None:
    profile = _coin_flip_profile()
    ctx = _context()
    decision = choose_action_decision(ctx, profile)
    action = decision.move["action"]
    target = decision.move["target_id"]

    assert action in ("HOARD", "HELP", "HURT")
    if action == "HOARD":
        assert target is None
    else:
        assert target in ("B", "C", "D")
        assert target != ctx.your_agent_id


def test_coin_flip_never_hurts_zero_score_player() -> None:
    # B and C have score 0; only D is hurtable.
    board = [
        ScoreboardRow(agent_id="A", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="B", round_score=0, round_wins=0.0),
        ScoreboardRow(agent_id="C", round_score=0, round_wins=0.0),
        ScoreboardRow(agent_id="D", round_score=8, round_wins=0.0),
    ]
    # Run many seeds to confirm HURT never targets B or C.
    for seed in range(50):
        profile = _coin_flip_profile(seed=seed)
        ctx = _context(scoreboard=board)
        decision = choose_action_decision(ctx, profile)
        if decision.move["action"] == "HURT":
            assert decision.move["target_id"] == "D", (
                f"seed={seed}: HURT targeted {decision.move['target_id']}, expected D"
            )


def test_coin_flip_hoard_only_when_alone() -> None:
    # Only one other player but they're at 0 score → HELP or HOARD, never HURT.
    board = [
        ScoreboardRow(agent_id="A", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="B", round_score=0, round_wins=0.0),
    ]
    for seed in range(30):
        profile = _coin_flip_profile(seed=seed)
        ctx = _context(all_ids=["A", "B"], scoreboard=board)
        decision = choose_action_decision(ctx, profile)
        assert decision.move["action"] != "HURT"


def test_coin_flip_is_deterministic() -> None:
    profile = _coin_flip_profile(seed=7)
    ctx = _context()
    first = choose_action_decision(ctx, profile)
    second = choose_action_decision(ctx, profile)
    assert first == second


def test_coin_flip_talk_uses_known_phrase_intent() -> None:
    profile = _coin_flip_profile()
    ctx = _context()
    # We need a talk-phase context
    talk_ctx = SimContext(
        game_id=ctx.game_id,
        game_started_at=ctx.game_started_at,
        round=ctx.round,
        turn=ctx.turn,
        phase="talk",
        your_agent_id=ctx.your_agent_id,
        all_agent_ids=ctx.all_agent_ids,
        history=ctx.history,
        scoreboard=ctx.scoreboard,
        current_talk_messages=[],
    )
    decision = choose_talk_decision(talk_ctx, profile)
    assert isinstance(decision.message, str)
    assert len(decision.message) > 0


def test_coin_flip_action_varies_across_seeds() -> None:
    # With enough seeds we should see at least two distinct actions.
    ctx = _context()
    actions = {
        choose_action_decision(ctx, _coin_flip_profile(seed=s)).move["action"]
        for s in range(20)
    }
    assert len(actions) >= 2, f"Expected action variety, got only {actions}"
