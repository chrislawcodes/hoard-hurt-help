"""Who counts as the platform rather than as a player.

The flag is stored once at account creation and never recomputed. Both of the
things you would naturally key on instead change after the fact, and the two
tests below are the reason the column exists at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.engine.bots.seating import get_or_create_bots_user
from app.identity.internal_accounts import is_internal_email
from app.models.user import User, UserRole
from app.routes.auth import sync_google_user
from app.schemas.auth import GoogleUserInfo


def _info(email: str, sub: str = "sub-x") -> GoogleUserInfo:
    return GoogleUserInfo(sub=sub, email=email, name="Someone")


def test_domain_rule_matches_configured_domains() -> None:
    assert is_internal_email("ludumlabs@house.local") is True
    assert is_internal_email("harness-A@local.test") is True
    assert is_internal_email("bots@agentludum.local") is True
    assert is_internal_email("someone@gmail.com") is False
    assert is_internal_email(None) is False
    assert is_internal_email("") is False


def test_domain_rule_is_case_insensitive_and_ignores_lookalikes() -> None:
    assert is_internal_email("Person@HOUSE.LOCAL") is True
    # A domain that merely ends with an internal one is a different domain.
    assert is_internal_email("someone@nothouse.local") is False
    assert is_internal_email("house.local@gmail.com") is False


@pytest.mark.asyncio
async def test_a_real_signup_is_not_internal(db) -> None:
    user = await sync_google_user(db, _info("player@gmail.com"))
    await db.commit()
    assert user.is_internal is False


@pytest.mark.asyncio
async def test_an_internal_domain_signup_is_flagged(db) -> None:
    user = await sync_google_user(db, _info("rig@house.local"))
    await db.commit()
    assert user.is_internal is True


@pytest.mark.asyncio
async def test_the_flag_survives_an_email_rewrite(db) -> None:
    """sync_google_user rewrites users.email on a later login.

    A rule evaluated at read time would move this account out of the excluded
    group the moment the address changed, and last month's numbers would stop
    reproducing.
    """
    user = await sync_google_user(db, _info("rig@house.local", sub="sub-stable"))
    await db.commit()
    assert user.is_internal is True

    # Same identity, new address on a later login.
    await sync_google_user(db, _info("rig-renamed@gmail.com", sub="sub-stable"))
    await db.commit()

    stored = (
        await db.execute(select(User).where(User.google_sub == "sub-stable"))
    ).scalar_one()
    assert stored.email == "rig-renamed@gmail.com"
    assert stored.is_internal is True, "the flag must not follow the address"


@pytest.mark.asyncio
async def test_the_flag_survives_a_role_change(db) -> None:
    """promote_user / demote_user change the role after the fact."""
    user = await sync_google_user(db, _info("player@gmail.com", sub="sub-role"))
    await db.commit()
    assert user.is_internal is False

    user.role = UserRole.ADMIN
    await db.commit()
    assert user.is_internal is False, "the flag must not follow the role"

    user.role = UserRole.USER
    await db.commit()
    assert user.is_internal is False


@pytest.mark.asyncio
async def test_the_bots_account_is_created_internal(db) -> None:
    bots_user = await get_or_create_bots_user(db)
    await db.commit()
    assert bots_user.is_internal is True
