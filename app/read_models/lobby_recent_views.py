"""Read model for the lobby's finished-match sections."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.match_naming import is_smoke_test_match_name
from app.models.agent import Agent, AgentKind
from app.models.match import Match, GameState
from app.models.player import Player
from app.read_models.agent_display import agent_display_name


def _lobby_timestamp(match: Match) -> datetime:
    """Pick the timestamp we want to show for a finished or cancelled match."""
    return match.completed_at or match.cancelled_at or match.started_at or match.scheduled_start


async def load_lobby_recent_views(
    db: AsyncSession,
) -> dict[str, list[dict[str, Any]]]:
    """Build the lobby's finished-match sections in one read-side projection."""

    player_counts = (
        select(
            Player.match_id.label("match_id"),
            func.count(Player.id).label("player_count"),
            func.coalesce(
                func.sum(case((Agent.kind == AgentKind.BOT, 1), else_=0)),
                0,
            ).label("bot_count"),
            func.coalesce(
                func.sum(case((Agent.kind != AgentKind.BOT, 1), else_=0)),
                0,
            ).label("agent_count"),
        )
        .join(Agent, Agent.id == Player.agent_id)
        .group_by(Player.match_id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                Match,
                func.coalesce(player_counts.c.player_count, 0),
                func.coalesce(player_counts.c.bot_count, 0),
                func.coalesce(player_counts.c.agent_count, 0),
            )
            .outerjoin(player_counts, player_counts.c.match_id == Match.id)
            .where(Match.state.in_([GameState.COMPLETED, GameState.CANCELLED]))
            .order_by(Match.scheduled_start.desc())
        )
    ).all()

    winner_ids = {
        match.winner_player_id
        for match, *_ in rows
        if match.state == GameState.COMPLETED and match.winner_player_id is not None
    }
    winner_rows: dict[int, dict[str, Any]] = {}
    if winner_ids:
        winner_rows = {
            player_id: {
                "display_name": agent_display_name(agent),
                "is_bot": agent.kind == AgentKind.BOT,
            }
            for player_id, agent in (
                await db.execute(
                    select(Player.id, Agent)
                    .join(Agent, Agent.id == Player.agent_id)
                    .where(Player.id.in_(winner_ids))
                )
            ).all()
        }

    completed: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    bots_only: list[dict[str, Any]] = []
    cancelled: list[dict[str, Any]] = []
    for match, player_count, bot_count, agent_count in rows:
        if is_smoke_test_match_name(str(match.name)):
            continue
        timestamp = _lobby_timestamp(match)
        view: dict[str, Any] = {
            "id": match.id,
            "game_type": match.game,
            "name": match.name,
            "state": match.state,
            "player_count": int(player_count),
            "bot_count": int(bot_count),
            "agent_count": int(agent_count),
            "timestamp": timestamp,
            "timestamp_label": "Completed" if match.state == GameState.COMPLETED else "Cancelled",
            "winner_display_name": (
                winner_rows.get(match.winner_player_id, {}).get("display_name")
                if match.winner_player_id
                else None
            ),
            "winner_is_bot": (
                winner_rows.get(match.winner_player_id, {}).get("is_bot", False)
                if match.winner_player_id
                else False
            ),
            "watch_url": f"/games/{match.game}/matches/{match.id}",
        }
        if view["winner_display_name"]:
            view["summary"] = f"Won by {view['winner_display_name']}"
        elif match.state == GameState.COMPLETED:
            view["summary"] = "Finished"
        else:
            view["summary"] = "Cancelled"
        if match.state == GameState.COMPLETED:
            completed.append(view)
            if int(agent_count) > 0:
                recent.append(view)
            elif int(player_count) > 0:
                bots_only.append(view)
        elif match.state == GameState.CANCELLED:
            cancelled.append(view)

    for group in (completed, recent, bots_only, cancelled):
        group.sort(key=lambda v: v["timestamp"], reverse=True)

    return {
        "completed": completed,
        "recent": recent,
        "bots_only": bots_only,
        "cancelled": cancelled,
    }
