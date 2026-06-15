"""The canonical-host middleware refuses the Railway domain in real deployments."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.canonical_host import CanonicalHostMiddleware, canonical_host_of

_RAILWAY = "hoard-hurt-help-production.up.railway.app"
_CANONICAL = "agentludum.com"


def _client(*, enabled: bool, host: str) -> TestClient:
    async def ok(request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", ok), Route("/healthz", ok)])
    app.add_middleware(
        CanonicalHostMiddleware, canonical_host=_CANONICAL, enabled=enabled
    )
    # base_url drives the Host header TestClient sends.
    return TestClient(app, base_url=f"https://{host}")


def test_canonical_host_of_parses_hostname() -> None:
    assert canonical_host_of("https://agentludum.com") == "agentludum.com"
    assert canonical_host_of("https://AgentLudum.com:443/path") == "agentludum.com"
    assert canonical_host_of("") is None


def test_allows_the_canonical_host() -> None:
    r = _client(enabled=True, host=_CANONICAL).get("/")
    assert r.status_code == 200


def test_refuses_a_non_canonical_host() -> None:
    r = _client(enabled=True, host=_RAILWAY).get("/")
    assert r.status_code == 421
    assert _CANONICAL in r.text


def test_healthz_is_allowed_even_on_the_wrong_host() -> None:
    # Railway's deploy health check can arrive on the Railway domain.
    r = _client(enabled=True, host=_RAILWAY).get("/healthz")
    assert r.status_code == 200


def test_disabled_lets_any_host_through() -> None:
    # Off outside a real deployment, so local dev and tests are unaffected.
    r = _client(enabled=False, host=_RAILWAY).get("/")
    assert r.status_code == 200
