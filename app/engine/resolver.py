"""Generic talk-phase, round-winner, and game finalization.

Game-agnostic turn-lifecycle helpers shared by the scheduler and game modules:
talk-phase materialization, round-win awarding, and end-of-game ranking. The
PD-specific per-turn scoring moved to app/games/hoard_hurt_help/scoring.py.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.state_machine import assert_transition
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn, TurnMessage

_Key = TypeVar("_Key")


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


def round_award(round_scores: Mapping[_Key, int]) -> tuple[list[_Key], float]:
    """Who wins a round, and what share of the win each winner takes.

    The whole round-award rule, with no database in it: the highest in-round
    score wins, everyone level with it ties, and a tie splits one win evenly
    (two winners take 0.5 each, three take 1/3). ``award_round_winners`` applies
    it to live play; the replay's per-turn standings apply it to history (see
    ``app/games/replay_standings.py``), so the rail can never invent a different
    rule than the one the match was actually scored by.

    Edge cases are the ones the DB version has always had: no players means no
    winners and a zero share, and an all-zero round still has winners — every
    seat is level on 0, so they all split the round.

    Keys are opaque (player id, seat name, …); only the caller reads them back.
    """
    top = max(round_scores.values(), default=0)
    winners = [key for key, score in round_scores.items() if score == top]
    share = 1.0 / len(winners) if winners else 0.0
    return winners, share


async def award_round_winners(db: AsyncSession, game: Match, round_num: int) -> None:
    """At end of a round, award fractional round-wins to the top scorers.

    Updates total_round_wins and total_round_score on each player. Who wins and
    what each winner's share is comes from :func:`round_award` — the same
    function the replay's standings rail runs over history, so the two can't
    drift apart.

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

    winner_ids, share = round_award({p.id: p.current_round_score for p in players})
    winners = set(winner_ids)

    for p in players:
        if p.id in winners:
            p.total_round_wins += share
        p.total_round_score += p.current_round_score

    game.rounds_awarded = round_num
    await db.commit()


def finish_order_sort_key(player: Player) -> tuple[float, float]:
    """Sort key for the finish order: most round-wins, then highest total score.

    ``finalize_game`` (winner pick) and ``BaseGameModule.final_placement``
    (default finish order) both sort with it so the two can't diverge.
    Ascending sort on the negated fields ranks winner first and, being stable,
    keeps input order for full ties — exactly the behavior of the two inline
    sorts this key replaced.

    NOT the only encoding: leaderboard/Elo tiers rank via the per-game
    ``GameModule.match_placement_key`` protocol method (PD's returns
    ``(round_wins, total_score)`` — the same ordering, different signature).
    A PD tiebreak change must update both, or the match page's winner and the
    rating tiers will disagree.
    """
    return (-player.total_round_wins, -player.total_round_score)


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
        ranked = sorted(players, key=finish_order_sort_key)
        winner = ranked[0]

    assert_transition(game.state, GameState.COMPLETED)
    game.state = GameState.COMPLETED
    game.completed_at = datetime.now(timezone.utc)
    if winner is not None:
        game.winner_player_id = winner.id

    await db.commit()
