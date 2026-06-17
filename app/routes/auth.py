"""Google OAuth + sign-out routes."""

import logging

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.google import oauth
from app.auth.session import clear_session, set_session_user
from app.config import settings
from app.deps import DbSession
from app.models.user import User, UserRole
from app.routes.nav_context import resolve_play_setup_state
from app.schemas.auth import GoogleUserInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


async def sync_google_user(db: AsyncSession, userinfo: GoogleUserInfo) -> User:
    """Create the user on first sign-in, or fill in names we didn't have yet.

    given_name/family_name come straight from Google, so we capture them from the
    start rather than backfilling later. Rows created before we stored names get
    filled on the user's next login; a name that's already set is never
    overwritten.
    """
    role = (
        UserRole.ADMIN
        if userinfo.email.lower() in settings.platform_admin_emails_set
        else UserRole.USER
    )
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
            role=role,
        )
        db.add(user)
        await db.flush()
        return user
    if user.email != userinfo.email:
        # users.email is unique; another row could already hold this address
        # (e.g. an orphaned/duplicate row). google_sub is the real identity key,
        # so on collision keep the stored email and log rather than raise. Role
        # only changes for the platform-admin floor below, so an in-app role
        # promotion is preserved unless the email itself is a floor admin.
        clash = (
            await db.execute(
                select(User.id).where(
                    User.email == userinfo.email, User.id != user.id
                )
            )
        ).scalar_one_or_none()
        if clash is None:
            user.email = userinfo.email
        else:
            logger.warning(
                "skipping email refresh for user %s: %s already in use by user %s",
                user.id,
                userinfo.email,
                clash,
            )
    if user.given_name is None and userinfo.given_name is not None:
        user.given_name = userinfo.given_name
    if user.family_name is None and userinfo.family_name is not None:
        user.family_name = userinfo.family_name
    if userinfo.email.lower() in settings.platform_admin_emails_set:
        user.role = UserRole.ADMIN
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

    if user.disabled_at is not None:
        request.session.pop("next_after_login", None)
        return RedirectResponse(url="/disabled", status_code=status.HTTP_303_SEE_OTHER)

    next_url = request.session.pop("next_after_login", "/") or "/"
    if next_url == "/":
        next_url = (await resolve_play_setup_state(db, user)).next_url
    return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
