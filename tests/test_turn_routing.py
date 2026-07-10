"""Unit tests for the sticky provider-aware turn routing helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


from app.engine.turn_routing import (
    ConnectionRouteState,
    TurnPin,
    can_connection_claim_turn,
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
    connection = _connection(1, {"claude"})
    empty_pin = TurnPin(None, None)

    assert can_connection_claim_turn(
        connection, ConnectionProvider.CLAUDE, empty_pin, now=NOW
    )


def test_ineligible_when_provider_is_not_enabled_on_any_live_connection() -> None:
    connection = _connection(1, {"openai"})
    empty_pin = TurnPin(None, None)

    assert not can_connection_claim_turn(connection, "claude", empty_pin, now=NOW)


def test_sticky_pin_keeps_the_same_connection() -> None:
    connections = [
        _connection(1, {"claude"}),
        _connection(2, {"claude"}),
    ]
    pin = TurnPin(served_by_connection_id=1, served_pinned_at=NOW)

    assert can_connection_claim_turn(
        connections[0],
        "claude",
        pin,
        now=NOW,
        connections_by_id={c.connection_id: c for c in connections},
    )
    assert not can_connection_claim_turn(
        connections[1],
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

    assert can_connection_claim_turn(
        connections[1],
        ConnectionProvider.CLAUDE,
        pin,
        now=NOW,
        connections_by_id={c.connection_id: c for c in connections},
    )


def test_zero_coverage_returns_no_candidate() -> None:
    connection = _connection(1, {"openai"})
    empty_pin = TurnPin(None, None)

    assert not can_connection_claim_turn(connection, "claude", empty_pin, now=NOW)


def test_provider_none_skips_cover_check_so_any_live_connection_claims() -> None:
    """Provider-agnostic routing: with provider=None a live connection may claim a
    turn regardless of which providers it has enabled (agents are no longer tied
    to a provider). A dead connection still cannot claim."""
    live = _connection(1, {"gemini"})  # enabled providers are irrelevant now
    dead = _connection(2, {"claude"}, last_seen_at=COLD)
    empty_pin = TurnPin(None, None)

    assert can_connection_claim_turn(live, None, empty_pin, now=NOW) is True
    assert can_connection_claim_turn(dead, None, empty_pin, now=NOW) is False


def test_provider_none_respects_sticky_pin_to_a_live_connection() -> None:
    """With provider=None the sticky pin still wins: a turn pinned to a live
    connection can't be stolen by another connection."""
    a = _connection(1, set())
    b = _connection(2, set())
    pinned_to_a = TurnPin(served_by_connection_id=1, served_pinned_at=WARM)
    by_id = {1: a, 2: b}

    assert can_connection_claim_turn(a, None, pinned_to_a, now=NOW, connections_by_id=by_id) is True
    assert can_connection_claim_turn(b, None, pinned_to_a, now=NOW, connections_by_id=by_id) is False
