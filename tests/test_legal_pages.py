"""Tests for the public /privacy and /terms pages and the footer links to them.

Most of the content assertions here guard an *absence*. Both pages are written
to promise as little as possible, because this is an Alpha where nearly every
fact stated on them will change — so the failure mode worth catching is a
well-meaning edit that adds a commitment back. Each such test names the sentence
an earlier draft actually carried.

The exception is the strategy-text caveat, which is guarded as a presence: the
terms grant a broad license over strategy text while the product keeps it off
other players' screens, so both pages have to keep saying that out loud or they
start contradicting each other.
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


def _prose(resp: object) -> str:
    """Page text with runs of whitespace collapsed, lowercased.

    The templates wrap prose across source lines, so a sentence a test cares
    about is split by a newline and indentation in the response body. Matching
    the raw text would make every assertion here hostage to where the paragraph
    happens to wrap — a reflow would fail the suite while the promise it guards
    sat there unchanged.
    """
    return " ".join(getattr(resp, "text").split()).lower()


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
    body = _prose(resp)
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
    assert "do not put anything confidential in a strategy" in _prose(resp)


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_both_pages_warn_that_alpha_data_can_be_wiped(
    client: AsyncClient, path: str
) -> None:
    """The database really does get reset; users are told before it happens to them."""
    resp = await client.get(path, follow_redirects=False)
    assert "wipe" in _prose(resp)


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_both_pages_frame_themselves_as_a_snapshot(
    client: AsyncClient, path: str
) -> None:
    """The frame is what lets the rest of the page speak in the present tense.

    Both pages state plain facts — one cookie, no analytics, these providers —
    and in an Alpha that changes weekly every one of those is a promise waiting
    to go stale. The frame at the top is what makes them descriptions instead.
    Lose it and the pages quietly become a list of commitments again.
    """
    resp = await client.get(path, follow_redirects=False)
    assert "not a commitment about the future" in _prose(resp)


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_neither_page_promises_notice_before_changing(
    client: AsyncClient, path: str
) -> None:
    """Changing the site should never require sending anyone an announcement.

    An earlier draft promised to "say something on the site" before a significant
    change, and the terms promised warning before charging. Both are work nothing
    performs, on a project where the whole point is changing fast.
    """
    resp = await client.get(path, follow_redirects=False)
    assert "without notice" in _prose(resp)


@pytest.mark.parametrize("path", LEGAL_PATHS)
async def test_neither_page_promises_export_or_deletion(
    client: AsyncClient, path: str
) -> None:
    """No self-serve export or delete exists, so neither page may promise one.

    An earlier draft offered to do both by hand over email. That committed one
    person to work no code performs, which is the kind of promise a policy should
    not be making — so the pages now state the channel and withhold the promise
    on purpose. This pins the withholding, because "we will delete your account"
    is exactly the sentence a well-meaning edit adds back.
    """
    resp = await client.get(path, follow_redirects=False)
    body = _prose(resp)
    assert "not promising" in body
    assert "we will delete your account" not in body


async def test_terms_state_the_age_limit_and_governing_law(client: AsyncClient) -> None:
    """Two clauses with no home anywhere else in the product."""
    resp = await client.get("/terms", follow_redirects=False)
    body = resp.text
    assert "18 or older" in body
    assert "State of California" in body


async def test_privacy_discloses_that_we_record_how_you_arrived(
    client: AsyncClient,
) -> None:
    """Guarded as a presence, like the strategy-text caveat above.

    The site records where a visitor came from — campaign tags on the link, the
    referring site, the landing page — and saves it against the account if they
    sign up. An earlier version of this page said the opposite in as many words
    ("no analytics"), which was true when it was written and stopped being true
    the day the engagement dashboard shipped.

    So this pins the disclosure rather than the wording of any one sentence: if
    someone trims this paragraph while the capture is still running, the page
    goes back to being wrong about the thing a privacy policy exists to state.
    """
    prose = _prose(await client.get("/privacy", follow_redirects=False))

    assert "how you arrived" in prose, "the page must say we record where you came from"
    assert "campaign tags" in prose, "the page must name what is taken from the link"
    assert "only reaches our database if you sign up" in prose, (
        "the page must say when this stops being a cookie and becomes a record"
    )
    assert "no analytics" not in prose, (
        "the old claim contradicts the feature that now ships"
    )
