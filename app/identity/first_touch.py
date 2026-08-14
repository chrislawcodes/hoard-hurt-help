"""Remember where a visitor came from, so a signup can be attributed to it.

A visitor lands on ``/?utm_source=hermesagent``, reads the front page, clicks
around, and only then signs in. By the time the request reaches
``/auth/google/login`` the campaign tag is long gone from the URL, so capturing at
the sign-in route records nothing for anyone who did not sign in on their very
first click. It has to happen on the **first request of the session** and ride in
the session through the Google round trip.

**Off by default.** Recording this writes a session cookie for anonymous
visitors, which the site does not do today: nothing writes to the session until
someone signs in, and Starlette only sets the cookie when the session changes.
Pushing to main auto-deploys, so merging is not the moment to start tracking
visitors. ``FIRST_TOUCH_CAPTURE_ENABLED`` stays false until the site carries a
privacy note; with it off this middleware returns before touching the session and
the site behaves exactly as it does now.

**Ordering matters and fails silently.** ``add_middleware`` inserts at the front,
so the LAST middleware added is the OUTERMOST. This one must run *inside*
``SessionMiddleware`` — meaning its ``add_middleware`` call must appear *before*
the ``SessionMiddleware`` call in ``app/main.py``. Registered after, there is no
``request.session`` yet, capture records nothing forever, and nothing raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

logger = logging.getLogger(__name__)

SESSION_KEY = "first_touch"

# Not page views: assets, health checks, and the machine-facing surfaces. The
# OAuth and well-known paths are excluded too — a client arriving straight at
# them is not a visitor landing on the site.
_SKIP_PREFIXES = (
    "/static",
    "/healthz",
    "/api",
    "/mcp",
    "/sse",
    "/openapi.json",
    "/.well-known",
    "/auth",
)

# Capped to the database column widths, and capped HERE rather than at the
# database write: the session is a signed cookie with a hard ~4KB browser limit,
# so an over-long value would be a problem long before it reached a column.
_MAX_LENGTHS = {
    "utm_source": 120,
    "utm_medium": 120,
    "utm_campaign": 120,
    "referrer_host": 255,
    "landing_path": 255,
}

# Written when capture ran and found no campaign tag and no external referrer.
# Distinct from an absent record, which means capture never ran at all — without
# the difference, the largest row on the sources table silently means "we failed
# to record this".
CHANNEL_DIRECT = "direct"
CHANNEL_MCP = "mcp"


def _clip(value: str | None, key: str) -> str | None:
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed[: _MAX_LENGTHS[key]]


def _referrer_host(referer: str | None, own_host: str | None) -> str | None:
    """The referring site's host, or None for internal navigation.

    ``hostname``, not ``netloc``, and the difference is the whole point. ``netloc``
    keeps the userinfo and the port, so a referrer like
    ``https://alice:hunter2@intranet.example.com/x`` would store the username and
    password — exactly what the privacy policy promises we do not keep. ``hostname``
    is already lowercased and drops both.

    The port matters for a second reason: ``own_host`` below has no port, so with
    ``netloc`` the self-comparison failed on any non-443 address and the site
    recorded itself as its own traffic source in dev and preview.
    """
    if not referer:
        return None
    host = urlsplit(referer).hostname
    if not host:
        return None
    if own_host and host == own_host.lower():
        # Moving around our own site is not a traffic source.
        return None
    return host


def build_first_touch(request: Request) -> dict[str, Any]:
    """The record for this request. Always returns a channel, never an empty dict."""
    params = request.query_params
    captured = {
        "utm_source": _clip(params.get("utm_source"), "utm_source"),
        "utm_medium": _clip(params.get("utm_medium"), "utm_medium"),
        "utm_campaign": _clip(params.get("utm_campaign"), "utm_campaign"),
        "referrer_host": _clip(
            _referrer_host(request.headers.get("referer"), request.url.hostname),
            "referrer_host",
        ),
        "landing_path": _clip(request.url.path, "landing_path"),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    # "direct" only when we looked and found nothing to attribute.
    captured["channel"] = (
        None
        if (captured["utm_source"] or captured["referrer_host"])
        else CHANNEL_DIRECT
    )
    return captured


class FirstTouchMiddleware(BaseHTTPMiddleware):
    """Record where this session came from, once, on its first page view."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            self._maybe_capture(request)
        except Exception:
            # fail-open: advisory only — attribution is reporting, never part of
            # serving the page. A broken capture must not cost a visitor their
            # page view.
            logger.exception("first-touch capture failed path=%s", request.url.path)
        return await call_next(request)

    def _maybe_capture(self, request: Request) -> None:
        if not settings.first_touch_capture_enabled:
            # Returning before touching request.session is what keeps the site
            # cookie-free while this is off.
            return
        if request.method != "GET":
            return
        if any(request.url.path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return
        session = request.scope.get("session")
        if session is None:
            # No SessionMiddleware in the stack yet — see the ordering note in the
            # module docstring. Nothing useful to do, and nowhere to put it.
            return
        if SESSION_KEY in session:
            # First touch means first. Never overwrite.
            return
        session[SESSION_KEY] = build_first_touch(request)


def pop_first_touch(request: Request) -> dict[str, Any] | None:
    """Read the recorded first touch, if there is one."""
    session = request.scope.get("session")
    if not isinstance(session, dict):
        return None
    value = session.get(SESSION_KEY)
    return value if isinstance(value, dict) else None
