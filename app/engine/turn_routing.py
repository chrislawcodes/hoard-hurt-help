"""Provider-aware turn routing helpers.

This module stays DB-free so the sticky routing rules can be unit tested in
isolation. The route layer will map database rows into these snapshots and use
the same eligibility logic when it is wired in later.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping, Sequence

from app.aware_datetime import ensure_aware
from app.engine.connection_health import LIVE_WINDOW_SECONDS
from app.models.connection import ConnectionProvider


def _provider_value(provider: str | ConnectionProvider) -> str:
    """Normalize provider enum values and raw strings to a plain slug."""
    return provider.value if isinstance(provider, ConnectionProvider) else provider


@dataclass(frozen=True, slots=True)
class ConnectionRouteState:
    """Snapshot of one live connection used by the routing helper."""

    connection_id: int
    enabled_providers: frozenset[str]
    paused: bool = False
    deleted: bool = False
    last_seen_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TurnPin:
    """Sticky pin stored on a player row."""

    served_by_connection_id: int | None
    served_pinned_at: datetime | None


@dataclass(frozen=True, slots=True)
class TurnClaimResult:
    """Result of a claim attempt."""

    claimed: bool
    pin: TurnPin


def connection_is_dead(connection: ConnectionRouteState, *, now: datetime | None = None) -> bool:
    """Return True when the connection should be treated as dead for failover."""
    now = now or datetime.now(timezone.utc)
    last_seen = connection.last_seen_at
    if connection.paused or connection.deleted:
        return True
    if last_seen is None:
        return True
    return (now - ensure_aware(last_seen)).total_seconds() > LIVE_WINDOW_SECONDS


def connection_covers_provider(
    connection: ConnectionRouteState, provider: str | ConnectionProvider
) -> bool:
    """Return True when the connection has that provider enabled."""
    return _provider_value(provider) in connection.enabled_providers


def can_connection_claim_turn(
    connection: ConnectionRouteState,
    provider: str | ConnectionProvider | None,
    pin: TurnPin,
    *,
    now: datetime | None = None,
    connections_by_id: Mapping[int, ConnectionRouteState] | None = None,
) -> bool:
    """Return True when this connection may claim the player's next turn.

    Agents are no longer tied to a provider: any of the user's live connections
    may serve any of the user's agents. Pass ``provider=None`` for that
    provider-agnostic routing. (A concrete ``provider`` still gates on the
    connection covering it — kept for the routing unit tests and any future
    provider-scoped use.)

    The sticky rule is:
    - no pin, or
    - already pinned to this connection, or
    - the pinned connection is dead.
    """
    if connection_is_dead(connection, now=now):
        return False
    if provider is not None and not connection_covers_provider(connection, provider):
        return False

    pinned_connection_id = pin.served_by_connection_id
    if pinned_connection_id is None or pinned_connection_id == connection.connection_id:
        return True

    if connections_by_id is None:
        return False
    pinned_connection = connections_by_id.get(pinned_connection_id)
    return pinned_connection is None or connection_is_dead(pinned_connection, now=now)


def eligible_connection_ids(
    connections: Sequence[ConnectionRouteState],
    provider: str | ConnectionProvider,
    *,
    pin: TurnPin | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Return the live connections that can currently serve this provider.

    The result is sorted by connection id so the fallback choice is
    deterministic when more than one live connection can cover the provider.
    """
    pin = pin or TurnPin(None, None)
    now = now or datetime.now(timezone.utc)
    by_id = {connection.connection_id: connection for connection in connections}
    eligible = [
        connection.connection_id
        for connection in sorted(connections, key=lambda connection: connection.connection_id)
        if can_connection_claim_turn(
            connection,
            provider,
            pin,
            now=now,
            connections_by_id=by_id,
        )
    ]
    return eligible


def choose_connection_id_for_provider(
    connections: Sequence[ConnectionRouteState],
    provider: str | ConnectionProvider,
    *,
    pin: TurnPin | None = None,
    now: datetime | None = None,
) -> int | None:
    """Pick the sticky connection for this provider, or None if nobody qualifies."""
    eligible = eligible_connection_ids(connections, provider, pin=pin, now=now)
    if not eligible:
        return None
    pin_connection_id = None if pin is None else pin.served_by_connection_id
    if pin_connection_id in eligible:
        return pin_connection_id
    return eligible[0]


class TurnPinClaimStore:
    """Small in-memory store that models the atomic pin claim behavior.

    The production route will use a conditional UPDATE. This store gives the
    same semantics for unit tests without needing a database.
    """

    def __init__(
        self,
        connections: Sequence[ConnectionRouteState],
        pin: TurnPin | None = None,
    ) -> None:
        self._connections = {connection.connection_id: connection for connection in connections}
        self._pin = pin or TurnPin(None, None)
        self._lock = asyncio.Lock()

    @property
    def pin(self) -> TurnPin:
        return self._pin

    def eligible_connection_ids(
        self,
        provider: str | ConnectionProvider,
        *,
        now: datetime | None = None,
    ) -> list[int]:
        return eligible_connection_ids(
            list(self._connections.values()), provider, pin=self._pin, now=now
        )

    async def try_claim(
        self,
        connection_id: int,
        provider: str | ConnectionProvider,
        *,
        now: datetime | None = None,
    ) -> TurnClaimResult:
        """Attempt to claim the pin for one connection.

        Exactly one concurrent claimant wins when the pin is unset. If the pin is
        already owned by another live connection, this returns a no-op failure.
        """
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            connection = self._connections[connection_id]
            if not can_connection_claim_turn(
                connection,
                provider,
                self._pin,
                now=now,
                connections_by_id=self._connections,
            ):
                return TurnClaimResult(False, self._pin)

            self._pin = TurnPin(connection_id, now)
            return TurnClaimResult(True, self._pin)
