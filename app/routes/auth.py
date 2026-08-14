"""Google OAuth + sign-out routes."""

import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_errors import api_error
from app.auth.google import oauth
from app.auth.session import clear_session, set_session_user
from app.config import settings
from app.deps import DbSession
from app.identity.first_touch import pop_first_touch
from app.identity.internal_accounts import is_internal_email
from app.models.user import User, UserRole
from app.routes.nav_context import resolve_play_setup_state
from app.schemas.auth import GoogleUserInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _apply_first_touch(
    user: User,
    first_touch: dict[str, object] | None,
    source_channel: str | None,
) -> None:
    """Copy a captured first touch onto a brand-new user row.

    ``source_channel`` is set directly by callers that know the account was born
    somewhere with no web session at all — the MCP sign-in paths pass "mcp", so
    those signups are never silently counted as direct web traffic.
    """
    if not settings.first_touch_capture_enabled:
        # Off means off. The MCP paths pass source_channel directly and have no
        # browser session, so they bypassed the middleware's check entirely and
        # kept writing a source while capture was disabled.
        return
    if source_channel is not None:
        user.first_source_channel = source_channel
        return
    if not first_touch:
        # Capture was off, or this path has no session. Leaving every column NULL
        # is meaningful: it means "never captured", which the dashboard shows as
        # unknown rather than folding it in with genuine direct visits.
        return
    user.first_utm_source = _as_str(first_touch.get("utm_source"))
    user.first_utm_medium = _as_str(first_touch.get("utm_medium"))
    user.first_utm_campaign = _as_str(first_touch.get("utm_campaign"))
    user.first_referrer_host = _as_str(first_touch.get("referrer_host"))
    user.first_landing_path = _as_str(first_touch.get("landing_path"))
    user.first_source_channel = _as_str(first_touch.get("channel"))


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


async def sync_google_user(
    db: AsyncSession,
    userinfo: GoogleUserInfo,
    *,
    first_touch: dict[str, object] | None = None,
    source_channel: str | None = None,
) -> User:
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
            # Decided once, here, and never recomputed. The email below can be
            # rewritten on a later login and the role can change, so evaluating
            # this at read time would let an account drift in and out of the
            # excluded group between page loads.
            # Domain OR platform admin — matching migration 0053 exactly. The
            # domain rule alone never fires for a real Google address, so every
            # admin account created after the deploy was counted as a real user
            # on their own dashboard, permanently, since the flag is write-once.
            is_internal=is_internal_email(userinfo.email) or role is UserRole.ADMIN,
        )
        # Attribution is written ONLY here, on the branch that creates the account.
        # A returning user's original source must never be overwritten by wherever
        # they happened to be when they signed in again.
        _apply_first_touch(user, first_touch, source_channel)
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
        raise api_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            code="GOOGLE_AUTH_FAILED",
            message=str(exc),
        ) from exc

    userinfo_raw = token.get("userinfo") or await oauth.google.userinfo(token=token)
    userinfo = GoogleUserInfo(**dict(userinfo_raw))

    user = await sync_google_user(db, userinfo, first_touch=pop_first_touch(request))
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
