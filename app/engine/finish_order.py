"""In what order did the players finish a completed match.

The cooperator-wins tiebreak chain: most round wins, then highest total
score, then — for games that care about cooperation — how much a player
helped and was helped. This is the one home for "who finished ahead of
whom": both ``finalize_game`` (the recorded winner) and
``BaseGameModule.final_placement`` (the full finish order, used by ratings
and the replay rail's final state) sort with the same
``GameModule.match_placement_key`` through the functions below, so the two
can never disagree about who won.

A tie is real, not a bug: a game's own ``match_placement_key`` decides how
many fields have to agree for two players to be inseparable. For Hoard Hurt
Help that is seven fields deep, so a full tie is rare — but when every field
agrees, ``winner`` returns ``None`` rather than inventing a seat-order or
id-based tiebreak to force a single name out. See
docs/operations/what-shipped-and-why.md for the evidence this was measured
against and the alternatives that were rejected.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.turn import Turn, TurnSubmission
from app.read_models.matches import load_players

_Id = TypeVar("_Id")


class MatchPlacementKeyed(Protocol):
    """Structural stand-in for a ``GameModule``: only the one method this
    module needs.

    Deliberately not ``app.games.base.GameModule`` itself: ``final_placement``
    (base.py) already calls into this module, so importing ``GameModule``
    back here — even only for a type hint — would close an import cycle.
    Any object with a compatible ``match_placement_key`` (every real game
    module) satisfies this structurally, with no inheritance required.
    """

    def match_placement_key(
        self,
        *,
        round_wins: float,
        total_score: int,
        help_received: int = 0,
        help_given: int = 0,
        hurt_received: int = 0,
        hurt_given: int = 0,
        hoard_points: int = 0,
    ) -> tuple[float, ...]: ...


@dataclass(frozen=True)
class CooperationTally:
    """One competitor's cooperation counts. Zero for a competitor who never
    appears in the folded moves (never acted, or acted only before the cutoff
    a caller cares about)."""

    help_received: int = 0
    help_given: int = 0
    hurt_received: int = 0
    hurt_given: int = 0
    hoard_points: int = 0


_ZERO_TALLY = CooperationTally()


def cooperation_tally(
    moves: Iterable[tuple[_Id, _Id | None, str, int, bool]],
) -> dict[_Id, CooperationTally]:
    """Fold ``(actor, target, action, points_delta, was_defaulted)`` rows into
    a per-actor cooperation tally.

    The one home for "what counts": a defaulted HELP or HURT counts toward
    neither side — it is the missed-turn default, not a choice anyone made.
    A defaulted HOARD's ``points_delta`` still counts — the score floor still
    paid out. Works on any id type: a DB ``player_id`` (int) for the
    DB-backed finish order below, an agent id (str) for the DB-free
    spectator standings in ``app/engine/game_insights.py`` — so both fold
    their own rows through this one function instead of each re-deciding
    what a HELP or HURT "counts as".
    """
    help_received: Counter[_Id] = Counter()
    help_given: Counter[_Id] = Counter()
    hurt_received: Counter[_Id] = Counter()
    hurt_given: Counter[_Id] = Counter()
    hoard_points: Counter[_Id] = Counter()
    seen: set[_Id] = set()
    for actor, target, action, points_delta, was_defaulted in moves:
        seen.add(actor)
        if action == "HOARD":
            hoard_points[actor] += points_delta
        elif not was_defaulted:
            if action == "HELP":
                help_given[actor] += 1
                if target is not None:
                    help_received[target] += 1
                    seen.add(target)
            elif action == "HURT":
                hurt_given[actor] += 1
                if target is not None:
                    hurt_received[target] += 1
                    seen.add(target)
    return {
        pid: CooperationTally(
            help_received=help_received[pid],
            help_given=help_given[pid],
            hurt_received=hurt_received[pid],
            hurt_given=hurt_given[pid],
            hoard_points=hoard_points[pid],
        )
        for pid in seen
    }


@dataclass(frozen=True)
class FinishRecord:
    """One player's finish-line stats for a completed match: their ``Player``
    totals plus their cooperation tally from every resolved turn. Feeds
    ``placement_groups``/``winner`` through the match's own
    ``match_placement_key``."""

    player_id: int
    seat_name: str
    round_wins: float
    total_score: int
    help_received: int
    help_given: int
    hurt_received: int
    hurt_given: int
    hoard_points: int


async def load_finish_records(db: AsyncSession, match_id: str) -> list[FinishRecord]:
    """Build one ``FinishRecord`` per player of a match.

    One query for the cooperation tally — ``TurnSubmission`` joined to
    ``Turn``, resolved turns only, for this match — never one query per
    player, folded in Python via :func:`cooperation_tally`.
    """
    players = await load_players(db, match_id)
    rows = (
        await db.execute(
            select(
                TurnSubmission.player_id,
                TurnSubmission.target_player_id,
                TurnSubmission.action,
                TurnSubmission.points_delta,
                TurnSubmission.was_defaulted,
            )
            .join(Turn, Turn.id == TurnSubmission.turn_id)
            .where(Turn.match_id == match_id, Turn.resolved_at.is_not(None))
        )
    ).all()
    tallies: dict[int, CooperationTally] = cooperation_tally(tuple(row) for row in rows)
    return [
        FinishRecord(
            player_id=p.id,
            seat_name=p.seat_name,
            round_wins=p.total_round_wins,
            total_score=p.total_round_score,
            help_received=tallies.get(p.id, _ZERO_TALLY).help_received,
            help_given=tallies.get(p.id, _ZERO_TALLY).help_given,
            hurt_received=tallies.get(p.id, _ZERO_TALLY).hurt_received,
            hurt_given=tallies.get(p.id, _ZERO_TALLY).hurt_given,
            hoard_points=tallies.get(p.id, _ZERO_TALLY).hoard_points,
        )
        for p in players
    ]


def _placement_key(record: FinishRecord, game: MatchPlacementKeyed) -> tuple[float, ...]:
    return game.match_placement_key(
        round_wins=record.round_wins,
        total_score=record.total_score,
        help_received=record.help_received,
        help_given=record.help_given,
        hurt_received=record.hurt_received,
        hurt_given=record.hurt_given,
        hoard_points=record.hoard_points,
    )


def placement_groups(
    records: Sequence[FinishRecord], game: MatchPlacementKeyed
) -> list[list[FinishRecord]]:
    """Group finish records by placement: records with an identical
    ``match_placement_key`` share a group. Best group first (index 0).

    Within a group, ordered by ``seat_name`` — display only, never a
    tiebreak: a group with more than one member IS the answer for "who
    finished here" — nobody in it out-finished anyone else, so there is
    deliberately no seat-order or id-based key breaking it further.
    """
    keyed = [(_placement_key(r, game), r) for r in records]
    keyed.sort(key=lambda pair: (tuple(-x for x in pair[0]), pair[1].seat_name))

    groups: list[list[FinishRecord]] = []
    last_key: tuple[float, ...] | None = None
    for placement_key, record in keyed:
        if placement_key != last_key:
            groups.append([])
            last_key = placement_key
        groups[-1].append(record)
    return groups


def winner(records: Sequence[FinishRecord], game: MatchPlacementKeyed) -> FinishRecord | None:
    """The sole top-placed record, or ``None`` when two or more share the top
    group — the shared-win case described in the module docstring."""
    groups = placement_groups(records, game)
    if not groups:
        return None
    top = groups[0]
    return top[0] if len(top) == 1 else None
