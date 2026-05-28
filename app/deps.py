"""FastAPI dependencies shared across routes."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.auth.session import get_user_from_session
from app.db import get_session
from app.engine.tokens import verify_agent_key
from app.models.player import Player
from app.models.user import User


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(request: Request, db: DbSession) -> User | None:
    """Return the signed-in User or None (does not raise)."""
    return await get_user_from_session(request, db)


async def require_user(request: Request, db: DbSession) -> User:
    user = await get_user_from_session(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "NOT_SIGNED_IN",
                    "message": "Sign in with Google to continue.",
                    "details": {},
                }
            },
        )
    return user


async def require_admin(request: Request, db: DbSession) -> User:
    user = await require_user(request, db)
    if user.email.lower() not in settings.admin_emails_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "NOT_ADMIN",
                    "message": "Admin access required.",
                    "details": {},
                }
            },
        )
    return user


async def require_agent_key(
    db: DbSession,
    x_agent_key: Annotated[str | None, Header()] = None,
) -> Player:
    """Validate `X-Agent-Key`. Returns the Player. Raises 401 on miss."""
    if not x_agent_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_KEY",
                    "message": "Missing X-Agent-Key header.",
                    "details": {},
                }
            },
        )
    # Look up by trying every Player (we don't know which game). Tiny scale —
    # acceptable for v1. If this becomes a hot path we'd index by a key prefix.
    rows = (await db.execute(select(Player))).scalars().all()
    for p in rows:
        if verify_agent_key(x_agent_key, p.agent_key_hash):
            return p
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "code": "INVALID_KEY",
                "message": "Invalid X-Agent-Key.",
                "details": {},
            }
        },
    )
