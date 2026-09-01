"""What a seat said on a turn.

The one home for a question the read side used to answer in four places — three
of them agreeing, and one, the match export, not asking it at all. That is how
three seats in M_7975 exported as silent across all 35 of their moves while the
viewer showed them talking the whole match.

Talk lives in ``TurnMessage.text``, and has only done so since migration 0013
added the talk phase. Before that a seat's words rode along with its move in
``TurnSubmission.message``. Both eras are still in the database and both are
still read, so answering "what did this seat say" means asking the talk row
first and falling back to the move for matches played before talk had a row of
its own.

The ``None`` / ``""`` split carries the rule and is not cosmetic. A seat holding
a talk row that says nothing DID speak, and its silence is the answer:
``finalize_talk_phase`` materializes an empty row for every active player, so an
empty row is the normal shape of a modern quiet turn, not a missing answer. Only
an ABSENT talk row means the answer lives on the move instead.
"""

from __future__ import annotations


def seat_talk_text(talk_text: str | None, submission_message: str) -> str:
    """The words one seat said on one turn.

    ``talk_text`` is that seat's ``TurnMessage.text`` for the turn, or ``None``
    when the seat has no talk row at all. ``submission_message`` is the
    pre-talk-phase fallback carried on the seat's own move.
    """
    if talk_text is None:
        return submission_message
    return talk_text
