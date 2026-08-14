"""The date-and-timezone window shared by the admin reporting pages.

``/admin/reports`` and ``/admin/engagement`` both take ``start_date``,
``end_date`` and ``tz``, and both turn them into the same pair of UTC bounds.
They used to carry their own character-for-character copy of this code, which is
how the timezone fix below landed on one page and not the other.

This does not live in ``app/aware_datetime.py`` — that module is pure stdlib and
a dozen ``app/engine/`` modules import it, so putting an ``HTTPException``-raising
request parser there would drag the HTTP layer into the game engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status


@dataclass(frozen=True)
class AdminWindow:
    """A reader-local date range plus the UTC bounds to query the DB with.

    ``start_utc``/``end_utc`` are a half-open pair: ``start_utc <= t < end_utc``.
    """

    start: date | None
    end: date | None
    zone: ZoneInfo | timezone
    start_utc: datetime | None
    end_utc: datetime | None

    @property
    def start_text(self) -> str:
        """What the page echoes back into its date input; blank when unset."""
        return self.start.isoformat() if self.start else ""

    @property
    def end_text(self) -> str:
        return self.end.isoformat() if self.end else ""

    @property
    def tz_name(self) -> str:
        return self.zone.key if isinstance(self.zone, ZoneInfo) else "UTC"


def _parse_timezone(value: str | None) -> ZoneInfo | timezone:
    """The reader's timezone. Everything on these pages is measured in it.

    Notably "came back another day": a US evening session spans two UTC days and
    would otherwise read as a return visit that never happened.
    """
    if value is None or value.strip() == "":
        return timezone.utc
    try:
        return ZoneInfo(value.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        # ZoneInfo raises a plain ValueError for an absolute or ..-containing
        # key, so catching only the not-found error turned `?tz=../x` into a
        # 500 and an incident row rather than the 400 it is.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="tz must be a valid IANA timezone name.",
        ) from exc


def _parse_date(value: str | None, field_name: str) -> date | None:
    if value is None or value.strip() == "":
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must use YYYY-MM-DD.",
        ) from exc


def parse_admin_window(
    start_date: str | None,
    end_date: str | None,
    tz: str | None,
) -> AdminWindow:
    """Read the three query params, or raise a 400 naming the bad one.

    Dates are checked before the timezone, so a request with two things wrong is
    told about its dates first.
    """
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    zone = _parse_timezone(tz)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date.",
        )

    start_utc: datetime | None = None
    end_utc: datetime | None = None
    if start is not None:
        start_utc = datetime.combine(start, time.min, tzinfo=zone).astimezone(
            timezone.utc
        )
    if end is not None:
        # End is inclusive in the UI, so compare strictly before the next local day.
        end_utc = datetime.combine(
            end + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc)

    return AdminWindow(
        start=start,
        end=end,
        zone=zone,
        start_utc=start_utc,
        end_utc=end_utc,
    )
