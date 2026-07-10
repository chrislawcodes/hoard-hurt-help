"""Agent-identity resolution for the MCP instructions flow.

Given a connection, these functions resolve which active AI agent the caller is
asking about and pull that agent's match, seat identity, targets, and strategy
text. This is a cold path: it never claims a turn and does not depend on an
open turn window — turn claiming and serving live in ``agent_play_next_turn``.
Like that module, it sits above ``agent_play_reads`` and is not imported by the
per-match verbs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aware_datetime import ensure_aware
from app.engine.agent_play_reads import load_open_turns, sorted_seat_names
from app.models.agent import Agent, AgentKind, AgentStatus
from app.models.agent_version import AgentVersion
from app.models.connection import Connection
from app.models.match import Match, GameState
from app.models.player import Player
from app.models.turn import Turn

# One (agent, player, match, version) row of the connection's in-play seats; the
# version is optional because the AgentVersion join is an outer join.
_AgentMatchRow = tuple[Agent, Player, Match, AgentVersion | None]


async def _active_ai_agent_ids(db: AsyncSession, connection: Connection) -> list[int]:
    """The connection user's active, non-archived AI agent ids, sorted."""
    return sorted(
        (
            await db.execute(
                select(Agent.id).where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def _identity_candidate_rows(
    db: AsyncSession, connection: Connection
) -> list[_AgentMatchRow]:
    """Every (agent, player, match, version) the user has in a live or upcoming
    match — the candidate set the identity picker ranks over.

    An ACTIVE match resolves the seat's pinned version (what turn serving uses,
    stamped at match start); an upcoming match previews the agent's current
    pointer — the version that will be pinned when it starts.
    """
    return [
        cast(_AgentMatchRow, row)
        for row in (
            await db.execute(
                select(Agent, Player, Match, AgentVersion)
                .join(Player, Player.agent_id == Agent.id)
                .join(Match, Match.id == Player.match_id)
                .join(
                    AgentVersion,
                    AgentVersion.id
                    == case(
                        (Match.state == GameState.ACTIVE, Player.agent_version_id),
                        else_=Agent.current_version_id,
                    ),
                    isouter=True,
                )
                .where(
                    Agent.user_id == connection.user_id,
                    Agent.kind == AgentKind.AI,
                    Agent.status == AgentStatus.ACTIVE,
                    Agent.archived_at.is_(None),
                    Player.left_at.is_(None),
                    Match.state.in_(
                        [GameState.ACTIVE, GameState.SCHEDULED, GameState.REGISTERING]
                    ),
                )
            )
        ).all()
    ]


def _rank_agent_matches(
    match_rows: list[list[_AgentMatchRow]],
    open_turns_by_match: dict[str, Turn],
) -> list[list[_AgentMatchRow]]:
    """Order the candidate matches so the most urgent one sorts first.

    Ranking key (smallest wins): active-before-upcoming, then the soonest "when"
    (an active match's open-turn deadline, else the match's scheduled start, else
    the far future), then match id as a stable final tiebreak. Open turns come
    from a single batched lookup rather than one query per match.
    """
    ranked: list[tuple[tuple[object, ...], list[_AgentMatchRow]]] = []
    for rows in match_rows:
        match = rows[0][2]
        current_turn = (
            open_turns_by_match.get(match.id)
            if match.state == GameState.ACTIVE
            else None
        )
        if current_turn is not None:
            when = ensure_aware(current_turn.deadline_at)
        elif match.scheduled_start is not None:
            when = ensure_aware(match.scheduled_start)
        else:
            when = datetime.max.replace(tzinfo=timezone.utc)
        ranked.append(
            ((0 if match.state == GameState.ACTIVE else 1, when, match.id), rows)
        )
    return [rows for _key, rows in sorted(ranked, key=lambda item: item[0])]


def _extract_agent_identity(
    selected_rows: list[_AgentMatchRow], selected_agent_id: int
) -> tuple[Match, str, list[Any], str] | None:
    """Pull the chosen agent's identity out of its match's rows, or None if the
    seat or its current version is missing.
    """
    match = selected_rows[0][2]
    your_player = next(
        (
            player
            for agent, player, _match, _version in selected_rows
            if agent.id == selected_agent_id
        ),
        None,
    )
    version = next(
        (
            version
            for agent, _player, _match, version in selected_rows
            if agent.id == selected_agent_id and version is not None
        ),
        None,
    )
    if your_player is None or version is None:
        return None
    seat_name_by_agent_id = {
        player.agent_id: player.seat_name
        for _agent, player, _match, _version in selected_rows
    }
    all_agent_ids = sorted_seat_names(seat_name_by_agent_id)
    return (
        match,
        seat_name_by_agent_id[your_player.agent_id],
        all_agent_ids,
        version.strategy_text,
    )


async def agent_identity_for(
    db: AsyncSession,
    connection: Connection,
    *,
    agent_id: int | None = None,
    match_id: str | None = None,
) -> tuple[Match | None, str | None, list[Any], str | None]:
    """Resolve one active agent's match, identity, targets, and strategy.

    This is for the MCP instructions flow, not turn claiming. It looks at the
    user's active AI agents and their live or upcoming matches, but it never
    claims a turn and it does not depend on an open turn window. For a live
    match the strategy is the seat's pinned version — the same text turn
    serving emits (see ``_identity_candidate_rows``).
    """
    active_agent_ids = await _active_ai_agent_ids(db, connection)
    if not active_agent_ids:
        return None, None, [], None
    if agent_id is None and len(active_agent_ids) > 1:
        return None, None, list(active_agent_ids), None

    selected_agent_id = agent_id or active_agent_ids[0]
    if selected_agent_id not in active_agent_ids:
        return None, None, [], None

    candidate_rows = await _identity_candidate_rows(db, connection)
    if not candidate_rows:
        return None, None, [], None

    rows_by_match_id: dict[str, list[_AgentMatchRow]] = {}
    for agent, player, match, version in candidate_rows:
        rows_by_match_id.setdefault(match.id, []).append((agent, player, match, version))

    match_rows = [
        rows
        for rows in rows_by_match_id.values()
        if any(agent.id == selected_agent_id for agent, _player, _match, _version in rows)
    ]
    if not match_rows:
        return None, None, [], None
    if match_id is not None:
        match_rows = [rows for rows in match_rows if rows[0][2].id == match_id]
        if not match_rows:
            return None, None, [], None

    # Batch the open-turn lookup for every candidate match in one query instead of
    # one per match; only active matches consult it during ranking.
    open_turns_by_match = await load_open_turns(
        db, [rows[0][2].id for rows in match_rows]
    )
    selected_rows = _rank_agent_matches(match_rows, open_turns_by_match)[0]
    identity = _extract_agent_identity(selected_rows, selected_agent_id)
    if identity is None:
        return None, None, [], None
    return identity
