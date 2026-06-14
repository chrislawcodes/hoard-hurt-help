"""Per-game payload hooks: PD payload unchanged; games that supply state surface it.

The agent turn payload now asks the game module for optional private/public game
state. PD supplies none, so its payload must not carry the keys at all (parity).
A game that returns state must serialize the keys.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.agent import CurrentTurn, TurnStatic, YourTurnResponse

_STATIC = TurnStatic(
    match_id="M_X",
    rules_version="v1",
    rules="...",
    base_prompt="...",
    total_rounds=7,
    turns_per_round=7,
    your_agent_id="A",
    all_agent_ids=["A", "B"],
)
_CURRENT = CurrentTurn(
    round=1, turn=1, deadline=datetime(2026, 6, 14, tzinfo=timezone.utc), turn_token="tk"
)


def test_pd_payload_omits_game_state_keys() -> None:
    """No state supplied (PD) → keys absent from the serialized payload."""
    payload = YourTurnResponse(static=_STATIC, history=[], scoreboard=[], current=_CURRENT)
    dumped = payload.model_dump(mode="json")
    assert "your_private_state" not in dumped
    assert "public_state" not in dumped
    assert set(dumped) == {"status", "static", "history", "scoreboard", "current"}


def test_game_state_surfaces_when_present() -> None:
    """A game that returns state → keys present in the serialized payload."""
    payload = YourTurnResponse(
        static=_STATIC,
        history=[],
        scoreboard=[],
        current=_CURRENT,
        your_private_state={"dice": [5, 5, 1]},
        public_state={"standing_bid": {"quantity": 3, "face": 5}},
    )
    dumped = payload.model_dump(mode="json")
    assert dumped["your_private_state"] == {"dice": [5, 5, 1]}
    assert dumped["public_state"] == {"standing_bid": {"quantity": 3, "face": 5}}
