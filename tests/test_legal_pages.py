"""Tests for the public /privacy and /terms pages and the footer links to them.

The content assertions here are deliberate: these two pages make promises to
users, and a few of those promises are load-bearing enough that quietly losing
them in an edit should break the build. The strategy-text caveat is the sharpest
one — the terms grant a broad license over strategy text while the product keeps
it off other players' screens, so both pages have to keep saying that out loud or
they start contradicting each other.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import User
from app.routes.web_contact import CONTACT_EMAIL
from app.routes.web_legal import LAST_UPDATED
from tests.factories import make_user
from tests.conftest import signed_in_cookies as _signed_in

LEGAL_PATHS = ("/privacy", "/terms")


async def _seed_disabled_user(reset_db: async_sessionmaker) -> User:
    async with reset_db() as db:
        user = await make_user(db)
        user.disabled_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_legal_page_public(client: AsyncClient, path: str) -> None:
    """Both pages load signed-out — the reader deciding whether to sign up."""
    resp = await client.get(path, follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_legal_page_shows_last_updated(client: AsyncClient, path: str) -> None:
    """A legal page with no date on it is worthless for telling what you agreed to."""
    resp = await client.get(path, follow_redirects=False)
    assert LAST_UPDATED in resp.text


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_legal_pages_link_to_contact_without_exposing_address(
    client: AsyncClient, path: str
) -> None:
    """Same rule as the footer: link to /contact, never print the mailbox."""
    resp = await client.get(path, follow_redirects=False)
    assert 'href="/contact"' in resp.text
    assert "mailto:" not in resp.text
    assert CONTACT_EMAIL not in resp.text


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_footer_links_to_both_legal_pages(
    client: AsyncClient, reset_db: async_sessionmaker, path: str
) -> None:
    """Every page's footer points at both, so the pages are reachable from anywhere.

    ``reset_db`` is required, not decorative: the front page reads the match
    tables to build its board, so without it the request 500s before a footer
    ever renders.
    """
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 200
    assert f'href="{path}"' in resp.text


async def test_disabled_user_can_still_read_the_terms(
    client: AsyncClient, reset_db: async_sessionmaker
) -> None:
    """A locked-out user has to be able to read the terms they were disabled under."""
    user = await _seed_disabled_user(reset_db)
    resp = await client.get(
        "/terms",
        cookies=_signed_in(user.id),
        follow_redirects=False,
    )
    assert resp.status_code == 200


async def test_terms_claim_a_license_over_strategy_text(client: AsyncClient) -> None:
    """The license has to name strategies, or it does not cover the thing it must."""
    resp = await client.get("/terms", follow_redirects=False)
    body = resp.text.lower()
    assert "license" in body
    assert "strateg" in body


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_both_pages_warn_that_strategies_are_not_secret(
    client: AsyncClient, path: str
) -> None:
    """Neither page may promise secrecy the license expressly overrides.

    The honest line is "not shown to other players today", never "kept private" —
    the day a strategy lands in a published data set, the wrong wording turns a
    product fact into a broken promise.
    """
    resp = await client.get(path, follow_redirects=False)
    body = resp.text.lower()
    assert "do not put anything confidential in a strategy" in body


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_both_pages_warn_that_alpha_data_can_be_wiped(
    client: AsyncClient, path: str
) -> None:
    """The database really does get reset; users are told before it happens to them."""
    resp = await client.get(path, follow_redirects=False)
    assert "wipe" in resp.text.lower()


async def test_terms_state_the_age_limit_and_governing_law(client: AsyncClient) -> None:
    """Two clauses with no home anywhere else in the product."""
    resp = await client.get("/terms", follow_redirects=False)
    body = resp.text
    assert "18 or older" in body
    assert "State of California" in body
