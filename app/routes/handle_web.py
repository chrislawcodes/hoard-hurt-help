"""Pick or change your public handle.

One shared page reached via the handle gate (`require_user_with_handle`): new
users land here before creating their first agent, existing agent owners land
here at their next visit, and anyone can return here to change their handle. The
form is pre-filled with a suggestion so picking one is near-zero friction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from starlette.responses import Response

from app.deps import DbSession, require_user
from app.identity import handle as handle_mod
from app.identity import word_filter
from app.identity.handle import HandleError
from app.models.user import User
from app.routes.web_support import _is_any_admin, safe_internal_next
from app.templating import templates

router = APIRouter()

_DEFAULT_NEXT = "/me/agents"


def _safe_next(raw: str | None) -> str:
    """Only allow a local path, to avoid an open redirect; else the default."""
    return safe_internal_next(raw) or _DEFAULT_NEXT


async def _taken_keys(db: DbSession) -> set[str]:
    rows = (await db.execute(select(User.handle_key).where(User.handle_key.is_not(None)))).all()
    return {row[0] for row in rows}


def _cooldown_until(user: User) -> datetime | None:
    """When the user may next change their handle, or None if free to change."""
    if user.handle is None or user.handle_changed_at is None:
        return None
    changed = user.handle_changed_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    until = changed + timedelta(days=handle_mod.CHANGE_COOLDOWN_DAYS)
    return until if datetime.now(timezone.utc) < until else None


def _render(
    request: Request,
    user: User,
    *,
    value: str,
    next_url: str,
    changing: bool,
    error: str | None = None,
    cooldown_until: datetime | None = None,
) -> Response:
    return templates.TemplateResponse(
        request,
        "handle.html",
        {
            "user": user,
            "is_admin": _is_any_admin(user),
            "value": value,
            "next_url": next_url,
            "changing": changing,
            "error": error,
            "cooldown_until": cooldown_until,
        },
    )


@router.get("/me/handle", response_class=HTMLResponse)
async def handle_form(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    next: str | None = None,
) -> Response:
    next_url = _safe_next(next)
    if user.handle is not None:
        return _render(
            request,
            user,
            value=user.handle,
            next_url=next_url,
            changing=True,
            cooldown_until=_cooldown_until(user),
        )
    suggestion = handle_mod.suggest(
        given_name=user.given_name,
        email=user.email,
        taken=(await _taken_keys(db)).__contains__,
    )
    return _render(request, user, value=suggestion, next_url=next_url, changing=False)


@router.post("/me/handle")
async def handle_submit(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    handle: Annotated[str, Form()],
    next: Annotated[str | None, Form()] = None,
) -> Response:
    next_url = _safe_next(next)
    changing = user.handle is not None

    # Never echo a blocked/reserved value back into the field.
    safe_echo = "" if (word_filter.contains_blocked(handle) or word_filter.is_reserved(handle)) else handle

    try:
        display = handle_mod.validate(handle)
    except HandleError as exc:
        return _render(
            request,
            user, value=safe_echo, next_url=next_url, changing=changing, error=str(exc)
        )

    key = handle_mod.key_for(display)

    # No-op: re-submitting the same handle just continues, no cooldown burned.
    if user.handle_key == key:
        return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)

    cooldown_until = _cooldown_until(user)
    if changing and cooldown_until is not None:
        return _render(
            request,
            user,
            value=user.handle or display,
            next_url=next_url,
            changing=True,
            error=(
                "You changed your handle recently. You can change it again on "
                f"{cooldown_until:%b %d, %Y}."
            ),
            cooldown_until=cooldown_until,
        )

    taken = (
        await db.execute(
            select(User.id).where(User.handle_key == key, User.id != user.id)
        )
    ).first()
    if taken is not None:
        return _render(
            request,
            user,
            value=display,
            next_url=next_url,
            changing=changing,
            error="That handle is taken. Try another.",
        )

    user.handle = display
    user.handle_key = key
    user.handle_changed_at = datetime.now(timezone.utc)
    await db.commit()
    return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
