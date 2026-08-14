"""Where a visitor came from, and the switch that keeps it turned off.

The first test here is the deploy gate. Merging this branch deploys it — Railway
auto-deploys from main — so the flag is the only thing standing between merging
and setting a tracking cookie on every visitor to a site with no privacy notice.
"""

from __future__ import annotations

import pytest
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.identity.first_touch import (
    CHANNEL_DIRECT,
    SESSION_KEY,
    FirstTouchMiddleware,
    _referrer_host,
    build_first_touch,
)
from app.main import create_app


@pytest.fixture
def capture_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "first_touch_capture_enabled", True)


@pytest.fixture
def capture_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "first_touch_capture_enabled", False)


@pytest.mark.asyncio
async def test_no_cookie_is_set_while_capture_is_off(reset_db, client, capture_off) -> None:
    """THE DEPLOY GATE.

    Today an anonymous visitor gets no cookie at all: nothing writes to the
    session until sign-in, and Starlette only sets the cookie when the session
    changes. With the flag off that must stay exactly true, campaign tag or not.
    """
    response = await client.get("/?utm_source=hermesagent&utm_medium=social")

    assert response.status_code in (200, 307, 303)
    assert "set-cookie" not in {k.lower() for k in response.headers.keys()}, (
        "capture is off, so this request must not start a session cookie"
    )


@pytest.mark.asyncio
async def test_capture_records_the_campaign_when_on(reset_db, client, capture_on) -> None:
    await client.get("/?utm_source=hermesagent&utm_medium=social&utm_campaign=alpha")
    # The value rides in the session cookie, so a second request sees it.
    assert client.cookies.get("hhh_session") is not None


def test_first_touch_record_holds_the_campaign() -> None:
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"utm_source=hermesagent&utm_medium=social",
        "headers": Headers({"host": "agentludum.com"}).raw,
        "server": ("agentludum.com", 443),
        "scheme": "https",
    }
    record = build_first_touch(Request(scope))

    assert record["utm_source"] == "hermesagent"
    assert record["utm_medium"] == "social"
    assert record["landing_path"] == "/"
    # A campaign tag was found, so this is not "direct".
    assert record["channel"] is None


def test_a_visit_with_nothing_to_attribute_is_recorded_as_direct() -> None:
    """"direct" and "never captured" must not be the same thing.

    Folding them together makes the biggest line on the sources table silently
    mean "we failed to record this".
    """
    from starlette.datastructures import Headers
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": Headers({"host": "agentludum.com"}).raw,
        "server": ("agentludum.com", 443),
        "scheme": "https",
    }
    assert build_first_touch(Request(scope))["channel"] == CHANNEL_DIRECT


def test_internal_navigation_is_not_a_traffic_source() -> None:
    assert _referrer_host("https://reddit.com/r/x", "agentludum.com") == "reddit.com"
    assert _referrer_host("https://agentludum.com/guide", "agentludum.com") is None
    assert _referrer_host(None, "agentludum.com") is None
    assert _referrer_host("not-a-url", "agentludum.com") is None


def test_referrer_never_keeps_credentials_or_a_port() -> None:
    """The privacy page promises the site NAME, not the address. Pin that.

    Reading the netloc instead of the hostname keeps whatever is in front of the
    @ sign, so a referrer carrying HTTP basic-auth credentials would have stored
    a username and password in our database and rendered them on an admin page.
    """
    assert (
        _referrer_host("https://alice:hunter2@intranet.example.com/secret", "agentludum.com")
        == "intranet.example.com"
    ), "credentials in the referrer must never be stored"
    assert _referrer_host("https://reddit.com:8443/r/x", "agentludum.com") == "reddit.com", (
        "the port is not part of the site's name, and splits one source into two rows"
    )


def test_our_own_site_on_a_nonstandard_port_is_still_not_a_source() -> None:
    """Dev and preview run on a port; production does not.

    With the port left on, the self-comparison failed everywhere except 443 — so
    the site recorded itself as its own traffic source in every local session.
    """
    assert _referrer_host("http://127.0.0.1:8766/games", "127.0.0.1") is None
    assert _referrer_host("http://localhost:8000/guide", "localhost") is None


def test_values_are_capped_before_they_reach_the_cookie() -> None:
    """The session is a signed cookie with a hard size limit, not server storage."""
    from starlette.datastructures import Headers
    from starlette.requests import Request

    long_value = "x" * 500
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/" + long_value,
        "query_string": f"utm_source={long_value}".encode(),
        "headers": Headers({"host": "agentludum.com"}).raw,
        "server": ("agentludum.com", 443),
        "scheme": "https",
    }
    record = build_first_touch(Request(scope))

    assert len(record["utm_source"]) == 120
    assert len(record["landing_path"]) == 255


def test_capture_middleware_runs_inside_the_session_middleware() -> None:
    """The ordering the spec calls the most likely silent failure in the feature.

    add_middleware inserts at the front, so the LAST added is the OUTERMOST.
    FirstTouchMiddleware must therefore appear AFTER SessionMiddleware in the
    resulting list — that is what puts it inside, where request.session exists.
    Reverse them and capture records nothing forever, with every test still green.
    """
    app = create_app()
    classes = [middleware.cls for middleware in app.user_middleware]

    assert FirstTouchMiddleware in classes
    assert SessionMiddleware in classes
    assert classes.index(FirstTouchMiddleware) > classes.index(SessionMiddleware), (
        "FirstTouchMiddleware must be INSIDE SessionMiddleware, or request.session "
        "does not exist when it runs"
    )


@pytest.mark.asyncio
async def test_signing_out_clears_the_recorded_source(capture_on) -> None:
    """Otherwise the next person to sign in on this browser inherits it."""
    from starlette.requests import Request

    from app.auth.session import clear_session

    scope: dict = {"type": "http", "session": {"user_id": 1, SESSION_KEY: {"a": 1}}}
    clear_session(Request(scope))

    assert "user_id" not in scope["session"]
    assert SESSION_KEY not in scope["session"]
