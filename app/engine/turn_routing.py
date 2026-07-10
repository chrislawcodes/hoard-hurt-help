"""Provider-aware turn routing helpers.

This module stays DB-free so the sticky routing rules can be unit tested in
isolation: it defines the connection/pin snapshots and the eligibility
predicates (``connection_is_dead``, ``connection_covers_provider``,
``can_connection_claim_turn``). The production claim path in
``app/engine/agent_play_next_turn.py`` (``_claim_pin`` /
``_build_candidate_lookups``) maps its own database rows into
``ConnectionRouteState``/``TurnPin`` and calls ``can_connection_claim_turn`` to
decide eligibility, then performs the actual claim as a conditional SQL
``UPDATE`` rather than through any in-memory store here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Mapping

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
