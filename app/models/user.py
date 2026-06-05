"""User table — one row per Google identity."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    given_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Public, chosen display name shown as "by @handle" on the leaderboard.
    # `handle` keeps the case the user typed; `handle_key` is its lowercased form
    # and carries the unique index, so uniqueness is case-insensitive while the
    # displayed capitalization is preserved. Both are NULL until the user picks
    # one (required before owning an agent). Google identity stays the auth layer;
    # the handle is display only and never used for authentication.
    handle: Mapped[str | None] = mapped_column(String(20), nullable=True)
    handle_key: Mapped[str | None] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    # When the handle was last set or changed — powers the 30-day change cooldown.
    handle_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
