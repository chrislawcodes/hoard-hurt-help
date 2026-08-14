"""User table — one row per Google identity."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enum_types import FlexibleEnumType


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    given_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    family_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        FlexibleEnumType(UserRole, length=16),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
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
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Where this account came from, captured once on the visitor's first page view
    # and carried through the Google sign-in round trip. Raw inputs are stored and
    # the display label is derived at read time, so a bad labelling rule can be
    # changed later without losing the data.
    #
    # Only the referrer's *host* is kept, never the full URL — a referring URL can
    # carry personal data in its query string.
    first_utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_utm_medium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    first_referrer_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_landing_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Three-valued, and the distinction matters:
    #   "mcp"    — account created through an MCP sign-in, where no web session existed
    #   "direct" — capture ran and found no campaign tag and no external referrer
    #   NULL     — capture never ran (feature off, or the row predates this feature)
    # Without the explicit "direct", a row that was never captured is
    # indistinguishable from a genuine direct visit, and the biggest line on the
    # sources table would silently mean "we failed to record this".
    first_source_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # House, harness and admin accounts. Set once when the account is created and
    # never recomputed: both the email address and the role can change on a later
    # login, so deriving this at read time would let an account drift in and out of
    # the excluded group between two page loads.
    is_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
