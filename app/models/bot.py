"""Bot table — a persistent agent owned by a user, with one stable credential.

A bot holds the single paste-once credential (`sk_bot_<hex>`) and is the unit
that enters games. Auth resolves a presented key to a bot via `key_lookup`
(an indexed sha256 of the key); the plaintext is shown once at issue/reissue
and never stored. One user may own several bots.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BotStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class BotProvider(str, enum.Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"
    OPENAI = "openai"
    HERMES = "hermes"
    OPENCLAW = "openclaw"


class Bot(Base):
    __tablename__ = "bots"
    # A user's bot names are distinct so the owner can tell them apart.
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_bots_user_id_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # sha256(plaintext key), unique + indexed for O(1) auth lookup. Never store plaintext.
    key_lookup: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Last 4 chars of the key, shown in the UI to distinguish bots. Not secret.
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[BotStatus] = mapped_column(
        Enum(BotStatus, native_enum=False, length=16),
        nullable=False,
        default=BotStatus.ACTIVE,
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Owner-set cap on how many games this bot plays at once (token-budget guardrail).
    max_concurrent_games: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # Consecutive missed (defaulted) turns before the bot is flagged / auto-paused.
    stall_threshold: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    # First time this bot's key was used on an authenticated agent call — i.e.
    # the moment it first "connected". Set once, never reset (a reissue does not
    # clear it). NULL = never connected. Powers the onboarding handshake (005).
    first_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Which AI provider this bot uses. NULL = not configured (runner defaults to Claude).
    provider: Mapped[BotProvider | None] = mapped_column(
        Enum(BotProvider, native_enum=False, length=16),
        nullable=True,
    )
    # Specific model ID within the provider (e.g. "claude-sonnet-4-6"). NULL = provider default.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
