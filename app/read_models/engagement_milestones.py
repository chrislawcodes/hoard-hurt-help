"""How far a cohort of signups got, and who is stuck where.

Counts are **independent**, not a strict waterfall. A user is counted at every
milestone they reached, whether or not they reached the ones above it. That is not
a simplification — it is the only reading that survives contact with this product,
which has at least four different paths through it:

* AI agent set up through the web
* AI agent connected through MCP, which stamps a live connection before the user
  has an agent or even a handle
* manual human play, which never reaches ``ai_connected`` at all — and which is
  the DEFAULT for a brand-new user
* platform bots, which are not a user journey

Under a strict funnel, every path that skips or reorders a rung shows up as a drop
that did not happen, and someone playing every day by hand reads as having quit at
"picked a handle".

Two numbers are still derived at read time rather than stored:

* ``returned`` — whether two play sessions fall on different days depends on the
  timezone you ask in, so it cannot be frozen at write time.
* the windowed play counts — same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from typing import Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.match_naming import TEST_NAME_PREFIX
from app.models.match import Match
from app.models.player import Player
from app.models.turn import Turn, TurnSubmission
from app.models.user import User
from app.models.user_milestone import MilestoneKind, UserMilestone

# Below this many people, a percentage is theatre: one person changes it by five
# points. Show the raw counts instead.
SMALL_SAMPLE_THRESHOLD = 20

# Display order. Not an ordering the data has to obey — see the module docstring.
MILESTONE_ORDER: tuple[tuple[MilestoneKind, str], ...] = (
    (MilestoneKind.SIGNED_UP, "Signed up"),
    (MilestoneKind.PICKED_HANDLE, "Picked a handle"),
    (MilestoneKind.SET_UP_AI_AGENT, "Set up an AI agent"),
    (MilestoneKind.SET_UP_HUMAN_PLAY, "Set up manual play"),
    (MilestoneKind.AI_CONNECTED, "AI connected"),
    (MilestoneKind.JOINED_MATCH, "Joined a match"),
    (MilestoneKind.PLAYED_TURN, "Played a turn"),
)

RETURNED_LABEL = "Came back another day"
STUCK_LIST_CAP = 50


@dataclass(frozen=True)
class MilestoneCount:
    key: str
    label: str
    count: int
    # How many fewer than the row above. Deliberately NOT called "lost here":
    # with several paths through the product those are different claims.
    fewer_than_above: int | None


@dataclass(frozen=True)
class StuckUser:
    user_id: int
    label: str
    furthest: str


@dataclass(frozen=True)
class EngagementReport:
    cohort_size: int
    steps: list[MilestoneCount]
    returned_count: int
    ai_connected_share: float | None
    stuck: list[StuckUser]
    stuck_remainder: int
    small_sample: bool

    @property
    def show_percentages(self) -> bool:
        return not self.small_sample


def _cohort_users(
    signed_up_after: datetime | None, signed_up_before: datetime | None
) -> Select[tuple[int]]:
    """Users who signed up in the window, excluding accounts that are ours.

    The internal filter does nearly all the work. Filtering platform bots by agent
    kind is not enough: in the dev database the house, sim and harness accounts own
    more than 500 of 646 player rows and all but one run agents marked as real AI.
    """
    stmt = select(User.id).where(User.is_internal.is_(False))
    if signed_up_after is not None:
        stmt = stmt.where(User.created_at >= signed_up_after)
    if signed_up_before is not None:
        stmt = stmt.where(User.created_at < signed_up_before)
    return stmt


def _genuine_submissions(
    cohort: Select[tuple[int]],
) -> Select[tuple[int, datetime | None]]:
    """Real moves by cohort users: no missed deadlines, no smoke-test matches.

    ``was_defaulted`` covers both a missed deadline and a connector fallback, and a
    NULL timestamp is treated as defaulted too — matching what /admin/reports
    already does, so the two admin pages cannot disagree about the same week.
    """
    return (
        select(Player.user_id, TurnSubmission.submitted_at)
        .join(Player, Player.id == TurnSubmission.player_id)
        .join(Turn, Turn.id == TurnSubmission.turn_id)
        .join(Match, Match.id == Turn.match_id)
        .where(
            Player.user_id.in_(cohort),
            TurnSubmission.was_defaulted.is_(False),
            TurnSubmission.submitted_at.is_not(None),
            Player.autopilot_at.is_(None),
            ~Match.name.ilike(f"{TEST_NAME_PREFIX}%"),
        )
    )


def _distinct_local_days(moments: Sequence[datetime], zone: tzinfo) -> int:
    """How many different calendar days these fall on, in the reader's timezone.

    Not UTC: a US evening session spans two UTC days and would read as a return
    visit that never happened.
    """
    return len({moment.astimezone(zone).date() for moment in moments if moment})


async def load_engagement_report(
    db: AsyncSession,
    *,
    signed_up_after: datetime | None = None,
    signed_up_before: datetime | None = None,
    zone: tzinfo,
) -> EngagementReport:
    cohort = _cohort_users(signed_up_after, signed_up_before)
    cohort_ids = set((await db.execute(cohort)).scalars().all())
    cohort_size = len(cohort_ids)

    reached_by_milestone: dict[MilestoneKind, set[int]] = {}
    if cohort_ids:
        rows = (
            await db.execute(
                select(UserMilestone.milestone, UserMilestone.user_id).where(
                    UserMilestone.user_id.in_(cohort_ids)
                )
            )
        ).all()
        for milestone, user_id in rows:
            reached_by_milestone.setdefault(milestone, set()).add(user_id)

    steps: list[MilestoneCount] = []
    previous: int | None = None
    for kind, label in MILESTONE_ORDER:
        count = len(reached_by_milestone.get(kind, ()))
        steps.append(
            MilestoneCount(
                key=kind.value,
                label=label,
                count=count,
                fewer_than_above=None if previous is None else max(0, previous - count),
            )
        )
        previous = count

    # Play days, per user, in the reader's timezone.
    days_by_user: dict[int, list[datetime]] = {}
    if cohort_ids:
        for user_id, submitted_at in (
            await db.execute(_genuine_submissions(cohort))
        ).all():
            days_by_user.setdefault(user_id, []).append(submitted_at)
    returned = {
        user_id
        for user_id, moments in days_by_user.items()
        if _distinct_local_days(moments, zone) >= 2
    }

    # Reported against people who chose an AI agent, not against everyone. Manual
    # play is the default for a new user, so measuring it against all signups would
    # make a healthy AI setup rate look like a failure.
    ai_owners = reached_by_milestone.get(MilestoneKind.SET_UP_AI_AGENT, set())
    connected = reached_by_milestone.get(MilestoneKind.AI_CONNECTED, set())
    ai_share = len(connected & ai_owners) / len(ai_owners) if ai_owners else None

    stuck_all = await _load_stuck(db, cohort_ids, reached_by_milestone, returned)
    return EngagementReport(
        cohort_size=cohort_size,
        steps=steps,
        returned_count=len(returned),
        ai_connected_share=ai_share,
        stuck=stuck_all[:STUCK_LIST_CAP],
        stuck_remainder=max(0, len(stuck_all) - STUCK_LIST_CAP),
        small_sample=cohort_size < SMALL_SAMPLE_THRESHOLD,
    )


async def _load_stuck(
    db: AsyncSession,
    cohort_ids: set[int],
    reached_by_milestone: dict[MilestoneKind, set[int]],
    returned: set[int],
) -> list[StuckUser]:
    """Everyone who has not come back, and the furthest rung they reached."""
    if not cohort_ids:
        return []
    users = (
        await db.execute(
            select(User.id, User.handle, User.email)
            .where(User.id.in_(cohort_ids))
            .order_by(User.created_at.desc())
        )
    ).all()

    stuck: list[StuckUser] = []
    for user_id, handle, email in users:
        if user_id in returned:
            continue
        furthest = "Nothing yet"
        for kind, label in MILESTONE_ORDER:
            if user_id in reached_by_milestone.get(kind, ()):
                furthest = label
        stuck.append(
            StuckUser(
                user_id=user_id,
                # Users with no handle are exactly the ones stuck earliest, so
                # falling back to the address is what makes the list usable. This
                # page is admin-only and /admin/users already shows addresses.
                label=handle or email,
                furthest=furthest,
            )
        )
    return stuck


async def count_genuine_turns(
    db: AsyncSession,
    *,
    played_after: datetime | None = None,
    played_before: datetime | None = None,
) -> int:
    """Genuine moves in the window, by anyone who is not an internal account."""
    everyone = select(User.id).where(User.is_internal.is_(False))
    stmt = select(func.count()).select_from(_genuine_submissions(everyone).subquery())
    if played_after is not None or played_before is not None:
        inner = _genuine_submissions(everyone)
        if played_after is not None:
            inner = inner.where(TurnSubmission.submitted_at >= played_after)
        if played_before is not None:
            inner = inner.where(TurnSubmission.submitted_at < played_before)
        stmt = select(func.count()).select_from(inner.subquery())
    return (await db.scalar(stmt)) or 0
