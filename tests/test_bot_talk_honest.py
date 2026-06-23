"""Truthfulness is how often a bot tells the truth (honest vs. a flat lie). Talk
still reacts to the table, but no longer randomly flips a bot's target between
the talk and act phases."""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.bots import BotContext, BotProfile, choose_bot_action_decision
from app.engine.bots.runtime import choose_bot_talk_decision
from app.engine.bot_presets import BOT_PRESETS
from app.schemas.agent import ScoreboardRow, TalkMessage

IDS = ["AI_1", "AI_2", "AI_3"]


def _ctx(*, talk: list[TalkMessage], turn: int = 5) -> BotContext:
    return BotContext(
        game_id="G",
        game_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        round=1,
        turn=turn,
        phase="act",
        your_agent_id="AI_1",
        all_agent_ids=IDS,
        history=[],
        scoreboard=[ScoreboardRow(agent_id=a, round_score=4, round_wins=0.0) for a in IDS],
        current_talk_messages=talk,
    )


def test_every_bot_preset_is_mostly_honest() -> None:
    # Truthfulness is "how often the bot tells the truth." Every bot sits in the
    # 80-95 band, so the whole table is readable — each bot only lies a little,
    # with the lie-rate matching its character (Pragmatist lowest at 80).
    for preset in BOT_PRESETS:
        assert 80 <= preset.truthfulness <= 95, (preset.id, preset.truthfulness)


def test_truthfulness_picks_honest_or_a_lie() -> None:
    # Two modes only. A fully-truthful bot is always honest; a 0 bot always lies.
    honest = {
        choose_bot_talk_decision(
            _ctx(talk=[], turn=2),
            BotProfile(strategy="coalition_seeker", truthfulness=100, trust_model="even", seed=s, version="v1"),
        ).truth_mode
        for s in range(20)
    }
    liar = {
        choose_bot_talk_decision(
            _ctx(talk=[], turn=2),
            BotProfile(strategy="coalition_seeker", truthfulness=0, trust_model="even", seed=s, version="v1"),
        ).truth_mode
        for s in range(20)
    }
    assert honest == {"honest"}
    assert liar == {"false"}


def test_seed_ignores_this_turns_talk() -> None:
    # Two contexts identical except the talk produce the same deterministic seed,
    # so a bot's tie-breaks don't shift between the (talk-blind) talk phase and
    # the (talk-aware) act phase — no more "names one player, hits another".
    quiet = _ctx(talk=[])
    noisy = _ctx(talk=[TalkMessage(agent_id="AI_2", message="AI_3, I'm watching the whole table")])
    assert quiet.seed_basis() == noisy.seed_basis()


def test_action_still_reacts_to_a_cooperation_offer() -> None:
    # Talk is still heard: a cooperation offer pulls a coalition_seeker into
    # helping the player who offered. (Past the ice-breaker window.)
    profile = BotProfile(
        strategy="coalition_seeker", truthfulness=80, trust_model="even", seed=1, version="v1"
    )
    no_offer = choose_bot_action_decision(_ctx(talk=[]), profile).move
    offer = [TalkMessage(agent_id="AI_2", message="AI_1, let's pair up — mutual help this turn!")]
    with_offer = choose_bot_action_decision(_ctx(talk=offer), profile).move
    assert no_offer["action"] == "HOARD"
    assert with_offer == {"action": "HELP", "target_id": "AI_2"}
