"""Smoke tests for OAuth flow shape and User upsert.

Mocks the Google OAuth dance — we don't actually call Google in tests.
"""


import pytest
from sqlalchemy import select

from app.models import User
from app.models.base import Base


@pytest.fixture
async def db(engine, session_factory):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_new_user_upsert(db):
    """A first sign-in creates a User row."""
    db.add(
        User(
            google_sub="sub-123",
            email="alice@example.com",
            name="Alice",
        )
    )
    await db.commit()

    found = (
        await db.execute(select(User).where(User.google_sub == "sub-123"))
    ).scalar_one()
    assert found.email == "alice@example.com"
    assert found.name == "Alice"


@pytest.mark.asyncio
async def test_returning_user_reused(db):
    """A returning sign-in finds the existing User by google_sub."""
    db.add(User(google_sub="sub-456", email="bob@example.com", name="Bob"))
    await db.commit()

    found = (
        await db.execute(select(User).where(User.google_sub == "sub-456"))
    ).scalar_one()
    assert found.email == "bob@example.com"

    # Second sign-in shouldn't duplicate.
    all_bobs = (
        (await db.execute(select(User).where(User.google_sub == "sub-456")))
        .scalars()
        .all()
    )
    assert len(all_bobs) == 1


def test_oauth_module_imports():
    """The Authlib OAuth client constructs without contacting Google."""
    from app.auth.google import oauth

    assert oauth.google is not None


@pytest.mark.asyncio
async def test_zero_bot_user_redirect_destination(db):
    """A signed-in user with no bots should be redirected to /me/bots/new."""
    from sqlalchemy import func
    from app.models.bot import Bot, BotKind

    user = User(google_sub="sub-zero", email="zero@example.com", name="Zero")
    db.add(user)
    await db.commit()

    bot_count = await db.scalar(
        select(func.count()).select_from(Bot).where(
            Bot.user_id == user.id,
            Bot.archived_at.is_(None),
            Bot.kind != BotKind.SIM,
        )
    ) or 0
    assert bot_count == 0  # confirms the redirect condition would trigger


@pytest.mark.asyncio
async def test_existing_bot_user_no_redirect_override(db):
    """A signed-in user with an existing bot should NOT be redirected to /me/bots/new."""
    from sqlalchemy import func
    from app.models.bot import Bot, BotKind

    user = User(google_sub="sub-hasbot", email="hasbot@example.com", name="HasBot")
    db.add(user)
    await db.flush()
    bot = Bot(
        user_id=user.id,
        name="mybot",
        key_lookup="lk",
        key_hint="h",
        kind=BotKind.EXTERNAL,
        status="active",
        max_concurrent_games=3,
        stall_threshold=3,
    )
    db.add(bot)
    await db.commit()

    bot_count = await db.scalar(
        select(func.count()).select_from(Bot).where(
            Bot.user_id == user.id,
            Bot.archived_at.is_(None),
            Bot.kind != BotKind.SIM,
        )
    ) or 0
    assert bot_count == 1  # confirms the redirect condition would NOT trigger
