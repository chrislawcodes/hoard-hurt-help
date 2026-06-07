"""Google OAuth + sign-out routes."""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import oauth
from app.auth.session import clear_session, set_session_user
from app.config import settings
from app.deps import DbSession
from app.models.agent import Agent, AgentKind
from app.models.user import User
from app.schemas.auth import GoogleUserInfo

router = APIRouter(prefix="/auth", tags=["auth"])


async def sync_google_user(db: AsyncSession, userinfo: GoogleUserInfo) -> User:
    """Create the user on first sign-in, or fill in names we didn't have yet.

    given_name/family_name come straight from Google, so we capture them from the
    start rather than backfilling later. Rows created before we stored names get
    filled on the user's next login; a name that's already set is never
    overwritten.
    """
    user = (
        await db.execute(select(User).where(User.google_sub == userinfo.sub))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=userinfo.sub,
            email=userinfo.email,
            name=userinfo.name,
            given_name=userinfo.given_name,
            family_name=userinfo.family_name,
        )
        db.add(user)
        await db.flush()
        return user
    if user.given_name is None and userinfo.given_name is not None:
        user.given_name = userinfo.given_name
    if user.family_name is None and userinfo.family_name is not None:
        user.family_name = userinfo.family_name
    return user


@router.get("/google/login")
async def google_login(request: Request, next: str = "/"):
    request.session["next_after_login"] = next
    # Prefer the explicitly-configured redirect URI (GOOGLE_REDIRECT_URI) so the
    # callback is correct behind a TLS-terminating proxy; fall back to url_for.
    redirect_uri = settings.google_redirect_uri or str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="google_callback")
async def google_callback(request: Request, db: DbSession):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "GOOGLE_AUTH_FAILED",
                    "message": str(exc),
                    "details": {},
                }
            },
        ) from exc

    userinfo_raw = token.get("userinfo") or await oauth.google.userinfo(token=token)
    userinfo = GoogleUserInfo(**dict(userinfo_raw))

    user = await sync_google_user(db, userinfo)
    await db.commit()

    set_session_user(request, user.id)

    next_url = request.session.pop("next_after_login", "/") or "/"
    if next_url == "/":
        agent_count = await db.scalar(
            select(func.count()).select_from(Agent).where(
                Agent.user_id == user.id,
                Agent.archived_at.is_(None),
                Agent.kind == AgentKind.AI,
            )
        ) or 0
        if agent_count == 0:
            next_url = "/me/agents"
    return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
