"""Create a new match that starts from a point in a finished one.

A match killed mid-flight cannot be rewound. Once a turn's deadline passes the
missing moves are recorded as defaults, and a default scores as a HOARD — so the
fabricated moves sit in the data looking like decisions. M_7558 lost rounds five
through seven that way to a bug in the match runner's own stuck-agent alarm: 120
of its 280 moves were defaults.

This builds a NEW match holding the position as of a chosen turn, with the same
players in the same seats, and leaves it ready to be played from there by the
ordinary path — real agents over MCP, the real scheduler, the real serving code.
Nothing here plays a turn or scores one.

TWO RULES, both of which are the whole point:

**It never touches the source match.** The result is a new row with a new id.
The original stays exactly as recorded, so a recovery cannot destroy the
evidence of what went wrong, and a bad resume can simply be cancelled.

**It copies the recorded numbers rather than recomputing them.** Every seeded
submission keeps its own `points_delta` and `round_score_after`. Re-scoring the
history with today's resolver would silently restate the position whenever the
payoffs have moved since — replaying a v10 match under v11 costs an attacker
four to five points a round — and the whole purpose is to resume from the
position that actually existed. Turns played AFTER the cut are new play and are
scored by today's rules, which is correct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.tokens import generate_match_id
from app.models.match import GameState, Match, MatchKind
from app.models.player import Player
from app.models.turn import Turn, TurnMessage, TurnSubmission

__all__ = ["ResumePoint", "ResumeError", "resume_match_from"]


class ResumeError(Exception):
    """The requested resume cannot be built. Carries a reason for the caller."""


@dataclass(frozen=True)
class ResumePoint:
    round: int
    turn: int

    def __post_init__(self) -> None:
        if self.round < 1 or self.turn < 1:
            raise ResumeError("round and turn both start at 1")

    def as_tuple(self) -> tuple[int, int]:
        return (self.round, self.turn)


async def resume_match_from(
    db: AsyncSession,
    source: Match,
    at: ResumePoint,
    *,
    name: str | None = None,
    created_by_user_id: int | None = None,
) -> tuple[Match, int, int]:
    """Build a new match holding `source`'s position just before `at`.

    Returns the new match, how many turns were seeded, and how many of the
    seeded moves were recorded as defaults rather than chosen. That default
    count is returned rather than logged because it is the number that decides
    whether a resume is worth running: a position built on fabricated moves is
    not the position anyone played.
    """
    if at.round > source.total_rounds or at.turn > source.turns_per_round:
        raise ResumeError(
            f"R{at.round}T{at.turn} is outside a "
            f"{source.total_rounds}x{source.turns_per_round} match"
        )

    players = list(
        (await db.execute(select(Player).where(Player.match_id == source.id))).scalars().all()
    )
    if not players:
        raise ResumeError(f"{source.id} has no players to carry over")

    turns = list(
        (
            await db.execute(
                select(Turn).where(Turn.match_id == source.id).order_by(Turn.round, Turn.turn)
            )
        )
        .scalars()
        .all()
    )
    seeded_turns = [t for t in turns if (t.round, t.turn) < at.as_tuple()]
    if not seeded_turns:
        raise ResumeError(
            f"nothing to carry over: {source.id} has no resolved turn before R{at.round}T{at.turn}"
        )
    unresolved = [t for t in seeded_turns if t.resolved_at is None]
    if unresolved:
        first = unresolved[0]
        raise ResumeError(
            f"R{first.round}T{first.turn} never resolved, so the position before "
            f"R{at.round}T{at.turn} is incomplete"
        )

    now = datetime.now(timezone.utc)
    count = await db.scalar(select(func.count()).select_from(Match)) or 0
    new_match = Match(
        id=generate_match_id(count + 1),
        name=name or f"{source.name} — resumed at R{at.round}T{at.turn}",
        game=source.game,
        created_by_user_id=created_by_user_id,
        state=GameState.REGISTERING,
        # Far enough out that the seats can be brought back online before the
        # auto-start poller picks it up; an admin can still start it by hand.
        scheduled_start=now + timedelta(minutes=10),
        min_players=source.min_players,
        max_players=source.max_players,
        per_turn_deadline_seconds=source.per_turn_deadline_seconds,
        total_rounds=source.total_rounds,
        turns_per_round=source.turns_per_round,
        current_round=at.round,
        current_turn=at.turn,
        rules_version=source.rules_version,
        match_kind=MatchKind.MANUAL.value,
        coaching=source.coaching,
        mutual_help_mode=source.mutual_help_mode,
    )
    db.add(new_match)
    await db.flush()

    # Standings AS OF THE CUT, not the source's finals. The source's totals
    # cover rounds this match has not replayed yet; carrying them straight over
    # counted those rounds twice and produced five round wins in a three-round
    # match. Recomputed from the recorded scores using the game's own
    # `round_award`, so the rule for who wins a round is not written twice.
    standings = await _standings_at(db, source, at, players)

    player_map: dict[int, Player] = {}
    for old in players:
        wins, points, in_round = standings[old.id]
        seat = Player(
            match_id=new_match.id,
            user_id=old.user_id,
            agent_id=old.agent_id,
            agent_version_id=old.agent_version_id,
            seat_name=old.seat_name,
            model_self_report=old.model_self_report,
            chosen_provider=old.chosen_provider,
            joined_at=now,
            total_round_wins=wins,
            total_round_score=points,
            # A cut in the MIDDLE of a round resumes with that round's score
            # intact: the turn loop only zeroes scores when a round starts at
            # turn 1, so this has to be right or the round is scored from a
            # position nobody played to.
            current_round_score=in_round,
        )
        db.add(seat)
        player_map[old.id] = seat
    await db.flush()

    defaulted = 0
    for old_turn in seeded_turns:
        turn = Turn(
            match_id=new_match.id,
            round=old_turn.round,
            turn=old_turn.turn,
            # A fresh token: `turn_token` is globally unique, and reusing the
            # source's would both collide and let an agent holding an old token
            # act on the new match.
            turn_token=f"{new_match.id}-{old_turn.round}-{old_turn.turn}",
            opened_at=old_turn.opened_at,
            deadline_at=old_turn.deadline_at,
            phase=old_turn.phase,
            talk_resolved_at=old_turn.talk_resolved_at,
            resolved_at=old_turn.resolved_at,
        )
        db.add(turn)
        await db.flush()
        defaulted += await _copy_turn_contents(db, old_turn, turn, player_map)

    # Every round before this one finished and was folded into the carried
    # totals; the round being resumed into has not been awarded whether the cut
    # is at its first turn or its middle. Saying so stops the loop awarding a
    # round twice, which would double-count both wins and points.
    new_match.rounds_awarded = at.round - 1
    await db.flush()
    return new_match, len(seeded_turns), defaulted


async def _standings_at(
    db: AsyncSession, source: Match, at: ResumePoint, players: list[Player]
) -> dict[int, tuple[float, int, int]]:
    """Each player's (round wins, points, current-round score) as of the cut.

    Rebuilt from the recorded per-turn scores rather than read off the source's
    Player rows, because those rows hold the FINISHED match's totals — which
    include rounds this resume has not replayed yet.

    Who won a round comes from `round_award`, the same function the live loop
    awards with, so this cannot drift into a second definition of a round win.
    """
    from app.engine.resolver import round_award

    rows = (
        await db.execute(
            select(Turn.round, Turn.turn, TurnSubmission.player_id,
                   TurnSubmission.round_score_after)
            .join(TurnSubmission, TurnSubmission.turn_id == Turn.id)
            .where(Turn.match_id == source.id)
        )
    ).all()
    by_turn: dict[tuple[int, int], dict[int, int]] = {}
    for rnd, turn, player_id, score in rows:
        by_turn.setdefault((rnd, turn), {})[player_id] = score

    wins: dict[int, float] = {p.id: 0.0 for p in players}
    points: dict[int, int] = {p.id: 0 for p in players}
    in_round: dict[int, int] = {p.id: 0 for p in players}

    for rnd in range(1, at.round + 1):
        last_seeded = max(
            (t for (r, t) in by_turn if r == rnd and (r, t) < at.as_tuple()), default=None
        )
        if last_seeded is None:
            continue
        scores = by_turn[(rnd, last_seeded)]
        if (rnd, source.turns_per_round) < at.as_tuple():
            # The round finished before the cut, so it is already decided.
            winners, share = round_award(scores)
            for pid in winners:
                wins[pid] = wins.get(pid, 0.0) + share
            for pid, score in scores.items():
                points[pid] = points.get(pid, 0) + score
        else:
            # The round the match is resuming into: carried, not yet awarded.
            in_round = dict(scores)

    return {p.id: (wins[p.id], points[p.id], in_round.get(p.id, 0)) for p in players}


async def _copy_turn_contents(
    db: AsyncSession, old_turn: Turn, turn: Turn, player_map: dict[int, Player]
) -> int:
    """Copy one turn's moves and talk across. Returns how many moves were defaults."""
    submissions = list(
        (
            await db.execute(select(TurnSubmission).where(TurnSubmission.turn_id == old_turn.id))
        )
        .scalars()
        .all()
    )
    defaulted = 0
    for sub in submissions:
        target = player_map.get(sub.target_player_id) if sub.target_player_id else None
        db.add(
            TurnSubmission(
                turn_id=turn.id,
                player_id=player_map[sub.player_id].id,
                action=sub.action,
                target_player_id=target.id if target else None,
                quantity=sub.quantity,
                face=sub.face,
                message=sub.message,
                thinking=sub.thinking,
                # Recorded, never recomputed — see the module docstring.
                points_delta=sub.points_delta,
                round_score_after=sub.round_score_after,
                was_defaulted=sub.was_defaulted,
                submitted_at=sub.submitted_at,
            )
        )
        if sub.was_defaulted:
            defaulted += 1

    messages = list(
        (await db.execute(select(TurnMessage).where(TurnMessage.turn_id == old_turn.id)))
        .scalars()
        .all()
    )
    for msg in messages:
        db.add(
            TurnMessage(
                turn_id=turn.id,
                player_id=player_map[msg.player_id].id,
                text=msg.text,
                thinking=msg.thinking,
                was_defaulted=msg.was_defaulted,
                submitted_at=msg.submitted_at,
            )
        )
    return defaulted
