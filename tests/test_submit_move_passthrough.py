"""Submit packs PD's action OR a free-form move into the generic move dict.

PD bots send action/target_id (unchanged). A non-PD game sends a free-form
`move` the platform passes through untouched. message/thinking ride along.
"""

from __future__ import annotations

from app.engine.agent_play import _pack_move
from app.schemas.agent import SubmitRequest


def test_pd_action_packs_unchanged() -> None:
    assert _pack_move(
        action="HELP", target_id="B", message="hi", thinking="", move=None
    ) == {"action": "HELP", "target_id": "B", "message": "hi", "thinking": ""}


def test_free_form_move_passes_through() -> None:
    assert _pack_move(
        action=None,
        target_id=None,
        message="swimming in fives",
        thinking="have two",
        move={"type": "BID", "quantity": 3, "face": 5},
    ) == {
        "type": "BID",
        "quantity": 3,
        "face": 5,
        "message": "swimming in fives",
        "thinking": "have two",
    }


def test_submit_request_accepts_both_shapes() -> None:
    pd = SubmitRequest(turn_token="tk", action="HOARD")
    assert pd.action == "HOARD" and pd.move is None
    ld = SubmitRequest(turn_token="tk", move={"type": "CHALLENGE"})
    assert ld.action is None and ld.move == {"type": "CHALLENGE"}
