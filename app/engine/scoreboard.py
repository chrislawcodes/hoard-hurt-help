"""Project player rows into the agent-facing scoreboard schema.

The agent API, the spectator API, and the Sim service all need the same
`(agent_id, round_score, round_wins)` shape. They differ only in which players
they query (e.g. whether to include bots that have left), so this keeps the row
mapping in one place while each caller owns its own query.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.player import Player
from app.schemas.agent import ScoreboardRow


def scoreboard_rows(players: Iterable[Player]) -> list[ScoreboardRow]:
    """Map player rows to scoreboard rows in the order given."""
    return [
        ScoreboardRow(
            agent_id=p.agent_id,
            round_score=p.current_round_score,
            round_wins=p.total_round_wins,
        )
        for p in players
    ]
