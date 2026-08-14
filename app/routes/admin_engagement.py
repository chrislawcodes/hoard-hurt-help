"""The engagement page: where new users fall out, and which source produced them.

Admin-only. Reads the durable milestone records plus the surviving play rows;
writes nothing.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.deps import DbSession, require_platform_admin
from app.models.user import User
from app.read_models.engagement_milestones import (
    count_genuine_turns,
    load_engagement_report,
)
from app.read_models.signup_sources import load_signup_sources
from app.templating import templates

router = APIRouter()


def _parse_timezone(value: str | None) -> ZoneInfo | timezone:
    """The reader's timezone. Everything on this page is measured in it.

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


@router.get("/admin/engagement", response_class=HTMLResponse)
async def admin_engagement(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    start_date: str | None = None,
    end_date: str | None = None,
    tz: str | None = None,
) -> HTMLResponse:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    zone = _parse_timezone(tz)
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date must be on or before end_date.",
        )

    signed_up_after: datetime | None = None
    signed_up_before: datetime | None = None
    if start is not None:
        signed_up_after = datetime.combine(start, time.min, tzinfo=zone).astimezone(
            timezone.utc
        )
    if end is not None:
        # End is inclusive in the UI, so compare strictly before the next local day.
        signed_up_before = datetime.combine(
            end + timedelta(days=1), time.min, tzinfo=zone
        ).astimezone(timezone.utc)

    played_after, played_before = signed_up_after, signed_up_before

    report = await load_engagement_report(
        db,
        signed_up_after=signed_up_after,
        signed_up_before=signed_up_before,
        zone=zone,
    )
    sources = await load_signup_sources(
        db, signed_up_after=signed_up_after, signed_up_before=signed_up_before
    )
    # Counts turns PLAYED in the window, by anyone. The first version passed the
    # signup window here, so the three numbers under one heading described three
    # different groups of people: signups in the window, their play over all time,
    # and everyone else's play filtered by dates that were never about playing.
    turns = await count_genuine_turns(
        db, played_after=played_after, played_before=played_before
    )

    return templates.TemplateResponse(
        request,
        "admin/engagement.html",
        {
            "user": user,
            "is_admin": True,
            "report": report,
            "sources": sources,
            "turns_in_window": turns,
            "start_date": start.isoformat() if start else "",
            "end_date": end.isoformat() if end else "",
            "tz": zone.key if isinstance(zone, ZoneInfo) else "UTC",
            # An unbounded window has no "previous period" to compare against, so
            # the page shows no comparison rather than an invented one.
            "windowed": start is not None or end is not None,
        },
    )
