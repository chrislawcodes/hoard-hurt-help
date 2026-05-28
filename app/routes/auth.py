"""Google OAuth + sign-out routes."""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth.google import oauth
from app.auth.session import clear_session, set_session_user
from app.deps import DbSession
from app.models.user import User
from app.schemas.auth import GoogleUserInfo

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/google/login")
async def google_login(request: Request, next: str = "/"):
    request.session["next_after_login"] = next
    redirect_uri = str(request.url_for("google_callback"))
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

    user = (
        await db.execute(select(User).where(User.google_sub == userinfo.sub))
    ).scalar_one_or_none()
    if user is None:
        user = User(
            google_sub=userinfo.sub,
            email=userinfo.email,
            name=userinfo.name,
        )
        db.add(user)
        await db.flush()
    await db.commit()

    set_session_user(request, user.id)

    next_url = request.session.pop("next_after_login", "/") or "/"
    return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request):
    clear_session(request)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
