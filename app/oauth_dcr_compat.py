"""Accept OAuth client registrations that omit ``refresh_token``.

The MCP SDK's Dynamic Client Registration (DCR) handler rejects any client whose
``grant_types`` do not include BOTH ``authorization_code`` and ``refresh_token``
(and whose ``response_types`` omit ``code``), answering ``invalid_client_metadata``.
That is stricter than RFC 7591, where ``refresh_token`` is an optional grant type.
A standards-compliant client that registers for only the authorization-code flow —
e.g. Google's Antigravity IDE — is turned away during the silent registration
handshake, before the user ever reaches a sign-in screen.

Upstream bug (open, unfixed): https://github.com/jlowin/fastmcp/issues/2460

Until the SDK relaxes the check, this middleware normalizes the request at our own
front door: for ``POST /register`` only, it adds the grant / response types the
handler over-requires when the client left them out. Our OAuth proxy already
treats every registered client as supporting both grant types (it mints refresh
tokens regardless), so this only makes the client's recorded metadata match how
the server already behaves — it grants the client nothing it could not already do.

This is a pure-ASGI middleware (not ``BaseHTTPMiddleware``) so it never buffers
streaming MCP / SSE responses; it only ever reads the small JSON body of a
registration POST.

TODO: remove this once fastmcp#2460 is fixed and the pin is bumped past the
release that carries the fix.
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# The DCR registration endpoint, as mounted by the MCP app (its OAuth metadata
# advertises this as ``registration_endpoint``).
_REGISTRATION_PATH = "/register"
_REQUIRED_GRANT_TYPES = ("authorization_code", "refresh_token")
_REQUIRED_RESPONSE_TYPE = "code"


class OAuthRegistrationCompatMiddleware:
    """Fill in the grant / response types the SDK over-requires on DCR POSTs."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != _REGISTRATION_PATH
        ):
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
        patched = _normalize_registration_body(body)
        if patched is body:
            # Nothing to change — replay the original body untouched. We still
            # replay because reading the body above consumed the receive channel.
            await self.app(scope, _replay(body), send)
            return

        scope = dict(scope)
        scope["headers"] = _with_content_length(scope.get("headers", []), len(patched))
        await self.app(scope, _replay(patched), send)


async def _read_body(receive: Receive) -> bytes:
    """Drain the full request body from the ASGI ``receive`` channel."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] != "http.request":
            # e.g. http.disconnect — no (more) body to read.
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return b"".join(chunks)


def _normalize_registration_body(body: bytes) -> bytes:
    """Return ``body`` with the over-required grant / response types added.

    Returns the original object unchanged when nothing needs adding or the body
    is not the JSON object DCR expects — a malformed request is the SDK's to
    reject with a proper error, not ours to silently repair. We only act when the
    client *explicitly* sent a list that is missing a required value; an omitted
    field already defaults to a valid value inside the SDK.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(data, dict):
        return body

    changed = False
    grant_types = data.get("grant_types")
    if isinstance(grant_types, list):
        for required in _REQUIRED_GRANT_TYPES:
            if required not in grant_types:
                grant_types.append(required)
                changed = True
    response_types = data.get("response_types")
    if isinstance(response_types, list) and _REQUIRED_RESPONSE_TYPE not in response_types:
        response_types.append(_REQUIRED_RESPONSE_TYPE)
        changed = True

    if not changed:
        return body
    logger.info(
        "Relaxed DCR registration: added the grant / response types the MCP SDK "
        "over-requires for a client that registered only the authorization-code flow."
    )
    return json.dumps(data).encode("utf-8")


def _with_content_length(
    headers: list[tuple[bytes, bytes]], length: int
) -> list[tuple[bytes, bytes]]:
    """Replace the Content-Length header so it matches the rewritten body."""
    out = [(name, value) for (name, value) in headers if name.lower() != b"content-length"]
    out.append((b"content-length", str(length).encode("ascii")))
    return out


def _replay(body: bytes) -> Receive:
    """A one-shot ``receive`` that yields ``body`` as a single ASGI message."""
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            # The app over-read; report end-of-body rather than block forever.
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive
