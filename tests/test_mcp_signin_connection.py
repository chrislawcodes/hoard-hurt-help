"""Sign-in connection bootstrap: the /me/connections page should flip to
"connected" as soon as a user signs in via OAuth — at the token exchange
(_ConnectAtSignInGoogleProvider) and again when a client initializes its MCP
session (SigninConnectionMiddleware) — not only after the first tool call.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastmcp.server.dependencies import AccessToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models import Base
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from mcp_server import connection_identity, server, signin_middleware


def _token(*, sub: str = "sub-123", email: str = "agent@example.com") -> AccessToken:
    return AccessToken(
        token="access-token-1",
        client_id=sub,
        scopes=["openid", "email", "profile"],
        subject=sub,
        claims={
            "sub": sub,
            "email": email,
            "name": "Agent One",
            "email_verified": True,
        },
    )


@pytest.fixture
async def db_session_factory(
    engine: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield session_factory


async def test_signin_creates_active_connection_without_counting_a_call(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A fresh sign-in yields a live, agent-ready connection — and the handshake
    is not billed as an API call (that is what separates it from a tool call)."""
    async with db_session_factory() as db:
        _access, _userinfo, connection = await server._connection_from_token(
            db, _token(), provider=ConnectionProvider.GEMINI
        )
        await db.commit()

        # Live + connected -> the page shows "connected", not "waiting".
        assert connection.status == ConnectionStatus.ACTIVE
        assert connection.first_connected_at is not None
        # The handshake is not a paid model inference.
        assert connection.api_call_count == 0

        # Only the connecting client's provider is enabled (one client ==
        # one provider), so the user can create that provider's agents.
        provider_rows = (
            (
                await db.execute(
                    select(ConnectionProviderRow).where(
                        ConnectionProviderRow.connection_id == connection.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert {(r.provider, r.enabled) for r in provider_rows} == {
            (ConnectionProvider.GEMINI, True)
        }


async def test_tool_path_still_records_the_call(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tool path records the authenticated call (cost signal), unlike the
    sign-in handshake. The client is identified by its DCR client_id (read from
    the raw bearer JWT), which is stable per client across stateless-HTTP requests.
    The initialize path creates the connection; tool calls look it up via that id
    and record the call."""
    tok = _token()
    DCR_ID = "dcr-uuid-gemini"
    monkeypatch.setattr(connection_identity, "_dcr_client_id_from_request", lambda: DCR_ID)
    async with db_session_factory() as db:
        # Simulate initialize: create the connection with provider + DCR id.
        await server._connection_from_token(
            db, tok, provider=ConnectionProvider.GEMINI, oauth_client_id=DCR_ID
        )
        await db.commit()

    async with db_session_factory() as db:
        # Simulate a tool call: _resolve_oauth_connection reads the DCR id.
        _access, _userinfo, connection = await server._resolve_oauth_connection(db, tok)
        await db.commit()
        refreshed = (
            await db.execute(select(Connection).where(Connection.id == connection.id))
        ).scalar_one()
        assert refreshed.status == ConnectionStatus.ACTIVE
        assert refreshed.provider is ConnectionProvider.GEMINI
        assert refreshed.api_call_count == 1


async def test_tool_call_routes_to_the_matching_client_not_another(
    db_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for spec 016's real bug: one user, two MCP clients.

    Each client has its own DCR client_id, so a Codex tool call must resolve to
    the Codex (openai) connection — never collapse onto the Gemini one. Before the
    fix, both clients shared the Google subject as the lookup key, so the second
    client's traffic was silently routed into the first client's connection."""
    tok = _token()
    GEMINI_DCR = "dcr-uuid-gemini"
    CODEX_DCR = "dcr-uuid-codex"

    # Two initialize handshakes — one per client — create two connections.
    monkeypatch.setattr(
        connection_identity, "_dcr_client_id_from_request", lambda: GEMINI_DCR
    )
    async with db_session_factory() as db:
        await server._connection_from_token(
            db, tok, provider=ConnectionProvider.GEMINI, oauth_client_id=GEMINI_DCR
        )
        await db.commit()
    monkeypatch.setattr(
        connection_identity, "_dcr_client_id_from_request", lambda: CODEX_DCR
    )
    async with db_session_factory() as db:
        await server._connection_from_token(
            db, tok, provider=ConnectionProvider.OPENAI, oauth_client_id=CODEX_DCR
        )
        await db.commit()

    # A Codex tool call (CODEX_DCR on the request) must hit the openai connection.
    async with db_session_factory() as db:
        _a, _u, connection = await server._resolve_oauth_connection(db, tok)
        await db.commit()
        assert connection.provider is ConnectionProvider.OPENAI
        assert connection.oauth_client_id == CODEX_DCR


async def test_signin_is_idempotent_one_connection_per_user(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Repeated sign-ins (e.g. reconnects) reuse the same connection, never
    spawning duplicates."""
    async with db_session_factory() as db:
        _a1, _u1, first = await server._connection_from_token(
            db, _token(), provider=ConnectionProvider.GEMINI
        )
        await db.commit()
        _a2, _u2, second = await server._connection_from_token(
            db, _token(), provider=ConnectionProvider.GEMINI
        )
        await db.commit()

        assert first.id == second.id
        live = (
            (
                await db.execute(
                    select(Connection).where(
                        Connection.deleted_at.is_(None),
                        Connection.mcp_connected_at.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(live) == 1


async def test_initialize_middleware_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bootstrap failure must not break the session — initialize still runs."""
    monkeypatch.setattr(signin_middleware, "get_access_token", lambda: _token())

    async def _boom(token: object, provider: object) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(connection_identity, "_bootstrap_signin_connection", _boom)

    seen = {}

    async def _call_next(context: object) -> str:
        seen["called"] = True
        return "initialized"

    class _Ctx:
        message = None

    middleware = server.SigninConnectionMiddleware()
    result = await middleware.on_initialize(_Ctx(), _call_next)  # type: ignore[arg-type]

    assert result == "initialized"
    assert seen["called"] is True


async def test_initialize_middleware_skips_when_unauthenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No token (discovery / pre-auth) -> no bootstrap attempt, session proceeds."""
    monkeypatch.setattr(signin_middleware, "get_access_token", lambda: None)

    bootstrap_called = {"value": False}

    async def _bootstrap(token: object, provider: object) -> None:
        bootstrap_called["value"] = True

    monkeypatch.setattr(connection_identity, "_bootstrap_signin_connection", _bootstrap)

    async def _call_next(context: object) -> str:
        return "initialized"

    middleware = server.SigninConnectionMiddleware()
    result = await middleware.on_initialize(object(), _call_next)  # type: ignore[arg-type]

    assert result == "initialized"
    assert bootstrap_called["value"] is False


# --- connect-at-sign-in (OAuth token-exchange hook) ------------------------


def _make_id_token(*, sub: str = "sub-777", email: str = "owner@example.com") -> str:
    """A Google-shaped id_token. Only the payload segment is read (unsigned)."""

    def seg(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    header = seg({"alg": "RS256", "typ": "JWT"})
    payload = seg(
        {"sub": sub, "email": email, "email_verified": True, "name": "Owner"}
    )
    return f"{header}.{payload}.signature"


def test_decode_jwt_claims_reads_payload() -> None:
    claims = server._decode_jwt_claims(_make_id_token(sub="abc", email="x@y.com"))
    assert claims["sub"] == "abc"
    assert claims["email"] == "x@y.com"


def test_userinfo_from_claims_requires_email() -> None:
    with pytest.raises(RuntimeError):
        server._userinfo_from_claims({"sub": "abc"})


async def test_signin_hook_syncs_user_without_creating_a_connection(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The token-exchange hook syncs the signed-in user but creates NO connection.

    Each provider gets its own MCP connection, and at sign-in we don't yet know
    which AI client (provider) is connecting. The connection is created a moment
    later, at the MCP initialize handshake, where ``clientInfo`` names the provider.
    """
    async with db_session_factory() as db:
        user = await server._sync_signin_user(db, {"id_token": _make_id_token()})
        assert user is not None
        await db.commit()

        # No connection is born at sign-in — the provider isn't known yet.
        connections = (await db.execute(select(Connection))).scalars().all()
        assert connections == []


async def test_signin_hook_skips_when_no_id_token(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A refresh-token exchange (no id_token) is a no-op, not an error."""
    async with db_session_factory() as db:
        result = await server._sync_signin_user(db, {"access_token": "x"})
        assert result is None


def _initialize_message(name: str | None) -> SimpleNamespace:
    """A stand-in for the MCP initialize request carrying a client's name."""
    return SimpleNamespace(params=SimpleNamespace(clientInfo=SimpleNamespace(name=name)))


def test_client_provider_from_initialize_maps_the_connecting_client() -> None:
    # The real string Gemini CLI sends on the handshake -> the Gemini provider.
    msg = _initialize_message("gemini-cli-mcp-client")
    assert server._client_provider_from_initialize(msg) is ConnectionProvider.GEMINI


def test_client_provider_from_initialize_unknown_client_is_none() -> None:
    msg = _initialize_message("some-other-client")
    assert server._client_provider_from_initialize(msg) is None


def test_client_provider_from_initialize_is_fail_open_on_bad_message() -> None:
    # A message without clientInfo must not raise — it yields None (enable none).
    assert server._client_provider_from_initialize(object()) is None
    assert server._client_provider_from_initialize(_initialize_message(None)) is None


def test_auth_provider_skips_consent_and_hooks_signin() -> None:
    """The provider is our connect-at-sign-in subclass with the consent screen
    turned off."""
    provider = server._build_auth_provider()
    assert isinstance(provider, server._ConnectAtSignInGoogleProvider)
    assert provider._require_authorization_consent is False


def test_auth_provider_issues_long_lived_login_token() -> None:
    """Login tokens last 90 days, not Google's 1-hour default, so clients aren't
    forced to re-auth every hour / after every deploy."""
    provider = server._build_auth_provider()
    assert server._MCP_ACCESS_TOKEN_TTL_SECONDS == 90 * 24 * 60 * 60
    assert provider._fastmcp_access_token_expiry_seconds == 90 * 24 * 60 * 60
