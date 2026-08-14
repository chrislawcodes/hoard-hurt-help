"""Who counts as the platform rather than as a player.

The flag is stored once at account creation and never recomputed. Both of the
things you would naturally key on instead change after the fact, and the two
tests below are the reason the column exists at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.engine.bots.seating import get_or_create_bots_user
from app.config import settings
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
async def test_the_flag_is_decided_once_and_does_not_follow_a_later_role_change(
    db,
) -> None:
    """Write-once is the point; the value written must be right in the first place.

    An earlier version of this test asserted only the write-once half, and in doing
    so pinned a real bug: the account-creation rule looked at the email domain
    alone while migration 0053 also flagged admins. No real Google address is on an
    internal domain, so no admin created after the deploy was ever flagged — they
    appeared on their own dashboard as a real user, permanently, because the flag
    is never recomputed.
    """
    user = await sync_google_user(db, _info("player@gmail.com", sub="sub-role"))
    await db.commit()
    assert user.is_internal is False

    # A later promotion does NOT change it — that half was always right.
    user.role = UserRole.ADMIN
    await db.commit()
    assert user.is_internal is False, "the flag must not follow the role"


@pytest.mark.asyncio
async def test_an_admin_is_internal_from_the_moment_they_sign_up(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Matching migration 0053, which flags every admin-role account.

    Without this the same person is classified differently depending on which side
    of the deploy their row was created.
    """
    monkeypatch.setattr(settings, "platform_admin_emails", "boss@gmail.com")
    user = await sync_google_user(db, _info("boss@gmail.com", sub="sub-admin"))
    await db.commit()

    assert user.role is UserRole.ADMIN
    assert user.is_internal is True, (
        "an admin signing up after the deploy must be flagged, like 0053 flags "
        "the ones that existed before it"
    )


@pytest.mark.asyncio
async def test_the_bots_account_is_created_internal(db) -> None:
    bots_user = await get_or_create_bots_user(db)
    await db.commit()
    assert bots_user.is_internal is True
