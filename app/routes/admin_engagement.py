"""The engagement page: where new users fall out, and which source produced them.

Admin-only. Reads the durable milestone records plus the surviving play rows;
writes nothing.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.deps import DbSession, require_platform_admin
from app.models.user import User
from app.read_models.engagement_milestones import (
    count_genuine_turns,
    load_engagement_report,
)
from app.read_models.signup_sources import load_signup_sources
from app.routes.admin_date_window import parse_admin_window
from app.templating import templates

router = APIRouter()


@router.get("/admin/engagement", response_class=HTMLResponse)
async def admin_engagement(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_platform_admin)],
    start_date: str | None = None,
    end_date: str | None = None,
    tz: str | None = None,
) -> HTMLResponse:
    window = parse_admin_window(start_date, end_date, tz)
    signed_up_after, signed_up_before = window.start_utc, window.end_utc
    played_after, played_before = signed_up_after, signed_up_before

    report = await load_engagement_report(
        db,
        signed_up_after=signed_up_after,
        signed_up_before=signed_up_before,
        zone=window.zone,
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
            "start_date": window.start_text,
            "end_date": window.end_text,
            "tz": window.tz_name,
            # An unbounded window has no "previous period" to compare against, so
            # the page shows no comparison rather than an invented one.
            "windowed": window.start is not None or window.end is not None,
        },
    )
