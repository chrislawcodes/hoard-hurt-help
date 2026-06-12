"""Session-cookie helpers."""

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

SESSION_USER_KEY = "user_id"


async def get_user_from_session(request: Request, db: AsyncSession) -> User | None:
    """Look up the signed-in user via the session cookie. Returns None if absent."""
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    return (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()


def set_session_user(request: Request, user_id: int) -> None:
    request.session[SESSION_USER_KEY] = user_id


def clear_session(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def raise_account_disabled(request: Request) -> None:
    """Raise the appropriate response for a disabled account.

    Three branches:
      HX-Request header  -> 200 + HX-Redirect:/disabled  (full-page nav, not swap)
      Accept: text/html  -> 303 redirect to /disabled
      anything else      -> 403 JSON ACCOUNT_DISABLED
    """
    if request.headers.get("HX-Request"):
        raise HTTPException(
            status_code=200,
            headers={"HX-Redirect": "/disabled"},
        )
    if "text/html" in request.headers.get("Accept", ""):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/disabled"},
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": {
                "code": "ACCOUNT_DISABLED",
                "message": "This account has been disabled.",
                "details": {},
            }
        },
    )


def raise_not_signed_in() -> None:
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
