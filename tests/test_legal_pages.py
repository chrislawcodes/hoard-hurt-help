"""Tests for the public /privacy and /terms pages and the footer links to them.

Most of the content assertions here guard an *absence*. Both pages are written
to promise as little as possible, because this is an Alpha where nearly every
fact stated on them will change — so the failure mode worth catching is a
well-meaning edit that adds a commitment back. Each such test names the sentence
an earlier draft actually carried.

The exceptions are guarded as presences, and each is one of the six things
CalOPPA asks a privacy policy for, or the honest counterpart to a right the terms
claim: that strategy is listed as public, that page-view counting is disclosed,
that arrivals are recorded, and that there is no way to export or delete.

Several tests here have had their coverage deliberately narrowed as the privacy
page was cut to that six-item floor. Where that happened the docstring says so
and says what was given up, so a future reader can tell a considered trim from an
accidental one.
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


async def test_terms_license_is_broad_enough_to_cover_strategies(
    client: AsyncClient,
) -> None:
    """The licence covers strategies by scope now, not by naming them.

    It used to assert the word "strateg" appeared here, because the two pages
    disagreed: the product hid strategy text while the licence claimed the right
    to publish it, so naming them explicitly was the bridge. The privacy page now
    lists strategy as public outright, so there is nothing to bridge and the word
    was cut from this page entirely.

    What replaces it is the scope that makes the licence reach strategies at all:
    "anything you put here", "for any purpose". Lose either and the licence
    narrows to something a strategy could fall outside of.
    """
    prose = _prose(await client.get("/terms", follow_redirects=False))

    assert "license" in prose
    assert "anything you put here" in prose, (
        "the licence must reach everything a user writes, strategies included"
    )
    assert "for any purpose" in prose, (
        "listing purposes would limit us to them; this is the flexibility"
    )


async def test_terms_keep_the_licence_irrevocable_and_passable(
    client: AsyncClient,
) -> None:
    """The two clauses that took the longest to settle, both nearly cut.

    "Can't take that permission back": a licence silent on revocability is
    generally revocable at will. Match history is shared, so one player
    withdrawing damages every other player's record, and published work cannot be
    unpublished. Five words standing between us and unpicking published output.

    "Pass those rights on to others": added for selling results. Publishing a free
    dataset needs only our own publish right, but a *buyer* needs rights we cannot
    grant without this.
    """
    prose = _prose(await client.get("/terms", follow_redirects=False))

    assert "can't take that permission back" in prose
    assert "pass those rights on to others" in prose


async def test_privacy_lists_strategy_as_public(client: AsyncClient) -> None:
    """Strategy sits in the public list, ahead of the product actually showing it.

    This overstates what is public on purpose. Today ``web_viewer`` 403s a
    non-player, so strategy text is hidden — but the licence in the terms permits
    publishing it whenever we choose, and a reader told "hidden" would be
    surprised the day we do. Overstating exposure is the safe direction: it makes
    a reader more careful and can mislead nobody.

    It replaces the "do not put anything confidential in a strategy" warning this
    page used to carry, which said the same thing at four times the length.
    """
    prose = _prose(await client.get("/privacy", follow_redirects=False))
    assert "what is public" in prose
    assert "your scores, your strategy" in prose, (
        "strategy must sit in the public list, not be described as hidden"
    )


# The "do not put anything confidential in a strategy" test used to sit here. It
# was deleted with the sentence it guarded, deliberately: that warning existed to
# bridge a gap between a product that hid strategies and a licence that could
# publish them. The privacy page now says "your strategy" is public in its own
# public list, which is the warning — repeating it here was the same sentence
# twice. `test_privacy_lists_strategy_as_public` is what guards it now.


async def test_the_terms_warn_that_alpha_data_can_be_wiped(client: AsyncClient) -> None:
    """The database really does get reset; users are told before it happens to them.

    Terms only, narrowed for the same reason as the test above: this is a
    heads-up, not one of the six things CalOPPA asks the privacy page for, and
    that page now carries only those six.
    """
    assert "wipe" in _prose(await client.get("/terms", follow_redirects=False))


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
async def test_neither_page_promises_to_delete_an_account(
    client: AsyncClient, path: str
) -> None:
    """Nothing performs deletion, so neither page may say it will happen.

    An earlier draft offered to export and delete by hand over email, committing
    one person to work no code performs. "We will delete your account" is exactly
    the sentence a well-meaning edit adds back, on either page.
    """
    assert "we will delete your account" not in _prose(
        await client.get(path, follow_redirects=False)
    )


async def test_privacy_says_there_is_no_export_or_delete(client: AsyncClient) -> None:
    """CalOPPA wants the review/change process disclosed. Ours is that there isn't one.

    Saying so IS the disclosure, which is why this is guarded as a presence rather
    than an absence — drop the sentence and the page stops answering one of the
    six questions it exists to answer.
    """
    assert "no button to export or delete your data" in _prose(
        await client.get("/privacy", follow_redirects=False)
    )


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

    **Coverage narrowed deliberately** when the page was cut to its CalOPPA floor.
    Three sentences this used to require are gone: the named campaign tags, "we
    only save it against an account if you sign up", and the error-log exception.
    CalOPPA asks for the *category* of information collected, which "we record how
    you arrived" satisfies, so nothing legally required was lost. What was lost is
    reader-facing — an anonymous visitor is no longer told their arrival is
    discarded unless they sign up, which was the friendliest fact on the page.
    That is a wording choice and it stays the author's to make, so what remains
    here are the two assertions that would make the page *wrong* rather than
    merely thin.
    """
    prose = _prose(await client.get("/privacy", follow_redirects=False))

    assert "how you arrived" in prose, "the page must say we record where you came from"
    assert "no analytics" not in prose, (
        "the old claim contradicts the feature that now ships"
    )


# The beacon and its disclosure are tested together on purpose. They are one
# change: shipping the script without the paragraph makes the privacy page false,
# and that has already happened once on this site — the page said "no analytics"
# for as long as it took someone to notice.


async def test_the_page_view_beacon_ships_on_every_page(
    reset_db, client: AsyncClient
) -> None:
    """Cloudflare's snippet, installed manually rather than injected.

    Automatic injection only works for traffic proxied through Cloudflare, and
    this domain is DNS-only — it resolves straight to the host. The automatic mode
    would have reported a healthy setup and collected nothing.
    """
    body = (await client.get("/", follow_redirects=False)).text
    assert "static.cloudflareinsights.com/beacon.min.js" in body
    assert "data-cf-beacon" in body


async def test_privacy_discloses_that_page_views_are_counted(
    client: AsyncClient,
) -> None:
    """A third party sees that a visit happened. The page has to say so.

    Renamed from ``..._names_the_company_...`` because the vendor is no longer
    named, and that is on purpose: CalOPPA asks for the *categories* of third
    parties, not their names, and a named vendor is a sentence that goes stale the
    day we switch. Cloudflare, Railway and the DNS provider are all now described
    by what they do.

    What still has to be there is that page views are counted by someone else at
    all. Ship the beacon without that and the page is wrong about who receives
    visitor data — which has already happened once here, when "no analytics"
    outlived the feature that made it true.
    """
    prose = _prose(await client.get("/privacy", follow_redirects=False))

    assert "count page views" in prose, (
        "the beacon ships on every page; the page must disclose that someone "
        "other than us counts visits"
    )
    assert "no third-party analytics service" not in prose, (
        "that claim is false the moment the beacon ships"
    )
