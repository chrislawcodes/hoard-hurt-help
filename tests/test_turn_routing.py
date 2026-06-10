"""Unit tests for the sticky provider-aware turn routing helper."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.engine.turn_routing import (
    ConnectionRouteState,
    TurnPin,
    TurnPinClaimStore,
    can_connection_claim_turn,
    choose_connection_id_for_provider,
    eligible_connection_ids,
)
from app.models.connection import ConnectionProvider

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
WARM = NOW - timedelta(seconds=20)
COLD = NOW - timedelta(minutes=10)


def _connection(
    connection_id: int,
    providers: set[str],
    *,
    paused: bool = False,
    deleted: bool = False,
    last_seen_at: datetime | None = WARM,
) -> ConnectionRouteState:
    return ConnectionRouteState(
        connection_id=connection_id,
        enabled_providers=frozenset(providers),
        paused=paused,
        deleted=deleted,
        last_seen_at=last_seen_at,
    )


def test_eligible_when_provider_is_enabled_and_pin_is_empty() -> None:
    connections = [
        _connection(1, {"claude"}),
        _connection(2, {"openai"}),
    ]

    eligible = eligible_connection_ids(connections, ConnectionProvider.CLAUDE, now=NOW)

    assert eligible == [1]
    assert (
        choose_connection_id_for_provider(
            connections, ConnectionProvider.CLAUDE, now=NOW
        )
        == 1
    )


def test_ineligible_when_provider_is_not_enabled_on_any_live_connection() -> None:
    connections = [_connection(1, {"openai"})]

    assert eligible_connection_ids(connections, "claude", now=NOW) == []
    assert choose_connection_id_for_provider(connections, "claude", now=NOW) is None


def test_sticky_pin_keeps_the_same_connection() -> None:
    connections = [
        _connection(1, {"claude"}),
        _connection(2, {"claude"}),
    ]
    pin = TurnPin(served_by_connection_id=1, served_pinned_at=NOW)

    eligible = eligible_connection_ids(connections, "claude", pin=pin, now=NOW)

    assert eligible == [1]
    assert choose_connection_id_for_provider(connections, "claude", pin=pin, now=NOW) == 1
    assert can_connection_claim_turn(
        connections[0],
        "claude",
        pin,
        now=NOW,
        connections_by_id={c.connection_id: c for c in connections},
    )


def test_failover_when_pinned_connection_is_dead() -> None:
    connections = [
        _connection(1, {"claude"}, last_seen_at=COLD),
        _connection(2, {"claude"}),
    ]
    pin = TurnPin(served_by_connection_id=1, served_pinned_at=NOW)

    eligible = eligible_connection_ids(
        connections, ConnectionProvider.CLAUDE, pin=pin, now=NOW
    )

    assert eligible == [2]
    assert (
        choose_connection_id_for_provider(
            connections, ConnectionProvider.CLAUDE, pin=pin, now=NOW
        )
        == 2
    )
    assert can_connection_claim_turn(
        connections[1],
        ConnectionProvider.CLAUDE,
        pin,
        now=NOW,
        connections_by_id={c.connection_id: c for c in connections},
    )


def test_zero_coverage_returns_no_candidate() -> None:
    connections = [
        _connection(1, {"openai"}),
        _connection(2, {"gemini"}),
    ]

    assert eligible_connection_ids(connections, "claude", now=NOW) == []
    assert choose_connection_id_for_provider(connections, "claude", now=NOW) is None


@pytest.mark.asyncio
async def test_two_simultaneous_claims_only_allow_one_winner() -> None:
    store = TurnPinClaimStore(
        [
            _connection(1, {"claude"}),
            _connection(2, {"claude"}),
        ]
    )

    results = await asyncio.gather(
        store.try_claim(1, "claude", now=NOW),
        store.try_claim(2, "claude", now=NOW),
    )

    assert sorted(result.claimed for result in results) == [False, True]
    assert store.pin.served_by_connection_id in {1, 2}
    assert store.pin.served_pinned_at == NOW
