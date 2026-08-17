"""Key sign-in on /mcp: who it lets in, and everyone it must keep out.

/mcp is Google-sign-in-only unless a connection's owner opts in, so most of
these assert a *rejection*. The opt-in is the whole security boundary: if a key
authenticated without it, every existing connection would silently gain a second
way in.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models import Base
from app.models.connection import Connection, ConnectionStatus
from app.models.user import User
from fastmcp.server.auth.auth import AccessToken
from mcp_server import connection_identity, key_auth, oauth_auth, server
from tests.conftest import signed_in_cookies
from tests.factories import make_connection, make_user

# The scopes the live provider enforces. Read off the real provider rather than
# written out here: it rewrites "email"/"profile" into full Google URIs, so a
# copy in the test would pass while production 403s on insufficient_scope.
_REQUIRED_SCOPES = server.mcp_app.auth.required_scopes or []


async def _verify(raw_key: str) -> AccessToken | None:
    """Verify a key exactly as the provider does, scopes and all."""
    return await key_auth.verify_connection_key(raw_key, scopes=_REQUIRED_SCOPES)


@pytest.fixture
async def db_factory(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Schema on the in-memory engine, with key_auth pointed at it.

    ``key_auth`` opens its own session (it runs at the auth layer, before any
    request-scoped session exists), so the factory is patched on that module.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(key_auth, "SessionLocal", session_factory)
    yield session_factory


async def _make_connection(
    db: AsyncSession,
    *,
    key_signin: bool,
    email: str = "player@example.com",
    google_sub: str = "sub-key-1",
    status: ConnectionStatus = ConnectionStatus.ACTIVE,
    deleted: bool = False,
) -> tuple[Connection, str]:
    """Create a user + connection and return it with its plaintext key."""
    user = User(google_sub=google_sub, email=email, name="Key Player")
    db.add(user)
    await db.flush()

    raw_key = generate_connection_key()
    connection = Connection(
        user_id=user.id,
        nickname="antigravity",
        key_lookup=bot_key_lookup(raw_key),
        key_hint=bot_key_hint(raw_key),
        status=status,
        mcp_key_signin_enabled=key_signin,
    )
    if deleted:
        from datetime import datetime, timezone

        connection.deleted_at = datetime.now(timezone.utc)
    db.add(connection)
    await db.commit()
    return connection, raw_key


async def test_opted_in_key_authenticates(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_factory() as db:
        connection, raw_key = await _make_connection(db, key_signin=True)
        connection_id = connection.id

    token = await _verify(raw_key)

    assert token is not None
    assert token.claims is not None
    assert token.claims[key_auth.CONNECTION_ID_CLAIM] == connection_id


async def test_key_without_opt_in_is_rejected(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The default. A connection that never opted in must not authenticate."""
    async with db_factory() as db:
        _connection, raw_key = await _make_connection(db, key_signin=False)

    assert await _verify(raw_key) is None


