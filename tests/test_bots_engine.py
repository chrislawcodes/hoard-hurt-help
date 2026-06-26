"""Tests for the deterministic bots engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.engine.bots import trust as trust_module
from app.engine.game_records import ActionRecord
from app.engine.bots import (
    BotContext,
    BotProfile,
    choose_bot_action_decision,
    choose_bot_talk_decision,
    compute_trust_map,
    extract_talk_signals,
    render_phrase,
)
from app.engine.bots.phrases import PHRASES
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
    game_id: str = "G_1",
    game_started_at: datetime | None = None,
) -> BotContext:
    agent_ids = all_agent_ids or ["AI_1", "AI_2", "AI_3", "AI_10"]
    board = scoreboard or [
        ScoreboardRow(agent_id="AI_1", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=12, round_wins=0.0),
        ScoreboardRow(agent_id="AI_3", round_score=10, round_wins=0.0),
        ScoreboardRow(agent_id="AI_10", round_score=12, round_wins=0.0),
    ]
    return BotContext(
        game_id=game_id,
        game_started_at=game_started_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
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


def test_render_phrase_uses_target_display_name() -> None:
    message = render_phrase(
        "curb_leader",
        "honest",
        seed=0,
        target_name="Sun Tzu",
    )

    assert "Sun Tzu" in message
    assert "AI_" not in message


def test_render_phrase_without_target_falls_back_to_someone() -> None:
    # A targeted line rendered with no target must substitute the placeholder,
    # never leak a raw "{target_name}" or an agent id.
    message = render_phrase("offer_help", "honest", seed=0)

    assert "{target_name}" not in message
    assert "AI_" not in message
    assert "someone" in message
    assert len(message) > 0


def test_telegraph_lines_name_the_target() -> None:
    # Honest lines that address a specific player name them. HELP invites the
    # target, hit_back/block_rival warn them, and curb_leader names the leader
    # it's rallying the table against.
    telegraph_intents = {
        "offer_help",
        "keep_ally",
        "repay_help",
        "mend_fences",
        "hit_back",
        "curb_leader",
        "block_rival",
    }

    for intent in telegraph_intents:
        for seed, _phrase in enumerate(PHRASES[intent]["honest"]):
            message = render_phrase(intent, "honest", seed=seed, target_name="Sun Tzu")
            assert "Sun Tzu" in message, (intent, seed)


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


def _rec(round_: int, turn: int, actor: str, action: str, target: str | None) -> ActionRecord:
    return ActionRecord(
        round=round_,
        turn=turn,
        actor_id=actor,
        action=action,
        target_id=target,
        message="",
        points_delta=0,
        round_score_after=0,
        was_defaulted=False,
    )


def test_betrayal_stings_then_fades_and_is_forgiven() -> None:
    # Round 1, turn 7: AI_1 HELPs AI_2 and AI_2 HURTs AI_1 the same turn — the -8
    # betrayal. The hit fades over the rounds that follow and is fully forgiven.
    betrayal = [
        _rec(1, 7, "AI_1", "HELP", "AI_2"),
        _rec(1, 7, "AI_2", "HURT", "AI_1"),
    ]

    def trust_in_traitor(latest_round: int) -> int:
        # A neutral action sets which round is "now" without touching trust.
        history = betrayal + [_rec(latest_round, 1, "AI_3", "HOARD", None)]
        return compute_trust_map(
            your_agent_id="AI_1",
            all_agent_ids=["AI_1", "AI_2", "AI_3"],
            history=history,
            signals=[],
            trust_model="even",
        )["AI_2"]

    assert trust_in_traitor(2) == -27  # one round later: most of the sting still bites
    assert trust_in_traitor(3) == -18  # fading
    assert trust_in_traitor(5) == 0  # four rounds on: trusted again


def test_betrayal_sensitivity_varies_by_personality() -> None:
    # Same betrayal, same moment (two rounds later). A forgiving "open" bot has
    # moved on; a "bitter" one (Long Memory) still won't go near the traitor.
    history = [
        _rec(1, 7, "AI_1", "HELP", "AI_2"),
        _rec(1, 7, "AI_2", "HURT", "AI_1"),
        _rec(3, 1, "AI_3", "HOARD", None),
    ]

    def trust_in_traitor(model: str) -> int:
        return compute_trust_map(
            your_agent_id="AI_1",
            all_agent_ids=["AI_1", "AI_2", "AI_3"],
            history=history,
            signals=[],
            trust_model=model,
        )["AI_2"]

    assert trust_in_traitor("open") == 0  # forgave it within two rounds
    assert trust_in_traitor("bitter") == -39  # still holds the grudge


def test_witnessed_betrayal_lowers_trust_in_the_traitor() -> None:
    # AI_2 betrays AI_3 (not me) this same round. From AI_1's seat that's a fresh
    # witnessed betrayal: a smaller hit than a personal one, but real.
    history = [
        _rec(1, 7, "AI_3", "HELP", "AI_2"),
        _rec(1, 7, "AI_2", "HURT", "AI_3"),
    ]
    trust = compute_trust_map(
        your_agent_id="AI_1",
        all_agent_ids=["AI_1", "AI_2", "AI_3"],
        history=history,
        signals=[],
        trust_model="even",
    )
    assert trust["AI_2"] == -18  # even: hurt_last -6 × witnessed factor 3, fresh
    assert trust["AI_3"] == 0


def test_a_known_traitor_is_not_rewarded_for_helping() -> None:
    # AI_2 betrayed AI_1 in round 1, then HELPs AI_1 again in round 2. The fresh
    # help would normally trigger reward_helper, but the betrayal memory keeps
    # AI_1 from cooperating back — it does not HELP the traitor.
    history = [
        _rec(1, 7, "AI_1", "HELP", "AI_2"),
        _rec(1, 7, "AI_2", "HURT", "AI_1"),
        _rec(2, 1, "AI_2", "HELP", "AI_1"),
    ]
    board = [
        ScoreboardRow(agent_id="AI_1", round_score=4, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=8, round_wins=0.0),
        ScoreboardRow(agent_id="AI_3", round_score=4, round_wins=0.0),
    ]
    context = _context(
        all_agent_ids=["AI_1", "AI_2", "AI_3"], history=history, scoreboard=board, turn=2
    )
    profile = BotProfile(strategy="loyal_partner", truthfulness=80, trust_model="even", seed=5, version="v1")
    decision = choose_bot_action_decision(context, profile)
    assert decision.move != {"action": "HELP", "target_id": "AI_2"}


def _mutual_round(round_: int, a: str, b: str, turns: int = 1) -> list[ActionRecord]:
    """`turns` turns in `round_` where a and b HELP each other (a mutual pact)."""
    out: list[ActionRecord] = []
    for t in range(1, turns + 1):
        out += [_rec(round_, t, a, "HELP", b), _rec(round_, t, b, "HELP", a)]
    return out


def test_partner_fatigue_erodes_a_farmed_partner(monkeypatch: pytest.MonkeyPatch) -> None:
    # AI_2 mutually helps AI_1 every turn of the round: without fatigue that is a
    # strong, above-threshold partner. The fatigue from farming it that many times
    # erodes its trust to neutral, so the bot will rotate to someone fresh.
    history = _mutual_round(1, "AI_1", "AI_2", turns=7)
    ids = ["AI_1", "AI_2", "AI_3"]

    farmed = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )["AI_2"]

    monkeypatch.setattr(trust_module, "PARTNER_FATIGUE", 0)
    without_fatigue = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )["AI_2"]

    assert without_fatigue >= 20  # would be a valid partner (the 20 threshold) without fatigue
    assert farmed < without_fatigue  # fatigue pulled it down
    assert farmed == 0  # heavily farmed → eroded to neutral, so selection rotates


def test_partner_fatigue_floors_at_zero_never_negative() -> None:
    # Even extreme farming only erodes trust to 0, never below — a stale ally is
    # "meh", not an enemy (going negative is the betrayal system's job, not this).
    history = _mutual_round(1, "AI_1", "AI_2", turns=7) + _mutual_round(2, "AI_1", "AI_2", turns=7)
    trust = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=["AI_1", "AI_2"], history=history, signals=[], trust_model="even"
    )
    assert trust["AI_2"] == 0


def test_partner_fatigue_leaves_a_hostile_player_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fatigue only discounts partners you've farmed (trust > 0). A player who HURT
    # you (negative trust, no mutual help) is not touched by it.
    history = [_rec(1, 7, "AI_2", "HURT", "AI_1")]
    ids = ["AI_1", "AI_2"]
    with_fatigue = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )["AI_2"]
    monkeypatch.setattr(trust_module, "PARTNER_FATIGUE", 0)
    without_fatigue = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )["AI_2"]
    assert with_fatigue < 0
    assert with_fatigue == without_fatigue  # fatigue did not touch the hostile player


def test_partner_fatigue_prefers_a_fresh_partner_over_a_farmed_one() -> None:
    # AI_2 was farmed across rounds 1–3; AI_3 is a fresh partner in the latest turn.
    # The fresh ally ends with higher trust, so the bot rotates toward it.
    history = (
        _mutual_round(1, "AI_1", "AI_2")
        + _mutual_round(2, "AI_1", "AI_2")
        + _mutual_round(3, "AI_1", "AI_2")
        + _mutual_round(4, "AI_1", "AI_3")
    )
    trust = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=["AI_1", "AI_2", "AI_3"], history=history, signals=[], trust_model="even"
    )
    assert trust["AI_3"] > trust["AI_2"]  # fresh partner outranks the farmed one


def test_partner_fatigue_is_deterministic() -> None:
    history = _mutual_round(1, "AI_1", "AI_2", turns=4) + _mutual_round(2, "AI_1", "AI_3")
    ids = ["AI_1", "AI_2", "AI_3"]
    first = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )
    second = compute_trust_map(
        your_agent_id="AI_1", all_agent_ids=ids, history=history, signals=[], trust_model="even"
    )
    assert first == second


def test_leader_pressure_hits_a_runaway_leader() -> None:
    # Giant Slayer drops everything to hit a leader who's 12+ points ahead of it.
    board = [
        ScoreboardRow(agent_id="AI_1", round_score=2, round_wins=0.0),
        ScoreboardRow(agent_id="AI_2", round_score=20, round_wins=0.0),
        ScoreboardRow(agent_id="AI_3", round_score=6, round_wins=0.0),
        ScoreboardRow(agent_id="AI_10", round_score=6, round_wins=0.0),
    ]
    context = _context(scoreboard=board)
    profile = BotProfile(strategy="leader_pressure", truthfulness=80, trust_model="even", seed=42, version="v1")
    decision = choose_bot_action_decision(context, profile)
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
    profile = BotProfile(strategy="crowd_follower", truthfulness=80, trust_model="even", seed=99, version="v1")
    decision = choose_bot_action_decision(context, profile)
    assert decision.move == {"action": "HELP", "target_id": "AI_3"}


def _record(
    *,
    actor_id: str,
    action: str,
    target_id: str | None,
    round_: int = 1,
    turn: int = 1,
    was_defaulted: bool = False,
) -> ActionRecord:
    return ActionRecord(
        round=round_,
        turn=turn,
        actor_id=actor_id,
        action=action,
        target_id=target_id,
        message="",
        points_delta=0,
        round_score_after=0,
        was_defaulted=was_defaulted,
    )


def test_crowd_choice_is_the_single_source_for_both_call_sites() -> None:
    # The strategy planner (BotPlan path) and the runtime move builder must
    # produce the same move from the same crowd, because both now call the one
    # crowd_choice core. This is the anti-drift lock for cluster D1.
    from app.engine.bots.runtime import _crowd_move
    from app.engine.bots.strategies import _copy_crowd_action, crowd_choice

    history = [
        _record(actor_id="AI_2", action="HELP", target_id="AI_3"),
        _record(actor_id="AI_4", action="HELP", target_id="AI_3"),
        _record(actor_id="AI_5", action="HURT", target_id="AI_1"),
    ]
    context = _context(history=history)

    choice = crowd_choice(context)
    assert choice == ("HELP", "AI_3")

    move = _crowd_move(context)
    assert move == {"action": "HELP", "target_id": "AI_3"}

    plan = _copy_crowd_action(context)
    assert plan is not None
    assert plan.intent == "follow_crowd"
    assert plan.target_id == "AI_3"


def test_crowd_choice_seeded_target_tiebreak_is_deterministic() -> None:
    # Two targets tie on count; the seeded tiebreak must pick the same one
    # every time and identically across the move dict and the plan.
    from app.engine.bots.runtime import _crowd_move
    from app.engine.bots.strategies import _copy_crowd_action, crowd_choice

    history = [
        _record(actor_id="AI_2", action="HELP", target_id="AI_3"),
        _record(actor_id="AI_4", action="HELP", target_id="AI_5"),
    ]
    context = _context(history=history)

    first = crowd_choice(context)
    second = crowd_choice(context)
    assert first == second
    assert first is not None
    action, target = first
    assert action == "HELP"
    assert target in {"AI_3", "AI_5"}

    assert _crowd_move(context) == {"action": action, "target_id": target}
    plan = _copy_crowd_action(context)
    assert plan is not None
    assert plan.target_id == target


def test_crowd_choice_majority_hoard_collapses_to_hoard() -> None:
    from app.engine.bots.runtime import _crowd_move
    from app.engine.bots.strategies import _copy_crowd_action, crowd_choice

    history = [
        _record(actor_id="AI_2", action="HOARD", target_id=None),
        _record(actor_id="AI_3", action="HOARD", target_id=None),
        _record(actor_id="AI_4", action="HELP", target_id="AI_5"),
    ]
    context = _context(history=history)

    assert crowd_choice(context) == ("HOARD", None)
    assert _crowd_move(context) == {"action": "HOARD", "target_id": None}
    plan = _copy_crowd_action(context)
    assert plan is not None
    assert plan.intent == "follow_crowd"
    assert plan.target_id is None


def test_crowd_choice_priority_order_help_beats_hurt_on_a_tie() -> None:
    # Equal counts of HELP and HURT resolve to HELP (HELP < HURT < HOARD).
    from app.engine.bots.strategies import crowd_choice

    history = [
        _record(actor_id="AI_2", action="HELP", target_id="AI_3"),
        _record(actor_id="AI_4", action="HURT", target_id="AI_5"),
    ]
    context = _context(history=history)
    choice = crowd_choice(context)
    assert choice is not None
    assert choice[0] == "HELP"


def test_crowd_choice_returns_none_without_crowd_signal() -> None:
    # Empty history and all-defaulted history both mean "no crowd signal".
    from app.engine.bots.runtime import _crowd_move
    from app.engine.bots.strategies import _copy_crowd_action, crowd_choice

    empty = _context(history=[])
    assert crowd_choice(empty) is None
    assert _copy_crowd_action(empty) is None
    assert _crowd_move(empty) == {"action": "HOARD", "target_id": None}

    defaulted = _context(
        history=[_record(actor_id="AI_2", action="HELP", target_id="AI_3", was_defaulted=True)]
    )
    assert crowd_choice(defaulted) is None
    assert _crowd_move(defaulted) == {"action": "HOARD", "target_id": None}


def test_crowd_choice_uses_only_the_latest_non_defaulted_turn() -> None:
    from app.engine.bots.strategies import crowd_choice

    history = [
        _record(actor_id="AI_2", action="HURT", target_id="AI_3", round_=1, turn=1),
        _record(actor_id="AI_4", action="HURT", target_id="AI_3", round_=1, turn=1),
        _record(actor_id="AI_5", action="HELP", target_id="AI_2", round_=1, turn=2),
    ]
    context = _context(history=history)
    # Only turn 2 counts; it has a single HELP.
    assert crowd_choice(context) == ("HELP", "AI_2")


def test_decisions_are_deterministic() -> None:
    context = _context()
    profile = BotProfile(strategy="coalition_seeker", truthfulness=80, trust_model="open", seed=7, version="v1")
    first = choose_bot_talk_decision(context, profile)
    second = choose_bot_talk_decision(context, profile)
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
        game_id="G_100",
    )
    context_b = _context(
        all_agent_ids=["AI_10", "AI_2", "AI_1"],
        scoreboard=list(reversed(board)),
        game_id="G_999",
    )
    profile = BotProfile(strategy="leader_pressure", truthfulness=80, trust_model="even", seed=11, version="v1")
    assert choose_bot_action_decision(context_a, profile).move == choose_bot_action_decision(context_b, profile).move
