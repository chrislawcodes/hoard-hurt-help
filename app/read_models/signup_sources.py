"""Which traffic source produced signups, and which of those went on to play.

This is the number no off-the-shelf analytics tool can give you, because only this
database knows whether the person who arrived from a link ever played a match.

The one distinction that makes the table honest: **"direct" and "we never captured
this" are different things.** Capture writes an explicit "direct" when it ran and
found no campaign tag and no external referrer; a row with nothing recorded at all
means capture never ran — because the feature was off, or the account predates it,
or it was created through a path with no browser session. Folding the two together
would make the largest line here silently mean "we failed to record this".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.user_milestone import MilestoneKind, UserMilestone

UNKNOWN_LABEL = "unknown"


@dataclass(frozen=True)
class SourceRow:
    label: str
    signups: int
    played: int

    @property
    def played_share(self) -> float | None:
        return self.played / self.signups if self.signups else None


def derive_label(
    utm_source: str | None, referrer_host: str | None, channel: str | None
) -> str:
    """The display label for one account's recorded first touch.

    Precedence: an explicit campaign tag beats the referring site, which beats
    whatever channel was recorded. Nothing at all is "unknown", never "direct".
    """
    if utm_source:
        return utm_source
    if referrer_host:
        return referrer_host
    if channel:
        return channel
    return UNKNOWN_LABEL


async def load_signup_sources(
    db: AsyncSession,
    *,
    signed_up_after: datetime | None = None,
    signed_up_before: datetime | None = None,
) -> list[SourceRow]:
    """Signups per source, and how many of them ever played a genuine turn."""
    stmt = select(
        User.id,
        User.first_utm_source,
        User.first_referrer_host,
        User.first_source_channel,
    ).where(User.is_internal.is_(False))
    if signed_up_after is not None:
        stmt = stmt.where(User.created_at >= signed_up_after)
    if signed_up_before is not None:
        stmt = stmt.where(User.created_at < signed_up_before)

    rows = (await db.execute(stmt)).all()
    if not rows:
        return []

    # Distinct users, never rows: one person can own several agents and dozens of
    # player rows, and a join would report them as dozens of players.
    played_ids = set(
        (
            await db.execute(
                select(UserMilestone.user_id).where(
                    UserMilestone.milestone == MilestoneKind.PLAYED_TURN,
                    UserMilestone.user_id.in_([row[0] for row in rows]),
                )
            )
        )
        .scalars()
        .all()
    )

    signups: dict[str, int] = {}
    played: dict[str, int] = {}
    for user_id, utm_source, referrer_host, channel in rows:
        label = derive_label(utm_source, referrer_host, channel)
        signups[label] = signups.get(label, 0) + 1
        if user_id in played_ids:
            played[label] = played.get(label, 0) + 1

    return sorted(
        (
            SourceRow(label=label, signups=count, played=played.get(label, 0))
            for label, count in signups.items()
        ),
        key=lambda row: (-row.signups, row.label),
    )