async def test_unknown_key_is_rejected(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_factory() as db:
        await _make_connection(db, key_signin=True)

    assert await _verify(generate_connection_key()) is None


async def test_rotated_out_key_is_rejected(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reissuing a key is how a leaked one is revoked, so the old key must stop
    working on /mcp at once — unlike the plain HTTP API, which still honours it.
    """
    async with db_factory() as db:
        connection, old_key = await _make_connection(db, key_signin=True)
        new_key = generate_connection_key()
        connection.prev_key_lookup = connection.key_lookup
        connection.key_lookup = bot_key_lookup(new_key)
        connection.key_hint = bot_key_hint(new_key)
        await db.commit()

    assert await _verify(new_key) is not None
    assert await _verify(old_key) is None


async def test_deleted_connection_key_is_rejected(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with db_factory() as db:
        _connection, raw_key = await _make_connection(
            db, key_signin=True, deleted=True
        )

    assert await _verify(raw_key) is None


async def test_token_resolves_to_its_own_connection(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The claim names the connection, so a key can never land on another one —
    including another connection belonging to the same user."""
    async with db_factory() as db:
        first, first_key = await _make_connection(db, key_signin=True)
        first_id = first.id
        second_key = generate_connection_key()
        second = Connection(
            user_id=first.user_id,
            nickname="second",
            key_lookup=bot_key_lookup(second_key),
            key_hint=bot_key_hint(second_key),
            status=ConnectionStatus.ACTIVE,
            mcp_key_signin_enabled=True,
        )
        db.add(second)
        await db.commit()
        second_id = second.id

    assert first_id != second_id
    for raw_key, expected_id in ((first_key, first_id), (second_key, second_id)):
        token = await _verify(raw_key)
        assert token is not None
        async with db_factory() as db:
            _access, _userinfo, connection = await connection_identity._connection_from_token(
                db, token, provider=None
            )
        assert connection.id == expected_id


async def test_paused_connection_authenticates_then_fails_downstream(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Lifecycle rules live in assert_connection_usable, not in key_auth, so a
    paused connection gets the same "resume it to play" error as on the OAuth
    path rather than a bare 401."""
    async with db_factory() as db:
        _connection, raw_key = await _make_connection(
            db, key_signin=True, status=ConnectionStatus.PAUSED
        )

    token = await _verify(raw_key)
    assert token is not None

    async with db_factory() as db:
        with pytest.raises(HTTPException) as excinfo:
            await connection_identity._connection_from_token(db, token, provider=None)
    assert excinfo.value.status_code == 403
    # Assert the specific branch: a 403 alone would also pass if this tripped
    # ACCOUNT_DISABLED, which would mean the paused check never ran.
    assert excinfo.value.detail["error"]["code"] == "CONNECTION_PAUSED"


def test_only_connection_keys_take_the_key_path() -> None:
    """Routing is by prefix, so a JWT bearer never reaches the key verifier."""
    assert key_auth.looks_like_connection_key(generate_connection_key())
    assert not key_auth.looks_like_connection_key("eyJhbGciOiJIUzI1NiJ9.e30.sig")
    assert not key_auth.looks_like_connection_key("")


async def test_provider_dispatches_a_key_with_the_scopes_it_enforces(
    db_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Goes through the REAL dispatcher, which is the only thing that proves the
    wiring.

    Two failures hide from a test that calls ``verify_connection_key`` directly:
    the provider not routing keys to the key path at all, and it passing a
    hardcoded scope list instead of its own ``required_scopes``. The second one
    shipped once — the Google provider rewrites "email"/"profile" into full
    googleapis.com URIs, so short names authenticate and then 403 with
    insufficient_scope on every call. Asserting against the provider's own
    ``required_scopes`` here is not circular, because the value under test comes
    from ``verify_token`` rather than from the argument this test passed in.
    """
    async with db_factory() as db:
        _connection, raw_key = await _make_connection(db, key_signin=True)

    token = await server.mcp_app.auth.verify_token(raw_key)

    assert token is not None
    assert set(token.scopes) == set(server.mcp_app.auth.required_scopes or [])
    assert "https://www.googleapis.com/auth/userinfo.email" in token.scopes


async def test_provider_leaves_non_key_bearers_to_the_oauth_path(
    db_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A JWT must never reach the key verifier — that is what keeps the new path
    from touching sign-in for Claude Code, Codex and Claude Desktop."""
    calls: list[str] = []

    async def _record(raw_key: str, *, scopes: object) -> None:
        calls.append(raw_key)
        return None

    monkeypatch.setattr(oauth_auth, "verify_connection_key", _record)

    assert await server.mcp_app.auth.verify_token("eyJhbGciOiJIUzI1NiJ9.e30.sig") is None
    assert calls == []


# --- the toggle that is the whole gate -------------------------------------


async def test_new_connections_default_to_off(reset_db: async_sessionmaker) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        await db.commit()
        assert connection.mcp_key_signin_enabled is False


async def test_owner_can_turn_key_signin_on_and_off(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        await db.commit()
        connection_id, user_id = connection.id, user.id

    cookies = signed_in_cookies(user_id)
    for enabled in (True, False):
        response = await client.post(
            f"/me/connections/{connection_id}/mcp-key-signin?enabled={str(enabled).lower()}",
            cookies=cookies,
        )
        assert response.status_code == 303
        async with reset_db() as db:
            refreshed = (
                await db.execute(
                    select(Connection).where(Connection.id == connection_id)
                )
            ).scalar_one()
            assert refreshed.mcp_key_signin_enabled is enabled


async def test_detail_page_offers_the_switch_and_never_prints_a_real_key(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    """The connection page is the one render path that has a real Connection in
    scope, so it is the one place a live key could leak into HTML. The config
    block must stay a placeholder whether the switch is on or off."""
    async with reset_db() as db:
        user = await make_user(db)
        connection, raw_key = await make_connection(db, user)
        await db.commit()
        connection_id, user_id = connection.id, user.id

    cookies = signed_in_cookies(user_id)
    off = await client.get(f"/me/connections/{connection_id}", cookies=cookies)
    assert off.status_code == 200
    assert "Allow key sign-in on MCP" in off.text
    # Off: no config block to copy yet.
    assert "antigravity-config" not in off.text
    assert raw_key not in off.text

    await client.post(
        f"/me/connections/{connection_id}/mcp-key-signin?enabled=true", cookies=cookies
    )
    on = await client.get(f"/me/connections/{connection_id}", cookies=cookies)
    assert on.status_code == 200
    assert "antigravity-config" in on.text
    assert "YOUR_CONNECTION_KEY" in on.text
    assert raw_key not in on.text
    assert "sk_conn_" not in on.text


async def test_leak_warning_does_not_promise_a_blanket_instant_cutoff(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    """Rotation is graceful (``keep_old_overlap=True``), so the two doors revoke at
    different moments: /mcp drops the old key at once, while the HTTP agent API
    honours it until the new key's first call. This warning is what an owner reads
    after a key leak, so a blanket "stops working straight away" would send them
    away believing a still-live key was dead. Pin both halves."""
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(db, user)
        await db.commit()
        connection_id, user_id = connection.id, user.id

    page = await client.get(
        f"/me/connections/{connection_id}", cookies=signed_in_cookies(user_id)
    )
    assert page.status_code == 200
    # The retired claim, which was true only of /mcp but read as universal.
    assert "the old one stops working straight away" not in page.text
    # Both doors named, and the gap given an end condition the owner can act on.
    assert "kills the old key on <code>/mcp</code> straight away" in page.text
    assert "still works on the connector" in page.text
    assert "until something calls that API with the new key" in page.text


async def test_mcp_signed_in_connections_are_not_offered_the_switch(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    """An OAuth connection holds no key, so offering it key sign-in would be a
    dead end."""
    async with reset_db() as db:
        user = await make_user(db)
        connection, _key = await make_connection(
            db, user, mcp_connected_at=datetime.now(timezone.utc)
        )
        await db.commit()
        connection_id, user_id = connection.id, user.id

    page = await client.get(
        f"/me/connections/{connection_id}", cookies=signed_in_cookies(user_id)
    )
    assert page.status_code == 200
    assert "Allow key sign-in on MCP" not in page.text


async def test_another_user_cannot_turn_it_on(
    reset_db: async_sessionmaker, client: AsyncClient
) -> None:
    """The toggle is the security gate, so it has to be owner-only."""
    async with reset_db() as db:
        owner = await make_user(db, 1)
        intruder = await make_user(db, 2)
        connection, _key = await make_connection(db, owner)
        await db.commit()
        connection_id, intruder_id = connection.id, intruder.id

    response = await client.post(
        f"/me/connections/{connection_id}/mcp-key-signin?enabled=true",
        cookies=signed_in_cookies(intruder_id),
    )
    assert response.status_code == 404

    async with reset_db() as db:
        refreshed = (
            await db.execute(select(Connection).where(Connection.id == connection_id))
        ).scalar_one()
        assert refreshed.mcp_key_signin_enabled is False
