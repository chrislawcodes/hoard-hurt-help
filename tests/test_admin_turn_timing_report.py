"""Admin turn-timing report tests.

Split from test_admin.py — the /admin/reports page: bucketed turn-timing
stats, the completion-date filter, and that its filter form keeps its own
copy separate from /admin/engagement's (same shared partial, different
{% set %} labels).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import GameState, Match, Player, Turn, TurnSubmission, User
from app.read_models.admin_reports import load_turn_timing_report
from tests.admin_support import reset_db
from tests.conftest import signed_in_cookies as _cookies
from tests.factories import make_agent, seed_email_user_with_role

# `reset_db` is only ever referenced as a fixture-name parameter below (that's
# how pytest wires a fixture to a test), never called by its imported name at
# module level, so ruff sees the import as unused unless it's listed here --
# the same pattern tests/conftest.py already uses for its own re-exports.
__all__ = ["reset_db"]


async def _seed_turn_timing_match(
    reset_db,
    *,
    match_id: str = "M_turn_report",
    name: str = "Report Match",
    completed_at: datetime | None = None,
) -> str:
    async with reset_db() as db:
        completed_at = completed_at or datetime.now(timezone.utc)
        seed_tag = match_id.lower().replace(" ", "-")
        owner1 = User(
            google_sub=f"sub-{seed_tag}-1",
            email=f"turn1+{seed_tag}@test.com",
            name=f"turn1+{seed_tag}@test.com",
        )
        owner2 = User(
            google_sub=f"sub-{seed_tag}-2",
            email=f"turn2+{seed_tag}@test.com",
            name=f"turn2+{seed_tag}@test.com",
        )
        owner3 = User(
            google_sub=f"sub-{seed_tag}-3",
            email=f"turn3+{seed_tag}@test.com",
            name=f"turn3+{seed_tag}@test.com",
        )
        db.add_all([owner1, owner2, owner3])
        await db.flush()

        agent1, version1 = await make_agent(db, owner1, name="AI_1")
        agent2, version2 = await make_agent(db, owner2, name="AI_2")
        agent3, version3 = await make_agent(db, owner3, name="AI_3")

        match = Match(
            id=match_id,
            name=name,
            game="hoard-hurt-help",
            state=GameState.COMPLETED,
            scheduled_start=completed_at - timedelta(hours=1),
            started_at=completed_at - timedelta(minutes=10),
            completed_at=completed_at,
        )
        db.add(match)
        await db.flush()

        player1 = Player(
            match_id=match.id,
            user_id=owner1.id,
            agent_id=agent1.id,
            seat_name="AI_1",
            agent_version_id=version1.id if version1 is not None else None,
            model_self_report=version1.model if version1 is not None else None,
        )
        player2 = Player(
            match_id=match.id,
            user_id=owner2.id,
            agent_id=agent2.id,
            seat_name="AI_2",
            agent_version_id=version2.id if version2 is not None else None,
            model_self_report=version2.model if version2 is not None else None,
        )
        player3 = Player(
            match_id=match.id,
            user_id=owner3.id,
            agent_id=agent3.id,
            seat_name="AI_3",
            agent_version_id=version3.id if version3 is not None else None,
            model_self_report=version3.model if version3 is not None else None,
        )
        db.add_all([player1, player2, player3])
        await db.flush()

        turn1_opened = completed_at - timedelta(seconds=90)
        turn2_opened = completed_at - timedelta(seconds=40)
        turn1 = Turn(
            match_id=match.id,
            round=1,
            turn=1,
            turn_token=f"{seed_tag}-tk-1",
            opened_at=turn1_opened,
            deadline_at=turn1_opened + timedelta(seconds=30),
            resolved_at=turn1_opened + timedelta(seconds=31),
        )
        turn2 = Turn(
            match_id=match.id,
            round=1,
            turn=2,
            turn_token=f"{seed_tag}-tk-2",
            opened_at=turn2_opened,
            deadline_at=turn2_opened + timedelta(seconds=30),
            resolved_at=turn2_opened + timedelta(seconds=31),
        )
        db.add_all([turn1, turn2])
        await db.flush()

        db.add_all(
            [
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player1.id,
                    action="HOARD",
                    points_delta=2,
                    round_score_after=2,
                    submitted_at=turn1_opened + timedelta(seconds=9),
                ),
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player2.id,
                    action="HELP",
                    points_delta=1,
                    round_score_after=1,
                    submitted_at=turn1_opened + timedelta(seconds=25),
                ),
                TurnSubmission(
                    turn_id=turn1.id,
                    player_id=player3.id,
                    action="HURT",
                    points_delta=-1,
                    round_score_after=-1,
                    submitted_at=turn1_opened + timedelta(seconds=40),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player1.id,
                    action="HOARD",
                    points_delta=2,
                    round_score_after=4,
                    submitted_at=turn2_opened + timedelta(seconds=15),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player2.id,
                    action="HELP",
                    points_delta=1,
                    round_score_after=2,
                    submitted_at=turn2_opened + timedelta(seconds=35),
                ),
                TurnSubmission(
                    turn_id=turn2.id,
                    player_id=player3.id,
                    action="HOARD",
                    points_delta=0,
                    round_score_after=0,
                    was_defaulted=True,
                    submitted_at=None,
                ),
            ]
        )
        await db.commit()
        return match.id


async def test_turn_timing_report_counts_and_buckets(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    await _seed_turn_timing_match(reset_db)
    async with reset_db() as db:
        report = await load_turn_timing_report(db)
    assert report.matches_scanned == 1
    assert report.matches_with_samples == 1
    assert report.turn_count == 2
    assert report.sample_count == 5
    assert report.defaulted_count == 1
    assert report.mean_seconds == pytest.approx(24.8)
    bucket_counts = {bucket.label: bucket.count for bucket in report.buckets}
    assert bucket_counts["0-10s"] == 1
    assert bucket_counts["10-20s"] == 1
    assert bucket_counts["20-30s"] == 1
    assert bucket_counts["30-45s"] == 2
    assert bucket_counts["45-60s"] == 0
    assert bucket_counts["60-90s"] == 0

    r = await client.get("/admin/reports", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Reporting" in r.text
    assert "Report Match" in r.text
    assert "0-10s" in r.text
    assert 'name="start_date"' in r.text
    assert 'name="end_date"' in r.text
    assert "Matches scanned" not in r.text
    assert "Turns scanned" not in r.text
    assert "Timed submissions" not in r.text
    assert "Defaulted rows" not in r.text


async def test_turn_timing_report_date_filter_limits_matches(client, reset_db):
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    included_at = datetime(2026, 6, 12, 6, 30, tzinfo=timezone.utc)
    excluded_at = datetime(2026, 6, 12, 8, 30, tzinfo=timezone.utc)
    await _seed_turn_timing_match(
        reset_db,
        match_id="M_turn_report_in_range",
        name="In Range",
        completed_at=included_at,
    )
    await _seed_turn_timing_match(
        reset_db,
        match_id="M_turn_report_out_of_range",
        name="Out of Range",
        completed_at=excluded_at,
    )

    async with reset_db() as db:
        report = await load_turn_timing_report(
            db,
            completed_after=datetime(2026, 6, 11, 7, tzinfo=timezone.utc),
            completed_before=datetime(2026, 6, 12, 7, tzinfo=timezone.utc),
        )
    assert report.matches_scanned == 1
    assert report.sample_count == 5
    assert [row.name for row in report.matches] == ["In Range"]

    r = await client.get(
        "/admin/reports?start_date=2026-06-11&end_date=2026-06-11&tz=America/Los_Angeles",
        cookies=_cookies(admin.id),
    )
    assert r.status_code == 200
    assert "In Range" in r.text
    assert "Out of Range" not in r.text
    assert 'value="2026-06-11"' in r.text
    assert 'name="tz"' in r.text
    assert 'value="America/Los_Angeles"' in r.text


async def test_reports_filter_form_shows_its_own_labels_not_engagements(
    client, reset_db
):
    """The filter form now renders through a shared partial
    (admin/_date_window_form.html) with this page's own labels/hint/clear-href
    set by {% set %} in reports.html, right before the {% include %}. Pins
    that /admin/reports keeps its own text after the dedup, and that
    /admin/engagement's copy (set the same way, one {% set %} block over)
    can't leak onto this page."""
    admin = await seed_email_user_with_role(reset_db, "admin@test.com")
    r = await client.get("/admin/reports", cookies=_cookies(admin.id))
    assert r.status_code == 200
    assert "Start date" in r.text
    assert "End date" in r.text
    assert "Filters by match completion date in your browser timezone." in r.text
    # No filter set yet, so the partial's conditional Clear link is hidden.
    assert 'href="/admin/reports">Clear</a>' not in r.text
    assert "Signed up from" not in r.text
    assert "Picks the signup cohort" not in r.text

    r2 = await client.get(
        "/admin/reports?start_date=2026-06-11", cookies=_cookies(admin.id)
    )
    assert r2.status_code == 200
    assert 'href="/admin/reports">Clear</a>' in r2.text


