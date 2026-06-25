"""Behavior tests for the reworked bot personalities and the ice-breaker."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.bots import (
    BotContext,
    BotProfile,
    choose_bot_action_decision,
    choose_bot_talk_decision,
)
from app.engine.bots.strategies import VALID_STRATEGIES
from app.engine.bot_presets import BOT_PRESETS, bot_preset_by_id
from app.engine.game_records import ActionRecord
from app.schemas.agent import ScoreboardRow

IDS = ["AI_1", "AI_2", "AI_3"]


def _ctx(
    *,
    strategy: str,
    history: list[ActionRecord],
    scores: dict[str, int] | None = None,
    turn: int = 5,
    me: str = "AI_1",
) -> tuple[BotContext, BotProfile]:
    s = scores or {a: 4 for a in IDS}
    board = [ScoreboardRow(agent_id=a, round_score=s.get(a, 0), round_wins=0.0) for a in IDS]
    ctx = BotContext(
        game_id="G",
        game_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        round=1,
        turn=turn,
        phase="act",
        your_agent_id=me,
        all_agent_ids=IDS,
        history=history,
        scoreboard=board,
        current_talk_messages=[],
    )
    profile = BotProfile(strategy=strategy, truthfulness=80, trust_model="even", seed=3, version="v1")
    return ctx, profile


def _helped(actor: str, target: str, turn: int = 4) -> ActionRecord:
    return ActionRecord(
        round=1, turn=turn, actor_id=actor, action="HELP", target_id=target,
        message="", points_delta=4, round_score_after=8, was_defaulted=False,
    )


def _hurt(actor: str, target: str, turn: int = 4) -> ActionRecord:
    return ActionRecord(
        round=1, turn=turn, actor_id=actor, action="HURT", target_id=target,
        message="", points_delta=-4, round_score_after=4, was_defaulted=False,
    )


# --- universal ice-breaker -------------------------------------------------


def test_icebreaker_reaches_out_in_the_first_turns() -> None:
    # Turn 1, no history: a bot that would otherwise hoard instead helps, so
    # cooperation can start.
    for strategy in ("coalition_seeker", "opportunist", "crowd_follower", "grudger"):
        ctx, profile = _ctx(strategy=strategy, history=[], scores={a: 0 for a in IDS}, turn=1)
        assert choose_bot_action_decision(ctx, profile).move["action"] == "HELP", strategy


def test_icebreaker_stops_after_turn_three() -> None:
    # Past the opening window, a bot with nothing to act on hoards again.
    ctx, profile = _ctx(strategy="opportunist", history=[], scores={a: 0 for a in IDS}, turn=4)
    assert choose_bot_action_decision(ctx, profile).move["action"] == "HOARD"


# --- Pragmatist: cooperate then betray -------------------------------------


def test_pragmatist_cooperates_then_betrays_at_the_buzzer() -> None:
    history = [_helped("AI_2", "AI_1")]  # AI_2 just helped me
    # Mid-round: it reciprocates (cooperates).
    ctx, profile = _ctx(strategy="pragmatist", history=history, turn=5)
    assert choose_bot_action_decision(ctx, profile).move == {"action": "HELP", "target_id": "AI_2"}
    # Final turn: it betrays the partner it expects to still help it — HURTing a
    # helper lands for the full -8. It targets AI_2 (the recent helper).
    ctx_last, profile = _ctx(strategy="pragmatist", history=history, turn=7)
    assert choose_bot_action_decision(ctx_last, profile).move == {
        "action": "HURT",
        "target_id": "AI_2",
    }
    # And it bluffs cooperatively in the talk phase so the partner still helps.
    assert choose_bot_talk_decision(ctx_last, profile).truth_mode == "false"


# --- Opportunist: cooperate when actually helped ---------------------------


def test_opportunist_reciprocates_real_help() -> None:
    history = [_helped("AI_2", "AI_1")]
    ctx, profile = _ctx(strategy="opportunist", history=history, turn=5)
    assert choose_bot_action_decision(ctx, profile).move == {"action": "HELP", "target_id": "AI_2"}


# --- Long Memory (grudger id): remembers both ------------------------------


def test_long_memory_rewards_a_helper() -> None:
    history = [_helped("AI_3", "AI_1")]
    ctx, profile = _ctx(strategy="grudger", history=history, turn=5)
    assert choose_bot_action_decision(ctx, profile).move == {"action": "HELP", "target_id": "AI_3"}


def test_long_memory_punishes_an_attacker() -> None:
    history = [_hurt("AI_2", "AI_1")]
    ctx, profile = _ctx(strategy="grudger", history=history, scores={"AI_1": 4, "AI_2": 8, "AI_3": 4}, turn=5)
    assert choose_bot_action_decision(ctx, profile).move == {"action": "HURT", "target_id": "AI_2"}


# --- roster bookkeeping ----------------------------------------------------


def test_coin_flip_gone_pragmatist_added() -> None:
    assert "coin_flip" not in VALID_STRATEGIES
    assert "pragmatist" in VALID_STRATEGIES
    assert bot_preset_by_id("coin_flip") is None
    names = {p.name for p in BOT_PRESETS}
    assert {"Pragmatist", "Long Memory", "Giant Slayer", "The Closer", "Instigator"} <= names
    assert "Coin Flip" not in names and "Grudger" not in names
