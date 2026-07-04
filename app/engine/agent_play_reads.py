"""DB-to-payload projection helpers for the agent-play service.

These functions read match/turn/player rows and project them into the public
schema shapes (scoreboards, standings, talk messages, current-turn payloads,
action history). They sit above ``agent_play_guards`` and below both the
per-match verbs and the connection-level next-turn fan-out, so any helper used
by both of those siblings lives here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence, cast

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.engine.agent_play_guards import _err, _seat_name_map
from app.games import get as get_game_module
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission
from app.schemas.agent import (
    Action,
    CurrentTurn,
    HistoryAction,
    HistoryTurn,
    ScoreboardRow,
    StandingRow,
    TalkMessage,
)

# How many of the most-recent resolved turns the per-poll payload carries. The
# poll is served on every loop iteration, so it must stay small: re-sending the
# whole transcript on each poll overflows an MCP client's tool-output buffer and
# trips its loop detection, which stops the play loop dead.
#
# Two turns is the floor that still covers play:
#   * Reactive strategies need only the LAST resolved turn — tit-for-tat mirrors
#     "your opponent's last move"; Pavlov repeats/switches on "your last action".
#   * The full scoreboard (always sent) already carries every rank-based signal
#     (Pavlov's rank delta, Always-Defect's "hit the leader").
#   * The extra turn is margin: the chained connector computes its per-turn delta
#     as "history newer than my last move", so one spare turn means a single
#     skipped poll never drops an event from that delta.
# The whole transcript is still reachable on demand via get_game_state /
# opponent_history / get_chat (all unwindowed), so nothing is lost — it is just
# pulled once instead of pushed every poll.
RECENT_HISTORY_TURNS = 2


@dataclass(frozen=True)
class _PublicActionRecord:
    round: int
    turn: int
    actor_id: str
    action: Action
    target_id: str | None
    message: str
    points_delta: int
    was_defaulted: bool


async def _load_public_action_records(
    db: AsyncSession,
    match_id: str,
    players: Sequence[Player],
    *,
    recent_turns: int | None = None,
) -> list[_PublicActionRecord]:
    """Project resolved-turn actions into public records, oldest to newest.

    When ``recent_turns`` is set, only the last N resolved turns are loaded (the
    rolling window the per-poll payload carries); ``None`` loads the whole match
    (the on-demand history channels). The limit is applied in the DB query, so a
    windowed read also touches only those turns' messages and submissions.
    """
    seat_name_by_agent_id = _seat_name_map(players)
    seat_name_by_player_id = {player.id: player.seat_name for player in players}
    turns_stmt = select(Turn).where(
        Turn.match_id == match_id, Turn.resolved_at.is_not(None)
    )
    if recent_turns is not None:
        # Take the newest N by (round, turn); the projection below re-sorts ascending
        # so the window is still emitted oldest-to-newest.
        turns_stmt = turns_stmt.order_by(
            Turn.round.desc(), Turn.turn.desc()
        ).limit(recent_turns)
    turns = (await db.execute(turns_stmt)).scalars().all()
    ordered_turns = sorted(turns, key=lambda t: (t.round, t.turn))
    turn_ids = [turn.id for turn in ordered_turns]
    if not turn_ids:
        return []

    # Two batched reads keyed by the turn-id set, then grouped in memory — instead
    # of a message + submission query per turn. Messages fold into a dict keyed by
    # (turn_id, player_id), so their row order is irrelevant. Submissions are the
    # emitted records, so they carry an explicit (turn_id, id) order that matches
    # the per-turn index scan the old per-turn query used — same within-turn order.
    message_rows = (
        await db.execute(
            select(TurnMessage, Player.id)
            .join(Player, Player.id == TurnMessage.player_id)
            .where(TurnMessage.turn_id.in_(turn_ids))
        )
    ).all()
    message_by_turn_player: dict[tuple[int, int], str] = {
        (msg.turn_id, player_id): msg.text for msg, player_id in message_rows
    }
    submission_rows = (
        await db.execute(
            select(TurnSubmission, Player.id, Player.agent_id)
            .join(Player, Player.id == TurnSubmission.player_id)
            .where(TurnSubmission.turn_id.in_(turn_ids))
            .order_by(TurnSubmission.turn_id, TurnSubmission.id)
        )
    ).all()
    submissions_by_turn: dict[int, list[tuple[TurnSubmission, int, int]]] = {}
    for submission, player_id, agent_id in submission_rows:
        submissions_by_turn.setdefault(submission.turn_id, []).append(
            (submission, player_id, agent_id)
        )

    public_actions: list[_PublicActionRecord] = []
    for turn in ordered_turns:
        for submission, player_id, agent_id in submissions_by_turn.get(turn.id, []):
            public_actions.append(
                _PublicActionRecord(
                    round=turn.round,
                    turn=turn.turn,
                    actor_id=seat_name_by_agent_id.get(agent_id, str(agent_id)),
                    action=cast(Action, submission.action),
                    target_id=(
                        seat_name_by_player_id.get(submission.target_player_id)
                        if submission.target_player_id is not None
                        else None
                    ),
                    message=message_by_turn_player.get(
                        (turn.id, player_id), submission.message
                    ),
                    points_delta=submission.points_delta,
                    was_defaulted=submission.was_defaulted,
                )
            )
    return public_actions


def _scoreboard_order(players: Sequence[Player]) -> list[Player]:
    """The one public-scoreboard ordering: best ``current_round_score`` first,
    ties broken by ``seat_name``. Both the per-match poll and the next-turn
    payload sort through this, so the emitted scoreboard order is identical.
    """
    return sorted(players, key=lambda player: (-player.current_round_score, player.seat_name))


def build_public_scoreboard_dicts(players: Sequence[Player]) -> list[dict[str, object]]:
    """Project players into the public scoreboard rows as plain dicts.

    The connection-level next-turn payload emits these as-is; the per-match poll
    wraps the same projection in ``ScoreboardRow`` (see
    :func:`_public_scoreboard`). Same fields, same order
    (``agent_id`` / ``round_score`` / ``round_wins``), same sort
    (:func:`_scoreboard_order`), so both paths stay byte-for-byte identical.
    """
    return [
        {
            "agent_id": player.seat_name,
            "round_score": player.current_round_score,
            "round_wins": player.total_round_wins,
        }
        for player in _scoreboard_order(players)
    ]


def _public_scoreboard(players: Sequence[Player]) -> list[ScoreboardRow]:
    return [
        ScoreboardRow(
            agent_id=player.seat_name,
            round_score=player.current_round_score,
            round_wins=player.total_round_wins,
        )
        for player in _scoreboard_order(players)
    ]


def sorted_seat_names(seat_name_by_agent_id: dict[int, str]) -> list[str]:
    """Seat names sorted for the public ``all_agent_ids`` list.

    One source for the ``sorted(seat_name_by_agent_id.values())`` expression
    that the per-match poll, submit, identity, and next-turn paths all repeat.
    """
    return sorted(seat_name_by_agent_id.values())


def build_turn_static_dict(
    match: Match,
    player: Player,
    *,
    all_agent_ids: list[str],
    your_strategy: str | None,
) -> dict[str, object]:
    """The one "static" (rules + identity) block of the your-turn payload.

    Both turn-serving paths emit this exact dict: the connection fan-out
    (``agent_play_next_turn._build_turn_payload``) embeds it as-is — its key
    order and conditional ``coach_note`` are wire-frozen for the operator
    connector — and the per-match poll (``agent_play.poll_turn``) validates it
    into ``TurnStatic``. One builder keeps the two payloads from drifting apart
    (a past drift silently dropped coach notes from the per-match poll).

    ``your_strategy`` stays a parameter because the two callers source it
    differently today: the poll reads the seat-pinned ``Player.agent_version_id``
    while the fan-out reads ``Agent.current_version_id``. They only diverge if a
    version changes mid-match (e.g. restore-version, which has no active-match
    guard) — a known open question, not a decision this builder should hide.
    """
    module = get_game_module(match.game)
    static: dict[str, object] = {
        "match_id": match.id,
        "game_id": match.id,
        "game": match.game,
        "rules_version": match.rules_version,
        "rules": module.rules_text(match.total_rounds, match.turns_per_round),
        "base_prompt": module.agent_base_prompt(
            your_agent_id=player.seat_name,
            all_agent_ids=all_agent_ids,
            total_rounds=match.total_rounds,
            turns_per_round=match.turns_per_round,
        ),
        "total_rounds": match.total_rounds,
        "turns_per_round": match.turns_per_round,
        "your_agent_id": player.seat_name,
        "all_agent_ids": all_agent_ids,
        "your_strategy": your_strategy,
    }
    # Sideline coaching (written by the web viewer): the note rides along only
    # for the round it targets, then falls out of the payload.
    if player.coach_note and player.coach_note_round == match.current_round:
        static["coach_note"] = player.coach_note
    return static


def _public_standings(players: Sequence[Player]) -> list[StandingRow]:
    ordered = _scoreboard_order(players)
    return [
        StandingRow(
            agent_id=player.seat_name,
            round_score=player.current_round_score,
            rank=index + 1,
        )
        for index, player in enumerate(ordered)
    ]


async def _load_talk_messages(db: AsyncSession, turn: Turn) -> list[TalkMessage]:
    if turn.phase != "act":
        return []
    rows = (
        (
            await db.execute(
                select(TurnMessage, Player.seat_name)
                .join(Player, Player.id == TurnMessage.player_id)
                .where(TurnMessage.turn_id == turn.id)
                .order_by(Player.seat_name)
            )
        )
        .all()
    )
    return [TalkMessage(agent_id=seat_name, message=msg.text) for msg, seat_name in rows]


async def _build_current_turn(db: AsyncSession, turn: Turn) -> CurrentTurn:
    return CurrentTurn(
        round=turn.round,
        turn=turn.turn,
        deadline=turn.deadline_at,
        turn_token=turn.turn_token,
        phase=cast(Literal["talk", "act"], turn.phase),
        talk_messages=await _load_talk_messages(db, turn),
    )


async def load_match_players(
    db: AsyncSession, match_id: str, *, exclude_left: bool = False
) -> Sequence[Player]:
    """All players seated in a match. With ``exclude_left`` set, drop those who
    have left (``left_at IS NOT NULL``) — the standings view's filter.

    The one loader behind the repeated ``select(Player).where(match_id == …)`` the
    per-match verbs and the next-turn payload both run.
    """
    stmt = select(Player).where(Player.match_id == match_id)
    if exclude_left:
        stmt = stmt.where(Player.left_at.is_(None))
    return (await db.execute(stmt)).scalars().all()


async def load_open_turn(db: AsyncSession, match_id: str) -> Turn | None:
    """Return the latest open (unresolved) turn for a match, or None.

    Orders by round, then turn, then id so a tie on (round, turn) resolves to the
    most recently created row.
    """
    return (
        await db.execute(
            select(Turn)
            .where(Turn.match_id == match_id, Turn.resolved_at.is_(None))
            .order_by(Turn.round.desc(), Turn.turn.desc(), Turn.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def load_open_turns(
    db: AsyncSession, match_ids: Sequence[str]
) -> dict[str, Turn]:
    """Batched sibling of :func:`load_open_turn`: the latest open (unresolved)
    turn for each of ``match_ids``, keyed by match id.

    Matches with no open turn are absent from the result (same as ``None`` from the
    single loader). Ordering matches ``load_open_turn`` per match: greatest
    ``(round, turn, id)`` wins — here by scanning ascending and letting the last
    row per match overwrite the earlier ones.
    """
    if not match_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.match_id.in_(match_ids), Turn.resolved_at.is_(None))
                .order_by(Turn.round.asc(), Turn.turn.asc(), Turn.id.asc())
            )
        )
        .scalars()
        .all()
    )
    open_turn_by_match: dict[str, Turn] = {}
    for turn in rows:
        open_turn_by_match[turn.match_id] = turn
    return open_turn_by_match


async def _load_active_phase_turn(
    db: AsyncSession,
    match_id: str,
    turn_token: str,
    expected_phase: Literal["talk", "act"],
    *,
    tolerate_phase_advance: bool = False,
) -> tuple[Match, Turn]:
    """Load the open turn for this token and confirm it's still in `expected_phase`.

    `tolerate_phase_advance` (talk only): instead of raising WRONG_PHASE when the
    turn has already moved past the expected phase, hand the turn back so the
    caller can respond gracefully. The talk->act handoff keeps the same token (see
    `_begin_act_phase`), so a slightly-late talk still finds its turn here; the
    caller (`submit_talk`) checks `turn.phase` and returns a "talk window closed"
    signal rather than a hard error. A fully-resolved turn is still rejected — that
    one is genuinely over.
    """
    game = (await db.execute(select(Match).where(Match.id == match_id))).scalar_one()
    if game.state != GameState.ACTIVE:
        raise _err(
            "GAME_NOT_ACTIVE",
            "Match is not active.",
            status.HTTP_409_CONFLICT,
        )

    turn = (
        await db.execute(
            select(Turn).where(Turn.match_id == game.id, Turn.turn_token == turn_token)
        )
    ).scalar_one_or_none()
    if turn is None:
        raise _err(
            "STALE_TURN_TOKEN",
            "turn_token doesn't match the open turn.",
            status.HTTP_409_CONFLICT,
        )
    if turn.resolved_at is not None:
        raise _err(
            "STALE_TURN_TOKEN",
            "Turn already resolved.",
            status.HTTP_409_CONFLICT,
        )
    if turn.phase != expected_phase:
        if tolerate_phase_advance:
            # The phase moved on; let the caller decide how to answer. Skip the
            # deadline check — the caller isn't going to record a move on this row.
            return game, turn
        raise _err(
            "WRONG_PHASE",
            f"Turn is not in {expected_phase} phase.",
            status.HTTP_409_CONFLICT,
        )
    if datetime.now(timezone.utc) >= ensure_aware(turn.deadline_at):
        raise _err("DEADLINE_PASSED", "Submission past deadline.", status.HTTP_410_GONE)
    return game, turn


async def _existing_message_for_player(
    db: AsyncSession, turn: Turn, player: Player
) -> TurnMessage | None:
    return (
        await db.execute(
            select(TurnMessage).where(
                TurnMessage.turn_id == turn.id,
                TurnMessage.player_id == player.id,
            )
        )
    ).scalar_one_or_none()


async def _existing_submission_for_player(
    db: AsyncSession, turn: Turn, player: Player
) -> TurnSubmission | None:
    return (
        await db.execute(
            select(TurnSubmission).where(
                TurnSubmission.turn_id == turn.id,
                TurnSubmission.player_id == player.id,
            )
        )
    ).scalar_one_or_none()


def _group_into_turns(actions: Sequence[_PublicActionRecord]) -> list[HistoryTurn]:
    by_rt: dict[tuple[int, int], list[HistoryAction]] = {}
    for action in sorted(actions, key=lambda x: (x.round, x.turn)):
        by_rt.setdefault((action.round, action.turn), []).append(
            HistoryAction(
                agent_id=action.actor_id,
                action=action.action,
                target_id=action.target_id,
                message=action.message,
                points_delta=action.points_delta,
            )
        )
    return [
        HistoryTurn(round=round_no, turn=turn_no, actions=acts)
        for (round_no, turn_no), acts in sorted(by_rt.items())
    ]


def _parse_cursor(since: str | None) -> tuple[int, int] | None:
    if not since:
        return None
    parts = since.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise _err("INVALID_CURSOR", "since must be 'round.turn'.", status.HTTP_400_BAD_REQUEST)
    return int(parts[0]), int(parts[1])
