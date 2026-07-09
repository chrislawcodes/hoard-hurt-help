"""Dev-only login — a real session without Google sign-in, so local dev and
automated UI checks (the preview browser, curl) can view login-gated pages.

This is a sign-in bypass, so it is locked out of production two ways: it is OFF
by default (``DEV_LOGIN_ENABLED`` unset), and even when on it is ignored unless
the app is serving non-secure cookies. Production always runs with
``COOKIE_SECURE=true`` behind HTTPS, so a stray ``DEV_LOGIN_ENABLED`` there still
cannot expose it — both conditions must hold. ``app.main.create_app`` only mounts
this router when :func:`dev_login_available` returns true.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.auth.session import set_session_user
from app.config import settings
from app.deps import DbSession
from app.models.user import User, UserRole

router = APIRouter()

# The seeded local user this logs in as when no ``user_id`` is given.
_DEV_USER_EMAIL = "dev@localhost"
_DEV_USER_GOOGLE_SUB = "dev-login-local"
_DEV_USER_HANDLE = "dev"


def dev_login_available() -> bool:
    """True only for local dev: explicitly enabled AND not serving secure cookies.

    ``cookie_secure`` is the production signal (``COOKIE_SECURE=true`` behind
    HTTPS), so requiring it to be false keeps this off in prod even if the flag
    is set there by mistake.
    """
    return settings.dev_login_enabled and not settings.cookie_secure


async def _ensure_dev_user(db: AsyncSession) -> User:
    """Return the seeded local dev user, creating it (with a handle) if missing."""
    user = (
        await db.execute(select(User).where(User.email == _DEV_USER_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=_DEV_USER_GOOGLE_SUB,
            email=_DEV_USER_EMAIL,
            name="Dev User",
            handle=_DEV_USER_HANDLE,
            handle_key=_DEV_USER_HANDLE.lower(),
            role=UserRole.USER,
        )
        db.add(user)
        await db.flush()
    return user


def _safe_next(target: str) -> str:
    """Only allow a same-site absolute path — never an off-site or scheme-relative URL."""
    if target.startswith("/") and not target.startswith(("//", "/\\")):
        return target
    return "/me/agents"


@router.get("/dev/login")
async def dev_login(
    request: Request,
    db: DbSession,
    user_id: Annotated[int | None, Query()] = None,
    next_url: Annotated[str, Query(alias="next")] = "/me/agents",
) -> Response:
    """Sign in without Google OAuth. Local dev only (guarded at mount time).

    With ``?user_id=`` it signs in as that existing user (handy for reproducing a
    specific person's view against a seeded dev DB); otherwise it signs in as the
    seeded ``dev@localhost`` user, creating it on first use. ``?next=`` must be a
    same-site path.
    """
    if user_id is not None:
        user = (
            await db.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user.")
    else:
        user = await _ensure_dev_user(db)
    set_session_user(request, user.id)
    await db.commit()
    return RedirectResponse(url=_safe_next(next_url), status_code=status.HTTP_303_SEE_OTHER)
