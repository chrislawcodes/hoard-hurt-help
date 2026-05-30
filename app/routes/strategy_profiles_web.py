"""Web routes for reusable strategy profiles.

A profile is a named, user-level strategy. One may be the default. At game
entry its text is *copied* into the player's StrategyPrompt (see web.py), so
editing a profile never changes a game already in progress.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update

from app.config import settings
from app.deps import DbSession, require_user
from app.models.strategy_profile import StrategyProfile
from app.models.user import User
from app.templating import templates

router = APIRouter(prefix="/me/strategy-profiles", tags=["strategy-profiles"])
logger = logging.getLogger(__name__)


def _is_admin(user: User) -> bool:
    return user.email.lower() in settings.admin_emails_set


async def _owned_profile(db: DbSession, user: User, profile_id: int) -> StrategyProfile:
    profile = (
        await db.execute(
            select(StrategyProfile).where(
                StrategyProfile.id == profile_id, StrategyProfile.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(404, detail="Strategy profile not found.")
    return profile


async def _clear_other_defaults(db: DbSession, user_id: int, keep_id: int) -> None:
    """Ensure at most one default per user."""
    await db.execute(
        update(StrategyProfile)
        .where(
            StrategyProfile.user_id == user_id,
            StrategyProfile.id != keep_id,
            StrategyProfile.is_default.is_(True),
        )
        .values(is_default=False)
    )


@router.get("", response_class=HTMLResponse)
async def list_profiles(
    request: Request,
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    profiles = (
        (
            await db.execute(
                select(StrategyProfile)
                .where(StrategyProfile.user_id == user.id)
                .order_by(StrategyProfile.name)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "strategy_profiles.html",
        {"user": user, "is_admin": _is_admin(user), "profiles": profiles},
    )


@router.post("")
async def create_profile(
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    prompt_text: Annotated[str, Form()],
    is_default: Annotated[bool, Form()] = False,
):
    name = name.strip()
    if not (1 <= len(name) <= 64):
        raise HTTPException(400, detail="Name must be 1–64 characters.")
    if not prompt_text.strip():
        raise HTTPException(400, detail="Strategy text can't be empty.")
    clash = (
        await db.execute(
            select(StrategyProfile).where(
                StrategyProfile.user_id == user.id, StrategyProfile.name == name
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(409, detail="You already have a profile with that name.")

    # The first profile a user creates becomes their default automatically.
    has_any = (
        await db.execute(select(StrategyProfile.id).where(StrategyProfile.user_id == user.id))
    ).first() is not None
    make_default = is_default or not has_any

    profile = StrategyProfile(
        user_id=user.id, name=name, prompt_text=prompt_text, is_default=make_default
    )
    db.add(profile)
    await db.flush()
    if make_default:
        await _clear_other_defaults(db, user.id, profile.id)
    await db.commit()
    return RedirectResponse("/me/strategy-profiles", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{profile_id}")
async def update_profile(
    profile_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
    name: Annotated[str, Form()],
    prompt_text: Annotated[str, Form()],
    is_default: Annotated[bool, Form()] = False,
):
    profile = await _owned_profile(db, user, profile_id)
    name = name.strip()
    if not (1 <= len(name) <= 64) or not prompt_text.strip():
        raise HTTPException(400, detail="Name and strategy text are required.")
    clash = (
        await db.execute(
            select(StrategyProfile).where(
                StrategyProfile.user_id == user.id,
                StrategyProfile.name == name,
                StrategyProfile.id != profile.id,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(409, detail="Another profile already has that name.")

    profile.name = name
    profile.prompt_text = prompt_text
    if is_default:
        profile.is_default = True
        await _clear_other_defaults(db, user.id, profile.id)
    await db.commit()
    return RedirectResponse("/me/strategy-profiles", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{profile_id}/delete")
async def delete_profile(
    profile_id: Annotated[int, Path()],
    db: DbSession,
    user: Annotated[User, Depends(require_user)],
):
    profile = await _owned_profile(db, user, profile_id)
    await db.delete(profile)
    await db.commit()
    return RedirectResponse("/me/strategy-profiles", status_code=status.HTTP_303_SEE_OTHER)
