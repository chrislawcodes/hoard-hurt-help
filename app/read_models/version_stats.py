"""Per-strategy-version performance projections.

Shared by the agent detail page (version hero + timeline) and the join screen's
agent cards, so both surfaces show the same record for a version. All reads are
batched over many version ids — never one query per version.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.match import GameState, Match, MatchKind
from app.models.player import Player


@dataclass(frozen=True)
class VersionStats:
    """Completed-match record for one strategy version.

    ``rated_wins`` counts completed rated matches whose recorded winner
    (``Match.winner_player_id``, picked by ``resolver.finalize_game``'s finish
    order: most round-wins, then highest total score) was this version's seat —
    the same first-place definition the match page uses. "Rated" means any
    completed match that is not a practice arena.
    """

    rated_matches: int = 0
    rated_wins: int = 0
    practice_matches: int = 0

    @property
    def record_label(self) -> str | None:
        """The record line shown in the UI, or None with no rated matches yet."""
        if not self.rated_matches:
            return None
        noun = "match" if self.rated_matches == 1 else "matches"
        return f"Won {self.rated_wins} of {self.rated_matches} rated {noun}"


@dataclass(frozen=True)
class VersionMatchLink:
    """One completed match a version played, linkable from its timeline row."""

    match_id: str
    game_type: str
    name: str


async def version_stats_by_id(
    db: AsyncSession, version_ids: Sequence[int]
) -> dict[int, VersionStats]:
    """Completed-match stats for many versions in one grouped query.

    Counts each version's seats (``Player.agent_version_id``) in COMPLETED
    matches, split rated vs practice-arena, plus rated wins. Versions with no
    completed matches are absent from the map (read a miss as all-zero). Seats
    whose player left (``left_at`` set) do not count as a played match.
    """
    if not version_ids:
        return {}
    is_practice = Match.match_kind == MatchKind.PRACTICE_ARENA.value
    rows = (
        await db.execute(
            select(
                Player.agent_version_id,
                func.sum(case((is_practice, 0), else_=1)),
                func.sum(
                    case(
                        (is_practice, 0),
                        (Match.winner_player_id == Player.id, 1),
                        else_=0,
                    )
                ),
                func.sum(case((is_practice, 1), else_=0)),
            )
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_version_id.in_(version_ids),
                Player.left_at.is_(None),
                Match.state == GameState.COMPLETED,
            )
            .group_by(Player.agent_version_id)
        )
    ).all()
    return {
        version_id: VersionStats(
            rated_matches=int(rated or 0),
            rated_wins=int(wins or 0),
            practice_matches=int(practice or 0),
        )
        for version_id, rated, wins, practice in rows
    }


async def recent_completed_matches_by_version(
    db: AsyncSession, version_ids: Sequence[int], *, per_version: int = 3
) -> dict[int, list[VersionMatchLink]]:
    """The most recent completed matches each version played, newest first.

    One query for all versions; capped at ``per_version`` links per version in
    memory. Versions that never completed a match are absent from the map.
    """
    if not version_ids:
        return {}
    rows = (
        await db.execute(
            select(Player.agent_version_id, Match.id, Match.game, Match.name)
            .join(Match, Match.id == Player.match_id)
            .where(
                Player.agent_version_id.in_(version_ids),
                Player.left_at.is_(None),
                Match.state == GameState.COMPLETED,
            )
            # completed_at is set by finalize_game; coalesce guards legacy rows
            # (NULL ordering differs between SQLite and Postgres).
            .order_by(
                func.coalesce(
                    Match.completed_at, Match.started_at, Match.scheduled_start
                ).desc(),
                Match.id.desc(),
            )
        )
    ).all()
    links: dict[int, list[VersionMatchLink]] = {}
    for version_id, match_id, game_type, name in rows:
        bucket = links.setdefault(version_id, [])
        if len(bucket) < per_version:
            bucket.append(
                VersionMatchLink(match_id=match_id, game_type=game_type, name=name)
            )
    return links
