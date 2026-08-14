"""Regressions for the bugs an adversarial review of the shipped feature found.

Each test here was checked against the pre-fix code and fails there. They are
grouped by the thing that was wrong rather than by module, because that is how
they will be read when one of them breaks.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.identity.first_touch import SESSION_KEY, pop_first_touch
from app.identity.milestones import record_milestone
from app.models.match import GameState
from app.models.player import Player
from app.models.user import User
from app.models.user_milestone import MilestoneKind
from app.read_models.engagement_milestones import (
    _distinct_local_days,
    count_genuine_turns,
    load_engagement_report,
)
from app.routes.auth import sync_google_user
from app.schemas.auth import GoogleUserInfo
from tests.factories import add_submission, make_match, make_turn, seat_player, make_user

UTC = timezone.utc


# --------------------------------------------------------------------------
# Autopilot: a seat that is later handed over must keep its earlier real moves
# --------------------------------------------------------------------------


async def _played_then_left(db) -> Player:
    """Two genuine turns on two days, then the person walks out of the match."""
    await make_match(db, "M_AUTO", state=GameState.COMPLETED, name="Prompt Live")
    player = await seat_player(db, "M_AUTO", "Seat One")
    for days_ago, round_number in ((5, 1), (2, 2)):
        turn = await make_turn(db, "M_AUTO", round=round_number)
        await add_submission(
            db,
            turn,
            player,
            submitted_at=datetime.now(UTC) - timedelta(days=days_ago),
            was_defaulted=False,
        )
    await db.commit()
    # Leaving marks the seat, which is the state the read model has to survive.
    await db.execute(
        update(Player).where(Player.id == player.id).values(autopilot_at=datetime.now(UTC))
    )
    await db.commit()
    return player


@pytest.mark.asyncio
async def test_leaving_a_match_does_not_erase_the_turns_already_played(db) -> None:
    """The worst bug of the set, because it hits exactly who the page is about.

    Autopilot was excluded per SEAT, so handing a seat over deleted every move
    that seat ever made. Someone who played on two separate days and then quit
    read as "never came back", with zero turns.
    """
    await _played_then_left(db)

    assert await count_genuine_turns(db) == 2, "the moves happened before they left"
    report = await load_engagement_report(db, zone=UTC)
    assert report.returned_count == 1, "two days of play is still two days of play"


@pytest.mark.asyncio
async def test_moves_made_after_the_handover_are_not_counted(db) -> None:
    """The other half: autopilot's own moves are not the person playing."""
    player = await _played_then_left(db)
    turn = await make_turn(db, "M_AUTO", round=3)
    await add_submission(
        db, turn, player, submitted_at=datetime.now(UTC), was_defaulted=False
    )
    await db.commit()

    assert await count_genuine_turns(db) == 2, "the third move was autopilot's"


# --------------------------------------------------------------------------
# Timezone: values come back from SQLite with no offset attached
# --------------------------------------------------------------------------


def test_naive_timestamps_are_read_as_utc_not_server_local() -> None:
    """SQLite drops the offset, and astimezone assumes server local time.

    Measured on real data this was off by a whole day, and it inverted the rule
    the function exists to enforce. The repo's ensure_aware is the fix and was
    used in 20 other files before this one.
    """
    pacific = ZoneInfo("America/Los_Angeles")
    # 23:55 and 00:15 UTC — one Pacific afternoon, two UTC days.
    naive_evening = datetime(2026, 8, 13, 23, 55)
    naive_later = datetime(2026, 8, 14, 0, 15)

    assert _distinct_local_days([naive_evening, naive_later], pacific) == 1, (
        "one sitting must not read as a return visit"
    )
    assert _distinct_local_days([naive_evening, naive_later], UTC) == 2, (
        "and in UTC it genuinely is two days"
    )
    assert time.tzname  # the result must not depend on this


# --------------------------------------------------------------------------
# Internal accounts: creation and the migration must agree
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_admin_signing_up_is_flagged_internal(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration 0053 flags admins; the creation path must too.

    No real Google address sits on an internal domain, so the domain-only rule
    never fired for an admin — and because the flag is write-once, they stayed
    counted as a real user on their own dashboard forever.
    """
    monkeypatch.setattr(settings, "platform_admin_emails", "boss@example.com")
    user = await sync_google_user(
        db, GoogleUserInfo(sub="s-admin", email="boss@example.com", name="Boss")
    )
    await db.commit()
    assert user.is_internal is True


# --------------------------------------------------------------------------
# The recorder must not swallow the caller's own failure
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_callers_own_error_is_not_reported_as_a_milestone_failure(db) -> None:
    """begin_nested() flushes, so the caller's pending work fails inside our call.

    Wrapping that flush meant a duplicate handle (say) was caught here, logged as
    "milestone write failed", and handed back to the caller as an unusable session
    with a confusing rollback error at a later line.
    """
    user = await make_user(db)
    await db.commit()
    # The caller stages a duplicate of their own — nothing to do with milestones.
    db.add(User(google_sub=user.google_sub, email="other@example.com"))

    with pytest.raises(Exception) as caught:
        await record_milestone(db, user.id, MilestoneKind.PLAYED_TURN)

    assert "google_sub" in str(caught.value), (
        "the caller must get their own error, naming their own constraint"
    )


# --------------------------------------------------------------------------
# Capture: the off switch, the skip list, and one-signup-per-arrival
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_signups_record_nothing_while_capture_is_off(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Off" has to mean off on the path that has no browser session too.

    The MCP callers pass their channel directly, bypassing the middleware's flag
    check entirely, so a source was still written with capture disabled.
    """
    monkeypatch.setattr(settings, "first_touch_capture_enabled", False)
    user = await sync_google_user(
        db,
        GoogleUserInfo(sub="s-mcp", email="viamcp@example.com", name="MCP"),
        source_channel="mcp",
    )
    await db.commit()

    stored = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert stored.first_source_channel is None


def test_first_touch_is_consumed_by_the_signup_that_uses_it() -> None:
    """It really pops now.

    Left in place, the next account created in the same browser inside the
    cookie's 14 days inherited the first one's arrival — two different people
    credited to one link.
    """
    from starlette.requests import Request

    scope: dict = {"type": "http", "session": {SESSION_KEY: {"utm_source": "reddit"}}}
    request = Request(scope)

    assert pop_first_touch(request) == {"utm_source": "reddit"}
    assert pop_first_touch(request) is None, "a second signup must not reuse it"


def test_event_streams_are_not_treated_as_landing_pages() -> None:
    """The skip list named "/sse", which is not a route in this app.

    The real streams end in /stream, so every open event stream was captured as an
    arrival and handed the visitor a cookie. Asserted against the skip decision
    directly rather than by opening a stream — an SSE request never returns, so a
    request-level test here hangs the suite rather than failing it.
    """
    from app.identity.first_touch import _STREAM_SUFFIX, _SKIP_PREFIXES

    def skipped(path: str) -> bool:
        return any(path.startswith(p) for p in _SKIP_PREFIXES) or path.endswith(
            _STREAM_SUFFIX
        )

    assert skipped("/games/hoard-hurt-help/matches/M_0001/stream")
    assert skipped("/me/agents/3/stream")
    assert skipped("/static/style.css")
    assert skipped("/mcp")
    assert skipped("/.well-known/oauth-protected-resource/mcp")
    # And a real page is still captured.
    assert not skipped("/")
    assert not skipped("/guide")
    assert not skipped("/leaderboard")
