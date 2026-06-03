"""ORM models. Import all here so Alembic autogen discovers them."""

from app.models.base import Base
from app.models.user import User
from app.models.match import GameState, Match
from app.models.bot import Bot, BotKind, BotStatus
from app.models.player import Player
from app.models.request_incident import RequestIncident
from app.models.strategy_prompt import StrategyPrompt
from app.models.turn import Turn, TurnMessage, TurnSubmission

__all__ = [
    "Base",
    "User",
    "Match",
    "GameState",
    "Bot",
    "BotKind",
    "BotStatus",
    "Player",
    "RequestIncident",
    "StrategyPrompt",
    "Turn",
    "TurnMessage",
    "TurnSubmission",
]
