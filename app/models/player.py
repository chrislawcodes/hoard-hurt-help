"""Player table — one row per match participation."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("match_id", "seat_name", name="uq_players_match_id_seat_name"),
        UniqueConstraint("agent_id", "match_id", name="uq_players_agent_id_match_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(
        ForeignKey("matches.id"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("agents.id"), nullable=False, index=True
    )
    served_by_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    served_pinned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    agent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_versions.id"),
        nullable=True,
        index=True,
    )
    seat_name: Mapped[str] = mapped_column(String(40), nullable=False)
    model_self_report: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The AI the user PICKED to play this seat, chosen at join. Turn routing only
    # lets a connection of this provider claim the seat, and "one AI = one game"
    # is enforced by refusing to pick a provider already chosen for another
    # not-finished seat. NULL only for legacy rows created before pick-at-join.
    chosen_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Provider that ACTUALLY played this seat, stamped when a connection first
    # claims a turn for it (the sticky pin). Source of truth for the public
    # "played by Claude/Gemini/…" badge. With matched routing this equals
    # chosen_provider, but it stays NULL until the seat's first turn is claimed —
    # so the badge only appears once the AI has really played.
    played_provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Seat-hold for join-before-connect. When set, this seat is "held": the user
    # joined with an agent whose AI provider wasn't live yet and has until this
    # deadline to bring it online. Cleared to NULL once the provider goes live
    # (seat confirmed). A held seat is NOT counted as a real player and is
    # released (row deleted) if the deadline passes before the provider is live.
    seat_reserved_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Human-player autopilot. Set when a human leaves a match in progress: the
    # seat is NOT removed (that is `left_at`), it keeps playing on autopilot —
    # the bot auto-submit pass records Hoard / an empty message for it every
    # turn, immediately, so it never makes the table wait. The seat stays ranked.
    autopilot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_round_wins: Mapped[float] = mapped_column(default=0.0, nullable=False)
    total_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_round_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Operator sideline-coaching: free-text note (≤280 chars) queued for a
    # specific round. Included in the next-turn payload whenever
    # match.current_round == coach_note_round. One active note per player slot.
    coach_note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    coach_note_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
