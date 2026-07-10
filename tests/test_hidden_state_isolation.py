"""Hidden per-player state never leaks across players' channels.

A sequential, hidden-information game exposes per-player secret state via the
`private_state_for` hook and shared state via `public_state_for`. The turn
payload (the connection-scoped next-turn fan-out) must return ONLY the
requesting player's private state — a player must never see another player's
secret. This is the Liar's Dice "your dice are yours alone" guarantee, enforced
at the platform layer. Each seat is served through its OWN connection, so the
per-connection ownership itself is part of what keeps the secrets apart.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.games as registry
from app.db import make_engine
from app.engine.tokens import generate_turn_token
from app.games.base import BaseGameModule, GameConfig, GameTheme
from app.models import Base, Match, GameState, PlayerState, Player
from app.models.turn import Turn
from app.routes.agent_next_turn import router as agent_next_turn_router
from tests.factories import seat_player


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _HiddenStub(BaseGameModule):
    """A hidden-info game: each player holds a secret no one else may see.

    Sequential, so the fan-out serves the open turn only to the active actor;
    the test flips `active_seat` (this stub's stand-in for real turn order) to
    serve each seat in sequence, the way a real sequential game would.
    """

    game_type = "hidden-stub"

    def __init__(self) -> None:
        self.active_seat: str | None = None

    async def active_actors(
        self, db: Any, matches: list[Match]
    ) -> dict[str, str | None]:
        return {match.id: self.active_seat for match in matches}

    def config_defaults(self) -> GameConfig:
        return GameConfig(
            total_rounds=1, turns_per_round=16, per_turn_deadline_seconds=30,
            min_players=2, max_players=6, simultaneous=False,
        )

    def rules_text(self, total_rounds: int = 7, turns_per_round: int = 7) -> str:
        return "Hidden stub rules."

    def strategy_presets(self) -> list:
        return []

    def default_strategy(self) -> str:
        return "Hidden stub strategy."

    def agent_base_prompt(
        self, *, your_agent_id: str, all_agent_ids: list[str],
        total_rounds: int = 7, turns_per_round: int = 7,
    ) -> str:
        return "Hidden stub base prompt."

    def validate_move(self, move: dict[str, Any], *, your_agent_id: str, all_agent_ids: list[str]) -> None:
        return None

    def move_effect(self, action: str) -> tuple[int, int | None]:
        return (0, None)

    def theme(self) -> GameTheme:
        return GameTheme(key=self.game_type, vars={})

    async def private_state_for(self, db: Any, match: Match, player: Player) -> dict[str, Any]:
        ps = (
            await db.execute(
                select(PlayerState).where(
                    PlayerState.match_id == match.id, PlayerState.player_id == player.id
                )
            )
        ).scalar_one_or_none()
        return {"secret": ps.state_json["secret"]} if ps is not None else {}

    async def public_state_for(self, db: Any, match: Match, viewer: Player | None) -> dict[str, Any]:
        return {"shared": "everyone sees this"}


async def _serve_turn(client: AsyncClient, key: str) -> dict[str, Any]:
    """Serve the seat owning `key` its open turn over the next-turn endpoint."""
    r = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["status"] == "your_turn", payload
    return payload


async def test_private_state_never_leaks_across_players(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _HiddenStub()
    registry.register(stub)

    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # The next-turn endpoint (and its long-poll hold) resolves the DB through the
    # imported SessionLocal/engine symbols, so bind them to the test engine.
    monkeypatch.setattr("app.db.SessionLocal", factory)
    monkeypatch.setattr("app.db.engine", engine)

    async with factory() as db:
        match = Match(
            id="M_HID", name="hid", game="hidden-stub", state=GameState.ACTIVE,
            scheduled_start=_now(), total_rounds=1, turns_per_round=16,
            per_turn_deadline_seconds=30, current_round=1, current_turn=1,
        )
        db.add(match)
        await db.flush()
        a = await seat_player(db, match.id, "Alice", i=0)
        b = await seat_player(db, match.id, "Bob", i=1)
        alice_key, bob_key = a._test_key, b._test_key
        db.add(PlayerState(match_id=match.id, player_id=a.id, state_json={"secret": "ALICE_DICE_55613"}))
        db.add(PlayerState(match_id=match.id, player_id=b.id, state_json={"secret": "BOB_DICE_22244"}))
        # An open, unresolved act-phase turn so the fan-out serves "your_turn".
        db.add(Turn(
            match_id=match.id, round=1, turn=1, turn_token=generate_turn_token(),
            opened_at=_now(), deadline_at=_now() + timedelta(seconds=30), phase="act",
        ))
        await db.commit()

    test_app = FastAPI()
    test_app.include_router(agent_next_turn_router)
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Each seat is served through its OWN connection key, when it holds the
        # action (a sequential game's turn goes only to the active actor).
        stub.active_seat = "Alice"
        alice_payload = await _serve_turn(client, alice_key)
        stub.active_seat = "Bob"
        bob_payload = await _serve_turn(client, bob_key)

    # Each player sees ONLY their own secret.
    assert alice_payload["your_private_state"] == {"secret": "ALICE_DICE_55613"}
    assert bob_payload["your_private_state"] == {"secret": "BOB_DICE_22244"}

    # Public state is shared and identical.
    assert alice_payload["public_state"] == {"shared": "everyone sees this"}
    assert bob_payload["public_state"] == {"shared": "everyone sees this"}

    # The other player's secret appears NOWHERE in the serialized payload.
    assert "BOB_DICE_22244" not in json.dumps(alice_payload, default=str)
    assert "ALICE_DICE_55613" not in json.dumps(bob_payload, default=str)

    await engine.dispose()
