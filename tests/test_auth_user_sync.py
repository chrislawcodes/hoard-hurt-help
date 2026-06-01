"""given_name/family_name are captured from Google at login (sync_google_user)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import make_engine
from app.models import Base
from app.models.user import User
from app.routes.auth import sync_google_user
from app.schemas.auth import GoogleUserInfo


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _info(**over) -> GoogleUserInfo:
    base = dict(
        sub="g-1",
        email="a@example.com",
        name="Ada Lovelace",
        given_name="Ada",
        family_name="Lovelace",
    )
    base.update(over)
    return GoogleUserInfo(**base)


def test_schema_parses_given_and_family() -> None:
    info = _info()
    assert info.given_name == "Ada"
    assert info.family_name == "Lovelace"
    # Google may omit them — the schema must tolerate that.
    bare = GoogleUserInfo(sub="s", email="e@x.com")
    assert bare.given_name is None
    assert bare.family_name is None


@pytest.mark.asyncio
async def test_creates_user_with_names(session) -> None:
    user = await sync_google_user(session, _info())
    await session.commit()
    assert user.given_name == "Ada"
    assert user.family_name == "Lovelace"


@pytest.mark.asyncio
async def test_fills_missing_names_on_existing_user(session) -> None:
    # A row created before we captured names (e.g. pre-migration).
    session.add(User(google_sub="g-1", email="a@example.com", name="Ada Lovelace"))
    await session.commit()

    user = await sync_google_user(session, _info())
    await session.commit()
    assert user.given_name == "Ada"
    assert user.family_name == "Lovelace"
    # Filled in place — no duplicate row.
    rows = (
        await session.execute(select(User).where(User.google_sub == "g-1"))
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_does_not_overwrite_existing_names(session) -> None:
    session.add(
        User(
            google_sub="g-1",
            email="a@example.com",
            name="Ada Lovelace",
            given_name="Ada",
            family_name="Lovelace",
        )
    )
    await session.commit()

    user = await sync_google_user(
        session, _info(given_name="DIFFERENT", family_name="CHANGED")
    )
    await session.commit()
    assert user.given_name == "Ada"
    assert user.family_name == "Lovelace"
