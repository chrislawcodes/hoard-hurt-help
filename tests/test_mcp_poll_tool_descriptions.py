"""The two poll tools must tell a client which one waits.

``get_next_turn`` and ``get_next_turns`` differ by one letter, and their
descriptions are all a client has to tell them apart. Picking wrong is silently
expensive: ``get_next_turns`` is the fan-out lane, exempt from holding
(``pace_idle(can_hold=False)``), so polling it opts out of the long poll without
any error — measured at 30 calls in 100 seconds on Codex 0.146.1.

These pin the two facts that changed that behaviour, and pin the hold number to
its live setting so the description cannot quietly become a lie.
"""

from __future__ import annotations

from app.engine.agent_idle import LONG_POLL_HOLD_SECONDS


async def _descriptions() -> dict[str, str]:
    """Registered descriptions, with newlines flattened to single spaces.

    The text is hard-wrapped, so a phrase can straddle a line break. Normalising
    here keeps these tests about the *wording* and lets anyone rewrap the
    paragraphs without a spurious failure.
    """
    from mcp_server.server import mcp_app

    return {
        tool.name: " ".join((tool.description or "").split())
        for tool in await mcp_app.list_tools()
    }


async def test_get_next_turn_says_it_blocks() -> None:
    """The polling tool must say the call itself is the wait.

    Nothing else in the tool list implies it, so without this a client has no
    reason to treat one call as one wait rather than something to hammer.
    """
    description = (await _descriptions())["get_next_turn"]
    assert "blocking call" in description
    assert "IS your wait" in description
    assert "poll in a loop" in description


async def test_get_next_turns_says_it_is_not_for_polling() -> None:
    """The fan-out tool must warn against looping on it, and point at the other.

    Its old description read "use this to discover how many agents you are
    running before fanning out" with no such warning, which is what invited the
    hot loop in the first place.
    """
    description = (await _descriptions())["get_next_turns"]
    assert "NOT a polling tool" in description
    assert "never waits" in description
    assert "Poll get_next_turn instead" in description


async def test_advertised_hold_tracks_the_live_setting() -> None:
    """The seconds figure clients read must come from the real setting.

    A hardcoded second copy of this number is exactly how the old 25s cap made a
    hold change silently do nothing. If ``agent_long_poll_hold_seconds`` moves,
    the advertised number must move with it.
    """
    description = (await _descriptions())["get_next_turn"]
    assert f"about {LONG_POLL_HOLD_SECONDS:.0f} seconds" in description


async def test_each_poll_tool_names_the_other() -> None:
    """Each description must name its twin, so the choice is explicit.

    The pair is only confusable together; a client reading either one alone
    should still learn which of the two to poll.
    """
    descriptions = await _descriptions()
    assert "get_next_turns" in descriptions["get_next_turn"]
    assert "get_next_turn" in descriptions["get_next_turns"]
