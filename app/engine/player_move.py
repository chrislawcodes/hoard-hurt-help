"""Shared 'record one player's action' core.

Both the bot auto-submit pass (`app/engine/bots/service.py`) and the human play
route (`app/routes/web_play.py`) record a move the same way: validate the move
against the *public* seat names, translate the chosen target seat name to the
internal `agent_id` the storage layer expects, then call the game module's
`record_submission`. Keeping that one sequence here means the two paths can't
drift (the same reason HTTP and MCP play share `agent_play`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.games.base import GameModule
from app.models.turn import Turn, TurnSubmission


async def record_player_action(
    db: AsyncSession,
    module: GameModule,
    turn: Turn,
    player: Any,
    *,
    move: dict[str, Any],
    all_seat_names: list[str],
    agent_id_by_seat_name: dict[str, int],
    existing: TurnSubmission | None,
    is_connector_fallback: bool = False,
) -> None:
    """Validate ``move`` (public seat names) and persist it for ``player``.

    ``move`` uses public seat names for ``target_id`` (what the player/bot chose);
    this translates it to the internal ``agent_id`` before recording, exactly as
    the agent HTTP path does. Does not commit — the caller owns the transaction.
    """
    module.validate_move(
        move, your_agent_id=player.seat_name, all_agent_ids=all_seat_names
    )
    internal_move: dict[str, Any] = {**move}
    target_seat_name = move.get("target_id")
    if target_seat_name is not None:
        internal_move["target_id"] = agent_id_by_seat_name.get(target_seat_name)
    await module.record_submission(
        db,
        turn,
        player,
        internal_move,
        existing=existing,
        is_connector_fallback=is_connector_fallback,
    )
