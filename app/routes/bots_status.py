"""Live status fragments and streams for bot setup."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from starlette.responses import Response

from app.broadcast import subscribe
from app.deps import DbSession, require_user
from app.engine.bot_activity import (
    bot_channel,
    compute_bot_health,
    compute_onboarding_status,
)
from app.models.user import User
from app.routes.bots_web_support import get_owned_bot
from app.templating import templates

router = APIRouter()


@router.get("/{bot_id}/status", response_class=HTMLResponse)
async def bot_status_fragment(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Response:
    """Owner-scoped onboarding panel refreshed by HTMX on SSE events."""
    bot = await get_owned_bot(db, user, bot_id)
    return templates.TemplateResponse(
        request,
        "bots/_status.html",
        {"bot": bot, "onboarding": await compute_onboarding_status(db, bot)},
    )


@router.get("/{bot_id}/health-badge", response_class=HTMLResponse)
async def bot_health_badge_fragment(
    bot_id: Annotated[int, Path()],
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> Response:
    """Owner-scoped health badge fragment polled by HTMX."""
    bot = await get_owned_bot(db, user, bot_id)
    return templates.TemplateResponse(
        request,
        "bots/_health_badge.html",
        {"health": await compute_bot_health(db, bot)},
    )


@router.get("/{bot_id}/stream")
async def bot_stream(
    bot_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
) -> StreamingResponse:
    """Per-bot SSE stream of onboarding events (`connected`, `moved`)."""
    await get_owned_bot(db, user, bot_id)

    async def event_gen() -> AsyncIterator[str]:
        async for msg in subscribe(bot_channel(bot_id)):
            yield msg

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
