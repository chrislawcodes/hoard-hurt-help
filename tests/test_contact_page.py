"""Tests for the public /contact page and the two links that point at it."""

from __future__ import annotations

from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import User
from app.routes.web_contact import CONTACT_EMAIL
from tests.factories import make_user
from tests.conftest import signed_in_cookies as _signed_in


async def _seed_disabled_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        user = await make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user


async def test_contact_page_public(client: AsyncClient) -> None:
    """/contact loads signed-out and shows the address as a mailto link."""
    resp = await client.get("/contact", follow_redirects=False)
    assert resp.status_code == 200
    assert f"mailto:{CONTACT_EMAIL}" in resp.text
    assert CONTACT_EMAIL in resp.text


async def test_footer_links_to_contact_without_exposing_address(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """The footer links to /contact but never carries the address itself.

    The footer renders on every page, so a mailto: there would put the mailbox
    in every page's HTML — the whole reason /contact exists as its own page.
    """
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert 'href="/contact"' in resp.text
    assert "mailto:" not in resp.text
    assert CONTACT_EMAIL not in resp.text


async def test_disabled_page_offers_a_way_to_get_in_touch(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A locked-out user can reach /contact — appealing is why that page exists."""
    user = await _seed_disabled_user(reset_db)
    resp = await client.get(
        "/disabled",
        cookies=_signed_in(user.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert 'href="/contact"' in resp.text
