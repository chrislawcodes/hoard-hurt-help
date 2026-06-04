"""Turn resolution, round-winner awarding, game finalization.

All math is in this file. Read it with spec.md §5 alongside.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.rules import (
    DEFAULT_MISSED_MESSAGE,
    HELP_POINTS,
    HOARD_POINTS,
    HURT_POINTS,
    MUTUAL_HELP_BONUS,
)
from app.engine.state_machine import assert_transition
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission


async def resolve_turn(db: AsyncSession, turn: Turn) -> None:
    """Resolve one turn: materialize submissions, apply payoffs, persist deltas.

    Order matters and matches spec.md §5:
      1. Default any missing submission to HOARD (was_defaulted=True).
      2. Compute raw deltas (Hoard +2, Help +4 to target, Hurt -4 to target).
      3. Add the mutual-help bonus (+4 each side) for any A↔B pair.
      4. Apply the score floor at 0 to the FINAL per-player delta, not per-hurt.
      5. Persist post-floor `points_delta` and `round_score_after`.
      6. Mark turn resolved.
    """
    # Players in this game.
    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == turn.match_id)))
        .scalars()
        .all()
    )

    # Materialize submissions, defaulting missing ones to HOARD.
    submissions: list[TurnSubmission] = list(
        (await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == turn.id)))
        .scalars()
        .all()
    )
    submitted_player_ids = {s.player_id for s in submissions}
    for p in players:
        if p.id not in submitted_player_ids:
            default = TurnSubmission(
                turn_id=turn.id,
                player_id=p.id,
                action="HOARD",
                target_player_id=None,
                message=DEFAULT_MISSED_MESSAGE,
                was_defaulted=True,
                submitted_at=None,
            )
            db.add(default)
            submissions.append(default)
    await db.flush()

    # Raw deltas (pre-floor).
    delta: dict[int, int] = {p.id: 0 for p in players}

    for s in submissions:
        if s.action == "HOARD":
            delta[s.player_id] += HOARD_POINTS
        elif s.action == "HELP" and s.target_player_id in delta:
            delta[s.target_player_id] += HELP_POINTS
        elif s.action == "HURT" and s.target_player_id in delta:
            delta[s.target_player_id] -= HURT_POINTS

    # Mutual-help bonus: for each HELP pair where both helped each other,
    # add +4 to each side, but only once per pair.
    help_targets = {
        s.player_id: s.target_player_id for s in submissions if s.action == "HELP"
    }
    seen_pairs: set[frozenset[int]] = set()
    for a, b in help_targets.items():
        if b is None:
            continue
        if help_targets.get(b) == a:
            pair = frozenset({a, b})
            if pair not in seen_pairs:
                delta[a] += MUTUAL_HELP_BONUS
                delta[b] += MUTUAL_HELP_BONUS
                seen_pairs.add(pair)

    # Apply floor on final delta and persist.
    sub_by_player: dict[int, TurnSubmission] = {s.player_id: s for s in submissions}
    for p in players:
        new_score = p.current_round_score + delta[p.id]
        if new_score < 0:
            new_score = 0
        actual_delta = new_score - p.current_round_score
        p.current_round_score = new_score
        s = sub_by_player[p.id]
        s.points_delta = actual_delta
        s.round_score_after = new_score

    turn.resolved_at = datetime.now(timezone.utc)
    await db.commit()


async def finalize_talk_phase(db: AsyncSession, turn: Turn) -> None:
    """Materialize missing talk messages and mark the talk phase resolved."""
    active_players: list[Player] = list(
        (
            await db.execute(
                select(Player).where(
                    Player.match_id == turn.match_id, Player.left_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    messages: list[TurnMessage] = list(
        (
            await db.execute(select(TurnMessage).where(TurnMessage.turn_id == turn.id))
        )
        .scalars()
        .all()
    )
    submitted_player_ids = {m.player_id for m in messages}
    for p in active_players:
        if p.id not in submitted_player_ids:
            db.add(
                TurnMessage(
                    turn_id=turn.id,
                    player_id=p.id,
                    text="",
                    thinking="",
                    was_defaulted=True,
                    submitted_at=None,
                )
            )
    await db.flush()
    turn.talk_resolved_at = datetime.now(timezone.utc)
    await db.commit()


async def award_round_winners(db: AsyncSession, game: Match, round_num: int) -> None:
    """At end of a round, award fractional round-wins to the top scorers.

    Updates total_round_wins and total_round_score on each player.

    Idempotent: a mid-game restart resumes the loop at the last turn of the
    round it died on, re-opens that already-resolved turn, and would call this
    again — double-counting wins and scores. Rounds are awarded in order, so
    `game.rounds_awarded` (the highest round already folded into the totals)
    lets us skip a repeat. See app/engine/scheduler.py:_run_game.
    """
    if round_num <= game.rounds_awarded:
        return

    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == game.id)))
        .scalars()
        .all()
    )

    top = max((p.current_round_score for p in players), default=0)
    winners = [p for p in players if p.current_round_score == top]
    share = 1.0 / len(winners) if winners else 0

    for w in winners:
        w.total_round_wins += share
    for p in players:
        p.total_round_score += p.current_round_score

    game.rounds_awarded = round_num
    await db.commit()


async def finalize_game(db: AsyncSession, game: Match) -> None:
    """End-of-game: pick winner, transition state, set completed_at."""
    players: list[Player] = list(
        (await db.execute(select(Player).where(Player.match_id == game.id)))
        .scalars()
        .all()
    )
    if not players:
        winner = None
    else:
        ranked = sorted(
            players,
            key=lambda p: (-p.total_round_wins, -p.total_round_score),
        )
        winner = ranked[0]

    assert_transition(game.state, GameState.COMPLETED)
    game.state = GameState.COMPLETED
    game.completed_at = datetime.now(timezone.utc)
    if winner is not None:
        game.winner_player_id = winner.id

    await db.commit()
