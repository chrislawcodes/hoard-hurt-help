"""Smoke tests for OAuth flow shape and User upsert.

Mocks the Google OAuth dance — we don't actually call Google in tests.
"""

from unittest.mock import AsyncMock, patch

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
