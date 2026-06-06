"""Bot table — a persistent agent owned by a user, with one stable credential.

A bot holds the single paste-once credential (`sk_bot_<hex>`) and is the unit
that enters games. Auth resolves a presented key to a bot via `key_lookup`
(an indexed sha256 of the key); the plaintext is shown once at issue/reissue
and never stored. One user may own several bots.
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class BotStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class BotKind(str, enum.Enum):
    EXTERNAL = "external"
    SIM = "sim"


class BotProvider(str, enum.Enum):
    CLAUDE = "claude"
    GEMINI = "gemini"
    OPENAI = "openai"
    HERMES = "hermes"
    OPENCLAW = "openclaw"


class Bot(Base):
    __tablename__ = "bots"
    # A user's bot names are distinct so the owner can tell them apart.
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_bots_user_id_name"),
        UniqueConstraint(
            "user_id", "sim_profile_id", name="uq_bots_user_id_sim_profile_id"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # sha256(plaintext key), unique + indexed for O(1) auth lookup. Never store plaintext.
    key_lookup: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # sha256 of the PREVIOUS key during a graceful reissue. Both the current and
    # this key authenticate, so reissuing never knocks a still-running bot
    # offline; the old key is cleared the first time the new key is used (see
    # require_bot). NULL = no outstanding reissue. A "revoke now" reissue skips
    # the overlap and clears this immediately for the leaked-key case.
    prev_key_lookup: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    # Last 4 chars of the key, shown in the UI to distinguish bots. Not secret.
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    # Whether this bot is an external LLM runner or a deterministic platform Sim.
    kind: Mapped[BotKind] = mapped_column(
        FlexibleEnumType(BotKind, length=16),
        nullable=False,
        default=BotKind.EXTERNAL,
        server_default=BotKind.EXTERNAL.value,
    )
    status: Mapped[BotStatus] = mapped_column(
        FlexibleEnumType(BotStatus, length=16),
        nullable=False,
        default=BotStatus.ACTIVE,
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Soft-delete marker. NULL = live. Set = the owner deleted the bot but it had
    # game history (players FK back to it), so the row is kept to preserve that
    # history. An archived bot is hidden from the owner's lists, can't enter new
    # games, and its key no longer authenticates — so it stops playing. A bot
    # with no history is hard-deleted instead and never gets here.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Last time this bot's key authenticated an agent call — the live heartbeat.
    # Stamped (throttled) on every authenticated call, so it answers "is the
    # runner alive right now?", which first_connected_at (set once) cannot.
    # NULL = never connected. Powers the operational-health badge (compute_bot_health).
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Which AI provider this bot uses. NULL = not configured (runner defaults to Claude).
    provider: Mapped[BotProvider | None] = mapped_column(
        FlexibleEnumType(BotProvider, length=16),
        nullable=True,
    )
    # Specific model ID within the provider (e.g. "claude-sonnet-4-6"). NULL = provider default.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Platform preset Sim identity, if this bot was auto-provisioned from a preset catalog.
    sim_profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sim_profile_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Sim traits are only meaningful when kind == sim. They stay nullable so
    # external bots keep the same shape without special casing.
    sim_strategy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sim_truthfulness: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sim_trust_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sim_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sim_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sim_fixture_pack: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OS process ID of the currently running agent runner. Reported by the runner
    # at startup so the operator can kill a stuck process. Cleared when the bot
    # disconnects (last_seen_at goes stale). NULL = runner not active or not reporting.
    runner_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
