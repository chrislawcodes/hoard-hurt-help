"""Refuse requests on a non-canonical host in real deployments.

The app answers on its Railway-assigned domain (``*.up.railway.app``) as well as
the canonical public domain. OAuth / MCP sign-in only trusts the canonical host,
so a client that registers the Railway URL hits a confusing "protected resource
does not match" error the first time it tries to authenticate. To leave exactly
one correct address, we refuse any request whose ``Host`` is not the canonical
host — with one exception: the deploy health-check path, which Railway pings on
its own domain and which must always succeed or deploys roll back.

This is a pure-ASGI middleware on purpose (not ``BaseHTTPMiddleware``) so it never
buffers the streaming MCP / SSE responses mounted under this app.
"""

from __future__ import annotations

from urllib.parse import urlparse

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Railway's deploy health check (railway.json ``healthcheckPath``) can arrive on
# the Railway domain, so it must never be refused or deploys fail.
_ALWAYS_ALLOWED_PATHS = frozenset({"/healthz"})


def canonical_host_of(base_url: str) -> str | None:
    """The bare hostname of ``base_url`` (e.g. ``agentludum.com``), or None."""
    host = urlparse(base_url).hostname
    return host.lower() if host else None


def _request_host(scope: Scope) -> str:
    """The request's Host header, lowercased and without any port."""
    for key, value in scope.get("headers", []):
        if key == b"host":
            return value.decode("latin-1").split(":", 1)[0].strip().lower()
    return ""


class CanonicalHostMiddleware:
    """Refuse non-canonical hosts. Enabled only in real deployments."""

    def __init__(
        self, app: ASGIApp, *, canonical_host: str | None, enabled: bool
    ) -> None:
        self.app = app
        self.canonical_host = canonical_host
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            not self.enabled
            or self.canonical_host is None
            or scope["type"] != "http"
            or scope.get("path", "") in _ALWAYS_ALLOWED_PATHS
            or _request_host(scope) == self.canonical_host
        ):
            await self.app(scope, receive, send)
            return
        # Wrong host — refuse with the one correct address. 421 Misdirected Request
        # is the status designed for "you contacted the wrong host for this resource".
        response = PlainTextResponse(
            f"This service is at https://{self.canonical_host}. "
            "Point your connection at that address and reconnect.",
            status_code=421,
        )
        await response(scope, receive, send)
