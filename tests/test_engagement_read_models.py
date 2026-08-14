"""The numbers behind /admin/engagement.

The first test is the one the whole redesign exists for: counts must be
independent, so a path that skips a rung is not reported as a drop.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.identity.milestones import record_milestone
from app.models.user_milestone import MilestoneKind
from app.read_models.engagement_milestones import (
    SMALL_SAMPLE_THRESHOLD,
    _distinct_local_days,
    load_engagement_report,
)
from app.read_models.signup_sources import (
    UNKNOWN_LABEL,
    derive_label,
    load_signup_sources,
)
from tests.factories import make_user

UTC = timezone.utc


def _steps(report) -> dict[str, int]:
    return {step.key: step.count for step in report.steps}


@pytest.mark.asyncio
async def test_counts_are_independent_not_a_waterfall(db) -> None:
    """A user who skipped a rung still counts at the ones they reached.

    This is the redesign in one test. Under a strict funnel, an MCP user who
    connects before building an agent — or a human player who never connects at
    all — is truncated out of every later step and reads as having quit.
    """
    user = await make_user(db)
    # Deliberately NOT picked_handle, and NOT set_up_ai_agent.
    await record_milestone(db, user.id, MilestoneKind.AI_CONNECTED)
    await record_milestone(db, user.id, MilestoneKind.PLAYED_TURN)
    await db.commit()

    counts = _steps(await load_engagement_report(db, zone=UTC))
    assert counts["played_turn"] == 1, "a missing earlier rung must not suppress this"
    assert counts["ai_connected"] == 1
    assert counts["picked_handle"] == 0


@pytest.mark.asyncio
async def test_internal_accounts_are_excluded_everywhere(db) -> None:
    real = await make_user(db, 1)
    internal = await make_user(db, 2)
    internal.is_internal = True
    await db.commit()
    await record_milestone(db, real.id, MilestoneKind.PLAYED_TURN)
    await record_milestone(db, internal.id, MilestoneKind.PLAYED_TURN)
    await db.commit()

    report = await load_engagement_report(db, zone=UTC)
    assert report.cohort_size == 1
    assert _steps(report)["played_turn"] == 1


@pytest.mark.asyncio
async def test_ai_connected_share_is_suppressed_on_a_tiny_denominator(db) -> None:
    """One AI owner is not a percentage, whatever the cohort size.

    The suppression used to key on cohort size, but this share's denominator is
    AI owners — usually far smaller. That printed "100.0% got it connected" one
    paragraph above the note saying percentages were hidden.
    """
    ai_user = await make_user(db, 1)
    human_user = await make_user(db, 2)
    await db.commit()
    await record_milestone(db, ai_user.id, MilestoneKind.SET_UP_AI_AGENT)
    await record_milestone(db, ai_user.id, MilestoneKind.AI_CONNECTED)
    await record_milestone(db, human_user.id, MilestoneKind.SET_UP_HUMAN_PLAY)
    await db.commit()

    assert (await load_engagement_report(db, zone=UTC)).ai_connected_share is None


@pytest.mark.asyncio
async def test_ai_connected_share_counts_only_ai_owners(db) -> None:
    """Measured against people who chose an AI, not against everyone.

    Manual play is the default for a new user, so dividing by all signups would
    make a perfect setup rate look like a failure.
    """
    for i in range(SMALL_SAMPLE_THRESHOLD):
        user = await make_user(db, i)
        await db.commit()
        await record_milestone(db, user.id, MilestoneKind.SET_UP_AI_AGENT)
        if i % 2 == 0:
            await record_milestone(db, user.id, MilestoneKind.AI_CONNECTED)
    # Manual players must not dilute the denominator.
    for i in range(SMALL_SAMPLE_THRESHOLD, SMALL_SAMPLE_THRESHOLD + 10):
        user = await make_user(db, i)
        await db.commit()
        await record_milestone(db, user.id, MilestoneKind.SET_UP_HUMAN_PLAY)
    await db.commit()

    report = await load_engagement_report(db, zone=UTC)
    assert report.ai_connected_share == 0.5


@pytest.mark.asyncio
async def test_small_cohorts_suppress_percentages(db) -> None:
    await make_user(db)
    await db.commit()
    report = await load_engagement_report(db, zone=UTC)
    assert report.small_sample is True
    assert report.show_percentages is False
    assert report.cohort_size < SMALL_SAMPLE_THRESHOLD


@pytest.mark.asyncio
async def test_empty_database_renders_without_dividing_by_zero(db) -> None:
    report = await load_engagement_report(db, zone=UTC)
    assert report.cohort_size == 0
    assert report.ai_connected_share is None
    assert report.stuck == []
    assert all(step.count == 0 for step in report.steps)


@pytest.mark.asyncio
async def test_stuck_list_names_users_without_a_handle(db) -> None:
    user = await make_user(db)
    user.handle = None
    user.handle_key = None
    await db.commit()

    report = await load_engagement_report(db, zone=UTC)
    assert len(report.stuck) == 1
    assert report.stuck[0].label == user.email
    assert report.stuck[0].furthest == "Signed up"


@pytest.mark.asyncio
async def test_stuck_list_is_capped_with_a_remainder(db) -> None:
    for i in range(55):
        await make_user(db, i)
    await db.commit()

    report = await load_engagement_report(db, zone=UTC)
    assert len(report.stuck) == 50
    assert report.stuck_remainder == 5


def test_a_session_across_utc_midnight_is_not_a_return_visit() -> None:
    """The exact false positive the timezone rule exists to prevent.

    A US evening session spans two UTC days. Counted in UTC it looks like someone
    came back the next day; counted where they actually are, it is one sitting.
    """
    pacific = ZoneInfo("America/Los_Angeles")
    evening = datetime(2026, 8, 13, 23, 55, tzinfo=UTC)  # 16:55 Pacific
    later = evening + timedelta(minutes=20)  # 00:15 UTC, still 17:15 Pacific

    assert _distinct_local_days([evening, later], UTC) == 2
    assert _distinct_local_days([evening, later], pacific) == 1


def test_source_label_keeps_direct_and_unknown_apart() -> None:
    """The distinction that stops the biggest row meaning "we failed to record"."""
    assert derive_label("hermesagent", None, None) == "hermesagent"
    assert derive_label(None, "reddit.com", None) == "reddit.com"
    assert derive_label(None, None, "direct") == "direct"
    assert derive_label(None, None, "mcp") == "mcp"
    assert derive_label(None, None, None) == UNKNOWN_LABEL


@pytest.mark.asyncio
async def test_sources_count_people_not_rows(db) -> None:
    """One busy user is one signup, however many agents or matches they own."""
    user = await make_user(db)
    user.first_utm_source = "hermesagent"
    await db.commit()
    await record_milestone(db, user.id, MilestoneKind.PLAYED_TURN)
    await db.commit()

    rows = await load_signup_sources(db)
    assert len(rows) == 1
    assert rows[0].label == "hermesagent"
    assert rows[0].signups == 1
    assert rows[0].played == 1
    assert rows[0].played_share == 1.0


@pytest.mark.asyncio
async def test_uncaptured_signups_render_as_unknown(db) -> None:
    captured = await make_user(db, 1)
    captured.first_source_channel = "direct"
    await make_user(db, 2)  # nothing captured at all
    await db.commit()

    labels = {row.label: row.signups for row in await load_signup_sources(db)}
    assert labels == {"direct": 1, UNKNOWN_LABEL: 1}
