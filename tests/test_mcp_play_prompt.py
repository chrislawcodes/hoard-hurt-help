"""The MCP play prompt: one wording, two places, and the guard rails that earn it.

``app/routes/connections_connect_guide.py`` serves the prompt to players and
``docs/setup-mcp.md`` documents it. They are supposed to be the same text. They
had already drifted (the doc grew three sentences the served copy never got, and
kept a stale "~25s" hold), which is exactly the failure a copy-paste pair invites,
so the first test here pins them together.

The rest pin the two phrases that were *measured* to change client behaviour — see
the comment above ``_PLAY_PROMPT`` for the Codex 0.146.1 numbers.
"""

from __future__ import annotations

from pathlib import Path

from app.routes.connections_connect_guide import _PLAY_PROMPT

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
SETUP_DOC: Path = REPO_ROOT / "docs" / "setup-mcp.md"


def test_play_prompt_matches_docs() -> None:
    """The served prompt appears verbatim in ``docs/setup-mcp.md``.

    Both copies are pasted by real players, so a doc that has drifted is a player
    running a different prompt than the site handed them.
    """
    doc = SETUP_DOC.read_text(encoding="utf-8")
    assert _PLAY_PROMPT in doc, (
        "docs/setup-mcp.md no longer contains the served play prompt verbatim. "
        "Update the ```text block in the 'MCP connection' section to match "
        "_PLAY_PROMPT in app/routes/connections_connect_guide.py."
    )


def test_play_prompt_sends_the_poll_loop_to_the_holding_tool() -> None:
    """The prompt must name ``get_next_turn`` and rule ``get_next_turns`` out.

    ``get_next_turns`` is the fan-out endpoint and the one lane that never holds
    (``pace_idle(can_hold=False)``). A client that polls it opts out of the long
    poll without knowing it, and falls into the hot loop the hold exists to
    prevent — measured at 30 calls in 100 seconds on Codex 0.146.1.
    """
    assert "get_next_turn" in _PLAY_PROMPT
    assert "Do NOT poll `get_next_turns`" in _PLAY_PROMPT


def test_play_prompt_says_the_call_itself_is_the_wait() -> None:
    """The prompt must say the call blocks.

    Nothing in the tool list tells a client that ``get_next_turn`` holds the
    request open, so without this sentence it has no reason to treat one call as
    one wait. Adding it took the same client to a 90.4s hold, then a fresh call
    2.2s later.
    """
    assert "blocking call" in _PLAY_PROMPT
    assert "the call itself IS your wait" in _PLAY_PROMPT
