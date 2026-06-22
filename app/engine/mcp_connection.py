"""MCP connection bootstrap helpers for OAuth/MCP play."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.connection_auth_loading import connection_user_load_options
from app.engine.tokens import bot_key_hint, bot_key_lookup, generate_connection_key
from app.models.connection import Connection, ConnectionProvider, ConnectionStatus
from app.models.connection_provider import ConnectionProvider as ConnectionProviderRow
from app.models.user import User

_MAX_ATTEMPTS = 3
# Serializes one user's bootstrap path inside this process; the partial unique
# index is still the source of truth for live-row uniqueness.
_USER_LOCKS: dict[int, asyncio.Lock] = {}


def _is_retryable_db_error(exc: Exception) -> bool:
    """True for the unique/locking races this helper is expected to absorb."""
    if isinstance(exc, IntegrityError):
        return True
    if isinstance(exc, OperationalError):
        return "locked" in str(getattr(exc, "orig", exc)).lower()
    return False


async def _ensure_mcp_connection_provider(
    db: AsyncSession,
    connection: Connection,
    provider: ConnectionProvider,
) -> None:
    """Mark this MCP connection as speaking for ``provider``.

    An MCP client speaks for exactly one AI provider (Gemini CLI is Gemini,
    Claude Code is Claude, and so on), and each provider the user signs in gets
    its OWN connection — so an MCP connection is single-provider. We set
    ``connection.provider`` (the connection's identity, and the column the
    one-per-(user, provider) unique index keys on) and keep one enabled
    ``connection_providers`` row so the coverage helpers — shared with machine
    connections — keep working unchanged.

    NOTE: the machine/connector path is deliberately different — one machine can
    run several CLIs, so it enables every provider it detects. That lives in
    ``app/routes/agent_next_turn.py`` and is intentionally untouched here.
    """
    connection.provider = provider
    row = (
        await db.execute(
            select(ConnectionProviderRow).where(
                ConnectionProviderRow.connection_id == connection.id,
                ConnectionProviderRow.provider == provider,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            ConnectionProviderRow(
                connection_id=connection.id,
                provider=provider,
                enabled=True,
                detected=False,
            )
        )
    else:
        row.enabled = True


async def _existing_mcp_connection(
    db: AsyncSession, user_id: int
) -> Connection | None:
    """The user's one live MCP connection, only if there is exactly one.

    Used as a fallback when we cannot tell which provider connected (an
    unidentified client). With several connections we cannot pick safely, so we
    return None rather than guess.
    """
    rows = (
        (
            await db.execute(
                select(Connection)
                .options(connection_user_load_options())
                .where(
                    Connection.user_id == user_id,
                    Connection.mcp_connected_at.is_not(None),
                    Connection.deleted_at.is_(None),
                )
                .order_by(Connection.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if len(rows) == 1 else None


async def _mcp_connection_once(
    db: AsyncSession,
    user_id: int,
    *,
    now: datetime,
    provider: ConnectionProvider | None,
    oauth_client_id: str | None = None,
) -> Connection | None:
    """Return the user's live MCP connection, creating or resurrecting it as needed.

    Lookup priority:
    1. ``oauth_client_id`` match — the stable per-registration key, works in
       stateless-HTTP mode where session memory is wiped between requests.
    2. ``provider`` match — used during ``initialize`` (when both are known) and
       as a fallback for clients that haven't re-initialized since the deploy.
    3. Single-connection fallback — only when provider is None and the user has
       exactly one live connection (keeps working for pre-deploy clients).

    ``provider`` is required to *create* a connection. When it is None and no
    ``oauth_client_id`` matches, we never create; see case 3 above.
    """
    if oauth_client_id is not None:
        live_by_client = (
            (
                await db.execute(
                    select(Connection)
                    .options(connection_user_load_options())
                    .where(
                        Connection.user_id == user_id,
                        Connection.oauth_client_id == oauth_client_id,
                        Connection.mcp_connected_at.is_not(None),
                        Connection.deleted_at.is_(None),
                    )
                    .order_by(Connection.id.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )
        if live_by_client is not None:
            if live_by_client.status != ConnectionStatus.PAUSED:
                live_by_client.last_seen_at = now
                if live_by_client.first_connected_at is None:
                    live_by_client.first_connected_at = now
                if live_by_client.status == ConnectionStatus.PENDING:
                    live_by_client.status = ConnectionStatus.ACTIVE
            return live_by_client

    if provider is None:
        return await _existing_mcp_connection(db, user_id)

    live_connection = (
        (
            await db.execute(
                select(Connection)
                .options(connection_user_load_options())
                .where(
                    Connection.user_id == user_id,
                    Connection.mcp_connected_at.is_not(None),
                    Connection.deleted_at.is_(None),
                    Connection.provider == provider,
                )
                .order_by(Connection.id.desc())
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if live_connection is not None:
        if live_connection.status != ConnectionStatus.PAUSED:
            live_connection.last_seen_at = now
            if live_connection.first_connected_at is None:
                live_connection.first_connected_at = now
            if live_connection.status == ConnectionStatus.PENDING:
                live_connection.status = ConnectionStatus.ACTIVE
        if oauth_client_id is not None and live_connection.oauth_client_id != oauth_client_id:
            live_connection.oauth_client_id = oauth_client_id
        await _ensure_mcp_connection_provider(db, live_connection, provider)
        return live_connection

    deleted_connection = (
        (
            await db.execute(
                select(Connection)
                .options(connection_user_load_options())
                .where(
                    Connection.user_id == user_id,
                    Connection.mcp_connected_at.is_not(None),
                    Connection.deleted_at.is_not(None),
                    Connection.provider == provider,
                )
                .order_by(Connection.deleted_at.desc(), Connection.id.desc())
                .limit(1)
            )
        )
        .scalar_one_or_none()
    )
    if deleted_connection is not None:
        deleted_connection.deleted_at = None
        deleted_connection.status = ConnectionStatus.ACTIVE
        deleted_connection.paused_at = None
        deleted_connection.paused_reason = None
        deleted_connection.runner_pid = None
        if deleted_connection.first_connected_at is None:
            deleted_connection.first_connected_at = now
        deleted_connection.last_seen_at = now
        if oauth_client_id is not None:
            deleted_connection.oauth_client_id = oauth_client_id
        await _ensure_mcp_connection_provider(db, deleted_connection, provider)
        return deleted_connection

    raw_key = generate_connection_key()
    connection = Connection(
        user_id=user_id,
        provider=provider,
        oauth_client_id=oauth_client_id,
        key_lookup=bot_key_lookup(raw_key),
        key_hint=bot_key_hint(raw_key),
        status=ConnectionStatus.ACTIVE,
        mcp_connected_at=now,
        first_connected_at=now,
        last_seen_at=now,
    )
    db.add(connection)
    await db.flush()
    await _ensure_mcp_connection_provider(db, connection, provider)
    return (
        (
            await db.execute(
                select(Connection)
                .options(connection_user_load_options())
                .where(Connection.id == connection.id)
            )
        )
        .scalar_one()
    )


async def mcp_connection_for(
    db: AsyncSession,
    user: User,
    *,
    provider: ConnectionProvider | None = None,
    oauth_client_id: str | None = None,
    now: datetime | None = None,
    max_attempts: int = _MAX_ATTEMPTS,
) -> Connection | None:
    """Return the user's MCP connection, creating it if new.

    Lookup priority: ``oauth_client_id`` (stable per-registration key, works in
    stateless-HTTP mode) → ``provider`` (used on initialize, fallback for old
    clients) → single-connection fallback (when provider is also None).

    ``provider`` is REQUIRED to create a new connection. ``oauth_client_id``
    is written to the row on every match so re-registrations stay current.

    Safe to call from concurrent OAuth callbacks: a partial unique index keeps
    one live row per (user, provider); retryable races re-read and converge.
    """
    resolved_now = now or datetime.now(timezone.utc)
    user_id = user.id
    lock = _USER_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        for attempt in range(max_attempts):
            try:
                async with db.begin_nested():
                    connection = await _mcp_connection_once(
                        db, user_id, now=resolved_now, provider=provider,
                        oauth_client_id=oauth_client_id,
                    )
                    await db.flush()
                    return connection
            except (IntegrityError, OperationalError) as exc:
                if not _is_retryable_db_error(exc) or attempt + 1 == max_attempts:
                    raise
    raise RuntimeError("mcp_connection_for retry loop exhausted")
