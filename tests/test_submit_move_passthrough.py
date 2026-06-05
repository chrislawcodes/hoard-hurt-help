"""Submit packs PD's action OR a free-form move into the generic move dict.

PD bots send action/target_id (unchanged). A non-PD game sends a free-form
`move` the platform passes through untouched. message/thinking ride along either
way.
"""

from __future__ import annotations

from app.routes.agent_api import _move_from_submit
from app.schemas.agent import SubmitRequest


def test_pd_action_packs_unchanged() -> None:
    body = SubmitRequest(turn_token="tk", action="HELP", target_id="B", message="hi")
    assert _move_from_submit(body) == {
        "action": "HELP",
        "target_id": "B",
        "message": "hi",
        "thinking": "",
    }


def test_free_form_move_passes_through() -> None:
    body = SubmitRequest(
        turn_token="tk",
        move={"type": "BID", "quantity": 3, "face": 5},
        message="swimming in fives",
        thinking="actually have two",
    )
    assert _move_from_submit(body) == {
        "type": "BID",
        "quantity": 3,
        "face": 5,
        "message": "swimming in fives",
        "thinking": "actually have two",
    }
