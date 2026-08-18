"""Per-game payload hooks: an empty state block is omitted, never sent as null.

The turn payload asks the game module for optional private/public game state
(`private_state_for` / `public_state_for`). A game that returns nothing for one of
them must leave that key OUT of the payload entirely rather than emit
`"public_state": null` — the payload has to stay byte-identical to what it was
before those hooks existed. PD is the game that exercises the empty side: it
returns pact values for `your_private_state` and `{}` for `public_state`, so one
served payload shows both halves of the gate at once.

This is asserted against the served payload (`/api/agent/next-turn`, whose only
builder is `agent_play_next_turn._build_turn_payload`), not against a response
model. It used to be checked through `YourTurnResponse`/`TurnStatic` in
`app/schemas/agent.py`, which no route ever served — so those models drifted out
of step with the builder (the last one still demanded a `rules` key the builder
had stopped emitting) and this test kept passing anyway.

The other half of the contract — a game that DOES return per-player state gets
the key, and no player ever sees another's — is covered live in
`tests/test_hidden_state_isolation.py`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.match import GameState
from tests.factories import make_match, make_turn, seat_player


async def test_empty_game_state_key_is_absent_not_null(client, reset_db) -> None:
    now = datetime.now(timezone.utc)
    async with reset_db() as db:
        match = await make_match(
            db,
            "M_0001",
            state=GameState.ACTIVE,
            scheduled_start=now - timedelta(minutes=5),
            started_at=now - timedelta(minutes=5),
            current_round=1,
            current_turn=1,
        )
        players = [await seat_player(db, match.id, f"AI_{i}", i=i) for i in range(2)]
        await make_turn(
            db,
            match.id,
            phase="act",
            resolved=False,
            opened_at=now,
            deadline_at=now + timedelta(seconds=60),
        )
        key = players[0]._test_key
        await db.commit()

    response = await client.get("/api/agent/next-turn", headers={"X-Connection-Key": key})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "your_turn"
    # PD's public_state_for returns {} -> the key must not be on the wire at all.
    assert "public_state" not in body
    # ...while the hook that DOES return something still lands, so the assertion
    # above is proving the gate, not an accidentally empty payload.
    assert body["your_private_state"]["pact_values"]
