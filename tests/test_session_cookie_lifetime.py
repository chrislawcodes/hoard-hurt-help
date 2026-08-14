"""The session cookie's lifetime, and the privacy page's claim about it.

Two failure modes, one file, because they are the same mistake seen from either
end.

The first is silence. ``SessionMiddleware`` defaults ``max_age`` to 14 days, so
deleting one keyword argument reverts the cookie to a value nobody chose, with
every other test still green — which is exactly how the site came to have a
14-day window in the first place.

The second is drift. The privacy page tells readers how long the cookie lasts.
That number is a disclosure, not decoration, and this file is what stops the
lifetime changing while the page keeps quoting the old figure. This repo has
already shipped one privacy sentence that was true when written and false a
week later; the fix then was a test, and this is that test applied before the
fact rather than after.
"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.main import SESSION_MAX_AGE_SECONDS

SECONDS_PER_DAY = 24 * 60 * 60

# The phrasing the privacy page used before the trim. Matched rather than
# required, so the page is free to stay silent — see the test below.
_COOKIE_LIFETIME_CLAIM = re.compile(r"the cookie lasts (\d+) days?")


@pytest.fixture
def capture_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capture writes to the session, which is what makes Starlette set the cookie.

    With the flag off an anonymous request sets no cookie at all, so there would
    be no ``Set-Cookie`` header to assert against.
    """
    monkeypatch.setattr(settings, "first_touch_capture_enabled", True)


async def test_session_cookie_carries_the_configured_lifetime(
    reset_db: async_sessionmaker, client: AsyncClient, capture_on: None
) -> None:
    """The browser really receives our number, not Starlette's default.

    Asserted on the wire rather than on the constant: reading
    ``SESSION_MAX_AGE_SECONDS`` back would still pass if the argument were
    dropped from the ``add_middleware`` call, which is the one mistake worth
    catching here.
    """
    response = await client.get("/?utm_source=hermesagent")

    set_cookie = " ".join(response.headers.get_list("set-cookie"))
    assert f"Max-Age={SESSION_MAX_AGE_SECONDS}" in set_cookie, (
        f"expected the configured lifetime on the wire, got: {set_cookie!r}"
    )
    assert "Max-Age=1209600" not in set_cookie, (
        "1209600 is Starlette's 14-day default — the argument has been dropped"
    )


async def test_privacy_page_does_not_state_a_stale_cookie_lifetime(
    client: AsyncClient,
) -> None:
    """If the page states the cookie's lifetime, it must be the real one.

    This used to *require* the page to state it. The privacy policy has since been
    cut to its CalOPPA floor and no longer mentions a lifetime at all — nothing
    asks it to, and the sentence went with everything else that was explanation
    rather than disclosure.

    So the assertion inverted. The risk worth guarding is **wrong** information,
    not missing information: a page that says nothing cannot go stale, but a page
    that says "14 days" while the cookie lives 90 is telling readers something
    false. That is exactly what happened before — the 14 was Starlette's default,
    which nobody had chosen, and the page had faithfully copied it.

    **It passes vacuously today**, and that is the intended state rather than a
    gap. It arms itself the moment anyone reintroduces the sentence. Known limit:
    it only recognises the phrasing the page previously used, so "lasts ninety
    days" would slip past — broadening the pattern is not worth the false
    positives it would invite on unrelated day counts.
    """
    prose = " ".join(
        (await client.get("/privacy", follow_redirects=False)).text.split()
    ).lower()

    stated = _COOKIE_LIFETIME_CLAIM.search(prose)
    if stated is None:
        return

    assert int(stated.group(1)) == SESSION_MAX_AGE_SECONDS // SECONDS_PER_DAY, (
        f"the page claims the cookie lasts {stated.group(1)} days, but it lasts "
        f"{SESSION_MAX_AGE_SECONDS // SECONDS_PER_DAY}"
    )


def test_the_lifetime_is_a_whole_number_of_days() -> None:
    """A lifetime that is not whole days cannot be stated on the page accurately.

    Keeps the check above meaningful: at, say, 90.5 days the floor division would
    quietly compare against 90 and a page saying "90 days" would be wrong by half
    a day while everything passed.
    """
    assert SESSION_MAX_AGE_SECONDS % SECONDS_PER_DAY == 0
