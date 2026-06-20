"""Assemble the bounded `TurnSummary` that the agent's next-turn call now returns.

Pure orchestration over `opponent_stats` and `board_signals`, plus the cheap
parts (your situation, compressed standings, the last-turn delta, and the
messages aimed at you). DB-free — the route maps rows into records first.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.engine.action_vocab import pd_action_names
from app.engine.board_signals import (
    alliance_formed_this_turn,
    compute_board_signals,
    detect_pattern_breaks,
)
from app.engine.game_records import ActionRecord, PlayerRecord
from app.engine.opponent_stats import (
    NEIGHBOR_RADIUS,
    build_opponent_view,
    rank_players,
    ranks_by_agent,
)
from app.schemas.agent import (
    DeltaAction,
    DirectedMessage,
    StandingRow,
    StandingsView,
    SummaryFlags,
    TurnDelta,
    TurnSummary,
    YourSituation,
)

MAX_BROADCASTS = 5
LEADERS_CAP = 3


def build_turn_summary(
    you: str,
    players: Sequence[PlayerRecord],
    actions: Sequence[ActionRecord],
    current_round: int,
    current_turn: int,
    deadline: datetime,
    turn_token: str,
) -> TurnSummary:
    ranks = ranks_by_agent(players)
    your_rank = ranks.get(you, len(players))
    me = next((p for p in players if p.agent_id == you), None)

    your_situation = YourSituation(
        round_score=me.round_score if me else 0,
        total_score=me.total_score if me else 0,
        round_wins=me.round_wins if me else 0.0,
        rank=your_rank,
        current_round=current_round,
        current_turn=current_turn,
        deadline=deadline,
        turn_token=turn_token,
    )

    standings_view = _standings(you, players, ranks, your_rank)

    last_rt = max(((a.round, a.turn) for a in actions), default=None)
    turn_delta = _delta(you, actions, last_rt)
    messages = _messages_for_you(you, actions, last_rt)

    pattern_breaks = detect_pattern_breaks(actions)
    flagged = set(pattern_breaks) | {m.from_agent_id for m in messages if not m.public}
    opponents, aggregate = build_opponent_view(you, players, actions, flagged)

    board_signals = compute_board_signals(players, actions, current_round)
    flags = SummaryFlags(
        pattern_breaks=pattern_breaks,
        new_alliance=alliance_formed_this_turn(actions, current_round),
        messages_for_you_count=sum(1 for m in messages if not m.public),
    )

    return TurnSummary(
        your_situation=your_situation,
        standings_view=standings_view,
        turn_delta=turn_delta,
        opponents=opponents,
        opponents_aggregate=aggregate,
        board_signals=board_signals,
        flags=flags,
        messages_for_you=messages,
    )


def _standings(
    you: str,
    players: Sequence[PlayerRecord],
    ranks: dict[str, int],
    your_rank: int,
) -> StandingsView:
    ordered = rank_players(players)
    top_score = ordered[0].round_score if ordered else 0
    leaders = [
        StandingRow(agent_id=p.agent_id, round_score=p.round_score, rank=ranks[p.agent_id])
        for p in ordered
        if p.round_score == top_score
    ][:LEADERS_CAP]
    neighbors = [
        StandingRow(agent_id=p.agent_id, round_score=p.round_score, rank=ranks[p.agent_id])
        for p in ordered
        if p.agent_id != you and abs(ranks[p.agent_id] - your_rank) <= NEIGHBOR_RADIUS
    ]
    return StandingsView(
        leaders=leaders,
        your_rank=your_rank,
        neighbors=neighbors,
        total_players=len(players),
    )


def _delta(
    you: str,
    actions: Sequence[ActionRecord],
    last_rt: tuple[int, int] | None,
) -> TurnDelta | None:
    if last_rt is None:
        return None
    last = [a for a in actions if (a.round, a.turn) == last_rt]
    involving_you = [
        DeltaAction(
            actor_id=a.actor_id,
            action=a.action,
            target_id=a.target_id,
            points_delta=a.points_delta,
        )
        for a in last
        if a.actor_id == you or a.target_id == you
    ]
    others = [a for a in last if a.actor_id != you]
    hoard_action, help_action, hurt_action = pd_action_names()
    hoard = sum(1 for a in others if a.action == hoard_action)
    helped = sum(1 for a in others if a.action == help_action)
    hurt = sum(1 for a in others if a.action == hurt_action)
    return TurnDelta(
        round=last_rt[0],
        turn=last_rt[1],
        involving_you=involving_you,
        others_summary=f"{hoard} hoarded, {helped} helped, {hurt} hurt",
    )


def _messages_for_you(
    you: str,
    actions: Sequence[ActionRecord],
    last_rt: tuple[int, int] | None,
) -> list[DirectedMessage]:
    """Messages aimed at you (directed) + the last few public broadcasts.

    Only the last resolved turn, action-derived (a message is "directed" when its
    action targeted you). The server-defaulted "did not submit" rows are skipped.
    No message-text parsing.
    """
    if last_rt is None:
        return []
    last = [a for a in actions if (a.round, a.turn) == last_rt and not a.was_defaulted]
    directed = [
        DirectedMessage(from_agent_id=a.actor_id, message=a.message, on_action=a.action, public=False)
        for a in last
        if a.target_id == you and a.actor_id != you and a.message
    ]
    broadcasts = [
        DirectedMessage(from_agent_id=a.actor_id, message=a.message, on_action=None, public=True)
        for a in last
        if a.actor_id != you and a.target_id != you and a.message
    ][:MAX_BROADCASTS]
    return directed + broadcasts
