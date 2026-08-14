"""The date/timezone window that /admin/reports and /admin/engagement share.

The two pages carried separate copies of this parsing for a while, and the
copies drifted: one learned that a path-shaped ``tz`` is a 400, the other kept
returning a 500. The route tests at the bottom are the anti-drift guard.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.models.user import User, UserRole
from app.routes.admin_date_window import parse_admin_window
from tests.conftest import signed_in_cookies
from tests.factories import make_user

# Both pages read the same three query params through the same helper.
ADMIN_WINDOW_PAGES = ["/admin/reports", "/admin/engagement"]

LA = "America/Los_Angeles"


def test_a_full_window_becomes_utc_bounds() -> None:
    window = parse_admin_window("2026-06-11", "2026-06-12", LA)

    assert window.start == date(2026, 6, 11)
    assert window.end == date(2026, 6, 12)
    assert window.zone == ZoneInfo(LA)
    # June is PDT, UTC-7: local midnight is 07:00 the same UTC day.
    assert window.start_utc == datetime(2026, 6, 11, 7, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 6, 13, 7, tzinfo=timezone.utc)


def test_the_end_date_is_inclusive() -> None:
    """A one-day window must cover that whole day, not zero seconds of it."""
    window = parse_admin_window("2026-06-11", "2026-06-11", "UTC")

    assert window.start_utc == datetime(2026, 6, 11, 0, 0, tzinfo=timezone.utc)
    assert window.end_utc == datetime(2026, 6, 12, 0, 0, tzinfo=timezone.utc)


def test_a_missing_window_is_unbounded_utc() -> None:
    window = parse_admin_window(None, None, None)

    assert (window.start, window.end) == (None, None)
    assert (window.start_utc, window.end_utc) == (None, None)
    assert window.zone == timezone.utc
    assert window.tz_name == "UTC"
    assert window.start_text == ""
    assert window.end_text == ""


def test_blank_strings_are_treated_as_missing() -> None:
    """An untouched date input submits "", not nothing."""
    window = parse_admin_window("", "  ", "")

    assert (window.start_utc, window.end_utc) == (None, None)
    assert window.zone == timezone.utc


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-06-11", None), (None, "2026-06-11")],
)
def test_one_sided_windows_bound_only_that_side(start: str | None, end: str | None) -> None:
    window = parse_admin_window(start, end, "UTC")

    assert (window.start_utc is None) == (start is None)
    assert (window.end_utc is None) == (end is None)


def test_the_form_values_are_echoed_back_as_text() -> None:
    window = parse_admin_window("2026-06-11", "2026-06-12", LA)

    assert window.start_text == "2026-06-11"
    assert window.end_text == "2026-06-12"
    assert window.tz_name == LA


@pytest.mark.parametrize("field", ["start_date", "end_date"])
def test_a_malformed_date_is_a_400_naming_the_field(field: str) -> None:
    params = {"start_date": "2026-06-11", "end_date": "2026-06-12", "tz": "UTC"}
    params[field] = "not-a-date"

    with pytest.raises(HTTPException) as caught:
        parse_admin_window(**params)

    assert caught.value.status_code == 400
    assert caught.value.detail == f"{field} must use YYYY-MM-DD."


def test_an_unknown_timezone_is_a_400() -> None:
    with pytest.raises(HTTPException) as caught:
        parse_admin_window(None, None, "Mars/Olympus_Mons")

    assert caught.value.status_code == 400
    assert caught.value.detail == "tz must be a valid IANA timezone name."


@pytest.mark.parametrize("bad_tz", ["../x", "/etc/UTC", "a/../../b"])
def test_a_path_shaped_timezone_is_a_400_not_a_crash(bad_tz: str) -> None:
    """ZoneInfo raises a plain ValueError for these, not ZoneInfoNotFoundError.

    Catching only the not-found error let them escape as a 500 plus an incident
    row, which is how a bad bookmark looked like a server fault.
    """
    with pytest.raises(HTTPException) as caught:
        parse_admin_window(None, None, bad_tz)

    assert caught.value.status_code == 400
    assert caught.value.detail == "tz must be a valid IANA timezone name."


def test_a_backwards_window_is_a_400() -> None:
    with pytest.raises(HTTPException) as caught:
        parse_admin_window("2026-06-12", "2026-06-11", "UTC")

    assert caught.value.status_code == 400
    assert caught.value.detail == "start_date must be on or before end_date."


def test_the_date_complaint_comes_before_the_timezone_complaint() -> None:
    """Two things wrong reports the dates first, so the message is stable."""
    with pytest.raises(HTTPException) as caught:
        parse_admin_window("not-a-date", None, "Mars/Olympus_Mons")

    assert caught.value.detail == "start_date must use YYYY-MM-DD."


async def _admin(session_factory) -> User:
    async with session_factory() as db:
        user = await make_user(db, 0, handle="windowadmin")
        user.role = UserRole.ADMIN
        await db.commit()
        return user


@pytest.mark.parametrize("path", ADMIN_WINDOW_PAGES)
async def test_both_pages_reject_a_path_shaped_timezone(reset_db, client, path: str) -> None:
    admin = await _admin(reset_db)

    response = await client.get(
        f"{path}?tz=../x", cookies=signed_in_cookies(admin.id)
    )

    assert response.status_code == 400


@pytest.mark.parametrize("path", ADMIN_WINDOW_PAGES)
async def test_both_pages_reject_a_backwards_window(reset_db, client, path: str) -> None:
    admin = await _admin(reset_db)

    response = await client.get(
        f"{path}?start_date=2026-06-12&end_date=2026-06-11",
        cookies=signed_in_cookies(admin.id),
    )

    assert response.status_code == 400


@pytest.mark.parametrize("path", ADMIN_WINDOW_PAGES)
async def test_both_pages_echo_the_window_back(reset_db, client, path: str) -> None:
    admin = await _admin(reset_db)

    response = await client.get(
        f"{path}?start_date=2026-06-11&end_date=2026-06-12&tz={LA}",
        cookies=signed_in_cookies(admin.id),
    )

    assert response.status_code == 200
    assert 'value="2026-06-11"' in response.text
    assert 'value="2026-06-12"' in response.text
    assert f'value="{LA}"' in response.text
