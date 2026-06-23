"""Bot talk should telegraph the move it is about to make.

These tests pin the two properties we want from the rewritten talk:

1. Honest talk points at the real move — a HELP line reads as a cooperation
   offer, a HURT line reads as a threat, and both name the target.
2. The truthfulness knob still buys real deception — a low-truth bot can hide a
   HURT behind a friendly bluff, while the move itself is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.engine.bots import (
    BotContext,
    BotProfile,
    choose_bot_action_decision,
    choose_bot_talk_decision,
    extract_talk_signals,
)
from app.engine.bots.phrases import PHRASES, VARIANTS_PER_BUCKET
from app.engine.bots.signals import _LEADER_WORDS, _OFFER_WORDS, _THREAT_WORDS
from app.engine.game_records import ActionRecord
from app.schemas.agent import ScoreboardRow, TalkMessage

HELP_INTENTS = {"offer_help", "keep_ally", "repay_help", "mend_fences"}
HURT_INTENTS = {"hit_back", "curb_leader", "block_rival"}
# HURT talk splits by what makes sense for the attack (talk is persuasion, and
# announcing a hit only warns the victim):
THREAT_HURT_INTENTS = {"hit_back", "block_rival"}  # deter / warn — carry a threat
RALLY_HURT_INTENTS = {"curb_leader"}  # rally the table — carry a leader word
# A target token that contains none of the signal keywords, so the assertions
# below test the phrase wording itself rather than the name.
TOKEN = "Zed"


def _has(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _ctx(
    *,
    ids: list[str],
    scoreboard: list[ScoreboardRow],
    history: list[ActionRecord],
    phase: str,
    me: str = "AI_1",
    turn: int = 2,
) -> BotContext:
    return BotContext(
        game_id="G",
        game_started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        round=1,
        turn=turn,
        phase=phase,
        your_agent_id=me,
        all_agent_ids=ids,
        history=history,
        scoreboard=scoreboard,
        current_talk_messages=[],
    )


def _runaway_leader_board() -> list[ScoreboardRow]:
    return [
        ScoreboardRow(agent_id="AI_1", round_score=2, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=2, round_wins=0.0),
        ScoreboardRow(agent_id="AI_3", round_score=20, round_wins=0.0),
    ]


# --- variety --------------------------------------------------------------


def test_every_bucket_has_eight_distinct_variants() -> None:
    # Bots should not repeat themselves: each (intent, truth mode) bucket holds
    # VARIANTS_PER_BUCKET distinct lines, all of which render without leaving a
    # stray placeholder behind.
    for intent, modes in PHRASES.items():
        for mode, lines in modes.items():
            assert len(lines) == VARIANTS_PER_BUCKET, (intent, mode, len(lines))
            assert len(set(lines)) == len(lines), f"duplicate line in {intent}/{mode}"
            for template in lines:
                rendered = template.format(target_name=TOKEN)
                assert "{" not in rendered and "}" not in rendered, (intent, mode, rendered)
                assert 0 < len(rendered) <= 200, (intent, mode, rendered)


# --- phrase-library keyword contract --------------------------------------


def test_help_lines_read_as_cooperation() -> None:
    for intent in HELP_INTENTS:
        for mode in ("honest", "partial"):
            for template in PHRASES[intent][mode]:
                text = template.format(target_name=TOKEN)
                assert _has(text, _OFFER_WORDS), (intent, mode, text)
                assert not _has(text, _THREAT_WORDS), (intent, mode, text)


def test_deter_and_warn_lines_carry_a_threat() -> None:
    # hit_back (deterrent) and block_rival (warning) name the target and carry a
    # threat word, never an offer word.
    for intent in THREAT_HURT_INTENTS:
        for mode in ("honest", "partial"):
            for template in PHRASES[intent][mode]:
                text = template.format(target_name=TOKEN)
                assert "{target_name}" in template, (intent, mode, template)
                assert _has(text, _THREAT_WORDS), (intent, mode, text)
                assert not _has(text, _OFFER_WORDS), (intent, mode, text)


def test_curb_leader_lines_rally_the_table() -> None:
    # curb_leader recruits others against the leader: it names the leader and
    # carries a leader word (so it reads as a leader_warning), and never an
    # offer word.
    for mode in ("honest", "partial"):
        for template in PHRASES["curb_leader"][mode]:
            text = template.format(target_name=TOKEN)
            assert "{target_name}" in template, (mode, template)
            assert _has(text, _LEADER_WORDS), (mode, text)
            assert not _has(text, _OFFER_WORDS), (mode, text)


def test_false_hurt_lines_read_as_friendly_bluffs() -> None:
    # A bot about to HURT but lying should sound cooperative: offer words, no
    # threat. That is what makes the betrayal land on other bots.
    for intent in HURT_INTENTS:
        for template in PHRASES[intent]["false"]:
            text = template.format(target_name=TOKEN)
            assert _has(text, _OFFER_WORDS), (intent, text)
            assert not _has(text, _THREAT_WORDS), (intent, text)


def test_hurt_misdirecting_lines_hide_the_hit() -> None:
    # The downplay mode must leak neither a threat nor an offer, so it reads as
    # a genuine "not my problem" deflection rather than a tell.
    for intent in HURT_INTENTS:
        for template in PHRASES[intent]["misdirecting"]:
            text = template.format(target_name=TOKEN)
            assert not _has(text, _THREAT_WORDS), (intent, text)
            assert not _has(text, _OFFER_WORDS), (intent, text)


def test_help_false_lines_hide_the_help() -> None:
    # A bot about to HELP but lying should sound cold — no offer word — so it
    # never leaks a cooperation signal it doesn't want to send.
    for intent in HELP_INTENTS:
        for template in PHRASES[intent]["false"]:
            text = template.format(target_name=TOKEN)
            assert not _has(text, _OFFER_WORDS), (intent, text)


def test_hoard_only_names_a_target_when_bluffing() -> None:
    # Honest/quiet HOARD talk must name no one, so it can never be mistaken for
    # a cooperation offer. Only the `false` bluff addresses a target.
    for mode in ("honest", "partial", "quiet", "misdirecting"):
        for template in PHRASES["play_own_game"][mode]:
            assert "{target_name}" not in template, (mode, template)
    for template in PHRASES["play_own_game"]["false"]:
        assert "{target_name}" in template, template
        assert _has(template.format(target_name=TOKEN), _OFFER_WORDS), template


# --- end-to-end telegraphing ----------------------------------------------


def test_honest_talk_telegraphs_a_help() -> None:
    ids = ["AI_1", "AI_2", "AI_3"]
    board = [ScoreboardRow(agent_id=a, round_score=4, round_wins=0.0) for a in ids]
    history = [
        ActionRecord(
            round=1,
            turn=1,
            actor_id="AI_2",
            action="HELP",
            target_id="AI_1",
            message="",
            points_delta=4,
            round_score_after=4,
            was_defaulted=False,
        )
    ]
    profile = BotProfile(
        strategy="coalition_seeker", truthfulness=100, trust_model="even", seed=3, version="v1"
    )

    action = choose_bot_action_decision(
        _ctx(ids=ids, scoreboard=board, history=history, phase="act"), profile
    )
    assert action.move == {"action": "HELP", "target_id": "AI_2"}

    talk = choose_bot_talk_decision(
        _ctx(ids=ids, scoreboard=board, history=history, phase="talk"), profile
    )
    signals = extract_talk_signals(
        [TalkMessage(agent_id="AI_1", message=talk.message)], all_agent_ids=ids
    )
    assert any(
        s.kind == "cooperation_offer" and s.target_id == "AI_2" for s in signals
    ), talk.message
    assert not any(s.kind == "threat" and s.target_id == "AI_2" for s in signals), talk.message


def test_honest_hurt_on_the_leader_rallies_the_table() -> None:
    # An honest bot attacking the runaway leader does not announce a personal
    # hit — it rallies the table. That reads as a leader_warning, not a threat,
    # and never as a cooperation offer.
    ids = ["AI_1", "AI_2", "AI_3"]
    board = _runaway_leader_board()
    profile = BotProfile(
        strategy="leader_pressure", truthfulness=100, trust_model="even", seed=1, version="v1"
    )

    action = choose_bot_action_decision(
        _ctx(ids=ids, scoreboard=board, history=[], phase="act"), profile
    )
    assert action.move == {"action": "HURT", "target_id": "AI_3"}

    talk = choose_bot_talk_decision(
        _ctx(ids=ids, scoreboard=board, history=[], phase="talk"), profile
    )
    signals = extract_talk_signals(
        [TalkMessage(agent_id="AI_1", message=talk.message)], all_agent_ids=ids, leader_id="AI_3"
    )
    assert any(s.kind == "leader_warning" and s.target_id == "AI_3" for s in signals), talk.message
    assert not any(
        s.kind == "cooperation_offer" and s.target_id == "AI_3" for s in signals
    ), talk.message


def test_deception_hides_the_move_without_changing_it() -> None:
    ids = ["AI_1", "AI_2", "AI_3"]
    board = _runaway_leader_board()

    # The move never depends on truthfulness — only the talk does.
    action = choose_bot_action_decision(
        _ctx(ids=ids, scoreboard=board, history=[], phase="act"),
        BotProfile(strategy="leader_pressure", truthfulness=0, trust_model="even", seed=0, version="v1"),
    )
    assert action.move == {"action": "HURT", "target_id": "AI_3"}

    revealed: list[int] = []
    bluffed: list[int] = []
    for seed in range(40):
        profile = BotProfile(
            strategy="leader_pressure", truthfulness=0, trust_model="even", seed=seed, version="v1"
        )
        talk = choose_bot_talk_decision(
            _ctx(ids=ids, scoreboard=board, history=[], phase="talk"), profile
        )
        signals = extract_talk_signals(
            [TalkMessage(agent_id="AI_1", message=talk.message)],
            all_agent_ids=ids,
            leader_id="AI_3",
        )
        if any(
            s.kind in {"threat", "leader_warning"} and s.target_id == "AI_3" for s in signals
        ):
            revealed.append(seed)
        if any(s.kind == "cooperation_offer" and s.target_id == "AI_3" for s in signals):
            bluffed.append(seed)

    # A low-truth bot should sometimes mask the attack behind a friendly bluff...
    assert bluffed, "expected at least one false cooperation bluff"
    # ...and must not tip its aggression on every single turn.
    assert len(revealed) < 40
